"""A LABELLED loopback double of an OBS WebSocket v5 server (never a real OBS, no capture, no output).

Stateful enough to exercise the real controller: scenes/items, inputs/settings, program scene, input kinds,
scene-item transforms and the stream output, with knobs for adversarial behaviour (ignored scene switch,
stream that never goes active, delayed activation). Every request is logged for assertions.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import uuid

import websockets


def _digest(text):
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


# Kind lists shaped like the pinned upstream (obs-studio 7a25c3fd, obs-websocket e28f8497):
# - PipeWire window/desktop kinds are OBS_SOURCE_CAP_OBSOLETE (== DISABLED): GetInputKindList omits them and
#   CreateInput refuses them with 605. An input of that kind can only pre-exist (older scene collection).
# - xcomposite/xshm are registered only when OBS runs on X11 (linux-capture.c), never on Wayland.
OBSOLETE_KINDS = {"pipewire-window-capture-source", "pipewire-desktop-capture-source"}
# obs_bounds_type string values exactly as obs-websocket serializes them (e28f8497 src/utils/Json.h enum map).
BOUNDS_TYPES = {"OBS_BOUNDS_NONE", "OBS_BOUNDS_STRETCH", "OBS_BOUNDS_SCALE_INNER", "OBS_BOUNDS_SCALE_OUTER",
                "OBS_BOUNDS_SCALE_TO_WIDTH", "OBS_BOUNDS_SCALE_TO_HEIGHT", "OBS_BOUNDS_MAX_ONLY"}
# Every key SetSceneItemTransform reads (e28f8497 src/requesthandler/RequestHandler_SceneItems.cpp); others are ignored.
TRANSFORM_FIELDS = {"positionX", "positionY", "rotation", "scaleX", "scaleY", "alignment", "boundsType",
                    "boundsAlignment", "boundsWidth", "boundsHeight", "cropLeft", "cropRight", "cropTop", "cropBottom",
                    "cropToBounds"}


def _default_transform():
    # libobs defaults for a new scene item: top-left alignment, scale 1, no bounds, no crop.
    return {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "scaleX": 1.0, "scaleY": 1.0, "alignment": 5,
            "boundsType": "OBS_BOUNDS_NONE", "boundsAlignment": 0, "boundsWidth": 0.0, "boundsHeight": 0.0,
            "cropLeft": 0, "cropRight": 0, "cropTop": 0, "cropBottom": 0, "cropToBounds": False}
DEFAULT_KINDS = {
    ("linux", "wayland"): ["browser_source", "pipewire-screen-capture-source", "image_source"],
    ("linux", "x11"): ["browser_source", "xcomposite_input", "xshm_input_v2", "pipewire-screen-capture-source",
                       "image_source"],
    ("windows", None): ["browser_source", "window_capture", "monitor_capture"],
    ("macos", None): ["browser_source", "screen_capture", "window_capture"],
}


class FakeObs:
    def __init__(self, platform="linux", password=None, kinds=None, session=None):
        self.platform = platform
        self.password = password
        if session is None and platform == "linux":
            session = "wayland"
        self.kinds = kinds if kinds is not None else DEFAULT_KINDS[(platform, session)]
        assert not OBSOLETE_KINDS & set(self.kinds), "upstream never lists obsolete kinds"
        self.scenes = {"Scene": []}
        self.scene_uuids = {"Scene": str(uuid.uuid4())}
        self.inputs = {}
        self.frames = {}            # inputName -> (w, h) the capture currently produces
        self.program = "Scene"
        self.stream_active = False
        self.activate_after = 0.2   # seconds after StartStream; None = never becomes active
        self.ignore_scene_switch = False
        # Canvas: GetVideoSettings answers this dict as-is (None = refuse with video_refuse_code).
        self.video = {"fpsNumerator": 30, "fpsDenominator": 1, "baseWidth": 1920, "baseHeight": 1080,
                      "outputWidth": 1920, "outputHeight": 1080}
        self.video_refuse_code = 702
        self.refuse = {}                # requestType -> status code returned instead of acting
        self.transform_sticky = False   # SetSceneItemTransform answers success but changes nothing
        self.log = []
        self.connections = 0
        self._next_id = 1
        self._loop = None
        self._server = None
        self.port = 0

    # ---- server lifecycle on its own thread --------------------------------------------------------------
    def start(self):
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def boot():
                self._server = await websockets.serve(self._conn, "127.0.0.1", 0)
                self.port = self._server.sockets[0].getsockname()[1]
                ready.set()
            self._loop.run_until_complete(boot())
            self._loop.run_forever()
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not ready.wait(5):  # explicit: an assert here is stripped by python -O and the server never starts
            raise RuntimeError("FakeObs did not start")
        return self

    def stop(self):
        async def down():
            self._server.close()
            await self._server.wait_closed()
        asyncio.run_coroutine_threadsafe(down(), self._loop).result(5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    def requests(self, kind=None):
        return [r for r in self.log if kind is None or r[0] == kind]

    # ---- state helpers ---------------------------------------------------------------------------------
    def add_input(self, scene, name, kind, settings=None, frames=None):
        self.scenes.setdefault(scene, [])
        self.inputs[name] = {"kind": kind, "settings": dict(settings or {}), "uuid": str(uuid.uuid4())}
        self._add_item(scene, name)
        if frames:
            self.frames[name] = frames

    def _add_item(self, scene, name, enabled=True):
        item = {"sceneItemId": self._next_id, "sourceName": name, "sceneItemEnabled": bool(enabled),
                "_transform": _default_transform()}
        self._next_id += 1
        self.scenes[scene].append(item)
        return item

    def items(self, scene):
        return [i["sourceName"] for i in self.scenes.get(scene, [])]

    def item(self, scene, name):
        return next(i for i in self.scenes[scene] if i["sourceName"] == name)

    def _source_size(self, name):
        if name in self.frames:
            return self.frames[name]
        i = self.inputs.get(name) or {}
        if i.get("kind") == "browser_source":   # a Browser Source's size is its width/height settings
            return int(i["settings"].get("width", 800)), int(i["settings"].get("height", 600))
        return 0, 0

    def _find_item(self, d):
        scene = self.scenes.get(d.get("sceneName"))
        if scene is None:
            return None
        return next((i for i in scene if i["sceneItemId"] == d.get("sceneItemId")), None)

    def _set_transform(self, item, t):
        if not isinstance(t, dict):
            return 300
        new = dict(item["_transform"])
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        ranges = {"positionX": (-90001, 90001), "positionY": (-90001, 90001), "rotation": (-360, 360),
                  "boundsWidth": (1, 90001), "boundsHeight": (1, 90001), "alignment": (0, 2 ** 32 - 1),
                  "boundsAlignment": (0, 2 ** 32 - 1), "cropLeft": (0, 100000), "cropRight": (0, 100000),
                  "cropTop": (0, 100000), "cropBottom": (0, 100000), "scaleX": (-90001, 90001),
                  "scaleY": (-90001, 90001)}
        changed = False
        for key, value in t.items():
            if key not in TRANSFORM_FIELDS:
                continue                                    # upstream ignores keys it does not read
            if key == "boundsType":
                if not isinstance(value, str):
                    return 401
                if value not in BOUNDS_TYPES:
                    return 400
            elif key == "cropToBounds":
                if not isinstance(value, bool):
                    return 401
            else:
                if not num(value):
                    return 401
                lo, hi = ranges[key]
                if not lo <= value <= hi:
                    return 402
            new[key] = value
            changed = True
        if not changed:
            return 703
        if not self.transform_sticky:
            item["_transform"] = new
        return 100

    # ---- protocol ----------------------------------------------------------------------------------------
    async def _conn(self, ws):
        self.connections += 1
        hello = {"obsWebSocketVersion": "5.6.0", "rpcVersion": 1}
        if self.password is not None:
            hello["authentication"] = {"salt": "pepper", "challenge": "c" + str(self.connections)}
        await ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(await ws.recv())
        if self.password is not None:
            want = _digest(_digest(self.password + "pepper") + hello["authentication"]["challenge"])
            if ident["d"].get("authentication") != want:
                await ws.close(code=4009, reason="Authentication failed.")
                return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("op") != 6:
                    continue
                d = msg["d"]
                self.log.append((d["requestType"], d.get("requestData") or {}))
                code, data = self._handle(d["requestType"], d.get("requestData") or {})
                status = {"result": code == 100, "code": code}
                if code != 100:
                    status["comment"] = "server-side detail that must never be echoed"
                await ws.send(json.dumps({"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"],
                                                         "requestStatus": status, "responseData": data}}))
        except websockets.exceptions.ConnectionClosed:
            pass

    def _activate(self):
        self.stream_active = True

    def _handle(self, kind, d):  # noqa: C901 - one flat dispatch is clearest for a fake
        if kind in self.refuse:
            return self.refuse[kind], None
        if kind == "GetVersion":
            return 100, {"obsVersion": "32.0.0", "obsWebSocketVersion": "5.6.0", "rpcVersion": 1, "platform": self.platform,
                         "availableRequests": []}
        if kind == "GetVideoSettings":
            if self.video is None:
                return self.video_refuse_code, None
            return 100, dict(self.video)
        if kind == "GetCurrentProgramScene":
            return 100, {"currentProgramSceneName": self.program, "sceneName": self.program}
        if kind == "SetCurrentProgramScene":
            if d.get("sceneName") not in self.scenes:
                return 600, None
            if not self.ignore_scene_switch:
                self.program = d["sceneName"]
            return 100, None
        if kind == "GetSceneList":
            return 100, {"currentProgramSceneName": self.program, "scenes": [
                {"sceneName": s, "sceneUuid": self.scene_uuids.setdefault(s, str(uuid.uuid4()))} for s in self.scenes]}
        if kind == "CreateScene":
            if d["sceneName"] in self.scenes:
                return 601, None
            self.scenes[d["sceneName"]] = []
            self.scene_uuids[d["sceneName"]] = str(uuid.uuid4())
            return 100, {"sceneUuid": self.scene_uuids[d["sceneName"]]}
        if kind == "GetInputList":
            return 100, {"inputs": [{"inputName": n, "inputKind": i["kind"], "inputUuid": i.get("uuid")}
                                    for n, i in self.inputs.items()]}
        if kind == "GetInputKindList":
            return 100, {"inputKinds": list(self.kinds)}
        if kind == "GetInputSettings":
            if d.get("inputName") not in self.inputs:
                return 600, None
            i = self.inputs[d["inputName"]]
            return 100, {"inputKind": i["kind"], "inputSettings": dict(i["settings"])}
        if kind == "SetInputSettings":
            if d.get("inputName") not in self.inputs:
                return 600, None
            i = self.inputs[d["inputName"]]
            i["settings"] = {**i["settings"], **d["inputSettings"]} if d.get("overlay", True) else dict(d["inputSettings"])
            return 100, None
        if kind == "CreateInput":
            if d["inputName"] in self.inputs:
                return 601, None
            if d["sceneName"] not in self.scenes:
                return 600, None
            if d["inputKind"] in OBSOLETE_KINDS or d["inputKind"] not in self.kinds:
                return 605, None
            self.inputs[d["inputName"]] = {"kind": d["inputKind"], "settings": dict(d.get("inputSettings") or {}),
                                           "uuid": str(uuid.uuid4())}
            item = self._add_item(d["sceneName"], d["inputName"])
            return 100, {"inputUuid": self.inputs[d["inputName"]]["uuid"], "sceneItemId": item["sceneItemId"]}
        if kind == "GetSceneItemList":
            if d.get("sceneName") not in self.scenes:
                return 600, None
            return 100, {"sceneItems": [{**{k: v for k, v in dict(i, sceneItemIndex=n).items() if not k.startswith("_")},
                                        "sourceUuid": i.get('_sourceUuid', self.inputs.get(i['sourceName'], {}).get('uuid'))}
                                        for n, i in enumerate(self.scenes[d["sceneName"]])]}
        if kind == "CreateSceneItem":
            if d["sceneName"] not in self.scenes or d["sourceName"] not in self.inputs:
                return 600, None
            # upstream: sceneItemEnabled is optional and defaults to true (RequestHandler_SceneItems.cpp)
            enabled = d.get("sceneItemEnabled", True)
            return 100, {"sceneItemId": self._add_item(d["sceneName"], d["sourceName"], enabled)["sceneItemId"]}
        if kind == "SetSceneItemIndex":
            items = self.scenes[d["sceneName"]]
            item = next(i for i in items if i["sceneItemId"] == d["sceneItemId"])
            items.remove(item)
            items.insert(d["sceneItemIndex"], item)
            return 100, None
        if kind == "SetSceneItemEnabled":
            item = next(i for i in self.scenes[d["sceneName"]] if i["sceneItemId"] == d["sceneItemId"])
            item["sceneItemEnabled"] = d["sceneItemEnabled"]
            return 100, None
        if kind == "GetSceneItemTransform":
            item = self._find_item(d)
            if item is None:
                return 600, None
            w, h = self._source_size(item["sourceName"])
            t = dict(item["_transform"])
            return 100, {"sceneItemTransform": {**t, "sourceWidth": w, "sourceHeight": h,
                                                "width": t["scaleX"] * w, "height": t["scaleY"] * h}}
        if kind == "SetSceneItemTransform":
            item = self._find_item(d)
            if item is None:
                return 600, None
            if "sceneItemTransform" not in d:
                return 300, None
            return self._set_transform(item, d["sceneItemTransform"]), None
        if kind == "PressInputPropertiesButton":
            return (100, None) if d.get("inputName") in self.inputs else (600, None)
        if kind == "GetStreamStatus":
            return 100, {"outputActive": self.stream_active, "outputReconnecting": False,
                         "outputTimecode": "00:00:01.000" if self.stream_active else "00:00:00.000",
                         "outputDuration": 0, "outputBytes": 1000 if self.stream_active else 0,
                         "outputSkippedFrames": 0, "outputTotalFrames": 30 if self.stream_active else 0}
        if kind == "StartStream":
            if self.stream_active:
                return 500, None
            if self.activate_after is not None:
                asyncio.get_running_loop().call_later(self.activate_after, self._activate)
            return 100, None
        if kind == "StopStream":
            if not self.stream_active:
                return 501, None
            self.stream_active = False
            return 100, None
        return 204, None
