"""Dependency setup without importing any optional third-party package at load time."""

import asyncio
import importlib
import importlib.util
import os
import subprocess
from pathlib import Path

DEPENDENCIES = ("vrchatapi", "urllib3", "cryptography", "PIL", "jinja2", "playwright")


class SetupError(RuntimeError):
    pass


def missing_dependencies():
    missing = []
    for name in DEPENDENCIES:
        try:
            available = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(name)
    return missing


async def ensure_python_dependencies(requirements_path, logger):
    missing = missing_dependencies()
    if not missing:
        return
    logger.info("VRC Friends: 正在自动安装缺失依赖：%s", ", ".join(missing))
    try:
        # Preserve AstrBot's mirror settings, core constraints and desktop paths.
        from astrbot.core import pip_installer

        await pip_installer.install(requirements_path=str(Path(requirements_path)))
    except asyncio.CancelledError:
        raise
    except Exception:
        raise SetupError(
            "Python 依赖自动安装失败，请检查机器人网络或 AstrBot 依赖安装日志后重载插件。"
        ) from None
    importlib.invalidate_caches()
    if missing_dependencies():
        raise SetupError(
            "依赖安装后仍无法加载，请重启 AstrBot；若仍失败，请检查依赖版本冲突。"
        )


async def run_install_process(command, *, timeout=300, env=None):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **options,
        )
    except OSError:
        raise SetupError("浏览器自动安装程序无法启动，请检查当前运行环境。") from None
    try:
        await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise SetupError("浏览器自动安装超时，请检查下载网络后重载插件。") from None
    if process.returncode != 0:
        raise SetupError(
            f"浏览器自动安装失败（退出码 {process.returncode}），请检查网络、磁盘空间和系统环境后重载插件。"
        )


async def install_chromium():
    # Same driver entry point as `python -m playwright install chromium`;
    # it also works when AstrBot uses a frozen Python runtime.
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    executable, cli = compute_driver_executable()
    await run_install_process(
        [executable, cli, "install", "chromium"], env=get_driver_env()
    )
