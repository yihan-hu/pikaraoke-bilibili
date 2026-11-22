"""Room lyrics and audience danmaku controls."""

from flask import jsonify, request
from flask_smorest import Blueprint

from pikaraoke.lib.current_app import get_karaoke_instance, is_admin
from pikaraoke.lib.room_overlays import RoomOverlays, lyrics_fetch, lyrics_search, parse_lrc

room_bp = Blueprint("room", __name__)


def manager():
    k = get_karaoke_instance()
    if "room_overlays" not in k.__dict__:
        k.room_overlays = RoomOverlays()
    return k, k.room_overlays


def broadcast_state(k, room):
    if k.socketio:
        k.socketio.emit("room_overlays", room.state())


@room_bp.route("/room/state")
def room_state():
    _, room = manager()
    return jsonify(room.state())


@room_bp.route("/room/lyrics/search")
def search_lyrics():
    if not is_admin():
        return jsonify(error="需要管理员登录"), 403
    query = request.args.get("q", "").strip()
    if not query or len(query) > 200:
        return jsonify(error="请输入歌名或歌手"), 400
    try:
        return jsonify(results=lyrics_search(query))
    except (OSError, ValueError):
        return jsonify(error="歌词服务暂时不可用，可稍后重试或粘贴歌词"), 502


@room_bp.route("/room/lyrics", methods=["POST"])
def update_lyrics():
    if not is_admin() or request.headers.get("X-Pikaraoke-Settings") != "1":
        return jsonify(error="需要管理员登录"), 403
    k, room = manager()
    data = request.get_json(silent=True) or {}
    track_key = k.playback_controller.get_now_playing().get("media_key")
    if data.get("track_key", track_key) != track_key:
        return jsonify(error="歌曲已切换，请重新选择歌词"), 409
    if "id" in data or "text" in data:
        if not track_key:
            return jsonify(error="请先播放要显示歌词的歌曲"), 400
        try:
            if "id" in data:
                record_id = data["id"]
                if record_id == "web":
                    record = room.get_web_lyrics(track_key)
                elif isinstance(record_id, int) and 0 < record_id < 2147483647:
                    record = lyrics_fetch(record_id)
                else:
                    raise ValueError("无效歌词编号")
            else:
                text = data["text"]
                if not isinstance(text, str) or len(text) > 50000:
                    raise ValueError("歌词最多50000字")
                record = {
                    "trackName": k.playback_controller.now_playing,
                    "syncedLyrics": text,
                    "plainLyrics": "" if parse_lrc(text) else text,
                }
            if k.playback_controller.get_now_playing().get("media_key") != track_key:
                return jsonify(error="歌曲已切换，请重新选择歌词"), 409
            room.select_lyrics(record, track_key)
        except (OSError, ValueError) as error:
            return jsonify(error=str(error)), 400
    for key, allowed in [("mode", ("synced", "plain")), ("position", ("top", "bottom"))]:
        if key in data:
            if data[key] not in allowed:
                return jsonify(error="无效歌词设置"), 400
            room.lyrics[key] = data[key]
    if room.lyrics["mode"] == "synced" and not room.lyrics["lines"]:
        room.lyrics["mode"] = "plain"
    if "visible" in data:
        room.lyrics["visible"] = data["visible"] is True
    if "offset" in data:
        try:
            offset = float(data["offset"])
            if not -120 <= offset <= 120:
                raise ValueError()
            room.lyrics["offset"] = offset
        except (ValueError, TypeError):
            return jsonify(error="校准范围为正负120秒"), 400
    if "auto_search" in data:
        room.set_auto_search(data["auto_search"] is True, lambda: broadcast_state(k, room))
    broadcast_state(k, room)
    return jsonify(room.state())


@room_bp.route("/room/danmaku", methods=["POST"])
def send_danmaku():
    k, room = manager()
    data = request.get_json(silent=True) or {}
    text, name = data.get("text", ""), data.get("name", "游客")
    if not isinstance(text, str) or not isinstance(name, str):
        return jsonify(error="请输入弹幕和名字"), 400
    try:
        item = room.danmaku(text, name, (request.remote_addr or "") + ":" + name)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if k.socketio:
        k.socketio.emit("danmaku", item)
    return jsonify(success=True)


@room_bp.route("/room/danmaku/settings", methods=["POST"])
def danmaku_settings():
    if not is_admin() or request.headers.get("X-Pikaraoke-Settings") != "1":
        return jsonify(error="需要管理员登录"), 403
    k, room = manager()
    data = request.get_json(silent=True) or {}
    if "enabled" in data:
        room.danmaku_enabled = data["enabled"] is True
    if data.get("clear") and k.socketio:
        k.socketio.emit("danmaku_clear")
    broadcast_state(k, room)
    return jsonify(room.state())
