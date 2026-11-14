"""Socket.IO event handlers for PiKaraoke."""

import logging

from flask import request

from pikaraoke.lib.current_app import get_karaoke_instance

# Track connected splash screen clients and the elected master
splash_connections = set()
master_splash_id = None
splash_versions = {}


def setup_socket_events(socketio):
    """Register Socket.IO event handlers.

    Args:
        socketio: The SocketIO instance.
    """

    def current_event(payload) -> bool:
        k = get_karaoke_instance()
        return (
            request.sid == master_splash_id
            and isinstance(payload, dict)
            and payload.get("stream_url") == k.playback_controller.now_playing_url
            and bool(k.playback_controller.now_playing_url)
        )

    @socketio.on("end_song")
    def end_song(payload) -> None:
        """Ignore stale-track and non-master completion events."""
        if current_event(payload):
            get_karaoke_instance().playback_controller.end_song(payload.get("reason"))

    @socketio.on("start_song")
    def start_song(payload=None) -> None:
        if current_event(payload):
            get_karaoke_instance().playback_controller.start_song()

    @socketio.on("playback_buffering")
    def playback_buffering(payload) -> None:
        if not current_event(payload):
            return
        k = get_karaoke_instance()
        duration = payload.get("duration")
        if isinstance(duration, (int, float)) and 0 < duration < 86400:
            k.playback_controller.now_playing_duration = (
                k.playback_controller.expected_duration or duration
            )
        k.playback_controller.set_buffering(bool(payload.get("buffering")))

    @socketio.on("clear_notification")
    def clear_notification() -> None:
        """Handle clear_notification WebSocket event to dismiss notifications."""
        k = get_karaoke_instance()
        k.reset_now_playing_notification()

    @socketio.on("register_splash")
    def register_splash(payload=None) -> None:
        """The most recently opened current-version player owns audio and completion."""
        global master_splash_id
        sid = request.sid
        version = payload.get("protocol", 1) if isinstance(payload, dict) else 1
        splash_connections.add(sid)
        splash_versions[sid] = version
        if version >= 2 or master_splash_id is None:
            master_splash_id = sid
        for client in splash_connections:
            role = "master" if client == master_splash_id else "slave"
            socketio.emit("splash_role", role, room=client)
            if splash_versions.get(client, 1) < 2:
                # Retire stale pages' audio without changing persisted preferences.
                socketio.emit("volume", 0, room=client)
                for key, value in [
                    ("disable_bg_music", True),
                    ("bg_music_volume", 0),
                    ("disable_bg_video", True),
                ]:
                    socketio.emit("preferences_update", {"key": key, "value": value}, room=client)
        logging.info("Splash registered, protocol %s, master %s", version, sid == master_splash_id)

    @socketio.on("playback_position")
    def handle_playback_position(payload) -> None:
        if not current_event(payload):
            return
        import math

        position = payload.get("position")
        if not isinstance(position, (int, float)) or not math.isfinite(position) or position < 0:
            return
        get_karaoke_instance().playback_controller.now_playing_position = position
        socketio.emit("playback_position", position, include_self=False)

    @socketio.on("disconnect")
    def handle_disconnect() -> None:
        """Handle Socket.IO client disconnection and manage splash role handover."""
        global master_splash_id
        sid = request.sid
        if sid in splash_connections:
            splash_connections.remove(sid)
            splash_versions.pop(sid, None)
            logging.info(f"Splash screen disconnected: {sid}")
            if sid == master_splash_id:
                master_splash_id = None
                logging.info("Master splash disconnected, electing new master")
                if splash_connections:
                    # Elect new master from remaining connections
                    new_master = max(
                        splash_connections, key=lambda client: splash_versions.get(client, 1)
                    )
                    master_splash_id = new_master
                    socketio.emit("splash_role", "master", room=new_master)
                    logging.info(f"New master splash elected: {new_master}")

    @socketio.on("request_mic_devices")
    def handle_request_mic_devices() -> None:
        """Client requests the current mic device list from the server."""
        k = get_karaoke_instance()
        socketio.emit(
            "mic_devices_state",
            k.sound_manager.get_enriched_devices(),
            room=request.sid,
        )

    @socketio.on("request_mic_settings")
    def handle_request_mic_settings() -> None:
        """Client requests current mic global settings (latency, echo cancel)."""
        k = get_karaoke_instance()
        socketio.emit(
            "mic_settings_state",
            k.sound_manager.get_mic_settings_state(),
            room=request.sid,
        )

    @socketio.on("mic_latency_change")
    def handle_mic_latency_change(data: dict) -> None:
        """Handle mic latency change from control UI."""
        k = get_karaoke_instance()
        latency_ms = int(data.get("latency_ms", 50))
        state = k.sound_manager.set_latency_ms(latency_ms)
        socketio.emit("mic_settings_state", state)

    @socketio.on("mic_echo_cancel_change")
    def handle_mic_echo_cancel_change(data: dict) -> None:
        """Handle echo cancellation toggle from control UI."""
        k = get_karaoke_instance()
        enabled = bool(data.get("enabled", False))
        state = k.sound_manager.set_echo_cancel(enabled)
        socketio.emit("mic_settings_state", state)

    @socketio.on("mic_refresh")
    def handle_mic_refresh() -> None:
        """Re-enumerate mic and output devices server-side and broadcast updated lists."""
        k = get_karaoke_instance()
        enriched = k.sound_manager.refresh()
        socketio.emit("mic_devices_state", enriched)

    @socketio.on("mic_update")
    def handle_mic_update(data: dict) -> None:
        """Handle mic configuration change from control UI.

        Persists settings and activates/deactivates mic server-side.
        """
        k = get_karaoke_instance()
        label = data.get("label", "")
        device_id = str(data.get("deviceId", ""))
        enabled = data.get("enabled", False)
        volume = data.get("volume", 1.0)

        if label:
            settings = k.sound_manager.load_settings()
            new_state = {"enabled": enabled, "volume": volume}
            if settings.get(label) == new_state:
                return
            settings[label] = new_state
            k.sound_manager.save_settings(settings)

        # Activate or deactivate the mic stream server-side
        if enabled:
            k.sound_manager.activate(device_id, volume)
        else:
            k.sound_manager.deactivate(device_id)

        logging.info(f"Mic update: {label} enabled={enabled} volume={volume}")
        socketio.emit("mic_update", data)
