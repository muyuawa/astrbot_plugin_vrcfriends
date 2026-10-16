import asyncio
from contextlib import suppress
from pathlib import Path
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api.message_components import Image, Reply as QuotedReply
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .bootstrap import SetupError, ensure_python_dependencies


class VRCFriends(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.root = (
            Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_vrcfriends"
        )
        self.store = self.service = self.controller = self.cards = None
        self.startup_error = ""
        self.poll_task = None
        self.browser_task = None

    async def initialize(self):
        try:
            await ensure_python_dependencies(
                Path(__file__).parent / "requirements.txt", logger
            )
            from .cards import Cards
            from .commands import Controller
            from .service import Service
            from .store import Store

            contact = str(self.config.get("contact_url", "")).strip()
            self.store = Store(self.root)
            self.service = Service(
                self.store,
                user_agent=f"AstrBot-VRCFriends/0.1.3 {contact}",
                proxy=str(self.config.get("proxy", "")),
            )
            self.controller = Controller(self.service)
            self.cards = Cards(
                self.root,
                self.store,
                str(self.config.get("browser_path", "")),
                **self.service.client_options,
            )
        except Exception as exc:
            self.startup_error = (
                str(exc)
                if isinstance(exc, SetupError)
                else "插件依赖初始化失败，请检查 AstrBot 日志并重载插件。"
            )
            logger.warning("VRC Friends: %s", self.startup_error)
            return
        self.poll_task = asyncio.create_task(
            self._poll_loop(), name="vrcfriends-http-poll"
        )
        self.browser_task = asyncio.create_task(
            self._prepare_browser(), name="vrcfriends-browser-setup"
        )

    async def _prepare_browser(self):
        try:
            await self.cards.prepare()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "VRC Friends: 浏览器自动准备失败，图片暂时降级为文字。请检查浏览器下载网络和系统组件后重载插件。"
            )

    def owner(self, event):
        # The platform type and sender QQ identify the owner, never a group number.
        return f"{event.get_platform_name()}:{event.get_sender_id()}"

    @filter.command("vrc")
    async def vrc(self, event: AstrMessageEvent):
        if self.startup_error or self.controller is None:
            yield event.plain_result(
                self.startup_error or "正在自动安装插件依赖，请稍后重试。"
            )
            event.stop_event()
            return
        from .cards import text_fallback

        text = event.get_message_str().strip()
        # AstrBot strips the wake prefix before the command filter.
        if text.startswith("/vrc"):
            text = text[4:].strip()
        elif text.startswith("vrc"):
            text = text[3:].strip()
        owner = self.owner(event)
        image_path = None
        if text.partition(" ")[0] == "背景":
            try:
                image_path = await self._image(event.get_messages())
            except Exception:
                yield event.plain_result(
                    "图片暂时无法读取，请重新发送图片并附上 /vrc 背景。"
                )
                event.stop_event()
                return
        reply = await self.controller.execute(
            owner, event.unified_msg_origin, event.is_private_chat(), text, image_path
        )
        if reply.card:
            if self.browser_task and not self.browser_task.done():
                yield event.plain_result(
                    "正在自动准备图片渲染环境，首次可能需要下载浏览器，请稍候。"
                )
            try:
                image = await self.cards.render(owner, reply.card)
            except Exception:
                logger.warning(
                    "VRC Friends: local card rendering unavailable; using text response."
                )
                yield event.plain_result(
                    text_fallback(reply.card) + "\n（图片渲染暂不可用，已改用文字。）"
                )
            else:
                yield event.image_result(image)
        elif reply.text:
            yield event.plain_result(reply.text)
        event.stop_event()

    async def _image(self, components):
        for component in components:
            if isinstance(component, Image):
                return await component.convert_to_file_path()
            if isinstance(component, QuotedReply) and component.chain:
                result = await self._image(component.chain)
                if result:
                    return result
        return None

    async def _send_alert(self, owner, session, friend):
        from .cards import text_fallback

        account = self.store.account(owner)
        if not account or not account["active"]:
            raise ValueError("Account disconnected")
        favorite_ids = {f["id"] for f in self.store.favorites(owner)}
        friend = {
            **friend,
            "favorite": friend["id"] in favorite_ids,
            "subscribed": True,
        }
        data = {
            "title": friend["name"] + " 上线啦",
            "owner_name": account["name"],
            "friends": [friend],
            "alert": True,
            "updated": time.time(),
            "hint": "/vrc 取消订阅 " + friend["id"],
        }
        try:
            path = await self.cards.render(owner, data)
        except Exception:
            chain = MessageChain().message(text_fallback(data))
        else:
            chain = MessageChain().file_image(path)
        return await self.service.deliver_notification(
            owner, session, friend, lambda: self.context.send_message(session, chain)
        )

    async def _poll_loop(self):
        while True:
            try:
                await self.service.poll(self._send_alert)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "VRC Friends: polling failed; will retry after the configured interval."
                )
            interval = max(60, min(3600, int(self.config.get("poll_interval", 120))))
            await asyncio.sleep(interval)

    async def terminate(self):
        if self.browser_task:
            self.browser_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.browser_task
        if self.poll_task:
            self.poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.poll_task
        if self.cards:
            await self.cards.close()
        if self.service:
            await self.service.close()
        elif self.store:
            self.store.close()
