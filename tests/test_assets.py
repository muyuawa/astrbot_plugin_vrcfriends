import base64
from io import BytesIO

import pytest
from PIL import Image


def picture():
    output = BytesIO()
    Image.new("RGB", (800, 450), "#94b7e2").save(output, "PNG")
    return output.getvalue()


class Response:
    def __init__(self, data=b"", status=200, **headers):
        self.data, self.status, self.headers = data, status, headers

    def read(self, amount):
        return self.data[:amount]

    def close(self):
        pass

    def release_conn(self):
        pass


def test_images_use_owner_cookies_only_for_api_and_drop_them_on_cdn_redirect(
    monkeypatch,
):
    from astrbot_plugin_vrcfriends.assets import ImageAssets

    assets = ImageAssets("Tests/1.0 https://example.org")
    calls = []

    def request(method, url, **options):
        calls.append((url, options))
        if len(calls) == 1:
            return Response(status=302, Location="https://files.vrchat.cloud/cover.png")
        return Response(picture())

    monkeypatch.setattr(assets.http, "request", request)
    data = assets._download(
        "https://api.vrchat.cloud/api/1/file/file_test/1/file", {"auth": "owner-secret"}
    )
    assert calls[0][1]["headers"]["Cookie"] == "auth=owner-secret"
    assert "Cookie" not in calls[1][1]["headers"]
    assert calls[0][1]["redirect"] is False
    assert calls[0][1]["headers"]["User-Agent"] == "Tests/1.0 https://example.org"
    with Image.open(BytesIO(base64.b64decode(data.split(",", 1)[1]))) as image:
        assert image.size == (512, 288)
    assets.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://files.vrchat.cloud/x",
        "https://evil.example/x",
        "https://127.0.0.1/x",
        "file:///etc/passwd",
        "https://api.vrchat.cloud/api/1/auth/user",
        "https://files.vrchat.cloud:8443/x",
    ],
)
def test_image_download_rejects_untrusted_urls_before_network(monkeypatch, url):
    from astrbot_plugin_vrcfriends.assets import ImageAssets

    assets = ImageAssets("Tests/1.0 https://example.org")

    def unexpected(*args, **kwargs):
        pytest.fail("untrusted image URL reached the network")

    monkeypatch.setattr(assets.http, "request", unexpected)
    with pytest.raises(ValueError):
        assets._download(url, {"auth": "secret"})
    assets.close()


def test_image_redirect_cannot_reach_private_network(monkeypatch):
    from astrbot_plugin_vrcfriends.assets import ImageAssets

    assets = ImageAssets("Tests/1.0 https://example.org")
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        return Response(status=302, Location="http://127.0.0.1/secret")

    monkeypatch.setattr(assets.http, "request", request)
    with pytest.raises(ValueError):
        assets._download("https://files.vrchat.cloud/picture.png", {})
    assert len(calls) == 1
    assets.close()


@pytest.mark.parametrize(
    "response",
    [Response(b"<html>blocked</html>"), Response(b"x" * (4 * 1024 * 1024 + 1))],
)
def test_invalid_and_oversized_images_do_not_reach_renderer(monkeypatch, response):
    from astrbot_plugin_vrcfriends.assets import ImageAssets

    assets = ImageAssets("Tests/1.0 https://example.org")
    monkeypatch.setattr(assets.http, "request", lambda *args, **kwargs: response)
    with pytest.raises(ValueError):
        assets._download("https://files.vrchat.cloud/picture.png", {})
    assets.close()


async def test_image_cache_is_account_scoped_and_failed_images_keep_the_card_usable(
    monkeypatch,
):
    from astrbot_plugin_vrcfriends.assets import ImageAssets

    assets = ImageAssets("Tests/1.0 https://example.org")
    calls = []

    def download(url, cookies):
        calls.append((url, cookies))
        if "bad" in url:
            raise ValueError("unavailable")
        return "data:image/jpeg;base64," + cookies["auth"]

    monkeypatch.setattr(assets, "_download", download)
    friends = [
        {
            "avatar_url": "https://files.vrchat.cloud/avatar.png",
            "world_image_url": "https://files.vrchat.cloud/bad.png",
        }
    ] * 2
    account = {"vrc_id": "usr_same", "cookies": {"auth": "a"}}
    first = await assets.load("qq:a", account, friends)
    assert first[0]["avatar_image"].endswith(",a") and first[0]["world_image"] == ""
    assert len(calls) == 2
    await assets.load("qq:a", account, friends)
    assert len(calls) == 2
    second = await assets.load("qq:b", {**account, "cookies": {"auth": "b"}}, friends)
    assert second[0]["avatar_image"].endswith(",b")
    assert len(calls) == 4
    assert "avatar_image" not in friends[0]
    assets.close()
