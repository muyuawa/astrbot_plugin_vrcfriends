import pytest
from PIL import Image

from astrbot_plugin_vrcfriends.commands import Controller

from .test_service import connected


async def test_group_login_is_rejected_without_creating_pending_credentials(tmp_path):
    service = await connected(tmp_path)
    controller = Controller(service)
    reply = await controller.execute("qq:300", "group:1", False, "登录 someone secret")
    assert "私聊" in reply.text
    assert "qq:300" not in service.pending
    assert service.store.account("qq:300") is None


async def test_favorites_only_prioritize_and_subscriptions_only_alert(tmp_path):
    service = await connected(tmp_path)
    service.clients["qq:100"].items = [
        {
            "id": "usr_a",
            "displayName": "Alice",
            "location": "private",
            "status": "active",
        },
        {
            "id": "usr_z",
            "displayName": "Zoe",
            "location": "private",
            "status": "join me",
        },
    ]
    controller = Controller(service)
    assert (
        "已收藏" in (await controller.execute("qq:100", "g:1", True, "收藏 Zoe")).text
    )
    reply = await controller.execute("qq:100", "g:1", True, "好友")
    assert reply.card["friends"][0]["id"] == "usr_z"
    assert service.store.subscriptions("qq:100") == []
    assert (
        "已订阅" in (await controller.execute("qq:100", "g:1", True, "订阅 Alice")).text
    )
    assert [f["id"] for f in service.store.favorites("qq:100")] == ["usr_z"]
    assert service.store.subscriptions("qq:200") == []


async def test_pagination_size_and_list_numbers_are_owner_and_session_scoped(tmp_path):
    service = await connected(tmp_path)
    service.clients["qq:100"].items = [
        {"id": "usr_" + str(i), "displayName": "F" + str(i), "location": "private"}
        for i in range(5)
    ]
    controller = Controller(service)
    await controller.execute("qq:100", "g:1", True, "每页 2")
    reply = await controller.execute("qq:100", "g:1", True, "好友 2")
    assert [f["name"] for f in reply.card["friends"]] == ["F2", "F3"]
    assert reply.card["page"] == 2 and reply.card["pages"] == 3
    assert reply.card["friends"][0]["number"] == 3
    assert (
        "已收藏" in (await controller.execute("qq:100", "g:1", True, "收藏 #03")).text
    )
    assert [f["id"] for f in service.store.favorites("qq:100")] == ["usr_2"]
    assert (
        "重新查询" in (await controller.execute("qq:200", "g:1", True, "收藏 #03")).text
    )
    assert (
        "重新查询" in (await controller.execute("qq:100", "g:2", True, "收藏 #03")).text
    )
    assert "范围" in (await controller.execute("qq:100", "g:1", True, "好友 9")).text


async def test_background_update_and_reset_are_private_to_sender(tmp_path):
    service = await connected(tmp_path / "store")
    controller = Controller(service)
    image = tmp_path / "bg.png"
    Image.new("RGB", (20, 20), "blue").save(image)
    assert (
        "已更新"
        in (await controller.execute("qq:100", "g:1", True, "背景", image)).text
    )
    assert service.store.background("qq:100")
    assert service.store.background("qq:200") is None
    await controller.execute("qq:200", "g:1", True, "背景 默认")
    assert service.store.background("qq:100")


async def test_login_and_all_supported_verification_aliases(tmp_path):
    service = await connected(tmp_path)
    controller = Controller(service)
    reply = await controller.execute("qq:300", "p:3", True, "登录 Alice password")
    assert "邮箱" in reply.text
    reply = await controller.execute("qq:300", "p:3", True, "验证 123456")
    assert "登录成功" in reply.text
    assert service.store.account("qq:300")["name"] == "Alice"
    assert (
        "没有待验证"
        in (await controller.execute("qq:400", "p:4", True, "恢复码 recovery")).text
    )


@pytest.mark.parametrize("action", ["Cookie", "cookie", "COOKIE", "CoOkIe"])
async def test_cookie_import_requires_private_chat_in_every_capitalization(
    tmp_path, action
):
    service = await connected(tmp_path)
    reply = await Controller(service).execute(
        "qq:300", "group:1", False, action + " secret-cookie"
    )
    assert "私聊" in reply.text
    assert service.store.account("qq:300") is None


async def test_all_friends_pagination_preserves_filter_and_last_page_uses_visible_number(
    tmp_path,
):
    service = await connected(tmp_path)
    service.clients["qq:100"].items = [
        {"id": "usr_" + str(i), "displayName": "F" + str(i), "location": "offline"}
        for i in range(5)
    ]
    controller = Controller(service)
    await controller.execute("qq:100", "g:1", True, "每页 2")
    first = await controller.execute("qq:100", "g:1", True, "好友 全部")
    assert first.card["hint"] == "/vrc 好友 全部 2"
    second = await controller.execute("qq:100", "g:1", True, first.card["hint"][5:])
    assert [f["name"] for f in second.card["friends"]] == ["F2", "F3"]
    last = await controller.execute("qq:100", "g:1", True, "好友 全部 3")
    assert "#05" in last.card["hint"]
    assert "#01" not in last.card["hint"]


async def test_search_finds_multiple_and_offline_friends_then_favorites_and_subscribes_by_number(
    tmp_path,
):
    service = await connected(tmp_path)
    service.clients["qq:100"].items = [
        {"id": "usr_a", "displayName": "Mio One", "location": "offline"},
        {"id": "usr_b", "displayName": "Mio Two", "location": "private"},
        {"id": "usr_c", "displayName": "Other", "location": "private"},
    ]
    controller = Controller(service)
    await controller.execute("qq:100", "g:1", True, "每页 1")
    first = await controller.execute("qq:100", "g:1", True, "查找 mio")
    assert first.card["total"] == 2 and first.card["pages"] == 2
    next_command = first.card["hint"][5:]
    second = await controller.execute("qq:100", "g:1", True, next_command)
    assert second.card["friends"][0]["id"] == "usr_a"
    assert second.card["friends"][0]["number"] == 2
    assert (
        "已收藏 Mio One"
        in (await controller.execute("qq:100", "g:1", True, "收藏 #02")).text
    )
    assert (
        "已订阅 Mio One"
        in (await controller.execute("qq:100", "g:1", True, "订阅 #02")).text
    )
    assert service.store.favorites("qq:200") == []
    other = await controller.execute("qq:200", "g:1", True, "查找 mio")
    assert other.card["total"] == 0


@pytest.mark.parametrize(
    "query,expected",
    [("usr_two", "usr_two"), ("123", "usr_one"), ('"Name 123"', "usr_one")],
)
async def test_search_accepts_ids_numeric_names_and_names_with_spaces(
    tmp_path, query, expected
):
    service = await connected(tmp_path)
    service.clients["qq:100"].items = [
        {"id": "usr_one", "displayName": "Name 123", "location": "offline"},
        {"id": "usr_two", "displayName": "Second", "location": "offline"},
    ]
    reply = await Controller(service).execute("qq:100", "g:1", True, "查找 " + query)
    assert [f["id"] for f in reply.card["friends"]] == [expected]
