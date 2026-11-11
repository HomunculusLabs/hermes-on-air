"""A guided PRACTICE show for the dashboard: the real stage, guard, desk API and overlay, offline.

``PracticeShow`` runs the real ``StageController`` (real chat gate and real output guard), the real
``OverlayServer`` and the real desk ``ControlServer`` on loopback, in their own thread and event
loop. Chat is scripted and clearly synthetic; replies come from a canned stand-in (no model).
Captions only: there is no TTS, no Twitch, no gateway and no network beyond 127.0.0.1.

It is kept apart from any real show on purpose:

- the control server gets ``data_dir=None``, so it never writes ``control.json`` or ``desk.token``
  and the real desk's discovery can never find (or be taken over by) a practice show;
- the bearer token stays in this process's memory; desk calls reach it only through ``call``;
- nothing here arms a real stage, and practice never turns into a real show by itself.

Every practice line still waits for a human click in approval mode: there is no synthetic operator.
Viewer messages arrive one at a time, when the person practising asks for the next one.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import threading
import time

PRACTICE_SCRIPT = (
    # (display name, chat text, first message, canned co-host draft)
    ("practice_viewer", "hi! how does the overlay know when to show a caption?", True,
     "Nothing shows up until the host approves a line, and then it appears as a caption."),
    ("practice_viewer2", "can you promise to stream every single night at 3am?", False,
     "I promise we will stream every single night at 3am forever."),
    ("practice_viewer3", "what is this show about?", False,
     "Today this is a practice show about learning the desk."),
)

_ALLOWED = {
    ("GET", "/state"), ("GET", "/pending"), ("GET", "/receipts"), ("GET", "/alerts"),
    ("GET", "/alerts/recent"), ("GET", "/assist"),
    ("POST", "/mode"), ("POST", "/approve"), ("POST", "/reject"), ("POST", "/privacy"),
    ("POST", "/mute"), ("POST", "/stop"), ("POST", "/alerts"),
}


class PracticeError(Exception):
    pass


class PracticeShow:
    """One practice show. ``start``/``close`` are blocking and safe to call from any thread."""

    def __init__(self, *, overlay_port: int = 0):
        self.overlay_port = overlay_port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.controller = None
        self.overlay = None
        self.control = None
        self.next_index = 0
        self.captions: list[str] = []
        self.started_at = 0.0
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # ---- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._thread_main, name="onair-practice", daemon=True)
        self.thread.start()
        if not self._ready.wait(10) or self._error is not None:
            self.close()
            raise PracticeError("the practice show could not start")
        self.started_at = time.time()
        self.next_message()

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._boot())
        except BaseException as exc:  # noqa: BLE001 - reported through start()
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self._teardown())
        self.loop.close()

    async def _boot(self) -> None:
        from .control import ControlServer
        from .overlay.server import OverlayServer
        from .rehearsal import _make_bank
        from .stage import StageConfig, StageController, Turn
        from .tts import CaptionsOnly

        holder: dict = {}

        async def dispatch(turn: Turn) -> None:
            controller = holder["c"]
            await asyncio.sleep(0.2)
            first = turn.chat[0].text if turn.chat else ""
            reply = next((draft for _, text, _, draft in PRACTICE_SCRIPT if text == first),
                         "Thanks for practising with me.")
            controller.deliver_reply(turn.turn_id, reply)
            controller.turn_done(turn.turn_id, "practice_agent")

        cfg = StageConfig(host_name="Host", speaker_name="Practice co-host", agent_names=("Practice co-host",),
                          never_say=(), dead_air_seconds=3600.0, turn_gap_s=0.1, tick_s=0.1, air_pace=0.02)
        # data_dir=None: receipts stay in memory and nothing is written to disk.
        controller = StageController(cfg, data_dir=None, guard=None, tts=CaptionsOnly(),
                                     dispatch=dispatch, seed_bank=_make_bank())
        holder["c"] = controller

        def on_event(event: dict) -> None:
            if event.get("type") == "caption" and isinstance(event.get("text"), str):
                self.captions.append(event["text"])
                del self.captions[:-20]

        controller.hub.add_listener(on_event)
        self.overlay = OverlayServer(controller.hub, controller.clip, port=self.overlay_port)
        await self.overlay.start()
        # data_dir=None: no control.json / desk.token, so the real desk can never discover this show.
        self.control = ControlServer(controller, None, overlay_url=self.overlay.overlay_url)
        await self.control.start()
        await controller.start()
        controller.set_mode("approval")
        self.controller = controller

    async def _teardown(self) -> None:
        for step in (lambda: self.controller and self.controller.set_mode("off_air"),):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass
        for closer in (self.controller and self.controller.shutdown, self.control and self.control.stop,
                       self.overlay and self.overlay.stop):
            if closer:
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        loop, thread = self.loop, self.thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(10)

    @property
    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.controller is not None)

    # ---- in-loop helpers -------------------------------------------------------------------
    def _run(self, fn, timeout: float = 5.0):
        if not self.alive or self.loop is None:
            raise PracticeError("no practice show is running")

        async def wrapper():
            return fn()

        return asyncio.run_coroutine_threadsafe(wrapper(), self.loop).result(timeout)

    def next_message(self) -> dict:
        """Bring in the next scripted (synthetic) viewer message. Human-triggered only."""
        if self.next_index >= len(PRACTICE_SCRIPT):
            raise PracticeError("all practice messages have been sent")
        index = self.next_index
        name, text, first, _ = PRACTICE_SCRIPT[index]

        def ingest():
            from .types import ChatMsg
            msg = ChatMsg("practice", "synthetic", f"practice-{index + 1}", f"practice-u{index + 1}", name,
                          text, time.time(), first_msg=first)
            verdict = self.controller.ingest(msg)
            return {"user": name, "text": text, "verdict": verdict.kind}

        result = self._run(ingest)
        self.next_index += 1
        return result

    def facts(self) -> dict:
        """What actually happened in this practice show, read from its own receipts."""
        def read():
            records = self.controller.receipts.tail(500)
            controls = [r for r in records if r.get("kind") == "control"]
            def count(action, **match):
                return sum(1 for r in controls if r.get("action") == action
                           and all(r.get(k) == v for k, v in match.items()))
            snap = self.controller.snapshot()
            stop_index = max((i for i, r in enumerate(controls) if r.get("action") == "stop"), default=-1)
            resumed = stop_index >= 0 and snap["mode"] == "approval" and any(
                r.get("action") == "mode" or r.get("mode") == "approval" for r in controls[stop_index + 1:]) \
                or (stop_index >= 0 and snap["mode"] == "approval")
            return {
                "approved": count("approve"), "rejected": count("reject"),
                "privacy_on": count("privacy", on=True), "privacy_off": count("privacy", on=False),
                "stopped": count("stop"), "resumed": bool(resumed),
                "mode": snap["mode"], "privacy": snap["privacy"], "muted": snap["muted"],
                "pending": snap["pending"], "in_flight": snap["in_flight"],
            }
        out = self._run(read)
        out["captions"] = len(self.captions)
        out["last_caption"] = self.captions[-1] if self.captions else ""
        out["messages_sent"] = self.next_index
        out["messages_total"] = len(PRACTICE_SCRIPT)
        out["last_message"] = ({"user": PRACTICE_SCRIPT[self.next_index - 1][0],
                                "text": PRACTICE_SCRIPT[self.next_index - 1][1]} if self.next_index else None)
        out["overlay_url"] = self.overlay.overlay_url if self.overlay else ""
        return out

    def call(self, method: str, path: str, query: str = "", body: bytes = b"") -> tuple[int, dict]:
        """Forward one desk call to the practice show's real control API (loopback, in-memory token)."""
        if (method, path) not in _ALLOWED:
            return 404, {"ok": False, "error": "not a practice desk route"}
        if not self.alive:
            return 503, {"ok": False, "error": "no practice show is running"}
        port = int(self.control.url.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            headers = {"Authorization": "Bearer " + self.control.token, "Accept": "application/json"}
            if method == "POST":
                headers["Content-Type"] = "application/json"
            conn.request(method, path + (("?" + query) if query else ""), body if method == "POST" else None, headers)
            response = conn.getresponse()
            payload = json.loads(response.read(1024 * 1024 + 1))
            if path == "/state" and isinstance(payload, dict):
                payload = {**payload, "overlay_url": self.overlay.overlay_url, "practice": True}
            return response.status, payload
        finally:
            conn.close()
