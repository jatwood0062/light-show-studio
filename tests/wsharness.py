"""A small harness to import the websocket modules, the pattern store, music and previews without Home Assistant:
the few Home Assistant modules they import are stubbed, and a real controller drives fake hardware.

Everything here is loaded under one package name (light_show_studio_ws), so the pattern registry is one."""
from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("voluptuous")
import voluptuous as vol  # noqa: E402

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"
NAME = "light_show_studio_ws"
STORAGE: dict[str, dict] = {}


def _stub(name: str, **attrs):
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class FakeStore:
    def __init__(self, hass, version, key):
        self.key = key
        self.saves = 0

    async def async_load(self):
        return copy.deepcopy(STORAGE.get(self.key))

    def async_delay_save(self, fn, delay=0):
        self.saves += 1
        STORAGE[self.key] = copy.deepcopy(fn())

    async def async_save(self, data):
        STORAGE[self.key] = copy.deepcopy(data)


def _install():
    _stub("homeassistant")
    _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f, CALLBACK_TYPE=object)
    ws = _stub("homeassistant.components.websocket_api")
    comp = _stub("homeassistant.components")
    comp.websocket_api = ws

    def websocket_command(schema):
        def deco(f):
            f._ws_schema = schema
            return f
        return deco

    def require_admin(f):
        f._admin = True
        return f

    ws.websocket_command, ws.require_admin = websocket_command, require_admin
    ws.async_response = lambda f: f
    ws.async_register_command = lambda hass, f: hass.commands.append(f)
    ws.ActiveConnection = object
    ws.event_message = lambda i, data: {"id": i, "event": data}
    _stub("homeassistant.auth")
    _stub("homeassistant.auth.permissions")
    _stub("homeassistant.auth.permissions.const", POLICY_CONTROL="control", POLICY_READ="read")
    helpers = _stub("homeassistant.helpers")
    storage = _stub("homeassistant.helpers.storage", Store=FakeStore)
    helpers.storage = storage
    pkg = sys.modules.setdefault(NAME, types.ModuleType(NAME))
    pkg.__path__ = [str(PKG)]


_install()


def mod(name: str):
    return importlib.import_module(f"{NAME}.{name}")


controller, patterns, mapping, user_patterns = mod("controller"), mod("patterns"), mod("mapping"), mod("user_patterns")
fade, mapped_patterns, zoneplayer = mod("fade"), mod("mapped_patterns"), mod("zoneplayer")


class FakeLoop:
    """call_later that tests can fire by hand, plus the real loop for tasks."""

    def __init__(self):
        self.real = asyncio.get_running_loop()

    def call_later(self, delay, cb, *args):
        return self.real.call_later(delay, cb, *args)


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.tasks = []
        self.data = {}
        self.commands = []

    def async_create_background_task(self, coro, name=None):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t


class FakeLight(controller.C9Controller):
    def __init__(self, hass, bulbs=10, on=True, entry_id=None):
        super().__init__(hass, "192.168.1.10", bulbs, True)
        self.is_on = on
        self.entry_id = entry_id
        self.sent: list[list] = []
        self.powered: list[bool] = []

    def _make_link(self, host):
        return None

    def _hw_power(self, on):
        self.powered.append(on)

    def _hw_frame(self, frame):
        self.sent.append(frame)

    def _hw_close(self):
        pass


class FakeMgr:
    """What the music and preview managers use of the MapManager."""

    def __init__(self, hass, lights: dict, model=None):
        self.hass = hass
        self.ctls = lights
        self.model = model or mapping.MapModel()

    def controller(self, entry_id):
        return self.ctls.get(entry_id)

    def lights(self):
        return [{"entry_id": e} for e in self.ctls]


class FakePermissions:
    def __init__(self, allowed):
        self.allowed = allowed

    def check_entity(self, entity_id, key):
        return key == "control" and entity_id in self.allowed


class FakeUser:
    _n = iter(range(1, 10**9))

    def __init__(self, admin=True, allowed=()):
        self.id = f"user{next(self._n)}"
        self.is_admin = admin
        self.permissions = FakePermissions(set(allowed))


class FakeConn:
    def __init__(self, user=None):
        self.user = user or FakeUser()
        self.results: list[tuple] = []
        self.errors: list[tuple] = []
        self.subscriptions: dict = {}

    def send_result(self, i, result=None):
        self.results.append((i, result))

    def send_error(self, i, code, message):
        self.errors.append((i, code, message))

    def send_message(self, msg):
        pass

    def close(self):
        for unsub in list(self.subscriptions.values()):
            unsub()
        self.subscriptions.clear()


_ids = iter(range(1, 10**9))


async def call(handler, hass, conn, msg):
    """Validate msg with the command's own schema (as Home Assistant would), then run the handler."""
    msg = {"id": next(_ids), **msg}
    data = vol.Schema({**handler._ws_schema, vol.Required("id"): int})(msg)
    res = handler(hass, conn, data)
    if inspect.isawaitable(res):
        await res
    return data["id"]


def aio(fn):
    def wrapper():
        async def go():
            STORAGE.clear()
            patterns.set_user_patterns([])
            hass = FakeHass()
            try:
                await fn(hass)
            finally:
                for t in hass.tasks:
                    t.cancel()
                patterns.set_user_patterns([])
        asyncio.run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


def palette(name="Mine", colours=((255, 0, 0), (0, 255, 0)), bpc=1, **kw):
    return {"name": name, "kind": "palette", "colours": [list(c) for c in colours], "bulbs_per_colour": bpc, **kw}


def pixels(name="Painted", lights=None, areas=None, **kw):
    return {"name": name, "kind": "pixels", "lights": lights or {}, "areas": areas or {}, **kw}
