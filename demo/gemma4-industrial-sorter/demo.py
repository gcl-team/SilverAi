from __future__ import annotations

import os
from typing import Dict, Iterable, Iterator

from sorter_agent import SorterAgent
from warehouse_gateway import WarehouseGateway

try:
    from prettytable import PrettyTable
except ImportError:
    PrettyTable = None

USE_LIVE_OPENAI = os.getenv("DEMO_USE_LIVE_OPENAI", "1") == "1"
TRACE_VERBOSE = os.getenv("DEMO_TRACE_VERBOSE", "1") == "1"

if TRACE_VERBOSE:
    os.environ.setdefault("OPENAI_RAW_PREVIEW_CHARS", "0")
    os.environ.setdefault("OPENAI_PARSED_PREVIEW_CHARS", "0")
LIVE_PLANNER_MODE_LABEL = "live OpenAI-compatible endpoint"


def _shorten(text: object, max_len: int = 140) -> str:
    raw = str(text).replace("\n", " ").strip()
    if len(raw) <= max_len:
        return raw
    return f"{raw[:max_len]}..."


def _collect_summary_rows(result: Dict[str, object]) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = [("status", result.get("status", "unknown"))]

    plan = result.get("plan") or result.get("first_plan")
    if isinstance(plan, dict):
        rows.extend(
            [
                ("plan_source", plan.get("source", "scripted")),
                ("plan_route", plan.get("route", "n/a")),
                ("plan_weight", plan.get("package_weight", "n/a")),
                ("plan_reason", plan.get("reason", "n/a")),
            ]
        )

    block = result.get("block")
    first_block = result.get("first_block")
    retry_block = result.get("retry_block")

    block_reason = "n/a"
    if isinstance(block, dict):
        block_reason = str(block.get("reason", "n/a"))
        rows.append(("block_reason", block_reason))

    if isinstance(first_block, dict):
        first_block_reason = str(first_block.get("reason", "n/a"))
        if first_block_reason != block_reason:
            rows.append(("first_block_reason", first_block_reason))

    if isinstance(retry_block, dict):
        retry_block_reason = str(retry_block.get("reason", "n/a"))
        if retry_block_reason != block_reason:
            rows.append(("retry_block_reason", retry_block_reason))

    execution = result.get("execution")
    if isinstance(execution, dict):
        rows.extend(
            [
                ("execution_status", execution.get("status", "n/a")),
                ("execution_route", execution.get("route", "n/a")),
                ("execution_weight", execution.get("package_weight", "n/a")),
            ]
        )

    replan = result.get("replan")
    if isinstance(replan, dict):
        rows.extend(
            [
                ("replan_route", replan.get("route", "n/a")),
                ("replan_weight", replan.get("package_weight", "n/a")),
            ]
        )

    return rows


def _print_result_summary(title: str, result: Dict[str, object]) -> None:
    rows = _collect_summary_rows(result)

    if PrettyTable is None:
        print(f"{title} (text mode):")
        for key, value in rows:
            print(f"  - {key}: {value}")
        return

    table = PrettyTable()
    table.field_names = ["Field", "Value"]
    table.align = "l"
    for key, value in rows:
        table.add_row([key, value])

    print(title)
    print(table)


def _planner_from_sequence(plans: Iterable[Dict[str, object]]):
    iterator: Iterator[Dict[str, object]] = iter(plans)

    def _planner(prompt: str) -> Dict[str, object]:
        _ = prompt
        try:
            plan = next(iterator)
        except StopIteration:
            return {
                "status": "planner_error",
                "reason": "Planner sequence exhausted.",
                "suggestion": "Add more scripted plans in demo.",
            }
        return dict(plan)

    return _planner


def _print_planner_trace(agent: SorterAgent) -> None:
    if not agent.planner_trace_log:
        print("Planner trace: no OpenAI-compatible endpoint call trace recorded.")
        return

    print("Planner trace (OpenAI-compatible endpoint):")
    for idx, trace in enumerate(agent.planner_trace_log, start=1):
        print(
            f"  Request {idx}: "
            f"model={trace.get('model', 'n/a')} "
            f"http={trace.get('http_status', 'n/a')}"
        )
        print(f"     endpoint: {_shorten(trace.get('endpoint', 'n/a'), 90)}")

        if trace.get("coerced_package_weight") is not None:
            print(f"     coerced_weight: {trace['coerced_package_weight']}")

        if trace.get("parsed_content_preview"):
            print(
                "     parsed_preview: "
                f"{_shorten(trace['parsed_content_preview'], 180)}"
            )

        if trace.get("error"):
            print(f"     error: {_shorten(trace['error'], 180)}")

        if trace.get("parse_error"):
            print(f"     parse_error: {_shorten(trace['parse_error'], 180)}")

        if TRACE_VERBOSE and trace.get("raw_response_preview"):
            print("     raw_preview: " f"{trace['raw_response_preview']}")


def run_safe_scenario() -> None:
    gateway = WarehouseGateway()
    agent = SorterAgent(gateway)

    if not USE_LIVE_OPENAI:
        agent._planner_request = _planner_from_sequence(
            [
                {
                    "status": "ok",
                    "route": "Express Belt",
                    "package_weight": 5.0,
                    "reason": "Fragile and high-priority package.",
                }
            ]
        )

    result = agent.propose_and_execute("PKG-100", {"priority": "high"})

    print("--- Scenario 1: Healthy state ---")
    planner_mode = LIVE_PLANNER_MODE_LABEL if USE_LIVE_OPENAI else "scripted"
    print(f"Planner mode: {planner_mode}")
    _print_result_summary("Scenario 1 Result", result)
    if USE_LIVE_OPENAI:
        _print_planner_trace(agent)


def run_overheat_replan_scenario() -> None:
    gateway = WarehouseGateway()
    gateway.update_motor_temp(85.0)
    agent = SorterAgent(gateway)

    if not USE_LIVE_OPENAI:
        agent._planner_request = _planner_from_sequence(
            [
                {
                    "status": "ok",
                    "route": "Express Belt",
                    "package_weight": 4.0,
                    "reason": "Urgent package route.",
                },
                {
                    "status": "ok",
                    "route": "Buffer Belt",
                    "package_weight": 4.0,
                    "reason": "Alternative route after safety feedback.",
                },
            ]
        )

    result = agent.propose_and_execute("PKG-200", {"priority": "urgent"})

    print("--- Scenario 2: Dangerous overheat + re-plan attempt ---")
    planner_mode = LIVE_PLANNER_MODE_LABEL if USE_LIVE_OPENAI else "scripted"
    print(f"Planner mode: {planner_mode}")
    _print_result_summary("Scenario 2 Result", result)
    if USE_LIVE_OPENAI:
        _print_planner_trace(agent)


def run_low_battery_scenario() -> None:
    gateway = WarehouseGateway()
    gateway.update_battery(10)
    agent = SorterAgent(gateway)

    if not USE_LIVE_OPENAI:
        agent._planner_request = _planner_from_sequence(
            [
                {
                    "status": "ok",
                    "route": "Standard Belt",
                    "package_weight": 3.0,
                    "reason": "Normal package route.",
                }
            ]
        )

    result = agent.propose_and_execute("PKG-300", {"priority": "normal"})

    print("--- Scenario 3: BatteryMin violation ---")
    print("Expected: Blocked because battery is below 20% threshold.")
    planner_mode = LIVE_PLANNER_MODE_LABEL if USE_LIVE_OPENAI else "scripted"
    print(f"Planner mode: {planner_mode}")
    _print_result_summary("Scenario 3 Result", result)
    if USE_LIVE_OPENAI:
        _print_planner_trace(agent)


def run_max_load_scenario() -> None:
    gateway = WarehouseGateway()
    gateway.update_belt_load(160.0)
    agent = SorterAgent(gateway)

    if not USE_LIVE_OPENAI:
        agent._planner_request = _planner_from_sequence(
            [
                {
                    "status": "ok",
                    "route": "Heavy Belt",
                    "package_weight": 8.0,
                    "reason": "Large package route.",
                }
            ]
        )

    result = agent.propose_and_execute("PKG-400", {"size": "large"})

    print("--- Scenario 4: MaxLoad violation ---")
    print("Expected: Blocked because belt_load exceeds MaxLoad threshold.")
    planner_mode = LIVE_PLANNER_MODE_LABEL if USE_LIVE_OPENAI else "scripted"
    print(f"Planner mode: {planner_mode}")
    _print_result_summary("Scenario 4 Result", result)
    if USE_LIVE_OPENAI:
        _print_planner_trace(agent)


def run_demo() -> None:
    print("=== Gemma Industrial Sorter Demo ===")
    if PrettyTable is None:
        print("PrettyTable not installed; using text output.")
        print("Install for table output: poetry run pip install prettytable")
    if USE_LIVE_OPENAI:
        print("Live OpenAI-compatible endpoint mode enabled via DEMO_USE_LIVE_OPENAI=1")
    else:
        print(
            "Scripted planner mode (CI-safe). "
            "Set DEMO_USE_LIVE_OPENAI=1 for live endpoint mode."
        )
    run_safe_scenario()
    print()
    run_overheat_replan_scenario()
    print()
    run_low_battery_scenario()
    print()
    run_max_load_scenario()


if __name__ == "__main__":
    run_demo()
