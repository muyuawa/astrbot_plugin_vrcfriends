import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from .test_service import connected


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    """Replace only the unavailable AstrBot transport; exercise our real adapter."""

    class Chain:
        def message(self, text):
            return self

        def file_image(self, path):
            return self

    class Star:
        def __init__(self, context):
            self.context = context

    modules = {
        "astrbot.api": {
            "AstrBotConfig": dict,
            "logger": SimpleNamespace(warning=lambda *args: None),
        },
        "astrbot.api.event": {
            "filter": SimpleNamespace(command=lambda *args: lambda f: f),
            "AstrMessageEvent": object,
            "MessageChain": Chain,
        },
        "astrbot.api.star": {"Context": object, "Star": Star},
        "astrbot.api.message_components": {
            "Image": type("Image", (), {}),
            "Reply": type("Reply", (), {}),
        },
        "astrbot.core.utils.astrbot_path": {
            "get_astrbot_data_path": lambda: str(tmp_path)
        },
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "astrbot_plugin_vrcfriends.main", raising=False)
    cls = importlib.import_module("astrbot_plugin_vrcfriends.main").VRCFriends
    yield cls
    sys.modules.pop("astrbot_plugin_vrcfriends.main", None)


@pytest.mark.parametrize("change", ["logout", "switch", "unsubscribe"])
async def test_inflight_render_does_not_send_after_login_or_subscription_changes(
    tmp_path, adapter, change
):
    service = await connected(tmp_path)
    owner, session = "qq:100", "group:1"
    service.store.set_subscription(owner, session, "usr_friend", "Friend", True)
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    class Render:
        async def render(self, *args):
            entered.set()
            await release.wait()
            return "demo.png"

    async def transport(*args):
        sent.append(args)
        return True

    plugin = object.__new__(adapter)
    plugin.store, plugin.service, plugin.cards = service.store, service, Render()
    plugin.context = SimpleNamespace(send_message=transport)
    await service.poll(plugin._send_alert)
    service.clients[owner].items[0]["location"] = "private"
    task = asyncio.create_task(service.poll(plugin._send_alert))
    await asyncio.wait_for(entered.wait(), 2)
    if change == "logout":
        await service.logout(owner)
    elif change == "switch":
        await service.login(owner, "New account", "secret")
        await service.verify(owner, "123456")
    else:
        service.store.set_subscription(owner, session, "usr_friend", "Friend", False)
    release.set()
    await task
    assert sent == []


async def test_plugin_constructor_defers_runtime_initialization_until_dependencies_ready(
    tmp_path, adapter
):
    plugin = adapter(SimpleNamespace(), {})
    assert plugin.store is None
    assert plugin.service is None
    assert plugin.controller is None
    assert plugin.cards is None
    await plugin.terminate()


async def test_commands_report_dependency_failure_without_crashing(tmp_path, adapter):
    plugin = adapter(SimpleNamespace(), {})
    plugin.startup_error = "Python 依赖自动安装失败，请检查网络后重载插件。"

    class Event:
        def plain_result(self, text):
            return text

        def stop_event(self):
            self.stopped = True

    event = Event()
    replies = [reply async for reply in plugin.vrc(event)]
    assert "自动安装失败" in replies[0]
    assert event.stopped
    await plugin.terminate()


async def test_failed_dependency_setup_is_visible_without_starting_polling(
    tmp_path, adapter, monkeypatch
):
    from astrbot_plugin_vrcfriends import main
    from astrbot_plugin_vrcfriends.bootstrap import SetupError

    async def fail_setup(*args):
        raise SetupError("Python 依赖自动安装失败，请检查网络后重载插件。")

    monkeypatch.setattr(main, "ensure_python_dependencies", fail_setup)
    plugin = adapter(SimpleNamespace(), {})
    await plugin.initialize()
    assert "自动安装失败" in plugin.startup_error
    assert plugin.poll_task is None and plugin.browser_task is None
    assert plugin.service is None
    await plugin.terminate()


async def test_browser_failure_keeps_login_and_chat_functions_available(
    tmp_path, adapter, monkeypatch
):
    from astrbot_plugin_vrcfriends.cards import Cards

    async def fail_browser(self):
        raise RuntimeError("download unavailable")

    monkeypatch.setattr(Cards, "prepare", fail_browser)
    plugin = adapter(SimpleNamespace(), {"contact_url": "https://example.org"})
    await plugin.initialize()
    await plugin.browser_task
    assert plugin.startup_error == ""
    assert plugin.controller is not None and plugin.poll_task is not None
    reply = await plugin.controller.execute("qq:100", "private:100", True, "帮助")
    assert "/vrc 登录" in reply.text
    await plugin.terminate()
