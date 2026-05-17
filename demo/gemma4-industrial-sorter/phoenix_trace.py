"""Phoenix tracing helpers for the Gemma industrial sorter demo."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from opentelemetry import trace as otel_trace


class PhoenixTracer:
    """Small wrapper to emit planner/guard/evaluator spans."""

    def __init__(self) -> None:
        self.enabled = bool(os.getenv("PHOENIX_COLLECTOR_ENDPOINT"))
        self._tracer = None

        if not self.enabled:
            return

        try:
            from phoenix.otel import register

            collector = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip()
            if collector and not collector.rstrip("/").endswith("/v1/traces"):
                collector = collector.rstrip("/") + "/v1/traces"

            # Registers exporter + global tracer provider using env vars.
            register(
                endpoint=collector,
                project_name=os.getenv("PHOENIX_PROJECT_NAME", "gemma4-industrial-sorter"),
                batch=False,
                auto_instrument=False,
                verbose=False,
                protocol="http/protobuf",
            )
            self._tracer = otel_trace.get_tracer(__name__)
        except Exception as exc:  # pragma: no cover - fail-open by design
            print(f"Phoenix tracing disabled: {exc}")
            self.enabled = False

    def emit_planner_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        planner_response: Dict[str, Any],
        gateway_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span(
                f"planner_attempt_{attempt_index}"
            ) as span:
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute(
                    "planner_status", str(planner_response.get("status", "unknown"))
                )
                span.set_attribute(
                    "planner_reason", str(planner_response.get("reason", ""))
                )
                span.set_attribute(
                    "planner_route", str(planner_response.get("route", ""))
                )
                weight = planner_response.get("package_weight")
                if isinstance(weight, (int, float)):
                    span.set_attribute("planner_weight", float(weight))

                if gateway_snapshot:
                    for key in ("motor_temp", "belt_load", "battery"):
                        value = gateway_snapshot.get(key)
                        if isinstance(value, (int, float)):
                            span.set_attribute(f"gateway_{key}", float(value))
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix planner event failed: {exc}")

    def emit_guard_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        failed_rule_name: str,
        violation_message: str,
        gateway_snapshot: Dict[str, Any],
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span("guard_check") as span:
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute("failed_rule", failed_rule_name)
                span.set_attribute("block_reason", violation_message)
                for key in ("motor_temp", "belt_load", "battery"):
                    value = gateway_snapshot.get(key)
                    if isinstance(value, (int, float)):
                        span.set_attribute(f"gateway_{key}", float(value))
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix guard event failed: {exc}")

    def emit_verdict_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        is_contradiction: bool,
        failed_rule: Optional[str] = None,
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span("contradiction_verdict") as span:
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute("is_contradiction", is_contradiction)
                if failed_rule:
                    span.set_attribute("failed_rule", failed_rule)
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix verdict event failed: {exc}")


_TRACER = PhoenixTracer()


def get_phoenix_tracer() -> PhoenixTracer:
    return _TRACER
