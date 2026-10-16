import re


STATUS = {
    "join me": ("blue", "欢迎加入"),
    "active": ("green", "在线"),
    "ask me": ("yellow", "询问加入"),
    "busy": ("red", "请勿打扰"),
}


def normalize_friend(raw, worlds):
    location = raw.get("location") or ""
    if not location and raw.get("_api_online") is False:
        location = "offline"
    private = location == "private"
    traveling = location.startswith("traveling")
    world_id = location.split(":", 1)[0] if location.startswith("wrld_") else ""
    game = private or traveling or bool(world_id)
    web = (
        not game
        and location == "offline"
        and (raw.get("_api_online") or raw.get("state") in {"active", "online"})
    )
    presence = (
        "game"
        if game
        else "web"
        if web
        else "offline"
        if location == "offline"
        else "unknown"
    )
    color, status = (
        STATUS.get(raw.get("status"), ("gray", "在线"))
        if game
        else (
            "gray",
            "网页在线" if web else "离线" if presence == "offline" else "状态未知",
        )
    )
    meta = ""
    if private:
        text = "私人房间 · 位置未公开"
    elif traveling:
        text = "正在切换房间"
    elif world_id:
        text = worlds.get(world_id, {}).get("name") or world_id
        instance = location.partition(":")[2]
        access = "公开房"
        if "~hidden(" in instance:
            access = "好友+房"
        elif "~friends(" in instance:
            access = "好友房"
        elif "~private(" in instance:
            access = "邀请+房" if "~canRequestInvite" in instance else "邀请房"
        elif "~group(" in instance:
            access = (
                "群组公开房"
                if "~groupAccessType(public)" in instance
                else "群组+房"
                if "~groupAccessType(plus)" in instance
                else "群组房"
            )
        region = re.search(r"~region\(([^)]+)\)", instance)
        meta = " · ".join(
            x
            for x in [
                access,
                "#" + instance.split("~")[0] if instance else "",
                region[1].upper() if region else "",
            ]
            if x
        )
    else:
        text = (
            "网页在线 · 未进入游戏"
            if web
            else "未在游戏中"
            if presence == "offline"
            else "位置暂不可用"
        )
    return {
        "id": raw["id"],
        "name": raw.get("displayName") or raw["id"],
        "presence": presence,
        "color": color,
        "status_label": status,
        "location_text": text,
        "meta": meta,
        "private": private,
        "avatar_url": raw.get("iconUrl")
        or raw.get("userIcon")
        or raw.get("profilePicOverride")
        or raw.get("currentAvatarThumbnailImageUrl")
        or raw.get("currentAvatarImageUrl")
        or "",
        "world_image_url": (
            worlds.get(world_id, {}).get("thumbnailImageUrl")
            or worlds.get(world_id, {}).get("imageUrl")
            or ""
        )
        if world_id
        else "",
        "favorite": False,
        "subscribed": False,
    }


def resolve_friend(query, friends, snapshot=None):
    query = query.strip()
    if query.startswith("#"):
        if (
            not snapshot
            or not query[1:].isdigit()
            or not 1 <= int(query[1:]) <= len(snapshot)
        ):
            raise ValueError("好友编号已失效，请重新查询好友列表。")
        query = snapshot[int(query[1:]) - 1]
    exact = [
        f
        for f in friends
        if f["id"] == query
        or (f.get("displayName") or f.get("name", "")).casefold() == query.casefold()
    ]
    matches = exact or [
        f
        for f in friends
        if query.casefold() in (f.get("displayName") or f.get("name", "")).casefold()
    ]
    if not query or not matches:
        raise ValueError("没有找到这位好友，请使用完整昵称或用户 ID。")
    if len(matches) > 1:
        choices = "\n".join(
            f"{f.get('displayName') or f.get('name')}  {f['id']}" for f in matches[:10]
        )
        raise ValueError("匹配到多个同名或相似好友，请使用用户 ID：\n" + choices)
    return matches[0]


class Transitions:
    def __init__(self):
        self.previous = {}

    def reset(self, owner):
        self.previous.pop(owner, None)

    def observe(self, owner, friends):
        current = {f["id"]: f["presence"] for f in friends}
        previous = self.previous.get(owner)
        self.previous[owner] = current
        if previous is None:
            return []
        return [
            fid
            for fid, presence in current.items()
            if presence == "game" and previous.get(fid) in {"offline", "web"}
        ]
