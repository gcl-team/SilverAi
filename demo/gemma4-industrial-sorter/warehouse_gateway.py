from __future__ import annotations

import math
from typing import Any, Dict


class WarehouseGateway:
    """Simulates an edge PLC gateway for an industrial sorter."""

    def __init__(self) -> None:
        self.state: Dict[str, Any] = {
            "motor_temp": 42.0,
            "belt_load": 30.0,
            "battery": 100,
            "safety_gate_status": "closed",
            "connection": "ETHERNET",
        }

    def snapshot(self) -> Dict[str, Any]:
        """Return a copy of the latest telemetry state."""
        return dict(self.state)

    def update_motor_temp(self, celsius: float) -> None:
        if isinstance(celsius, bool):
            raise ValueError("motor_temp telemetry must be numeric, not boolean.")
        self.state["motor_temp"] = float(celsius)

    def update_belt_load(self, load: float) -> None:
        if isinstance(load, bool):
            raise ValueError("belt_load telemetry must be numeric, not boolean.")
        self.state["belt_load"] = float(load)

    def update_battery(self, level: int) -> None:
        if isinstance(level, bool):
            raise ValueError("battery telemetry must be numeric, not boolean.")
        bounded = max(0, min(int(level), 100))
        self.state["battery"] = bounded

    def update_safety_gate_status(self, status: str) -> None:
        self.state["safety_gate_status"] = status.strip().lower()

    def update_connection(self, status: str) -> None:
        self.state["connection"] = status.strip().upper()

    def execute_sort_command(
        self,
        package_id: str,
        route: str,
        package_weight: float,
    ) -> Dict[str, Any]:
        """
        Simulate command execution on the hardware side.

        This method intentionally assumes pre-flight safety checks were already
        handled by SilverAi in the caller.
        """
        try:
            weight = float(package_weight)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "reason": f"Invalid package_weight: {package_weight}.",
                "suggestion": "Use a finite, non-negative package_weight value.",
            }

        if not math.isfinite(weight) or weight < 0:
            return {
                "status": "error",
                "reason": f"Invalid package_weight: {package_weight}.",
                "suggestion": "Use a finite, non-negative package_weight value.",
            }

        self.state["belt_load"] = float(self.state.get("belt_load", 0.0)) + weight

        return {
            "status": "executed",
            "package_id": package_id,
            "route": route,
            "package_weight": weight,
            "gateway_state": self.snapshot(),
        }
