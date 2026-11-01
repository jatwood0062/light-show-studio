"""Tests for choosing the leader speaker and the speakers that can group with it; pure, no Home Assistant."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"
spec = importlib.util.spec_from_file_location("outputs_under_test", PKG / "outputs.py")
o = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = o  # dataclasses look the module up here
spec.loader.exec_module(o)

A = ("media_player.a", "sonos", True, True)
B = ("media_player.b", "sonos", True, True)
MA = ("media_player.ma", "music_assistant", True, True)
CAST = ("media_player.cast", "cast", False, True)


def test_no_speakers_is_lights_only():
    assert o.plan_outputs([]) == o.Outputs(None, (), ())


def test_single_speaker_leads_alone():
    assert o.plan_outputs([CAST]) == o.Outputs("media_player.cast", (), ())


def test_same_platform_groups_in_order():
    c = ("media_player.c", "sonos", True, True)
    assert o.plan_outputs([A, c, B]) == o.Outputs("media_player.a", ("media_player.c", "media_player.b"), ())


def test_other_platform_or_no_grouping_is_ungrouped():
    p = o.plan_outputs([A, B, MA, CAST])
    assert p.leader == "media_player.a"
    assert p.members == ("media_player.b",)
    assert p.ungrouped == ("media_player.ma", "media_player.cast")


def test_leader_that_cannot_group_groups_nobody():
    p = o.plan_outputs([CAST, A, B])
    assert (p.leader, p.members, p.ungrouped) == ("media_player.cast", (), ("media_player.a", "media_player.b"))


def test_unavailable_leader_falls_back_to_next():
    down = ("media_player.down", "sonos", True, False)
    p = o.plan_outputs([down, A, B])
    assert (p.leader, p.members, p.ungrouped) == ("media_player.a", ("media_player.b",), ())


def test_unavailable_member_is_skipped_silently():
    down = ("media_player.down", "sonos", True, False)
    assert o.plan_outputs([A, down, B]) == o.Outputs("media_player.a", ("media_player.b",), ())


def test_all_unavailable_is_lights_only():
    assert o.plan_outputs([("media_player.x", "sonos", True, False)]).leader is None


def test_unknown_platform_never_groups():
    unknown = ("media_player.u", None, True, True)
    assert o.plan_outputs([unknown, ("media_player.v", None, True, True)]).members == ()


def test_duplicates_ignored():
    assert o.plan_outputs([A, A, B]) == o.Outputs("media_player.a", ("media_player.b",), ())


def test_normalize_speakers():
    assert o.normalize_speakers(["media_player.a", " media_player.a ", "", 3, "media_player.b"]) == [
        "media_player.a", "media_player.b"]
    assert o.normalize_speakers("media_player.a") == ["media_player.a"]
    assert o.normalize_speakers(None) == []


