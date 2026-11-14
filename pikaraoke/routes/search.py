"""YouTube search and download routes."""

from __future__ import annotations

import subprocess

import flask_babel
from flask import jsonify, render_template, request
from flask_smorest import Blueprint
from marshmallow import Schema, fields

from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name
from pikaraoke.lib.youtube_dl import get_search_results, get_stream_url

_ = flask_babel.gettext

search_bp = Blueprint("search", __name__)


class PreviewQuery(Schema):
    url = fields.String(required=True, metadata={"description": "YouTube video URL to preview"})


class DownloadBody(Schema):
    song_url = fields.String(required=True, metadata={"description": "YouTube URL to download"})
    song_added_by = fields.String(
        required=True,
        metadata={"description": "Name of the user requesting the download"},
    )
    song_title = fields.String(
        required=True, metadata={"description": "Display title for the song"}
    )
    queue = fields.Boolean(
        load_default=False,
        metadata={"description": "Whether to queue the song after download"},
    )


@search_bp.route("/search", methods=["GET"])
def search():
    """YouTube search page."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    source = request.args.get("source", "bilibili")
    if source not in ("youtube", "bilibili"):
        return jsonify(error="Unsupported source"), 400
    search_string = request.args.get("search_string")
    if search_string:
        non_karaoke = request.args.get("non_karaoke") == "true"
        try:
            query = (
                search_string
                if non_karaoke
                else search_string + (" 伴奏" if source == "bilibili" else " karaoke")
            )
            search_results = get_search_results(query, source=source)
        except (ValueError, OSError, subprocess.SubprocessError):
            return (
                render_template(
                    "dual_search.html",
                    source=source,
                    search_string=search_string,
                    search_results=None,
                    title=_("Add New"),
                    site_title=site_name,
                    error="搜索失败，请检查网络或登录状态",
                ),
                502,
            )
    else:
        search_string = None
        search_results = None
    # A result already on this machine gets a queue action instead of a pointless
    # second download. One indexed query for the page, not one per result.
    library_matches = (
        k.db.get_paths_by_youtube_ids([r.video_id for r in search_results])
        if search_results
        else {}
    )
    return render_template(
        "search.html" if source == "youtube" else "dual_search.html",
        source=source,
        site_title=site_name,
        # MSG: Title of the page used to get new songs into the library.
        title=_("Add New"),
        search_results=search_results,
        search_string=search_string,
        library_matches=library_matches,
    )


@search_bp.route("/preview")
@search_bp.arguments(PreviewQuery, location="query")
def preview(query):
    """Get a direct stream URL for previewing a YouTube video."""
    from pikaraoke.lib.bilibili import normalize_url

    try:
        url = normalize_url(query["url"])
    except (ValueError, OSError):
        return jsonify(error="Invalid video URL"), 400
    stream_url = get_stream_url(url)
    if stream_url is None:
        return jsonify({"error": "Could not fetch stream URL"}), 500
    return jsonify({"stream_url": stream_url})


@search_bp.route("/download", methods=["POST"])
@search_bp.arguments(DownloadBody, location="json")
def download(form):
    """Download a video from YouTube."""
    k = get_karaoke_instance()
    from pikaraoke.lib.bilibili import normalize_url

    try:
        song = normalize_url(form["song_url"])
        if "/video/av" in song:
            from urllib.parse import parse_qs, urlparse

            from pikaraoke.lib.bilibili import details

            number = int(parse_qs(urlparse(song).query).get("p", ["1"])[0])
            parts = details(song)["parts"]
            if number > len(parts):
                raise ValueError("Invalid part")
            song = parts[number - 1]["url"]
    except (ValueError, OSError):
        return jsonify(error="Invalid video URL"), 400
    user = form["song_added_by"]
    title = form["song_title"]
    queue = form.get("queue", False)

    from pikaraoke.lib.bilibili import media_id

    key = media_id(song)
    if key:
        path = k.db.get_paths_by_youtube_ids([key]).get(key)
        if path:
            if queue:
                k.queue_manager.enqueue(path, user)
            return jsonify(status="ok", existing=True)

    # Queue the download (processed serially by the download worker)
    k.download_manager.queue_download(song, queue, user, title)

    return jsonify({"status": "ok"})


@search_bp.route("/video-details")
def video_details():
    from pikaraoke.lib.bilibili import details

    try:
        return jsonify(details(request.args.get("url", "")))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return jsonify(error="无法读取分P，请检查链接、网络及 cookies"), 400


@search_bp.route("/bilibili-cookies", methods=["GET", "POST", "DELETE"])
def bilibili_cookies():
    from pikaraoke.lib.bilibili import cookie_path, save_cookies
    from pikaraoke.lib.current_app import is_admin

    if not is_admin():
        return jsonify(error="Administrator required"), 403
    if request.method != "GET" and request.headers.get("X-Pikaraoke-Settings") != "1":
        return jsonify(error="Invalid settings request"), 403
    if request.method == "DELETE":
        from pikaraoke.lib.bilibili_login import cancel_challenges

        cancel_challenges()
        cookie_path().unlink(missing_ok=True)
    if request.method == "POST":
        upload = request.files.get("cookies")
        if not upload:
            return jsonify(error="Missing cookies file"), 400
        content = upload.stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            return jsonify(error="File too large"), 400
        try:
            save_cookies(content.decode("utf-8-sig"))
        except (ValueError, UnicodeError, OSError):
            return jsonify(error="无效的 Netscape cookies 文件"), 400
    return jsonify(configured=cookie_path().exists())


@search_bp.route("/bilibili-login-status")
def bilibili_login_status():
    import requests

    from pikaraoke.lib.bilibili import cookie_path
    from pikaraoke.lib.current_app import is_admin

    if not is_admin():
        return jsonify(error="Administrator required"), 403
    if not cookie_path().exists():
        return jsonify(message="未配置登录信息")
    from pikaraoke.lib.bilibili import web_session

    try:
        with web_session() as client:
            data = client.get("https://api.bilibili.com/x/web-interface/nav", timeout=10).json()
        logged_in = bool(data.get("data", {}).get("isLogin"))
        return jsonify(
            logged_in=logged_in,
            message="登录有效" if logged_in else "登录已失效，请重新导入 cookies",
        )
    except (OSError, ValueError, requests.RequestException):
        return jsonify(error="无法检查登录，请检查网络或重新导入 cookies"), 502


@search_bp.route("/bilibili-login/qr", methods=["POST"])
def bilibili_login_qr():
    """Create an administrator-only, short-lived QR challenge."""
    from pikaraoke.lib.bilibili_login import create_challenge
    from pikaraoke.lib.current_app import is_admin

    if not is_admin() or request.headers.get("X-Pikaraoke-Settings") != "1":
        return jsonify(error="Administrator required"), 403
    try:
        response = jsonify(create_challenge())
        response.headers["Cache-Control"] = "no-store"
        return response
    except (OSError, ValueError):
        return jsonify(error="无法创建登录二维码，请检查网络后重试"), 502


@search_bp.route("/bilibili-login/poll", methods=["POST"])
def bilibili_login_poll():
    """Poll a challenge and persist the session only after phone confirmation."""
    from pikaraoke.lib.bilibili_login import poll_challenge
    from pikaraoke.lib.current_app import is_admin

    if not is_admin() or request.headers.get("X-Pikaraoke-Settings") != "1":
        return jsonify(error="Administrator required"), 403
    try:
        body = request.get_json(silent=True) or {}
        challenge_id = body.get("id", "")
        if not isinstance(challenge_id, str):
            return jsonify(error="Invalid challenge"), 400
        response = jsonify(poll_challenge(challenge_id))
        response.headers["Cache-Control"] = "no-store"
        return response
    except (OSError, ValueError):
        return jsonify(error="登录状态查询失败，请重试"), 502
