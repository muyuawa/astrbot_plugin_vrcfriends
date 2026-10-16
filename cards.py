import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import uuid

from jinja2 import Environment, FileSystemLoader, select_autoescape
from .bootstrap import install_chromium
from .assets import ImageAssets


class Cards:
    def __init__(self, root, store, executable_path="", user_agent="", proxy=""):
        self.root = Path(root) / "cards"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.executable_path = executable_path
        self.environment = Environment(
            loader=FileSystemLoader(Path(__file__).parent / "templates"),
            autoescape=select_autoescape(["html"]),
        )
        self.browser = None
        self.playwright = None
        self.lock = asyncio.Lock()
        self.install_attempted = False
        self.browser_error = ""
        self.assets = ImageAssets(user_agent, proxy)

    def html(self, owner, data):
        payload = dict(data)
        stamp = payload.get("updated") or datetime.now().timestamp()
        payload["time"] = datetime.fromtimestamp(
            stamp, timezone(timedelta(hours=8))
        ).strftime("%m-%d %H:%M")
        background = self.store.background(owner)
        payload["background"] = (
            "data:image/jpeg;base64,"
            + base64.b64encode(background.read_bytes()).decode()
            if background
            else ""
        )
        payload.setdefault("alert", False)
        payload.setdefault("favorite_count", len(self.store.favorites(owner)))
        payload.setdefault("subscription_count", 0)
        payload.setdefault("page_size", self.store.page_size(owner))
        payload.setdefault("all_count", payload.get("total", 0))
        payload.setdefault("web_count", 0)
        return self.environment.get_template("card.html").render(**payload)

    async def prepare(self):
        async with self.lock:
            await self._prepare_locked()

    async def _prepare_locked(self):
        from playwright.async_api import async_playwright

        if self.browser is not None and self.browser.is_connected():
            return
        if self.browser_error:
            raise RuntimeError(self.browser_error)
        try:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            options = {"headless": True}
            if self.executable_path:
                options["executable_path"] = self.executable_path
            try:
                self.browser = await self.playwright.chromium.launch(**options)
            except Exception as exc:
                missing = "executable doesn't exist" in str(exc).lower()
                if self.executable_path or not missing or self.install_attempted:
                    raise
                self.install_attempted = True
                await install_chromium()
                self.browser = await self.playwright.chromium.launch(**options)
        except Exception:
            self.browser_error = "本地浏览器自动准备失败，已切换文字回复。请检查 AstrBot 安装日志、网络及浏览器系统组件后重载插件。"
            raise RuntimeError(self.browser_error) from None

    async def render(self, owner, data):
        account = self.store.account(owner)
        if account and account["active"]:
            data = {
                **data,
                "friends": await self.assets.load(owner, account, data["friends"]),
            }
        async with self.lock:
            await self._prepare_locked()
            context = await self.browser.new_context(
                viewport={"width": 628, "height": 900}, device_scale_factor=2
            )
            try:
                page = await context.new_page()
                await page.route("**/*", lambda route: route.abort())
                await page.set_content(self.html(owner, data), wait_until="load")
                await page.evaluate("document.fonts.ready")
                await page.evaluate(
                    "Promise.all(Array.from(document.images, img => img.decode().catch(() => {})))"
                )
                folder = self.root / hashlib.sha256(owner.encode()).hexdigest()
                folder.mkdir(exist_ok=True)
                output = folder / (uuid.uuid4().hex + ".png")
                await page.locator(".canvas").screenshot(path=str(output))
                for previous in sorted(
                    folder.glob("*.png"), key=lambda p: p.stat().st_mtime
                )[:-12]:
                    previous.unlink(missing_ok=True)
                return str(output)
            finally:
                await context.close()

    async def close(self):
        self.assets.close()
        async with self.lock:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()


def text_fallback(data):
    lines = [data["title"], "当前 VRC：" + data["owner_name"]]
    for friend in data["friends"]:
        number = f"#{friend['number']:02d} " if friend.get("number") else ""
        flags = (" ★收藏" if friend.get("favorite") else "") + (
            " · 已订阅" if friend.get("subscribed") else ""
        )
        lines.append(
            f"{number}{friend['name']} [{friend['status_label']}]{flags}\n  {friend['location_text']}"
            + (f"\n  {friend['meta']}" if friend["meta"] else "")
        )
    if not data["friends"]:
        lines.append("暂无符合条件的好友。")
    if not data.get("alert"):
        lines.append(
            f"第 {data['page']}/{data['pages']} 页 · 每页 {data['page_size']} 人"
        )
    lines.append(data.get("hint", "/vrc 帮助"))
    return "\n".join(lines)
