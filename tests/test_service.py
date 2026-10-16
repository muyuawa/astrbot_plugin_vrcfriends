import pytest

from astrbot_plugin_vrcfriends.client import VRCClient
from astrbot_plugin_vrcfriends.service import Service
from astrbot_plugin_vrcfriends.store import Store


class FakeClient:
    def __init__(self, cookies=None, **kwargs):
        self.cookies = dict(cookies or {})
        self.items = [
            {"id": "usr_friend", "displayName": "Friend", "location": "offline"}
        ]
        self.failed = False

    def login(self, username, password):
        self.cookies = {"auth": username}
        return {"requiresTwoFactorAuth": ["emailOtp"]}

    def verify(self, code, method):
        if code != "123456":
            raise ValueError("验证码错误")
        return {
            "id": "usr_" + self.cookies["auth"],
            "displayName": self.cookies["auth"],
        }

    def export_cookies(self):
        return self.cookies

    def friends(self):
        if self.failed:
            raise RuntimeError("network unavailable")
        return self.items

    def world(self, world_id):
        return {"name": "World"}

    def logout(self):
        pass

    def close(self):
        pass


async def connected(tmp_path):
    service = Service(Store(tmp_path), client_factory=FakeClient)
    for owner in ["qq:100", "qq:200"]:
        assert await service.login(owner, owner, "SECRET") == ["emailOtp"]
        await service.verify(owner, "123456")
    return service


async def test_authentication_challenges_do_not_save_partial_sessions(tmp_path):
    service = Service(Store(tmp_path), client_factory=FakeClient)
    assert await service.login("a", "Alice", "password") == ["emailOtp"]
    assert service.store.account("a") is None
    with pytest.raises(ValueError, match="验证码"):
        await service.verify("a", "wrong")
    await service.verify("a", "123456")
    assert service.store.account("a")["vrc_id"] == "usr_Alice"
    assert service.store.account("a")["cookies"] == {"auth": "Alice"}


async def test_clients_cache_and_logout_are_independent(tmp_path):
    service = await connected(tmp_path)
    service.clients["qq:100"].items[0]["displayName"] = "Only Alice"
    a = await service.sync("qq:100", force=True)
    b = await service.sync("qq:200", force=True)
    assert a[0]["name"] == "Only Alice"
    assert b[0]["name"] == "Friend"
    await service.logout("qq:100")
    assert service.store.account("qq:100") is None
    assert service.store.account("qq:200")
    assert service.clients["qq:200"].export_cookies() == {"auth": "qq:200"}


async def test_notifications_are_session_scoped_deduplicated_and_retry_delivery(
    tmp_path,
):
    service = await connected(tmp_path)
    service.store.set_subscription("qq:100", "group:1", "usr_friend", "Friend", True)
    service.store.set_subscription("qq:200", "group:2", "usr_friend", "Friend", True)
    deliveries = []
    fail = True

    async def send(owner, session, friend):
        nonlocal fail
        if owner == "qq:100" and fail:
            fail = False
            raise RuntimeError("temporary send failure")
        deliveries.append((owner, session, friend["id"]))

    await service.poll(send)
    assert deliveries == []
    service.clients["qq:100"].items[0]["location"] = "private"
    await service.poll(send)
    assert deliveries == []
    await service.poll(send)
    assert deliveries == [("qq:100", "group:1", "usr_friend")]
    await service.poll(send)
    assert len(deliveries) == 1


async def test_reconnect_does_not_send_false_online_alert(tmp_path):
    service = await connected(tmp_path)
    service.store.set_subscription("qq:100", "group:1", "usr_friend", "Friend", True)
    deliveries = []

    async def send(*args):
        deliveries.append(args)

    await service.poll(send)
    service.clients["qq:100"].failed = True
    await service.poll(send)
    service.clients["qq:100"].failed = False
    service.clients["qq:100"].items[0]["location"] = "private"
    service.backoff.clear()
    await service.poll(send)
    assert deliveries == []


def test_sdk_cookies_are_private_and_raw_challenge_json_is_preserved():
    a = VRCClient(cookies={"auth": "a"}, user_agent="Tests/1.0 https://example.org")
    b = VRCClient(cookies={"auth": "b"}, user_agent="Tests/1.0 https://example.org")
    assert a.export_cookies() == {"auth": "a"}
    assert b.export_cookies() == {"auth": "b"}
    assert a.api.rest_client.cookie_jar is not b.api.rest_client.cookie_jar

    class Response:
        data = b'{"requiresTwoFactorAuth":["totp"]}'

        def release_conn(self):
            pass

    assert a._raw(lambda **kwargs: Response()) == {"requiresTwoFactorAuth": ["totp"]}
    a.close()
    b.close()


async def test_cookie_login_closes_a_superseded_pending_verification_client(tmp_path):
    class TrackedClient(FakeClient):
        closed = False

        def profile(self):
            return {"id": "usr_cookie", "displayName": "Cookie account"}

        def close(self):
            self.closed = True

    service = Service(Store(tmp_path), client_factory=TrackedClient)
    await service.login("qq:a", "Account", "password")
    pending = service.pending["qq:a"]["client"]
    await service.import_cookies("qq:a", {"auth": "replacement-cookie"})
    assert pending.closed
    assert not service.clients["qq:a"].closed
    assert "qq:a" not in service.pending
    await service.close()


@pytest.mark.parametrize("operation", ["password", "cookie"])
async def test_cancelled_login_closes_its_client_after_the_sdk_call_finishes(
    tmp_path, operation
):
    import asyncio
    import threading

    entered, release = threading.Event(), threading.Event()
    clients = []

    class SlowClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.closed = False
            clients.append(self)

        def login(self, *args):
            entered.set()
            assert release.wait(3)
            return super().login(*args)

        def profile(self):
            entered.set()
            assert release.wait(3)
            return {"id": "usr_cookie", "displayName": "Cookie account"}

        def close(self):
            self.closed = True

    service = Service(Store(tmp_path), client_factory=SlowClient)
    coroutine = (
        service.login("qq:a", "Account", "password")
        if operation == "password"
        else service.import_cookies("qq:a", {"auth": "cookie"})
    )
    task = asyncio.create_task(coroutine)
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[0].closed
    assert service.store.account("qq:a") is None
    assert "qq:a" not in service.pending
    await service.close()
