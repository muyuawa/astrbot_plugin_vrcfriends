import pytest

from astrbot_plugin_vrcfriends.domain import (
    Transitions,
    normalize_friend,
    resolve_friend,
)
from astrbot_plugin_vrcfriends.store import Store


def test_accounts_cookies_and_favorites_are_isolated_after_restart(tmp_path):
    store = Store(tmp_path)
    store.save_account("qq:100", "usr_a", "Alice", {"auth": "ALICE_SECRET"})
    store.save_account("qq:200", "usr_b", "Bob", {"auth": "BOB_SECRET"})
    store.set_favorite("qq:100", "usr_friend", "Friend", True)
    store.close()
    assert b"ALICE_SECRET" not in (tmp_path / "vrc.sqlite3").read_bytes()
    store = Store(tmp_path)
    assert store.account("qq:100")["cookies"] == {"auth": "ALICE_SECRET"}
    assert store.account("qq:200")["cookies"] == {"auth": "BOB_SECRET"}
    assert [x["id"] for x in store.favorites("qq:100")] == ["usr_friend"]
    assert store.favorites("qq:200") == []
    store.save_account("qq:100", "usr_new", "New account", {"auth": "NEW"})
    assert store.favorites("qq:100") == []


def test_subscriptions_are_owned_by_user_and_originating_session(tmp_path):
    store = Store(tmp_path)
    for owner in ["qq:100", "qq:200"]:
        store.save_account(owner, "usr_shared", "Account", {"auth": owner})
    store.set_subscription("qq:100", "group:1", "usr_friend", "Friend", True)
    store.set_subscription("qq:100", "group:2", "usr_friend", "Friend", True)
    store.set_subscription("qq:200", "group:1", "usr_friend", "Friend", True)
    store.set_subscription("qq:100", "group:1", "usr_friend", "Friend", False)
    assert [x["session"] for x in store.subscriptions("qq:100")] == ["group:2"]
    assert len(store.subscriptions("qq:200")) == 1
    store.logout("qq:100")
    assert store.account("qq:100") is None
    assert store.account("qq:200")["vrc_id"] == "usr_shared"


@pytest.mark.parametrize(
    "location,extra,presence,text",
    [
        ("private", {}, "game", "私人房间 · 位置未公开"),
        ("traveling", {}, "game", "正在切换房间"),
        ("traveling:traveling", {}, "game", "正在切换房间"),
        ("offline", {"_api_online": True}, "web", "网页在线 · 未进入游戏"),
        ("offline", {}, "offline", "未在游戏中"),
        ("", {"_api_online": False}, "offline", "未在游戏中"),
        ("", {}, "unknown", "位置暂不可用"),
    ],
)
def test_presence_and_hidden_locations(location, extra, presence, text):
    friend = normalize_friend(
        {
            "id": "usr_f",
            "displayName": "F",
            "location": location,
            "status": "busy",
            **extra,
        },
        {},
    )
    assert friend["presence"] == presence
    assert friend["location_text"] == text
    if presence != "game":
        assert friend["color"] == "gray"


def test_instance_details_and_duplicate_names():
    raw = {
        "id": "usr_a",
        "displayName": "A B",
        "status": "join me",
        "location": "wrld_abc:123~hidden(usr_owner)~region(jp)",
    }
    friend = normalize_friend(raw, {"wrld_abc": {"name": "A world"}})
    assert friend["location_text"] == "A world"
    assert friend["meta"] == "好友+房 · #123 · JP"
    assert resolve_friend("A B", [raw])["id"] == "usr_a"
    with pytest.raises(ValueError, match="同名|多个"):
        resolve_friend("A B", [raw, {**raw, "id": "usr_b"}])
    with pytest.raises(ValueError, match="重新查询"):
        resolve_friend("#01", [raw])


@pytest.mark.parametrize(
    "images,expected",
    [
        (
            {
                "iconUrl": "icon",
                "userIcon": "old-icon",
                "currentAvatarImageUrl": "avatar",
            },
            "icon",
        ),
        ({"userIcon": "old-icon"}, "old-icon"),
        ({"profilePicOverride": "profile"}, "profile"),
        ({"currentAvatarThumbnailImageUrl": "thumbnail"}, "thumbnail"),
        ({"currentAvatarImageUrl": "avatar"}, "avatar"),
    ],
)
def test_friend_avatar_fields_and_visible_world_cover(images, expected):
    worlds = {
        "wrld_one": {"name": "World", "thumbnailImageUrl": "cover", "imageUrl": "full"}
    }
    friend = normalize_friend(
        {"id": "usr_a", "location": "wrld_one:123", **images}, worlds
    )
    assert friend["avatar_url"] == expected
    assert friend["world_image_url"] == "cover"
    private = normalize_friend({"id": "usr_a", "location": "private", **images}, worlds)
    assert private["world_image_url"] == ""
    worlds["wrld_one"]["thumbnailImageUrl"] = ""
    assert (
        normalize_friend({"id": "usr_a", "location": "wrld_one:123"}, worlds)[
            "world_image_url"
        ]
        == "full"
    )


def test_online_alerts_ignore_first_sync_color_changes_and_reconnect():
    tracker = Transitions()
    assert tracker.observe("a", [{"id": "usr_1", "presence": "game"}]) == []
    assert tracker.observe("b", [{"id": "usr_1", "presence": "offline"}]) == []
    assert (
        tracker.observe("a", [{"id": "usr_1", "presence": "game", "color": "red"}])
        == []
    )
    assert tracker.observe("b", [{"id": "usr_1", "presence": "game"}]) == ["usr_1"]
    tracker.reset("b")
    assert tracker.observe("b", [{"id": "usr_1", "presence": "game"}]) == []


def test_backgrounds_are_separate_and_invalid_images_do_not_replace(tmp_path):
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (20, 20), "pink").save(source)
    store = Store(tmp_path / "data")
    store.set_background("qq:100", source)
    assert store.background("qq:100")
    assert store.background("qq:200") is None
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    with pytest.raises(ValueError):
        store.set_background("qq:100", bad)
    assert store.background("qq:100")
    store.reset_background("qq:200")
    assert store.background("qq:100")


def test_encrypted_sessions_cannot_be_moved_to_another_qq(tmp_path):
    from cryptography.fernet import InvalidToken

    store = Store(tmp_path)
    store.save_account("qq:100", "usr_a", "A", {"auth": "SECRET_A"})
    store.save_account("qq:200", "usr_b", "B", {"auth": "SECRET_B"})
    encrypted = store.db.execute(
        "SELECT cookies FROM accounts WHERE owner=?", ("qq:100",)
    ).fetchone()[0]
    store.db.execute(
        "UPDATE accounts SET cookies=? WHERE owner=?", (encrypted, "qq:200")
    )
    store.db.commit()
    with pytest.raises(InvalidToken):
        store.account("qq:200")


def test_page_size_is_validated_and_persisted_per_owner(tmp_path):
    store = Store(tmp_path)
    store.set_page_size("qq:100", 3)
    assert store.page_size("qq:100") == 3
    assert store.page_size("qq:200") == 8
    with pytest.raises(ValueError):
        store.set_page_size("qq:100", 0)
    store.close()
    assert Store(tmp_path).page_size("qq:100") == 3
