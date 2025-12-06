from typing import Any, Dict


class BatteryMin:
    """
    Blocks execution if battery level is below the minimum threshold.

    Expected State Key: 'battery' (int | float)
    """

    def __init__(self, min_level: int):
        self.min_level = min_level

    def check(self, state: Dict[str, Any]) -> bool:
        # Fail Safe: If 'battery' key is missing, assume 0%
        current_level = state.get("battery", 0)
        return current_level >= self.min_level

    def violation_message(self, state: Dict[str, Any]) -> str:
        current_level = state.get("battery", 0)
        return f"Battery critical: {current_level}%. Required: {self.min_level}%."

    def suggestion(self) -> str:
        return "Connect device to charger before proceeding."


class MaxTemp:
    """
    Blocks execution if device temperature is too high.

    Expected State Key: 'temperature' (int | float)
    """

    def __init__(self, max_celsius: int):
        self.max_celsius = max_celsius

    def check(self, state: Dict[str, Any]) -> bool:
        # Fail Safe: If 'temperature' is missing, assume 999 (Too hot)
        # Note: Depending on context, you might want to assume 0.
        # But for safety, missing thermal sensor data usually means "Stop".
        current_temp = state.get("temperature", 999)
        return current_temp <= self.max_celsius

    def violation_message(self, state: Dict[str, Any]) -> str:
        current_temp = state.get("temperature", 999)
        return (
            f"Overheating detected: {current_temp}°C. " f"Limit: {self.max_celsius}°C."
        )

    def suggestion(self) -> str:
        return "Allow device to cool down or activate cooling fans."


class RequireConnectivity:
    """
    Blocks execution if the specific network protocol is not active.

    Expected State Key: 'connection' (str) -> e.g., "BLE", "WIFI", "ETHERNET"
    Note: If key is missing, defaults to 'OFFLINE' (Fail-Safe).
    """

    def __init__(self, protocol: str):
        self.required_protocol = protocol.upper()

    def check(self, state: Dict[str, Any]) -> bool:
        raw_status = state.get("connection", "OFFLINE")
        current_status = str(raw_status).upper()
        return current_status == self.required_protocol

    def violation_message(self, state: Dict[str, Any]) -> str:
        raw_status = state.get("connection", "OFFLINE")
        current_status = str(raw_status).upper()
        return (
            f"Connection mismatch. Found: {current_status}. "
            f"Required: {self.required_protocol}."
        )

    def suggestion(self) -> str:
        return f"Establish {self.required_protocol} connection."
