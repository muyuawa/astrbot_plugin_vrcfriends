from html.parser import HTMLParser

import pytest

from astrbot_plugin_vrcfriends.cards import Cards, text_fallback
from astrbot_plugin_vrcfriends.store import Store


def test_rendered_card_escapes_friend_names_and_keeps_only_status_dots(tmp_path):
    store = Store(tmp_path)
    store.save_account("qq:1", "usr_a", "Owner", {"auth": "secret"})
    cards = Cards(tmp_path, store)
    friend = {
        "id": "usr_f",
        "name": "<script>alert(1)</script>",
        "presence": "game",
        "color": "blue",
        "status_label": "欢迎加入",
        "location_text": "<img src=x>",
        "meta": "",
        "private": False,
        "favorite": True,
        "subscribed": True,
        "number": 1,
    }
    payload = {
        "title": "好友在线",
        "owner_name": "Owner",
        "friends": [friend],
        "page": 1,
        "pages": 1,
        "page_size": 8,
        "kind": "好友",
        "total": 1,
        "hint": "/vrc 帮助",
    }
    html = cards.html("qq:1", payload)

    class Inspect(HTMLParser):
        def __init__(self):
            super().__init__()
            self.scripts = 0
            self.images = 0
            self.lights = []
            self.status_text_nodes = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            self.scripts += tag == "script"
            self.images += tag == "img"
            if "light" in attrs.get("class", "").split() and "aria-label" in attrs:
                self.lights.append(attrs["aria-label"])
            self.status_text_nodes += "vrc-status" in attrs.get("class", "").split()

    parsed = Inspect()
    parsed.feed(html)
    assert parsed.scripts == 0 and parsed.images == 0
    assert parsed.lights == ["欢迎加入"]
    assert parsed.status_text_nodes == 0
    assert "<script>" not in html
    assert "[欢迎加入]" in text_fallback(payload)


@pytest.mark.parametrize("alert", [False, True])
def test_cards_show_avatar_and_optional_world_cover_as_embedded_images(tmp_path, alert):
    store = Store(tmp_path)
    store.save_account("qq:1", "usr_a", "Owner", {"auth": "secret"})
    cards = Cards(tmp_path, store)
    friend = {
        "name": "Friend",
        "color": "red",
        "status_label": "请勿打扰",
        "number": 1,
        "location_text": "World",
        "meta": "",
        "private": False,
        "avatar_image": "data:image/jpeg;base64,YXZhdGFy",
        "world_image": "data:image/jpeg;base64,d29ybGQ=",
    }
    payload = {
        "title": "好友",
        "owner_name": "Owner",
        "friends": [friend],
        "alert": alert,
        "page": 1,
        "pages": 1,
        "kind": "好友",
        "total": 1,
        "hint": "/vrc 帮助",
    }
    html = cards.html("qq:1", payload)
    assert '<img class="avatar-image"' in html
    assert '<img class="world-image"' in html
    assert friend["avatar_image"] in html and friend["world_image"] in html
    assert 'aria-label="请勿打扰"' in html
    friend["world_image"] = ""
    friend["avatar_image"] = ""
    html = cards.html("qq:1", payload)
    assert "<img " not in html
