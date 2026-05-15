import importlib.util
import math
import urllib.request as urllib_request
from datetime import datetime
from pathlib import Path
from types import ModuleType

from silver_ai.rules import BatteryMin


def _load_module(module_name: str, file_name: str) -> ModuleType:
    demo_dir = Path(__file__).resolve().parents[1]
    module_path = demo_dir / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway_module = _load_module("demo_gateway", "warehouse_gateway.py")
sorter_module = _load_module("demo_sorter", "sorter_agent.py")
rules_module = _load_module("demo_rules", "sorter_rules.py")

WarehouseGateway = gateway_module.WarehouseGateway
SorterAgent = sorter_module.SorterAgent
MaxLoad = rules_module.MaxLoad
StateGate = rules_module.StateGate


def test_safe_execution_path():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Express Belt",
        "package_weight": 5.0,
        "reason": "Fragile and high-priority package.",
    }

    result = agent.propose_and_execute("PKG-001", {"priority": "high"})

    assert result["status"] == "success"
    assert result["execution"]["status"] == "executed"


def test_overheat_blocked_even_after_replan():
    gateway = WarehouseGateway()
    gateway.update_motor_temp(85.0)
    agent = SorterAgent(gateway)

    plans = iter(
        [
            {
                "status": "ok",
                "route": "Belt-A",
                "package_weight": 4.0,
                "reason": "Initial plan.",
            },
            {
                "status": "ok",
                "route": "Belt-B",
                "package_weight": 4.0,
                "reason": "Replanned route.",
            },
        ]
    )
    agent._planner_request = lambda prompt: next(plans)

    result = agent.propose_and_execute("PKG-002", {"priority": "urgent"})

    assert result["status"] == "blocked"
    assert "motor_temp" in result["block"]["reason"]
    assert result["block"] == result["retry_block"]
    assert result["first_block"]["reason"] == result["retry_block"]["reason"]


def test_low_battery_blocked():
    gateway = WarehouseGateway()
    gateway.update_battery(10)
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Standard Belt",
        "package_weight": 3.0,
        "reason": "Default route.",
    }

    result = agent.propose_and_execute("PKG-003", {"priority": "normal"})

    assert result["status"] == "blocked"
    assert "Battery critical" in result["block"]["reason"]
    assert result["first_block"] == result["block"]


def test_invalid_battery_telemetry_blocks_safely():
    gateway = WarehouseGateway()
    gateway.state["battery"] = "low"
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Standard Belt",
        "package_weight": 3.0,
        "reason": "Default route.",
    }

    result = agent.propose_and_execute("PKG-003B", {"priority": "normal"})

    assert result["status"] == "blocked"
    assert "invalid telemetry" in result["block"]["reason"]


def test_battery_min_rule_rejects_non_finite_telemetry():
    rule = BatteryMin(min_level=20.0)

    assert not rule.check({"battery": float("nan")})
    assert "invalid telemetry" in rule.violation_message({"battery": float("nan")})


def test_belt_overload_blocked():
    gateway = WarehouseGateway()
    gateway.update_belt_load(160.0)
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Heavy Belt",
        "package_weight": 8.0,
        "reason": "Oversized package.",
    }

    result = agent.propose_and_execute("PKG-004", {"size": "large"})

    assert result["status"] == "blocked"
    assert "Belt overload" in result["block"]["reason"]
    assert result["first_block"] == result["block"]


def test_projected_belt_overload_blocked_before_execution():
    gateway = WarehouseGateway()
    gateway.update_belt_load(95.0)
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Heavy Belt",
        "package_weight": 10.0,
        "reason": "Large package.",
    }

    result = agent.propose_and_execute("PKG-004B", {"size": "large"})

    assert result["status"] == "blocked"
    assert "projected" in result["block"]["reason"]
    assert result["first_block"] == result["block"]
    assert gateway.snapshot()["belt_load"] == 95.0


def test_invalid_belt_load_telemetry_blocks_safely():
    gateway = WarehouseGateway()
    gateway.state["belt_load"] = "not-a-number"
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Standard Belt",
        "package_weight": 3.0,
        "reason": "Default route.",
    }

    result = agent.propose_and_execute("PKG-004C", {"priority": "normal"})

    assert result["status"] == "blocked"
    assert "invalid telemetry" in result["block"]["reason"]


def test_invalid_motor_temp_telemetry_blocks_safely():
    gateway = WarehouseGateway()
    gateway.state["motor_temp"] = "hot"
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Standard Belt",
        "package_weight": 3.0,
        "reason": "Default route.",
    }

    result = agent.propose_and_execute("PKG-004D", {"priority": "normal"})

    assert result["status"] == "blocked"
    assert "invalid telemetry" in result["block"]["reason"]


def test_non_finite_motor_temp_telemetry_blocks_safely():
    gateway = WarehouseGateway()
    gateway.state["motor_temp"] = float("-inf")
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Standard Belt",
        "package_weight": 3.0,
        "reason": "Default route.",
    }

    result = agent.propose_and_execute("PKG-004E", {"priority": "normal"})

    assert result["status"] == "blocked"
    assert "invalid telemetry" in result["block"]["reason"]


def test_non_finite_projected_belt_load_blocks_safely():
    rule = MaxLoad(max_load=100.0)

    assert not rule.check({"projected_belt_load": float("-inf")})
    assert "invalid telemetry" in rule.violation_message(
        {"projected_belt_load": float("-inf")}
    )


def test_boolean_projected_belt_load_blocks_safely():
    rule = MaxLoad(max_load=100.0)

    assert not rule.check({"projected_belt_load": True})
    assert "invalid telemetry" in rule.violation_message({"projected_belt_load": True})


def test_projected_load_requires_valid_current_load_telemetry():
    rule = MaxLoad(max_load=100.0)

    state = {"belt_load": "bad", "projected_belt_load": 50}
    assert not rule.check(state)
    assert "invalid telemetry" in rule.violation_message(state)


def test_projected_load_still_blocks_when_current_is_already_over_limit():
    rule = MaxLoad(max_load=100.0)

    state = {"belt_load": 160, "projected_belt_load": 50}
    assert not rule.check(state)
    assert "current=160.00" in rule.violation_message(state)


def test_state_gate_rejects_boolean_telemetry():
    rule = StateGate("motor_temp", max_value=80.0)

    assert not rule.check({"motor_temp": False})
    assert "invalid telemetry" in rule.violation_message({"motor_temp": False})


def test_gateway_rejects_negative_package_weight():
    gateway = WarehouseGateway()

    result = gateway.execute_sort_command("PKG-N1", "Any Belt", -5.0)

    assert result["status"] == "error"
    assert "Invalid package_weight" in result["reason"]
    assert gateway.snapshot()["belt_load"] == 30.0


def test_gateway_rejects_non_finite_package_weight():
    gateway = WarehouseGateway()

    result = gateway.execute_sort_command("PKG-N2", "Any Belt", math.inf)

    assert result["status"] == "error"
    assert "Invalid package_weight" in result["reason"]
    assert gateway.snapshot()["belt_load"] == 30.0


def test_gateway_rejects_non_numeric_package_weight():
    gateway = WarehouseGateway()

    result = gateway.execute_sort_command("PKG-N3", "Any Belt", "heavy")

    assert result["status"] == "error"
    assert "Invalid package_weight" in result["reason"]
    assert gateway.snapshot()["belt_load"] == 30.0


def test_weight_coercion_accepts_qualitative_label():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._coerce_package_weight("medium") == 5.0


def test_weight_coercion_converts_pounds_to_kg():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("5 lbs (Estimated)")
    assert round(value, 4) == 2.2680


def test_weight_coercion_supports_scientific_notation():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("1e3 kg")
    assert value == 1000.0


def test_weight_coercion_supports_bare_scientific_notation_as_kg():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("1e3")
    assert value == 1000.0


def test_weight_coercion_converts_grams_to_kg():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("500 g")
    assert value == 0.5


def test_weight_coercion_converts_ounces_to_kg():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("16 oz")
    assert round(value, 6) == 0.453592


def test_weight_coercion_rejects_unsupported_units():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight("5 stone")
    except ValueError as exc:
        assert "Unsupported package_weight unit" in str(exc)
    else:
        raise AssertionError("Expected unsupported unit to fail")


def test_weight_coercion_rejects_negative_scientific_notation():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight("-1e3 kg")
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("Expected negative scientific notation weight to fail")


def test_weight_coercion_rejects_boolean_values():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight(True)
    except ValueError as exc:
        assert "not boolean" in str(exc)
    else:
        raise AssertionError("Expected boolean package weight to fail")


def test_weight_coercion_rejects_ambiguous_range_values():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight("10-20 kg")
    except ValueError as exc:
        assert "Ambiguous package_weight" in str(exc)
    else:
        raise AssertionError("Expected ambiguous range package weight to fail")


def test_weight_coercion_rejects_multiple_numeric_tokens():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight("5 kg; box 30x20x10 cm")
    except ValueError as exc:
        assert "Ambiguous package_weight" in str(exc)
    else:
        raise AssertionError("Expected multi-number package weight to fail")


def test_weight_coercion_rejects_negative_values():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight("-5 kg")
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("Expected negative package weight to fail")


def test_weight_coercion_rejects_non_finite_values():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._coerce_package_weight(math.inf)
    except ValueError as exc:
        assert "finite" in str(exc)
    else:
        raise AssertionError("Expected non-finite package weight to fail")


def test_default_endpoint_uses_localhost(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_ALLOW_REMOTE", raising=False)

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "http://127.0.0.1:1234/v1/chat/completions"


def test_remote_endpoint_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.delenv("OPENAI_ALLOW_REMOTE", raising=False)

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._build_openai_endpoint()
    except ValueError as exc:
        assert "OPENAI_ALLOW_REMOTE=1" in str(exc)
    else:
        raise AssertionError("Expected remote endpoint validation to fail")


def test_remote_endpoint_allowed_with_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("OPENAI_ALLOW_REMOTE", "1")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "https://example.com/v1/chat/completions"


def test_remote_endpoint_with_v1_base_path_not_duplicated(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_ALLOW_REMOTE", "1")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "https://example.com/v1/chat/completions"


def test_remote_endpoint_with_trailing_slash_v1_base_path_not_duplicated(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("OPENAI_ALLOW_REMOTE", "1")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "https://example.com/v1/chat/completions"


def test_remote_http_endpoint_rejected_with_remote_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.com")
    monkeypatch.setenv("OPENAI_ALLOW_REMOTE", "1")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    try:
        agent._build_openai_endpoint()
    except ValueError as exc:
        assert "must use https" in str(exc)
    else:
        raise AssertionError("Expected remote HTTP endpoint validation to fail")


def test_planner_request_adds_bearer_header_when_api_key_present(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-demo-token")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)
    captured_headers = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"route":"Express Belt","package_weight":5,'
                                '"reason":"Safe route."}'
                            )
                        }
                    }
                ]
            }
            return agent._safe_json_dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout):
        del timeout
        captured_headers.update({k.lower(): v for k, v in req.header_items()})
        return _FakeResponse()

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)

    result = agent._planner_request("Plan a route")

    assert result["status"] == "ok"
    assert captured_headers["authorization"] == "Bearer sk-demo-token"
    assert captured_headers["content-type"] == "application/json"


def test_planner_request_omits_bearer_header_when_api_key_blank(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)
    captured_headers = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"route":"Standard Belt","package_weight":3,'
                                '"reason":"Default route."}'
                            )
                        }
                    }
                ]
            }
            return agent._safe_json_dumps(payload).encode("utf-8")

    def _fake_urlopen(req, timeout):
        del timeout
        captured_headers.update({k.lower(): v for k, v in req.header_items()})
        return _FakeResponse()

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)

    result = agent._planner_request("Plan a route")

    assert result["status"] == "ok"
    assert "authorization" not in captured_headers
    assert captured_headers["content-type"] == "application/json"


def test_extract_planner_json_ignores_extra_non_json_braces():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    content = (
        "Planner output: "
        '{"route":"Express Belt","package_weight":5,"reason":"Primary"}'
        " trailing-debug {not-json}"
    )

    parsed = agent._extract_planner_json(content)

    assert parsed["route"] == "Express Belt"
    assert parsed["package_weight"] == 5
    assert parsed["reason"] == "Primary"


def test_extract_planner_json_uses_first_valid_object_when_multiple_exist():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    content = (
        '{"route":"A","package_weight":1,"reason":"First"} '
        '{"route":"B","package_weight":2,"reason":"Second"}'
    )

    parsed = agent._extract_planner_json(content)

    assert parsed["route"] == "A"
    assert parsed["package_weight"] == 1
    assert parsed["reason"] == "First"


def test_extract_planner_json_handles_single_line_fenced_payload():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    content = '```{"route":"A","package_weight":1,"reason":"Single"}```'

    parsed = agent._extract_planner_json(content)

    assert parsed["route"] == "A"
    assert parsed["package_weight"] == 1
    assert parsed["reason"] == "Single"


def test_extract_planner_json_handles_single_line_fenced_payload_with_language():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    content = '```json {"route":"B","package_weight":2,"reason":"Tagged"}```'

    parsed = agent._extract_planner_json(content)

    assert parsed["route"] == "B"
    assert parsed["package_weight"] == 2
    assert parsed["reason"] == "Tagged"


def test_propose_and_execute_accepts_non_serializable_metadata():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    agent._planner_request = lambda prompt: {
        "status": "ok",
        "route": "Express Belt",
        "package_weight": 5.0,
        "reason": "Safe default.",
    }

    metadata = {
        "created_at": datetime(2026, 5, 11, 9, 30),
        "payload": {"fragile", "high-priority"},
        "raw_blob": b"demo-bytes",
    }

    result = agent.propose_and_execute("PKG-999", metadata)

    assert result["status"] == "success"
    assert result["execution"]["status"] == "executed"
