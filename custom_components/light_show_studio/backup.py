"""Export and import of your settings as one JSON document: the map (areas, runs, zones), your own patterns,
the light schedules and holiday settings, the show playlists and schedules, and the saved looks. The shows
themselves (.fseq and audio files) and the lights (config entries) are not part of it."""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from . import looks as looks_mod, mapping
from .const import LIGHT_SCHEDULER_KEY, MAP_KEY, SCHEDULER_KEY, USER_PATTERNS_KEY

_LOGGER = logging.getLogger(__name__)
FORMAT = "light_show_studio_backup"
VERSION = 1
# section -> the keys that must be lists in it (every section is an object; the map may be null = never edited)
SECTIONS: dict[str, tuple[str, ...]] = {
    "user_patterns": ("patterns",), "map": (), "looks": ("looks",),
    "show_schedules": ("playlists", "schedules"), "light_schedules": ("schedules",)}
# what is on the lights follows an import at once for these; schedules pick their timers up at the next restart
NEEDS_RESTART = ("show_schedules", "light_schedules")


def export(hass: HomeAssistant) -> dict[str, Any]:
    """Everything the integration keeps in its Stores, as one document for async_import."""
    data: dict[str, Any] = {}
    if (store := hass.data.get(USER_PATTERNS_KEY)) is not None:
        data["user_patterns"] = {"patterns": store.view()}
    if (mgr := hass.data.get(MAP_KEY)) is not None:
        model = getattr(mgr, "_model", None)
        data["map"] = mapping.to_dict(model) if model is not None else None
    if (looks := hass.data.get(looks_mod.LOOKS_KEY)) is not None:
        data["looks"] = looks.data()
    if (sched := hass.data.get(SCHEDULER_KEY)) is not None:
        data["show_schedules"] = {"playlists": sched.playlists, "schedules": sched.schedules,
                                  "active_playlist": sched.active_playlist}
    if (lights := hass.data.get(LIGHT_SCHEDULER_KEY)) is not None:
        data["light_schedules"] = {"schedules": lights.schedules, "holidays": lights.holiday_overrides}
    return {"format": FORMAT, "version": VERSION, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": copy.deepcopy(data)}


def validate(doc: Any) -> dict[str, Any]:
    """The sections of a backup document, checked for version and shape. ValueError says what is wrong."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError("This is not a Light Show Studio settings file")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("The settings file has no version")
    if version > VERSION:
        raise ValueError("The settings file was made by a newer version of Light Show Studio")
    data = doc.get("data")
    if not isinstance(data, dict) or not data:
        raise ValueError("The settings file is empty")
    for name, section in data.items():
        if name not in SECTIONS:
            raise ValueError(f"The settings file has an unknown part: {str(name)[:40]}")
        if section is None and name == "map":
            continue
        if not isinstance(section, dict):
            raise ValueError(f"The {name} part of the settings file is damaged")
        for key in SECTIONS[name]:
            if not isinstance(section.get(key), list):
                raise ValueError(f"The {name} part of the settings file has no {key}")
    if isinstance(data.get("map"), dict):
        try:
            mapping.from_dict(data["map"], None, strict=False)
        except mapping.MapError as err:
            raise ValueError(f"The map in the settings file is damaged: {err}") from err
    if "looks" in data:
        looks_mod.clean_document(data["looks"])
    if not isinstance(data.get("light_schedules", {}).get("holidays", {}), dict):
        raise ValueError("The light_schedules part of the settings file is damaged")
    return data


async def async_import(hass: HomeAssistant, doc: Any) -> dict[str, Any]:
    """Replace the stored settings with a backup's, part by part, and read them back in. Parts the file does not
    have (and a map that was never edited) are left as they are. ValueError: nothing was changed.

    Patterns, the map and looks are live at once (running zones stop). Schedules are replaced too, but their
    timers and open windows are only worked out again when Home Assistant restarts."""
    data = copy.deepcopy(validate(doc))
    imported: list[str] = []

    async def replace(holder: Any, section: dict[str, Any]) -> None:
        await holder._store.async_save(section)  # noqa: SLF001  each manager reads its own store back, checking every entry
        await holder.async_load()

    if "user_patterns" in data and (store := hass.data.get(USER_PATTERNS_KEY)) is not None:  # first: the rest name them
        await replace(store, data["user_patterns"])
        imported.append("user_patterns")
    if data.get("map") is not None and (mgr := hass.data.get(MAP_KEY)) is not None:
        mgr.async_stop_all()
        mgr._model = None  # noqa: SLF001
        await replace(mgr, data["map"])
        mgr._notify()  # noqa: SLF001
        imported.append("map")
    if "looks" in data and (looks := hass.data.get(looks_mod.LOOKS_KEY)) is not None:
        await looks.async_replace(data["looks"])
        imported.append("looks")
    if "show_schedules" in data and (sched := hass.data.get(SCHEDULER_KEY)) is not None:
        section = data["show_schedules"]
        await replace(sched, {"playlists": section["playlists"], "schedules": section["schedules"],
                              "active_playlist": section.get("active_playlist")})
        imported.append("show_schedules")
    if "light_schedules" in data and (lights := hass.data.get(LIGHT_SCHEDULER_KEY)) is not None:
        section = data["light_schedules"]
        await replace(lights, {**lights._data(), "schedules": section["schedules"],  # noqa: SLF001  open windows are kept
                               "holidays": section.get("holidays", {})})
        imported.append("light_schedules")
    for key in (SCHEDULER_KEY, LIGHT_SCHEDULER_KEY):
        if (holder := hass.data.get(key)) is not None and hasattr(holder, "_notify"):
            holder._notify()  # noqa: SLF001  the app's lists follow
    _LOGGER.info("Imported settings: %s", ", ".join(imported) or "nothing")
    return {"imported": imported, "restart_required": any(s in imported for s in NEEDS_RESTART)}
