"""Govee streaming stays up: realtime mode is re-armed, a still picture is resent for a while, and a sequencer
frame that comes too soon is delayed, never dropped."""
from __future__ import annotations

import asyncio
import base64
import json

from test_zones import FakeHass, controller


def aio(fn):
    def run(*a, **kw):
        asyncio.run(fn(*a, **kw))
    run.__name__ = fn.__name__
    import inspect
    run.__signature__ = inspect.signature(fn)
    return run


class Lan:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def send(self, payload):
        self.sent.append(payload)
        return self.ok

    def close(self):
        pass

    def kinds(self):
        out = []
        for p in self.sent:
            msg = json.loads(p)["msg"]
            if msg["cmd"] == "razer":
                out.append("arm" if base64.b64decode(msg["data"]["pt"])[3] == 0xB1 else "frame")
            else:
                out.append(msg["cmd"])
        return out


class Light(controller.C9Controller):
    def __init__(self, hass, lan):
        self._lan = lan
        super().__init__(hass, "192.168.1.10", 3, True)
        self.is_on = True

    def _make_link(self, host):
        return self._lan

    def _hw_power(self, on):
        pass


RED = [(255, 0, 0)] * 3


@aio
async def test_realtime_mode_is_armed_again_only_after_a_pause(monkeypatch):
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    now = [100.0]
    monkeypatch.setattr(controller.time, "monotonic", lambda: now[0])
    ctl._hw_frame(RED)
    assert lan.kinds() == ["brightness", "arm", "frame"]
    for _ in range(10):  # frames flowing: never re-armed (the light flickers on every arm)
        now[0] += 0.5
        ctl._hw_frame(RED)
    assert lan.kinds()[3:] == ["frame"] * 10
    now[0] += controller.REARM_AFTER
    ctl._hw_frame(RED)
    assert lan.kinds()[13:] == ["arm", "frame"]  # after a pause: re-armed, no brightness reset
    ctl.shutdown()


@aio
async def test_a_failed_arm_is_tried_again_on_the_next_frame():
    lan = Lan(ok=False)
    ctl = Light(FakeHass(), lan)
    ctl._hw_frame(RED)
    ctl._hw_frame(RED)
    assert lan.kinds().count("arm") == 2
    ctl.shutdown()


@aio
async def test_a_still_picture_is_resent_for_a_while(monkeypatch):
    monkeypatch.setattr(controller, "KEEPALIVE", 0.02)
    monkeypatch.setattr(controller, "KEEP_FOR", 0.1)
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl._hw_frame(RED)
    await asyncio.sleep(0.07)
    assert lan.kinds().count("frame") >= 3  # resent
    await asyncio.sleep(0.15)
    n = lan.kinds().count("frame")
    await asyncio.sleep(0.06)
    assert lan.kinds().count("frame") == n  # stopped after KEEP_FOR
    ctl.shutdown()


@aio
async def test_no_keepalive_once_the_light_is_off(monkeypatch):
    monkeypatch.setattr(controller, "KEEPALIVE", 0.02)
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl._hw_frame(RED)
    ctl.is_on = False
    await asyncio.sleep(0.06)
    assert lan.kinds().count("frame") == 1
    ctl.shutdown()


@aio
async def test_a_sequencer_frame_that_comes_too_soon_is_delayed_not_dropped():
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl.enable_show()
    ctl.show_external(bytes([10, 0, 0] * 3))
    ctl.show_external(bytes([20, 0, 0] * 3))  # 0 ms later
    ctl.show_external(bytes([30, 0, 0] * 3))  # newest wins
    assert ctl.frame[0] == (10, 0, 0)
    await asyncio.sleep(0.04)
    assert ctl.frame[0] == (30, 0, 0)
    assert lan.kinds().count("frame") == 2
    ctl.shutdown()


@aio
async def test_keepalive_never_puts_back_the_picture_from_before_an_off(monkeypatch):
    monkeypatch.setattr(controller, "KEEPALIVE", 0.02)
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl._hw_frame(RED)
    ctl._hard_off()
    ctl.is_on = True  # back on (a show is about to start) before the keep-alive fires
    await asyncio.sleep(0.06)
    assert lan.kinds().count("frame") == 1
    ctl.shutdown()


@aio
async def test_twenty_ms_show_frames_all_get_through():
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl.enable_show()
    for i in range(1, 11):
        ctl.show_external(bytes([i, 0, 0] * 3))
        await asyncio.sleep(0.02)
    assert lan.kinds().count("frame") == 10
    ctl.shutdown()


@aio
async def test_a_light_that_comes_back_gets_realtime_mode_and_its_picture_again():
    lan = Lan()
    ctl = Light(FakeHass(), lan)
    ctl._hw_frame(RED)
    ctl.mark_seen()  # first check: nothing to recover
    assert lan.kinds() == ["brightness", "arm", "frame"]
    ctl.mark_missed(1)
    assert ctl.online is False
    ctl.mark_seen()
    assert lan.kinds()[3:] == ["brightness", "arm", "frame"]
    ctl.shutdown()


def test_motion_blur_is_the_average_of_two_moments():
    assert controller._blend([(200, 0, 10), (0, 0, 0)], [(100, 50, 11), (255, 255, 255)]) == [(150, 25, 10), (127, 127, 127)]
    assert "Fireworks" in controller.STATEFUL
