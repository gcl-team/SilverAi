from __future__ import annotations

import math
from typing import Any, Dict, Optional


def _coerce_telemetry_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        coerced = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return coerced if math.isfinite(coerced) else None


class MaxLoad:
    """Fail if current or projected belt load exceeds allowed limit."""

    def __init__(self, max_load: float):
        self.max_load = float(max_load)

    def check(self, state: Dict[str, Any]) -> bool:
        projected = state.get("projected_belt_load")
        if projected is not None:
            current = _coerce_telemetry_float(state.get("belt_load", 0.0))
            projected_value = _coerce_telemetry_float(projected)
            if current is None or projected_value is None:
                return False
            return current <= self.max_load and projected_value <= self.max_load

        current = _coerce_telemetry_float(state.get("belt_load", 0.0))
        if current is None:
            return False
        return current <= self.max_load

    def violation_message(self, state: Dict[str, Any]) -> str:
        projected = state.get("projected_belt_load")
        if projected is not None:
            current = _coerce_telemetry_float(state.get("belt_load", 0.0))
            projected_value = _coerce_telemetry_float(projected)
            if current is None or projected_value is None:
                return (
                    "Belt overload: invalid telemetry for belt_load or "
                    "projected_belt_load."
                )
            return (
                f"Belt overload: current={current:.2f}, "
                f"projected={projected_value:.2f}. Limit: {self.max_load:.2f}."
            )

        current = _coerce_telemetry_float(state.get("belt_load", 0.0))
        if current is None:
            return "Belt overload: invalid telemetry for belt_load."
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
        value = _coerce_telemetry_float(raw)
        if value is None:
            return False
        return value <= float(self.max_value)

    def violation_message(self, state: Dict[str, Any]) -> str:
        raw = state.get(self.key, 999.0)
        value = _coerce_telemetry_float(raw)
        limit = 0.0 if self.max_value is None else float(self.max_value)
        if value is None:
            return (
                f"State gate blocked: invalid telemetry for {self.key}. "
                f"Limit: {limit:.2f}."
            )
        return f"State gate blocked: {self.key}={value:.2f}. " f"Limit: {limit:.2f}."

    def suggestion(self) -> str:
        return "Wait for telemetry to return to safe limits before retrying."
