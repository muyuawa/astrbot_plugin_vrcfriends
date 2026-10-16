"""Synchronous vrchatapi operations; each instance has its own SDK CookieJar."""

import json
import re
from http.cookiejar import Cookie

import vrchatapi
from vrchatapi.api.authentication_api import AuthenticationApi
from vrchatapi.api.friends_api import FriendsApi
from vrchatapi.api.worlds_api import WorldsApi
from vrchatapi.exceptions import ApiException
from vrchatapi.models.two_factor_auth_code import TwoFactorAuthCode
from vrchatapi.models.two_factor_email_code import TwoFactorEmailCode


class VRCError(ValueError):
    def __init__(self, message, status=0, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class VRCClient:
    def __init__(self, cookies=None, user_agent="", proxy=""):
        if not re.search(r" (https://\S+|mailto:\S+)", user_agent):
            raise ValueError(
                "请管理员先在插件配置中填写 contact_url（有效的 HTTPS 联系地址或 mailto:邮箱）。"
            )
        configuration = vrchatapi.Configuration()
        configuration.retries = 0
        if proxy:
            configuration.proxy = proxy
        self.api = vrchatapi.ApiClient(configuration)
        self.api.user_agent = user_agent
        self.auth = AuthenticationApi(self.api)
        self.friend_api = FriendsApi(self.api)
        self.world_api = WorldsApi(self.api)
        for name, value in (cookies or {}).items():
            if name in {"auth", "twoFactorAuth"}:
                self.api.rest_client.cookie_jar.set_cookie(
                    Cookie(
                        0,
                        name,
                        value,
                        None,
                        False,
                        "api.vrchat.cloud",
                        False,
                        False,
                        "/",
                        True,
                        True,
                        None,
                        True,
                        None,
                        None,
                        {},
                        False,
                    )
                )

    def export_cookies(self):
        return {
            c.name: c.value
            for c in self.api.rest_client.cookie_jar
            if c.name in {"auth", "twoFactorAuth"}
        }

    def _raw(self, operation, *args, _login_attempt=False, **kwargs):
        try:
            response = operation(
                *args, _preload_content=False, _request_timeout=(10, 25), **kwargs
            )
        except ApiException as exc:
            try:
                body = json.loads(exc.body or "{}")
            except (ValueError, TypeError):
                body = {}
            if isinstance(body, dict) and body.get("requiresTwoFactorAuth"):
                return body
            detail = body.get("error", body) if isinstance(body, dict) else body
            message = detail.get("message", "") if isinstance(detail, dict) else detail
            message = message.lower() if isinstance(message, str) else ""
            headers = exc.headers or {}
            try:
                retry_after = min(3600, max(60, int(headers.get("Retry-After", 60))))
            except (ValueError, TypeError):
                retry_after = 60
            if exc.status == 429:
                raise VRCError(
                    "VRChat 请求过于频繁，请稍后重试。", 429, retry_after
                ) from None
            # Link confirmation can return only "Check your email"; a 2FA challenge takes priority above.
            if any(
                x in message
                for x in [
                    "login location",
                    "login place",
                    "new location",
                    "verifyloginplace",
                ]
            ) or (_login_attempt and "check your email" in message):
                raise VRCError(
                    "需要通过邮件确认新的登录地点。\n"
                    "请查看 VRChat 发到绑定邮箱的“please verify your login”邮件，"
                    "确认是本次操作后点击邮件中的确认链接。\n"
                    "完成后重新发送 /vrc 登录 用户名 密码；这一步需要点击邮件链接。",
                    exc.status,
                ) from None
            if "captcha" in message or "cloudflare" in message:
                raise VRCError(
                    "VRChat 要求网页人机验证，请先在 https://vrchat.com/home/login 完成验证后重试。",
                    403,
                ) from None
            if (
                _login_attempt
                and exc.status in {400, 401}
                and "invalid username/email or password" in message
            ):
                raise VRCError(
                    "用户名或密码错误，请检查后重新发送 /vrc 登录 用户名 密码。",
                    exc.status,
                ) from None
            if _login_attempt and exc.status == 401:
                raise VRCError(
                    "登录未通过，VRChat 未提供具体原因。\n"
                    "若绑定邮箱收到新地点登录确认邮件，请确认是本次操作并点击确认链接，"
                    "再重新发送 /vrc 登录 用户名 密码。\n"
                    "未收到确认邮件时，请检查用户名、密码，或到 VRChat 官网查看账号状态。",
                    401,
                ) from None
            if exc.status in {400, 401}:
                raise VRCError(
                    "认证失败：请检查用户名、密码或验证码；验证码可能已过期，会话失效时请重新登录。",
                    exc.status,
                ) from None
            if exc.status == 403:
                raise VRCError(
                    "VRChat 拒绝了当前登录或访问，请到官网检查邮箱验证、登录地点和账号状态。",
                    403,
                ) from None
            raise VRCError(
                f"VRChat 暂时无法访问（HTTP {exc.status or '网络错误'}），请稍后重试。",
                exc.status or 0,
            ) from None
        try:
            data = response.data
            return json.loads(data) if data else {}
        except (ValueError, UnicodeDecodeError):
            raise VRCError(
                "VRChat 返回了网页或无法识别的响应，请到官网确认是否需要额外验证。"
            ) from None
        finally:
            response.release_conn()

    def login(self, username, password):
        self.api.configuration.username = username
        self.api.configuration.password = password
        try:
            return self._raw(self.auth.get_current_user, _login_attempt=True)
        finally:
            self.api.configuration.username = None
            self.api.configuration.password = None

    def profile(self):
        return self._raw(self.auth.get_current_user)

    def verify(self, code, method):
        operations = {
            "totp": (self.auth.verify2_fa, TwoFactorAuthCode),
            "emailOtp": (self.auth.verify2_fa_email_code, TwoFactorEmailCode),
            "otp": (self.auth.verify_recovery_code, TwoFactorAuthCode),
        }
        if method not in operations:
            raise ValueError("这种验证方式需要在 VRChat 官网完成。")
        operation, model = operations[method]
        result = self._raw(operation, model(code=code))
        if result.get("verified") is not True:
            raise ValueError("验证码不正确或已经过期，请获取新验证码后重试。")
        return self.profile()

    def friends(self):
        result = {}
        for offline in [False, True]:
            for offset in range(0, 10000, 100):
                page = self._raw(
                    self.friend_api.get_friends, offline=offline, n=100, offset=offset
                )
                if not isinstance(page, list):
                    raise VRCError("好友列表返回格式异常，请稍后重试。")
                for friend in page:
                    friend["_api_online"] = not offline
                    result.setdefault(friend["id"], friend)
                if len(page) < 100:
                    break
            else:
                raise VRCError("好友列表超过当前安全读取上限，未保存不完整列表。")
        return list(result.values())

    def world(self, world_id):
        return self._raw(self.world_api.get_world, world_id)

    def logout(self):
        return self._raw(self.auth.logout)

    def close(self):
        self.api.close()
