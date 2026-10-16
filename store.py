"""Owner-scoped persistence. Passwords and verification codes never enter SQLite."""

import hashlib
import base64
import io
import json
import os
from pathlib import Path
import sqlite3
import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from PIL import Image, UnidentifiedImageError


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.root / "session.key"
        if not key_path.exists():
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        self.master_key = base64.urlsafe_b64decode(key_path.read_bytes())
        self.db = sqlite3.connect(self.root / "vrc.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts(owner TEXT PRIMARY KEY, vrc_id TEXT NOT NULL,
              name TEXT NOT NULL, cookies BLOB NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS cache(owner TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS favorites(owner TEXT, account TEXT, id TEXT, name TEXT,
              PRIMARY KEY(owner, account, id));
            CREATE TABLE IF NOT EXISTS subscriptions(owner TEXT, account TEXT, session TEXT, id TEXT, name TEXT,
              PRIMARY KEY(owner, account, session, id));
            CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY, page_size INTEGER NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def _cipher(self, owner):
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"vrcfriends-session-v1:" + owner.encode(),
        ).derive(self.master_key)
        return Fernet(base64.urlsafe_b64encode(key))

    def account(self, owner):
        row = self.db.execute(
            "SELECT * FROM accounts WHERE owner=?", (owner,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["cookies"] = json.loads(self._cipher(owner).decrypt(data["cookies"]))
        return data

    def save_account(self, owner, vrc_id, name, cookies):
        previous = self.db.execute(
            "SELECT vrc_id FROM accounts WHERE owner=?", (owner,)
        ).fetchone()
        if not previous or previous[0] != vrc_id:
            self.db.execute("DELETE FROM cache WHERE owner=?", (owner,))
        encrypted = self._cipher(owner).encrypt(json.dumps(cookies).encode())
        self.db.execute(
            "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,1)",
            (owner, vrc_id, name, encrypted),
        )
        self.db.commit()

    def suspend(self, owner):
        self.db.execute("UPDATE accounts SET active=0 WHERE owner=?", (owner,))
        self.db.commit()

    def logout(self, owner):
        self.db.execute("DELETE FROM accounts WHERE owner=?", (owner,))
        self.db.execute("DELETE FROM cache WHERE owner=?", (owner,))
        self.db.commit()

    def owners(self):
        return [
            r[0] for r in self.db.execute("SELECT owner FROM accounts WHERE active=1")
        ]

    def _account_id(self, owner):
        row = self.db.execute(
            "SELECT vrc_id FROM accounts WHERE owner=?", (owner,)
        ).fetchone()
        if not row:
            raise ValueError("请先在私聊中使用 /vrc 登录。")
        return row[0]

    def set_favorite(self, owner, friend_id, name, enabled):
        args = (owner, self._account_id(owner), friend_id)
        if enabled:
            self.db.execute(
                "INSERT OR REPLACE INTO favorites VALUES(?,?,?,?)", (*args, name)
            )
        else:
            self.db.execute(
                "DELETE FROM favorites WHERE owner=? AND account=? AND id=?", args
            )
        self.db.commit()

    def favorites(self, owner):
        account = self._account_id(owner)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT id,name FROM favorites WHERE owner=? AND account=? ORDER BY name",
                (owner, account),
            )
        ]

    def set_subscription(self, owner, session, friend_id, name, enabled):
        args = (owner, self._account_id(owner), session, friend_id)
        if enabled:
            self.db.execute(
                "INSERT OR REPLACE INTO subscriptions VALUES(?,?,?,?,?)", (*args, name)
            )
        else:
            self.db.execute(
                "DELETE FROM subscriptions WHERE owner=? AND account=? AND session=? AND id=?",
                args,
            )
        self.db.commit()

    def subscriptions(self, owner, session=None):
        args = [owner, self._account_id(owner)]
        query = "SELECT id,name,session FROM subscriptions WHERE owner=? AND account=?"
        if session is not None:
            query += " AND session=?"
            args.append(session)
        return [
            dict(r) for r in self.db.execute(query + " ORDER BY name,session", args)
        ]

    def save_friends(self, owner, friends):
        self.db.execute(
            "INSERT OR REPLACE INTO cache VALUES(?,?,?)",
            (owner, json.dumps(friends, ensure_ascii=False), time.time()),
        )
        self.db.commit()

    def cached_friends(self, owner):
        row = self.db.execute(
            "SELECT payload,updated FROM cache WHERE owner=?", (owner,)
        ).fetchone()
        return (json.loads(row[0]), row[1]) if row else ([], 0)

    def _background_path(self, owner):
        return self.root / (
            "background-" + hashlib.sha256(owner.encode()).hexdigest() + ".jpg"
        )

    def background(self, owner):
        path = self._background_path(owner)
        return path if path.exists() else None

    def set_background(self, owner, source):
        source = Path(source)
        if source.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("背景图片不能超过 8 MB。")
        try:
            with Image.open(source) as image:
                if (
                    image.format not in {"PNG", "JPEG", "WEBP"}
                    or image.width * image.height > 25_000_000
                ):
                    raise ValueError(
                        "请使用有效的 PNG、JPG 或 WebP 图片（最多 2500 万像素）。"
                    )
                image.load()
                image = image.convert("RGB")
                image.thumbnail((2000, 2600))
                buffer = io.BytesIO()
                image.save(buffer, "JPEG", quality=88)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("图片无法读取，原背景已保留。") from exc
        path = self._background_path(owner)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(buffer.getvalue())
        temporary.replace(path)

    def reset_background(self, owner):
        self._background_path(owner).unlink(missing_ok=True)

    def page_size(self, owner):
        row = self.db.execute(
            "SELECT page_size FROM preferences WHERE owner=?", (owner,)
        ).fetchone()
        return row[0] if row else 8

    def set_page_size(self, owner, size):
        if not 1 <= size <= 30:
            raise ValueError("每张图的好友人数请设置为 1–30。")
        self.db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (owner, size))
        self.db.commit()
