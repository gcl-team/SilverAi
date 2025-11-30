from silver_ai.rules import BatteryMin, MaxTemp, RequireConnectivity


def test_battery_min_pass():
    rule = BatteryMin(20)
    state = {"battery": 25}
    assert rule.check(state) is True


def test_battery_min_fail():
    rule = BatteryMin(20)
    state = {"battery": 10}
    assert rule.check(state) is False
    assert "10%" in rule.violation_message()


def test_battery_missing_key_defaults_to_zero():
    """Test Fail-Safe behavior"""
    rule = BatteryMin(10)
    state = {}  # Empty state
    assert rule.check(state) is False  # Should fail, assuming 0
    assert "0%" in rule.violation_message()


def test_max_temp_pass():
    rule = MaxTemp(80)
    state = {"temperature": 70}
    assert rule.check(state) is True


def test_max_temp_fail():
    rule = MaxTemp(80)
    state = {"temperature": 85}
    assert rule.check(state) is False


def test_max_temp_missing_sensor_fails_safe():
    """If sensor is broken (missing key), assume overheating."""
    rule = MaxTemp(80)
    state = {}
    assert rule.check(state) is False
    assert "999" in rule.violation_message()


def test_connectivity_pass():
    rule = RequireConnectivity("BLE")
    # Case insensitive check
    state = {"connection": "ble"}
    assert rule.check(state) is True


def test_connectivity_fail():
    rule = RequireConnectivity("WIFI")
    state = {"connection": "BLE"}
    assert rule.check(state) is False
    assert "Found: BLE" in rule.violation_message()


def test_connectivity_missing_key_defaults_offline():
    """If connection key is missing, assume OFFLINE (Fail-Safe)."""
    rule = RequireConnectivity("ETHERNET")
    state = {}
    assert rule.check(state) is False
    assert "Found: OFFLINE" in rule.violation_message()
