from dataclasses import dataclass
import math
import shlex

from cryptography.fernet import InvalidToken


HELP = """VRC 好友 · 聊天指令
私聊登录：/vrc 登录 用户名 密码
验证：/vrc 验证 123456 [totp/emailOtp/otp]
指定验证：/vrc 邮箱验证 123456
恢复码：/vrc 恢复码 你的恢复码
Cookie 登录：/vrc Cookie authCookie [twoFactorAuthCookie]
账号状态：/vrc 账号　退出：/vrc 退出
好友列表：/vrc 好友 [页码/昵称/用户ID]
包含离线：/vrc 好友 全部 [页码]
查找好友：/vrc 查找 关键字 [页码]（包含离线）
收藏优先：/vrc 收藏 昵称　取消：/vrc 取消收藏 昵称
收藏列表：/vrc 收藏 [页码]
上线提醒：/vrc 订阅 昵称　取消：/vrc 取消订阅 昵称
订阅列表：/vrc 订阅 [页码]
每张图人数：/vrc 每页 8（1–30）
自定义背景：发送图片并附上 /vrc 背景
恢复白底：/vrc 背景 默认
昵称可替换成当前会话最近列表的 #编号。收藏用于优先展示，订阅才发送上线提醒。"""


@dataclass
class Reply:
    text: str = ""
    card: dict | None = None


class Controller:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    async def execute(self, owner, session, private, text, image=None):
        try:
            return await self._execute(owner, session, private, text, image)
        except InvalidToken:
            return Reply(
                "本 QQ 的登录密文无法解密，请检查 session.key 是否被替换，或退出后重新登录。"
            )
        except ValueError as exc:
            return Reply(str(exc))
        except Exception:
            return Reply(
                "暂时无法完成请求，请检查网络连接后重试。登录信息不会在错误消息中显示。"
            )

    async def _execute(self, owner, session, private, text, image):
        action, _, argument = text.strip().partition(" ")
        argument = argument.strip()
        if (
            action in {"登录", "验证", "验证器", "邮箱验证", "恢复码"}
            or action.lower() == "cookie"
        ) and not private:
            return Reply("登录凭据和验证码请在私聊中发送：/vrc 登录。")
        if action in {"", "帮助", "help"}:
            return Reply(HELP)
        if action == "登录":
            if not argument:
                return Reply(
                    "私聊发送：/vrc 登录 用户名 密码\n包含空格的参数请用英文双引号包住。支持验证器、邮箱验证码与恢复码；密码和验证码不保存。"
                )
            try:
                parts = shlex.split(argument)
            except ValueError:
                return Reply("登录参数引号未配对，请检查后重新发送。")
            if len(parts) != 2:
                return Reply("格式：/vrc 登录 用户名 密码。包含空格时使用英文双引号。")
            methods = await self.service.login(owner, *parts)
            if methods:
                labels = {
                    "totp": "验证器验证码",
                    "emailOtp": "邮箱验证码",
                    "otp": "恢复码",
                }
                required = "、".join(labels.get(x, x) for x in methods)
                return Reply(
                    f"还需要验证：{required}。\n请发送 /vrc 验证 123456；恢复码使用 /vrc 恢复码 <恢复码>。\n也可指定 /vrc 验证 <验证码> <totp/emailOtp/otp>，10 分钟内完成。"
                )
            return Reply(
                "登录成功，账号已按当前 QQ 独立加密保存。使用 /vrc 好友 查看好友。"
            )
        if action in {"验证", "验证器", "邮箱验证", "恢复码"}:
            parts = argument.split()
            if not 1 <= len(parts) <= 2:
                return Reply(
                    "格式：/vrc 验证 <验证码> [totp/emailOtp/otp]，或 /vrc 恢复码 <恢复码>。"
                )
            aliases = {"验证器": "totp", "邮箱验证": "emailOtp", "恢复码": "otp"}
            method = aliases.get(action) or (parts[1] if len(parts) == 2 else None)
            await self.service.verify(owner, parts[0], method)
            return Reply("登录成功，已验证并按当前 QQ 独立加密保存。")
        if action.lower() == "cookie":
            parts = argument.split()
            if not 1 <= len(parts) <= 2 or any(
                "\r" in p or "\n" in p or ";" in p for p in parts
            ):
                return Reply(
                    "私聊格式：/vrc Cookie <auth Cookie 值> [twoFactorAuth Cookie 值]。只填写值，不带 Cookie 名。"
                )
            cookies = {"auth": parts[0]}
            if len(parts) == 2:
                cookies["twoFactorAuth"] = parts[1]
            await self.service.import_cookies(owner, cookies)
            return Reply("Cookie 验证成功，登录会话已独立加密保存。")
        if action == "退出":
            await self.service.logout(owner)
            return Reply(
                "已清除本 QQ 的登录会话并停止检测。收藏、订阅和背景保留，重新连接同一 VRC 账号后可继续使用。"
            )
        if action == "账号":
            account = self.store.account(owner)
            if not account:
                return Reply("当前 QQ 尚未登录，请在私聊中使用 /vrc 登录。")
            _, updated = self.store.cached_friends(owner)
            state = "已连接" if account["active"] else "登录失效，请重新登录"
            return Reply(
                f"当前 VRC：{account['name']}\n状态：{state}\n登录会话独立加密保存，不主动维持游戏在线。\n每张图：{self.store.page_size(owner)} 位好友"
            )
        if action == "每页":
            if not argument.isascii() or not argument.isdigit():
                return Reply(
                    f"当前每张图最多 {self.store.page_size(owner)} 位好友。设置：/vrc 每页 8（1–30）。"
                )
            self.store.set_page_size(owner, int(argument))
            return Reply(f"已设置：每张图最多 {int(argument)} 位好友，超出自动分页。")
        if action == "背景":
            if argument in {"默认", "重置"}:
                self.store.reset_background(owner)
                return Reply("已恢复默认白色背景，卡片保留轻毛玻璃效果。")
            if not image:
                return Reply(
                    "请发送图片并附上 /vrc 背景，或回复一张图片发送 /vrc 背景。恢复白底：/vrc 背景 默认。"
                )
            self.store.set_background(owner, image)
            return Reply("已更新你的 VRC 卡片背景，不影响其他 QQ。")
        query, search_page = "", 1
        if action == "查找":
            try:
                parts = shlex.split(argument)
            except ValueError:
                return Reply("查找参数引号未配对，请检查后重新发送。")
            if not parts:
                return Reply(
                    "格式：/vrc 查找 昵称或用户ID [页码]。支持部分昵称，包含离线好友；结果可用 #编号 收藏或订阅。"
                )
            if len(parts) > 1 and parts[-1].isascii() and parts[-1].isdigit():
                search_page = int(parts.pop())
            query = " ".join(parts).strip()
            if not query:
                return Reply("请输入好友昵称或用户 ID。")
        if action not in {"好友", "查找", "收藏", "订阅", "取消收藏", "取消订阅"}:
            return Reply("未知指令，请发送 /vrc 帮助。")
        friends = await self.service.sync(owner)
        favorites = {x["id"]: x for x in self.store.favorites(owner)}
        subscriptions = {x["id"]: x for x in self.store.subscriptions(owner, session)}
        friends = [
            {
                **f,
                "favorite": f["id"] in favorites,
                "subscribed": f["id"] in subscriptions,
            }
            for f in friends
        ]
        friends.sort(
            key=lambda f: (
                not f["favorite"],
                f["presence"] != "game",
                f["name"].casefold(),
            )
        )
        if action in {"取消收藏", "取消订阅"} or (
            action in {"收藏", "订阅"} and argument and not argument.isdigit()
        ):
            if not argument:
                return Reply("请提供好友昵称、用户 ID 或最近列表中的 #编号。")
            candidates = list(friends)
            if action.startswith("取消"):
                records = favorites if action == "取消收藏" else subscriptions
                known = {x["id"] for x in candidates}
                candidates += [
                    {"id": x["id"], "name": x["name"]}
                    for x in records.values()
                    if x["id"] not in known
                ]
            friend = self.service.resolve(owner, session, argument, candidates)
            enabled = not action.startswith("取消")
            if action in {"收藏", "取消收藏"}:
                self.store.set_favorite(owner, friend["id"], friend["name"], enabled)
                return Reply(
                    ("已收藏 " if enabled else "已取消收藏 ")
                    + friend["name"]
                    + ("，会优先展示。" if enabled else "。")
                )
            self.store.set_subscription(
                owner, session, friend["id"], friend["name"], enabled
            )
            return Reply(
                ("已订阅 " if enabled else "已取消订阅 ")
                + friend["name"]
                + ("，进入游戏时将在当前会话提醒你。" if enabled else "。")
            )
        page = 1
        if action == "查找":
            page = search_page
            selected = [
                f
                for f in friends
                if query.casefold() in f["name"].casefold()
                or query.casefold() in f["id"].casefold()
            ]
        elif action == "好友" and argument.startswith("全部"):
            suffix = argument.removeprefix("全部").strip()
            if suffix and not suffix.isdigit():
                return Reply("格式：/vrc 好友 全部 [页码]。")
            page = int(suffix or 1)
            selected = friends
        elif argument and not argument.isdigit():
            selected = [self.service.resolve(owner, session, argument, friends)]
        else:
            page = int(argument or 1)
            selected = (
                [f for f in friends if f["presence"] == "game"]
                if action == "好友"
                else [
                    f
                    for f in friends
                    if f["id"] in (favorites if action == "收藏" else subscriptions)
                ]
            )
        size = self.store.page_size(owner)
        pages = max(1, math.ceil(len(selected) / size))
        if not 1 <= page <= pages:
            return Reply(f"页码超出范围，当前共 {pages} 页。")
        self.service.remember_list(owner, session, selected)
        start = (page - 1) * size
        items = [
            {**f, "number": start + i + 1}
            for i, f in enumerate(selected[start : start + size])
        ]
        if page < pages:
            mode = "全部 " if action == "好友" and argument.startswith("全部") else ""
            if action == "查找":
                mode = shlex.quote(query) + " "
            hint = f"/vrc {action} {mode}{page + 1}"
        elif items:
            number = f"#{items[0]['number']:02d}"
            hint = (
                f"/vrc 取消{action} {number}"
                if action in {"收藏", "订阅"}
                else f"/vrc 收藏 {number}  ·  /vrc 订阅 {number}"
            )
        else:
            hint = "/vrc 帮助"
        account = self.store.account(owner)
        _, updated = self.store.cached_friends(owner)
        title = (
            f"找到 {len(selected)} 位好友"
            if action == "查找"
            else f"{sum(f['presence'] == 'game' for f in friends)} 位好友在线"
            if action == "好友"
            else "我的收藏"
            if action == "收藏"
            else "我的订阅"
        )
        card = {
            "title": title,
            "owner_name": account["name"],
            "friends": items,
            "page": page,
            "pages": pages,
            "page_size": size,
            "total": len(selected),
            "favorite_count": len(favorites),
            "subscription_count": len(subscriptions),
            "updated": updated,
            "hint": hint,
            "kind": action,
            "web_count": sum(f["presence"] == "web" for f in friends),
            "all_count": len(friends),
        }
        return Reply(card=card)
