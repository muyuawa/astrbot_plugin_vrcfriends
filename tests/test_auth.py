import json
import time
from types import SimpleNamespace

import pytest
from vrchatapi.exceptions import ApiException

from astrbot_plugin_vrcfriends.client import VRCClient, VRCError
from astrbot_plugin_vrcfriends.commands import Controller
from astrbot_plugin_vrcfriends.service import Service
from astrbot_plugin_vrcfriends.store import Store

from .test_service import FakeClient, connected


@pytest.mark.parametrize(
    "method,path,code",
    [
        ("totp", "/auth/twofactorauth/totp/verify", "123456"),
        ("emailOtp", "/auth/twofactorauth/emailotp/verify", "654321"),
        ("otp", "/auth/twofactorauth/otp/verify", "recovery-code"),
    ],
)
def test_sdk_routes_each_verification_method_and_confirms_identity(
    monkeypatch, method, path, code
):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")
    calls = []

    def request(http_method, url, **kwargs):
        calls.append((http_method, url, kwargs.get("body")))
        data = (
            {"verified": True}
            if http_method == "POST"
            else {"id": "usr_verified", "displayName": "Verified"}
        )
        return SimpleNamespace(
            data=json.dumps(data).encode(), release_conn=lambda: None
        )

    monkeypatch.setattr(client.api, "request", request)
    assert client.verify(code, method)["id"] == "usr_verified"
    assert calls[0] == ("POST", "https://api.vrchat.cloud/api/1" + path, {"code": code})
    assert calls[1][0:2] == ("GET", "https://api.vrchat.cloud/api/1/auth/user")
    client.close()


def test_sdk_401_two_factor_challenge_is_not_mistaken_for_wrong_password():
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def operation(**kwargs):
        error = ApiException(status=401)
        error.body = '{"requiresTwoFactorAuth":["totp","otp"]}'
        raise error

    assert client._raw(operation) == {"requiresTwoFactorAuth": ["totp", "otp"]}
    client.close()


@pytest.mark.parametrize(
    "status,message",
    [
        (401, '"Check your email"'),
        (403, "Check your email for a verification link"),
        (401, "New login location detected"),
    ],
)
def test_new_login_location_prompts_for_email_link_then_manual_login(
    monkeypatch, status, message
):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def request(*args, **kwargs):
        error = ApiException(status=status)
        error.body = json.dumps({"error": {"message": message + " SECRET"}}).encode()
        raise error

    monkeypatch.setattr(client.api, "request", request)
    with pytest.raises(VRCError) as caught:
        client.login("test-user", "test-password")
    text = str(caught.value)
    assert "登录地点" in text
    assert "确认链接" in text
    assert "/vrc 登录" in text
    assert "/vrc 验证" not in text
    assert "认证失败" not in text and "SECRET" not in text
    assert caught.value.status == status
    assert client.api.configuration.username is None
    assert client.api.configuration.password is None
    client.close()


def test_unspecified_login_401_mentions_location_confirmation_without_claiming_a_cause(
    monkeypatch,
):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def request(*args, **kwargs):
        error = ApiException(status=401)
        error.body = json.dumps({"error": {"message": "Unauthorized"}}).encode()
        raise error

    monkeypatch.setattr(client.api, "request", request)
    with pytest.raises(VRCError) as caught:
        client.login("test-user", "test-password")
    text = str(caught.value)
    assert "未提供具体原因" in text
    assert "新地点" in text and "确认链接" in text
    assert "用户名或密码错误" not in text
    assert "/vrc 登录" in text
    client.close()


def test_explicit_invalid_credentials_do_not_prompt_for_login_location(monkeypatch):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def request(*args, **kwargs):
        error = ApiException(status=401)
        error.body = json.dumps(
            {"error": {"message": "Invalid Username/Email or Password SECRET"}}
        ).encode()
        raise error

    monkeypatch.setattr(client.api, "request", request)
    with pytest.raises(VRCError) as caught:
        client.login("test-user", "test-password")
    text = str(caught.value)
    assert "用户名或密码错误" in text
    assert "登录地点" not in text and "SECRET" not in text
    client.close()


@pytest.mark.parametrize("challenge_status", [200, 401])
async def test_location_confirmation_does_not_save_login_and_retry_can_continue_with_email_otp(
    tmp_path, monkeypatch, challenge_status
):
    clients = []
    requests = []

    def factory(**options):
        client = VRCClient(user_agent="Tests/1.0 https://example.org")
        clients.append(client)

        def request(method, url, **kwargs):
            requests.append((method, url))
            if len(requests) == 1:
                error = ApiException(status=401)
                error.body = json.dumps(
                    {"error": {"message": "Check your email"}}
                ).encode()
                raise error
            if len(requests) == 2:
                data = {
                    "requiresTwoFactorAuth": ["emailOtp"],
                    "error": {"message": "Check your email"},
                }
                if challenge_status == 401:
                    error = ApiException(status=401)
                    error.body = json.dumps(data).encode()
                    raise error
            elif method == "POST":
                data = {"verified": True}
            else:
                data = {"id": "usr_confirmed", "displayName": "Confirmed"}
            return SimpleNamespace(
                data=json.dumps(data).encode(), release_conn=lambda: None
            )

        monkeypatch.setattr(client.api, "request", request)
        return client

    service = Service(Store(tmp_path), client_factory=factory)
    controller = Controller(service)
    owner = "qq:test"
    first = await controller.execute(
        owner, "private:test", True, "登录 test-user test-password"
    )
    assert "登录地点" in first.text and "确认链接" in first.text
    assert service.store.account(owner) is None
    assert owner not in service.pending
    assert (
        len(requests) == 1
    )  # Confirmation must never trigger automatic password retries.
    assert clients[0].api.configuration.password is None
    second = await controller.execute(
        owner, "private:test", True, "登录 test-user test-password"
    )
    assert "邮箱验证码" in second.text and "/vrc 验证 123456" in second.text
    assert "登录地点" not in second.text and "确认链接" not in second.text
    assert service.store.account(owner) is None
    third = await controller.execute(owner, "private:test", True, "验证 123456")
    assert "登录成功" in third.text
    assert service.store.account(owner)["vrc_id"] == "usr_confirmed"
    assert service.store.account("qq:other") is None
    assert owner not in service.pending
    await service.close()


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (429, {"error": {"message": "rate limited"}}, "频繁"),
        (401, {"error": {"message": "bad password SECRET"}}, "认证失败"),
        (403, {"error": {"message": "New login location detected"}}, "登录地点"),
        (403, {"error": {"message": "captcha required"}}, "人机验证"),
        (403, {"error": {"message": "account restricted"}}, "账号状态"),
    ],
)
def test_auth_errors_are_actionable_and_never_echo_server_secrets(
    status, body, expected
):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def operation(**kwargs):
        error = ApiException(status=status)
        error.body = json.dumps(body)
        error.headers = {"Retry-After": "90"}
        raise error

    with pytest.raises(VRCError, match=expected) as caught:
        client._raw(operation)
    assert "SECRET" not in str(caught.value)
    if status == 429:
        assert caught.value.retry_after == 90
    client.close()


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"error": "Check your email"}, "登录地点"),
        ({"message": "Check your email"}, "登录地点"),
        ({"error": None}, "未提供具体原因"),
    ],
)
def test_nonstandard_auth_error_bodies_keep_safe_actionable_messages(
    monkeypatch, body, expected
):
    client = VRCClient(user_agent="Tests/1.0 https://example.org")

    def request(*args, **kwargs):
        error = ApiException(status=401)
        error.body = json.dumps(body).encode()
        raise error

    monkeypatch.setattr(client.api, "request", request)
    with pytest.raises(VRCError, match=expected):
        client.login("test-user", "test-password")
    assert client.api.configuration.password is None
    client.close()


async def test_pending_login_expires_and_repeated_bad_codes_cancel_it(tmp_path):
    service = Service(Store(tmp_path), client_factory=FakeClient)
    await service.login("a", "A", "secret")
    service.pending["a"]["expires"] = time.monotonic() - 1
    with pytest.raises(ValueError, match="过期"):
        await service.verify("a", "123456")
    assert service.store.account("a") is None
    await service.login("a", "A", "secret")
    for _ in range(4):
        with pytest.raises(ValueError):
            await service.verify("a", "000000")
    with pytest.raises(ValueError, match="取消"):
        await service.verify("a", "000000")
    assert "a" not in service.pending


async def test_polling_never_reauthenticates_or_updates_own_presence(tmp_path):
    service = await connected(tmp_path)
    service.store.set_subscription("qq:100", "group:1", "usr_friend", "Friend", True)
    forbidden_calls = []

    def forbidden(*args):
        forbidden_calls.append(args)
        raise AssertionError("poll must not login or fetch the current profile")

    service.clients["qq:100"].login = forbidden
    service.clients["qq:100"].profile = forbidden

    async def send(*args):
        pass

    await service.poll(send)
    await service.poll(send)
    assert service.store.cached_friends("qq:100")[1] > 0
    assert forbidden_calls == []


def test_corrupt_session_can_be_replaced_by_a_verified_login(tmp_path):
    store = Store(tmp_path)
    store.save_account("a", "usr_old", "Old", {"auth": "old"})
    store.db.execute("UPDATE accounts SET cookies=? WHERE owner=?", (b"corrupted", "a"))
    store.db.commit()
    store.save_account("a", "usr_new", "New", {"auth": "new"})
    assert store.account("a")["vrc_id"] == "usr_new"


async def test_corrupt_session_can_be_logged_out_without_decrypting(tmp_path):
    service = Service(Store(tmp_path), client_factory=FakeClient)
    service.store.save_account("a", "usr_a", "A", {"auth": "old"})
    service.store.db.execute("UPDATE accounts SET cookies=?", (b"corrupt",))
    service.store.db.commit()
    await service.logout("a")
    assert service.store.account("a") is None


async def test_cookie_import_429_establishes_cooldown(tmp_path):
    class RateLimited(FakeClient):
        def profile(self):
            raise VRCError("rate limited", 429, 120)

    service = Service(Store(tmp_path), client_factory=RateLimited)
    with pytest.raises(VRCError):
        await service.import_cookies("a", {"auth": "secret"})
    assert service.backoff["a"] > time.monotonic() + 100
    with pytest.raises(ValueError, match="冷却"):
        await service.import_cookies("a", {"auth": "secret"})
