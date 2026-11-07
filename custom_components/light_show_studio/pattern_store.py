"""Your own patterns: stored (one Store, "light_show_studio.user_patterns"), kept in step with the pattern registry
that effects render from, and announced when one is edited, renamed or deleted so things that use it follow."""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import patterns, user_patterns
from .const import USER_PATTERNS_KEY, USER_PATTERNS_STORE_KEY

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY = 1.0

Listener = Callable[[str, "str | None"], "Awaitable[None] | None"]


class PatternStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, USER_PATTERNS_STORE_KEY)
        self.items: dict[str, user_patterns.UserPattern] = {}  # id -> pattern, in the order they were made
        self._listeners: list[Listener] = []

    # --- storage -------------------------------------------------------------------------
    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001  an unreadable store must not stop the integration
            _LOGGER.exception("Could not read your saved patterns; starting with none")
            data = None
        self.items = {}
        raw = data.get("patterns") if isinstance(data, dict) else None
        for row in raw[: user_patterns.MAX_PATTERNS] if isinstance(raw, list) else []:
            try:
                pid = row.get("id") if isinstance(row, dict) else None
                if not isinstance(pid, str) or not user_patterns._ID.match(pid) or pid in self.items:  # noqa: SLF001
                    raise ValueError("bad id")
                clean = user_patterns.validate({k: v for k, v in row.items() if k != "id"}, **self._rules(pid))
                self.items[pid] = user_patterns.UserPattern(clean, pid)
            except (ValueError, TypeError, AttributeError) as err:
                _LOGGER.warning("Dropping a saved pattern that is no longer valid: %s", err)
        patterns.set_user_patterns(list(self.items.values()))

    def _data(self) -> dict[str, Any]:
        return {"patterns": [p.to_dict() for p in self.items.values()]}

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    def _rules(self, pattern_id: str | None) -> dict[str, Any]:
        return {"builtin_names": patterns.PATTERN_LIST, "effects": patterns.PATTERN_EFFECTS,
                "taken": [p.name for pid, p in self.items.items() if pid != pattern_id]}

    # --- changes ---------------------------------------------------------------------------
    def add_listener(self, cb: Listener) -> None:
        """cb(old name, new name or None when deleted) after a pattern is edited, renamed or deleted."""
        self._listeners.append(cb)

    async def _announce(self, old: str, new: str | None) -> None:
        for cb in list(self._listeners):
            try:
                res = cb(old, new)
                if inspect.isawaitable(res):
                    await res
            except Exception:  # noqa: BLE001  one follower failing must not stop the rest
                _LOGGER.exception("A pattern follower failed")

    def get(self, ref: str) -> user_patterns.UserPattern | None:
        up = patterns.user_pattern(ref)
        return up if up is not None and up.id in self.items else None

    def view(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.items.values()]

    async def async_save_pattern(self, data: Any) -> dict[str, Any]:
        """Create a pattern (no id) or replace one (its id). ValueError says what is wrong; KeyError: no such id."""
        if not isinstance(data, dict):
            raise ValueError("A pattern must be an object")
        pid = data.get("id")
        old = None
        if pid is not None:
            if not isinstance(pid, str) or pid not in self.items:
                raise KeyError(f"No pattern {pid}")
            old = self.items[pid]
        elif len(self.items) >= user_patterns.MAX_PATTERNS:
            raise ValueError(f"At most {user_patterns.MAX_PATTERNS} patterns")
        clean = user_patterns.validate({k: v for k, v in data.items() if k != "id"}, **self._rules(pid))
        if pid is None:
            pid = user_patterns.new_id()
            while pid in self.items:
                pid = user_patterns.new_id()
        up = user_patterns.UserPattern(clean, pid)
        self.items[pid] = up
        patterns.register_user_pattern(up)
        self._save()
        await self._announce(old.name if old else up.name, up.name)
        return up.to_dict()

    async def async_delete_pattern(self, pattern_id: str) -> None:
        old = self.items.pop(pattern_id, None)
        if old is None:
            raise KeyError(f"No pattern {pattern_id}")
        patterns.unregister_user_pattern(pattern_id)
        self._save()
        await self._announce(old.name, None)


def get_store(hass: HomeAssistant) -> PatternStore | None:
    return hass.data.get(USER_PATTERNS_KEY)
