from __future__ import annotations

from typing import Any, Dict, Optional


class MaxLoad:
    """Fail if current belt load exceeds allowed limit."""

    def __init__(self, max_load: float):
        self.max_load = float(max_load)

    def check(self, state: Dict[str, Any]) -> bool:
        current = float(state.get("belt_load", 0.0))
        return current <= self.max_load

    def violation_message(self, state: Dict[str, Any]) -> str:
        current = float(state.get("belt_load", 0.0))
        return f"Belt overload: {current:.2f}. Limit: {self.max_load:.2f}."

    def suggestion(self) -> str:
        return "Re-route to a lower-load belt or wait for load to drop."


class StateGate:
    """Generic state threshold gate for numeric telemetry keys."""

    def __init__(self, key: str, *, max_value: Optional[float] = None):
        self.key = key
        self.max_value = max_value

    def check(self, state: Dict[str, Any]) -> bool:
        if self.max_value is None:
            return True

        raw = state.get(self.key, 999.0)
        value = float(raw)
        return value <= float(self.max_value)

    def violation_message(self, state: Dict[str, Any]) -> str:
        raw = state.get(self.key, 999.0)
        value = float(raw)
        limit = 0.0 if self.max_value is None else float(self.max_value)
        return f"State gate blocked: {self.key}={value:.2f}. " f"Limit: {limit:.2f}."

    def suggestion(self) -> str:
        return "Wait for telemetry to return to safe limits before retrying."
