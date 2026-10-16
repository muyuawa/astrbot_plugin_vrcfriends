import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from astrbot_plugin_vrcfriends import bootstrap


class Log:
    def info(self, *args):
        pass

    def warning(self, *args):
        pass


async def test_missing_dependencies_are_installed_once_with_astrbots_installer(
    tmp_path, monkeypatch
):
    module_name = "vrc_test_autoinstall_dependency"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(bootstrap, "DEPENDENCIES", (module_name,))
    installed = []

    async def install(*, requirements_path):
        installed.append(requirements_path)
        (tmp_path / (module_name + ".py")).write_text("ready = True\n")

    core = ModuleType("astrbot.core")
    core.pip_installer = SimpleNamespace(install=install)
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(module_name)
    await bootstrap.ensure_python_dependencies(requirements, Log())
    await bootstrap.ensure_python_dependencies(requirements, Log())
    assert installed == [str(requirements)]
    assert importlib.import_module(module_name).ready is True
    sys.modules.pop(module_name, None)


async def test_failed_python_install_has_actionable_error_and_does_not_claim_success(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        bootstrap, "DEPENDENCIES", ("vrc_test_dependency_never_installed",)
    )

    async def install(**kwargs):
        raise RuntimeError("offline")

    core = ModuleType("astrbot.core")
    core.pip_installer = SimpleNamespace(install=install)
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    with pytest.raises(bootstrap.SetupError, match="自动安装.*失败"):
        await bootstrap.ensure_python_dependencies(tmp_path / "requirements.txt", Log())


async def test_installer_success_without_importable_dependencies_is_rejected(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        bootstrap, "DEPENDENCIES", ("vrc_test_dependency_still_missing",)
    )

    async def install(**kwargs):
        return None

    core = ModuleType("astrbot.core")
    core.pip_installer = SimpleNamespace(install=install)
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    with pytest.raises(bootstrap.SetupError, match="仍无法加载"):
        await bootstrap.ensure_python_dependencies(tmp_path / "requirements.txt", Log())


async def test_installer_process_failure_and_timeout_are_detected():
    with pytest.raises(bootstrap.SetupError, match="失败"):
        await bootstrap.run_install_process(
            [sys.executable, "-c", "raise SystemExit(7)"], timeout=5
        )
    with pytest.raises(bootstrap.SetupError, match="超时"):
        await bootstrap.run_install_process(
            [sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.1
        )


async def test_cancelling_installer_stops_its_child_process(tmp_path):
    marker = tmp_path / "should-not-exist"
    code = (
        "import time; from pathlib import Path; time.sleep(2); Path("
        + repr(str(marker))
        + ").touch()"
    )
    task = asyncio.create_task(
        bootstrap.run_install_process([sys.executable, "-c", code], timeout=10)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.1)
    assert not marker.exists()
