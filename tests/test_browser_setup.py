from types import SimpleNamespace

import pytest

from astrbot_plugin_vrcfriends.cards import Cards
from astrbot_plugin_vrcfriends.store import Store


class Browser:
    async def close(self):
        pass

    def is_connected(self):
        return True


class Playwright:
    def __init__(self, launch):
        self.chromium = SimpleNamespace(launch=launch)

    async def stop(self):
        pass


async def test_missing_browser_is_installed_then_launched_only_once(
    tmp_path, monkeypatch
):
    from astrbot_plugin_vrcfriends import cards as module

    installed = []

    async def install():
        installed.append(True)

    async def launch(**kwargs):
        if not installed:
            raise RuntimeError("Executable doesn't exist at /missing/chromium")
        return Browser()

    monkeypatch.setattr(module, "install_chromium", install)
    renderer = Cards(tmp_path, Store(tmp_path))
    renderer.playwright = Playwright(launch)
    await renderer.prepare()
    await renderer.prepare()
    assert renderer.browser.is_connected()
    assert installed == [True]
    await renderer.close()


async def test_concurrent_browser_setup_shares_a_single_install(tmp_path, monkeypatch):
    import asyncio

    from astrbot_plugin_vrcfriends import cards as module

    installs = []

    async def install():
        await asyncio.sleep(0.01)
        installs.append(True)

    async def launch(**kwargs):
        if not installs:
            raise RuntimeError("Executable doesn't exist at /missing/chromium")
        return Browser()

    monkeypatch.setattr(module, "install_chromium", install)
    renderer = Cards(tmp_path, Store(tmp_path))
    renderer.playwright = Playwright(launch)
    await asyncio.gather(renderer.prepare(), renderer.prepare())
    assert installs == [True]
    await renderer.close()


async def test_browser_install_failure_is_not_repeated_on_each_render_attempt(
    tmp_path, monkeypatch
):
    from astrbot_plugin_vrcfriends import cards as module

    installed = []

    async def install():
        installed.append(True)
        raise RuntimeError("download failed")

    async def launch(**kwargs):
        raise RuntimeError("Executable doesn't exist at /missing/chromium")

    monkeypatch.setattr(module, "install_chromium", install)
    renderer = Cards(tmp_path, Store(tmp_path))
    renderer.playwright = Playwright(launch)
    for _ in range(2):
        with pytest.raises(Exception):
            await renderer.prepare()
    assert installed == [True]
    await renderer.close()


async def test_existing_or_custom_browser_is_used_without_download(
    tmp_path, monkeypatch
):
    from astrbot_plugin_vrcfriends import cards as module

    async def unexpected_install():
        raise AssertionError("existing browsers must not be reinstalled")

    options = []

    async def launch(**kwargs):
        options.append(kwargs)
        return Browser()

    monkeypatch.setattr(module, "install_chromium", unexpected_install)
    renderer = Cards(tmp_path, Store(tmp_path), "/existing/browser")
    renderer.playwright = Playwright(launch)
    await renderer.prepare()
    assert options == [{"headless": True, "executable_path": "/existing/browser"}]
    await renderer.close()
