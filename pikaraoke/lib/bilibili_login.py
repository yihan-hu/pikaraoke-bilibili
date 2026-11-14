"""Short-lived QR challenges and durable local Bilibili credentials."""

import base64
import http.cookiejar
import io
import tempfile
import time
import uuid
from pathlib import Path

import qrcode
from qrcode.image.pure import PyPNGImage

from pikaraoke.lib.bilibili import cookie_path, save_cookies, web_session

_challenges: dict = {}


def create_challenge() -> dict:
    """Return a QR image without exposing the account cookies to the browser."""
    now = time.monotonic()
    for challenge_id, challenge in list(_challenges.items()):
        if challenge["expires"] < now:
            challenge["session"].close()
            del _challenges[challenge_id]
    if len(_challenges) >= 8:
        raise ValueError("登录请求较多，请稍后再试")
    session = web_session()
    try:
        response = session.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate", timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise ValueError("无法生成二维码，请稍后重试")
        data = payload["data"]
        image = qrcode.make(data["url"], image_factory=PyPNGImage)
        buffer = io.BytesIO()
        image.save(buffer)
        challenge_id = uuid.uuid4().hex
        _challenges[challenge_id] = {
            "key": data["qrcode_key"],
            "session": session,
            "expires": now + 180,
            "next_poll": 0,
        }
        return {
            "id": challenge_id,
            "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
            "expires_in": 180,
        }
    except Exception:
        session.close()
        raise


def persist_login(cookies) -> None:
    """Save a successful account session atomically for subsequent app launches."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "cookies.txt"
        jar = http.cookiejar.MozillaCookieJar(str(path))
        for cookie in cookies:
            if cookie.domain.lstrip(".") == "bilibili.com":
                jar.set_cookie(cookie)
        jar.save(ignore_discard=True, ignore_expires=True)
        save_cookies(path.read_text(encoding="utf-8"))


def poll_challenge(challenge_id: str) -> dict:
    """Complete only the challenge the browser created; persist on confirmation."""
    challenge = _challenges.get(challenge_id)
    if not challenge:
        return {"status": "expired", "message": "二维码已失效，请重新生成"}
    now = time.monotonic()
    if now > challenge["expires"]:
        challenge["session"].close()
        del _challenges[challenge_id]
        return {"status": "expired", "message": "二维码已过期，请重新生成"}
    if now < challenge["next_poll"]:
        return {"status": "waiting", "message": "等待扫码确认"}
    challenge["next_poll"] = now + 2
    response = challenge["session"].get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": challenge["key"]},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError("暂时无法查询扫码状态，请重试")
    code = payload.get("data", {}).get("code")
    if code == 0:
        persist_login(challenge["session"].cookies)
        challenge["session"].close()
        del _challenges[challenge_id]
        return {"status": "success", "message": "登录成功，已保存到本机，重启后仍会记住"}
    if code == 86038:
        challenge["session"].close()
        del _challenges[challenge_id]
        return {"status": "expired", "message": "二维码已过期，请重新生成"}
    if code == 86090:
        return {"status": "scanned", "message": "已扫码，请在 B站 App 确认登录"}
    if code == 86101:
        return {"status": "waiting", "message": "请用 B站 App 扫码"}
    raise ValueError("扫码状态异常，请重新生成二维码")


def cancel_challenges() -> None:
    """Clearing login also cancels outstanding QR authorizations."""
    for challenge in list(_challenges.values()):
        challenge["session"].close()
    _challenges.clear()
