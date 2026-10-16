import asyncio
import time

from .client import VRCClient, VRCError
from .domain import Transitions, normalize_friend, resolve_friend


class Service:
    def __init__(self, store, client_factory=VRCClient, user_agent="", proxy=""):
        self.store = store
        self.factory = client_factory
        self.client_options = {"user_agent": user_agent, "proxy": proxy}
        self.clients = {}
        self.pending = {}
        self.locks = {}
        self.worlds = {}
        self.snapshots = {}
        self.transitions = Transitions()
        self.backoff = {}
        self.queue = {}
        self.last_alert = {}
        self.generations = {}

    def lock(self, owner):
        return self.locks.setdefault(owner, asyncio.Lock())

    async def _run(self, operation, *args):
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release the account lock while a synchronous SDK call still runs.
            try:
                await task
            finally:
                raise

    def _check_backoff(self, owner):
        if self.backoff.get(owner, 0) > time.monotonic():
            raise ValueError("当前账号正在等待请求冷却，请稍后重试。")

    def _client(self, owner):
        account = self.store.account(owner)
        if not account or not account["active"]:
            raise ValueError("尚未登录或登录已经失效，请在私聊中使用 /vrc 登录。")
        if owner not in self.clients:
            self.clients[owner] = self.factory(
                cookies=account["cookies"], **self.client_options
            )
        return self.clients[owner]

    def _commit_login(self, owner, client, profile):
        if not isinstance(profile, dict) or not str(profile.get("id", "")).startswith(
            "usr_"
        ):
            raise ValueError(
                "验证尚未完成，请按提示继续验证或到 VRChat 官网确认账号状态。"
            )
        old = self.clients.pop(owner, None)
        if old and old is not client:
            old.close()
        self.store.save_account(
            owner,
            profile["id"],
            profile.get("displayName") or profile["id"],
            client.export_cookies(),
        )
        self.generations[owner] = self.generations.get(owner, 0) + 1
        self.clients[owner] = client
        old_pending = self.pending.pop(owner, None)
        if old_pending and old_pending["client"] is not client:
            old_pending["client"].close()
        self.worlds.pop(owner, None)
        self.snapshots = {k: v for k, v in self.snapshots.items() if k[0] != owner}
        self.transitions.reset(owner)
        self.queue = {k: v for k, v in self.queue.items() if k[0] != owner}

    async def login(self, owner, username, password):
        async with self.lock(owner):
            self._check_backoff(owner)
            old_pending = self.pending.pop(owner, None)
            if old_pending:
                old_pending["client"].close()
            client = self.factory(**self.client_options)
            try:
                profile = await self._run(client.login, username, password)
                methods = profile.get("requiresTwoFactorAuth", [])
                if methods:
                    self.pending[owner] = {
                        "client": client,
                        "methods": methods,
                        "expires": time.monotonic() + 600,
                        "attempts": 0,
                    }
                    return methods
                self._commit_login(owner, client, profile)
                return []
            except (Exception, asyncio.CancelledError) as exc:
                client.close()
                if isinstance(exc, VRCError) and exc.status == 429:
                    self.backoff[owner] = time.monotonic() + exc.retry_after
                raise

    async def verify(self, owner, code, method=None):
        async with self.lock(owner):
            self._check_backoff(owner)
            pending = self.pending.get(owner)
            if not pending or pending["expires"] < time.monotonic():
                if pending:
                    pending["client"].close()
                    self.pending.pop(owner, None)
                raise ValueError("没有待验证的登录或登录验证已过期，请重新 /vrc 登录。")
            methods = pending["methods"]
            if method is None:
                method = (
                    "totp"
                    if "totp" in methods
                    else "emailOtp"
                    if "emailOtp" in methods
                    else "otp"
                    if methods == ["otp"]
                    else None
                )
            if method not in methods:
                raise ValueError(
                    "请选择服务器要求的验证方式："
                    + "、".join(methods)
                    + "。其他验证请到 VRChat 官网完成。"
                )
            if method in {"totp", "emailOtp"} and (
                not code.isascii() or not code.isdigit() or len(code) != 6
            ):
                raise ValueError(
                    "验证码应为 6 位数字；恢复码请使用 /vrc 恢复码 <恢复码>。"
                )
            try:
                profile = await self._run(pending["client"].verify, code, method)
                if profile.get("requiresTwoFactorAuth"):
                    pending["methods"] = profile["requiresTwoFactorAuth"]
                    raise ValueError(
                        "还需要完成额外验证：" + "、".join(pending["methods"])
                    )
                self._commit_login(owner, pending["client"], profile)
            except Exception as exc:
                pending["attempts"] += 1
                if isinstance(exc, VRCError) and exc.status == 429:
                    self.backoff[owner] = time.monotonic() + exc.retry_after
                if pending["attempts"] >= 5:
                    pending["client"].close()
                    self.pending.pop(owner, None)
                    raise ValueError(
                        "验证失败次数较多，本次登录已取消，请稍后重新登录。"
                    ) from None
                raise

    async def import_cookies(self, owner, cookies):
        async with self.lock(owner):
            self._check_backoff(owner)
            client = self.factory(cookies=cookies, **self.client_options)
            try:
                profile = await self._run(client.profile)
                self._commit_login(owner, client, profile)
            except (Exception, asyncio.CancelledError) as exc:
                client.close()
                if isinstance(exc, VRCError) and exc.status == 429:
                    self.backoff[owner] = time.monotonic() + exc.retry_after
                raise

    async def logout(self, owner):
        async with self.lock(owner):
            self.generations[owner] = self.generations.get(owner, 0) + 1
            client = self.clients.pop(owner, None)
            try:
                if not client:
                    account = self.store.account(owner)
                    if account:
                        client = self.factory(
                            cookies=account["cookies"], **self.client_options
                        )
                if client:
                    await self._run(client.logout)
            except Exception:
                pass
            finally:
                if client:
                    client.close()
                pending = self.pending.pop(owner, None)
                if pending:
                    pending["client"].close()
                self.store.logout(owner)
                self.transitions.reset(owner)
                self.snapshots = {
                    k: v for k, v in self.snapshots.items() if k[0] != owner
                }
                self.queue = {k: v for k, v in self.queue.items() if k[0] != owner}

    async def sync(self, owner, force=False):
        async with self.lock(owner):
            self._check_backoff(owner)
            client = self._client(owner)
            cached, updated = self.store.cached_friends(owner)
            if not force and time.time() - updated < 30:
                return cached
            try:
                raw = await self._run(client.friends)
                worlds = self.worlds.setdefault(owner, {})
                for fid in {
                    x["location"].split(":")[0]
                    for x in raw
                    if str(x.get("location", "")).startswith("wrld_")
                }:
                    if fid not in worlds:
                        try:
                            worlds[fid] = await self._run(client.world, fid)
                        except VRCError as exc:
                            if exc.status == 429:
                                raise
                friends = [normalize_friend(f, worlds) for f in raw]
                self.store.save_friends(owner, friends)
                account = self.store.account(owner)
                self.store.save_account(
                    owner, account["vrc_id"], account["name"], client.export_cookies()
                )
                return friends
            except Exception as exc:
                self.transitions.reset(owner)
                self.backoff[owner] = time.monotonic() + (
                    exc.retry_after
                    if isinstance(exc, VRCError) and exc.status == 429
                    else 60
                )
                if isinstance(exc, VRCError) and exc.status == 401:
                    self.store.suspend(owner)
                raise

    def resolve(self, owner, session, query, friends):
        saved = self.snapshots.get((owner, session))
        account = self.store.account(owner)
        snapshot = (
            saved["ids"]
            if saved
            and saved["expires"] > time.monotonic()
            and account
            and saved["account"] == account["vrc_id"]
            else None
        )
        return resolve_friend(query, friends, snapshot)

    def remember_list(self, owner, session, friends):
        self.snapshots[(owner, session)] = {
            "ids": [f["id"] for f in friends],
            "account": self.store.account(owner)["vrc_id"],
            "expires": time.monotonic() + 600,
        }

    async def deliver_notification(self, owner, session, friend, send):
        # Rendering may take time. Validate again immediately before transport.
        async with self.lock(owner):
            account = self.store.account(owner)
            if (
                not account
                or not account["active"]
                or account["vrc_id"] != friend.get("_account")
                or self.generations.get(owner, 0) != friend.get("_generation")
            ):
                return False
            if not any(
                s["id"] == friend["id"]
                for s in self.store.subscriptions(owner, session)
            ):
                return False
            result = await send()
            if result is False:
                raise RuntimeError("Message platform unavailable")
            return True

    async def poll(self, send):
        for owner, pending in list(self.pending.items()):
            if pending["expires"] < time.monotonic():
                async with self.lock(owner):
                    if self.pending.get(owner) is pending:
                        pending["client"].close()
                        self.pending.pop(owner, None)
        for owner in self.store.owners():
            subscriptions = self.store.subscriptions(owner)
            if not subscriptions:
                self.transitions.reset(owner)
                continue
            if self.backoff.get(owner, 0) > time.monotonic():
                continue
            try:
                friends = await self.sync(owner, force=True)
            except Exception:
                continue
            by_id = {f["id"]: f for f in friends}
            new_ids = self.transitions.observe(owner, friends)
            account_id = self.store.account(owner)["vrc_id"]
            for subscription in subscriptions:
                fid = subscription["id"]
                if (
                    fid in new_ids
                    and time.monotonic() - self.last_alert.get((owner, fid), -1000) > 60
                ):
                    key = (owner, subscription["session"], fid)
                    self.queue[key] = {
                        "friend": by_id[fid],
                        "account": account_id,
                        "generation": self.generations.get(owner, 0),
                        "expires": time.monotonic() + 300,
                        "attempts": 0,
                    }
            valid = {(s["session"], s["id"]) for s in self.store.subscriptions(owner)}
            for key, item in list(self.queue.items()):
                if key[0] != owner:
                    continue
                if self.queue.get(key) is not item:
                    continue
                if (
                    (key[1], key[2]) not in valid
                    or item["expires"] < time.monotonic()
                    or item["account"] != account_id
                    or by_id.get(key[2], {}).get("presence") != "game"
                ):
                    self.queue.pop(key, None)
                    continue
                try:
                    result = await send(
                        owner,
                        key[1],
                        {
                            **by_id[key[2]],
                            "subscribed": True,
                            "_account": item["account"],
                            "_generation": item["generation"],
                        },
                    )
                except Exception:
                    item["attempts"] += 1
                    if item["attempts"] >= 3:
                        self.queue.pop(key, None)
                else:
                    self.queue.pop(key, None)
                    if result is not False:
                        self.last_alert[(owner, key[2])] = time.monotonic()

    async def close(self):
        for owner in set(self.clients) | set(self.pending):
            async with self.lock(owner):
                if owner in self.clients:
                    self.clients[owner].close()
                if owner in self.pending:
                    self.pending[owner]["client"].close()
        self.store.close()
