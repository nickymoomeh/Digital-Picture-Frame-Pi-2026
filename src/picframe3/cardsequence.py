"""Optional display-driven card ordering; no timers or generator dependencies."""
from __future__ import annotations

import threading
from typing import Any

STATE_KEY = "information_card_sequence_v1"


class CardSequence:
    def __init__(self, sequence: list[str], library: Any):
        if (not isinstance(sequence, list) or not 2 <= len(sequence) <= 64
                or any(not isinstance(t, str) or not t.strip() or len(t) > 128 for t in sequence)
                or len(set(sequence)) != len(sequence)):
            raise ValueError("card_sequence requires 2–64 distinct, nonempty type names")
        if library is None:
            raise ValueError("card sequence requires the frame library")
        self.sequence = list(sequence)
        self.library = library
        self.error = None
        self._lock = threading.RLock()
        try:
            state = library.get_state(STATE_KEY)
        except Exception as exc:
            raise ValueError(f"cannot read card sequence state: {exc}") from exc
        if state is None:
            state = {"sequence": self.sequence, "next_type": sequence[0], "last_shown": {}}
        if (not isinstance(state, dict) or state.get("sequence") != self.sequence
                or state.get("next_type") not in self.sequence
                or not isinstance(state.get("last_shown"), dict)
                or any(t not in self.sequence or not isinstance(p, str)
                       for t, p in state["last_shown"].items())):
            raise ValueError("invalid/changed persisted card sequence; review its library state before resetting")
        self.next_type = state["next_type"]
        self.last_shown = dict(state["last_shown"])

    def shown(self, name: str, path: str) -> None:
        with self._lock:
            self._record_shown(name, path)

    def _record_shown(self, name: str, path: str) -> None:
        if self.error or name != self.next_type:
            raise ValueError(self.error or "card displayed out of sequence")
        following = self.sequence[(self.sequence.index(name) + 1) % len(self.sequence)]
        shown = {**self.last_shown, name: path}
        try:
            self.library.set_state(STATE_KEY, {
                "sequence": self.sequence, "next_type": following, "last_shown": shown,
            })
        except Exception as exc:
            self.error = f"cannot persist card sequence; further cards stopped: {exc}"
            raise ValueError(self.error) from exc
        self.next_type, self.last_shown = following, shown

    def state(self) -> dict:
        with self._lock:
            return {"sequence": list(self.sequence), "next_type": self.next_type if not self.error else None,
                    "last_shown": dict(self.last_shown), "sequence_error": self.error}
