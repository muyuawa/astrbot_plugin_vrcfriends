"""Fetch small VRChat images with account-scoped credentials and memory caching."""

import asyncio
import base64
from collections import OrderedDict
from io import BytesIO
import re
import time
from urllib.parse import urljoin, urlsplit

from PIL import Image, UnidentifiedImageError
import urllib3


API_HOSTS = {"api.vrchat.cloud", "api.vrchat.com"}
IMAGE_HOSTS = API_HOSTS | {"files.vrchat.cloud", "cdn.vrchat.com", "assets.vrchat.com"}
MAX_BYTES = 4 * 1024 * 1024


def allowed_image_url(url, redirected=False):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username
            or parsed.password
            or len(url) > 4096
        ):
            return False
        host = parsed.hostname or ""
        if host in API_HOSTS:
            return bool(
                re.fullmatch(
                    r"/api/1/(?:file/file_[A-Za-z0-9-]+/\d+/file|image/file_[A-Za-z0-9-]+/\d+/\d+)",
                    parsed.path,
                )
            )
        return host in IMAGE_HOSTS or (
            redirected and host.endswith((".amazonaws.com", ".cloudfront.net"))
        )
    except (ValueError, TypeError):
        return False


class _RateLimited(ValueError):
    pass


class ImageAssets:
    def __init__(self, user_agent="", proxy=""):
        self.user_agent = user_agent
        self.http = (
            urllib3.ProxyManager(proxy)
            if proxy
            else urllib3.PoolManager(num_pools=4, maxsize=4)
        )
        self.cache = OrderedDict()
        self.slots = asyncio.Semaphore(4)
        self.cooldown = {}

    def _download(self, url, cookies):
        for hop in range(4):
            if not allowed_image_url(url, redirected=hop > 0):
                raise ValueError("Unsupported image URL")
            headers = {"User-Agent": self.user_agent, "Accept": "image/*"}
            if urlsplit(url).hostname in API_HOSTS:
                values = [
                    f"{name}={value}"
                    for name, value in cookies.items()
                    if name in {"auth", "twoFactorAuth"}
                    and isinstance(value, str)
                    and not re.search(r"[\x00-\x20;]", value)
                ]
                if values:
                    headers["Cookie"] = "; ".join(values)
            response = self.http.request(
                "GET",
                url,
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
                timeout=urllib3.Timeout(connect=3, read=5),
            )
            try:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                if response.status == 429:
                    raise _RateLimited("Image request limited")
                if response.status != 200:
                    raise ValueError("Image unavailable")
                if int(response.headers.get("Content-Length", 0)) > MAX_BYTES:
                    raise ValueError("Image too large")
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Image too large")
            finally:
                response.close()
                response.release_conn()
            try:
                with Image.open(BytesIO(raw)) as source:
                    if source.width * source.height > 16_000_000:
                        raise ValueError("Image dimensions too large")
                    source.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    image = source.convert("RGBA")
                    background = Image.new("RGBA", image.size, "white")
                    background.alpha_composite(image)
                    output = BytesIO()
                    background.convert("RGB").save(output, "JPEG", quality=85)
                return (
                    "data:image/jpeg;base64,"
                    + base64.b64encode(output.getvalue()).decode()
                )
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
                raise ValueError("Invalid image") from None
        raise ValueError("Too many image redirects")

    async def _get(self, identity, url, cookies):
        key = (*identity, url)
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            self.cache.move_to_end(key)
            return cached[1]
        async with self.slots:
            if self.cooldown.get(identity, 0) > time.monotonic():
                return ""
            try:
                data = await asyncio.to_thread(self._download, url, cookies)
            except _RateLimited:
                self.cooldown[identity] = time.monotonic() + 90
                data = ""
            except Exception:
                data = ""
        self.cache[key] = (time.monotonic() + (3600 if data else 90), data)
        self.cache.move_to_end(key)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return data

    async def load(self, owner, account, friends):
        identity = (owner, account["vrc_id"])
        urls = {
            f.get(field) for f in friends for field in ("avatar_url", "world_image_url")
        }
        urls = sorted(url for url in urls if allowed_image_url(url))
        values = await asyncio.gather(
            *(self._get(identity, url, dict(account["cookies"])) for url in urls)
        )
        images = dict(zip(urls, values))
        return [
            {
                **f,
                "avatar_image": images.get(f.get("avatar_url"), ""),
                "world_image": images.get(f.get("world_image_url"), ""),
            }
            for f in friends
        ]

    def close(self):
        self.cache.clear()
        self.cooldown.clear()
        self.http.clear()
