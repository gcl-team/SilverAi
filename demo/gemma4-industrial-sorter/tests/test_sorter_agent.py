import importlib.util
from pathlib import Path
from types import ModuleType


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

WarehouseGateway = gateway_module.WarehouseGateway
SorterAgent = sorter_module.SorterAgent


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


def test_weight_coercion_accepts_qualitative_label():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._coerce_package_weight("medium") == 5.0


def test_weight_coercion_converts_pounds_to_kg():
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    value = agent._coerce_package_weight("5 lbs (Estimated)")
    assert round(value, 4) == 2.2680


def test_localhost_endpoint_is_allowed(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1234")
    monkeypatch.delenv("OPENAI_ALLOW_REMOTE", raising=False)

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "http://127.0.0.1:1234/v1/chat/completions"


def test_remote_endpoint_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.com:1234")
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
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.com:1234")
    monkeypatch.setenv("OPENAI_ALLOW_REMOTE", "1")

    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    assert agent._build_openai_endpoint() == "http://example.com:1234/v1/chat/completions"
