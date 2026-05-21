"""Phoenix tracing helpers for the Gemma industrial sorter demo."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode


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
                project_name=os.getenv(
                    "PHOENIX_PROJECT_NAME", "gemma4-industrial-sorter"
                ),
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
        planner_input: str,
        planner_response: Dict[str, Any],
        gateway_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span(
                f"planner_attempt_{attempt_index}"
            ) as span:
                planner_status = str(planner_response.get("status", "unknown"))
                planner_route = str(planner_response.get("route", "n/a"))
                planner_reason = str(planner_response.get("reason", ""))
                weight = planner_response.get("package_weight")

                if planner_status == "ok":
                    output_summary = (
                        f"Planned {planner_route}"
                        f" | weight={weight if weight is not None else 'n/a'}"
                        f" | reason={planner_reason}"
                    )
                else:
                    output_summary = (
                        f"Planner {planner_status} | reason={planner_reason or 'n/a'}"
                    )

                span.set_attribute(
                    SpanAttributes.OPENINFERENCE_SPAN_KIND,
                    OpenInferenceSpanKindValues.LLM.value,
                )
                span.set_attribute(
                    SpanAttributes.INPUT_VALUE,
                    f"## Planner Prompt\n\n{planner_input}",
                )
                span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, "text/plain")
                span.set_attribute(SpanAttributes.OUTPUT_VALUE, output_summary)
                span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "text/plain")
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute("planner_status", planner_status)
                span.set_attribute("planner_outcome", planner_status)
                span.set_attribute("planner_reason", planner_reason)
                span.set_attribute("planner_route", planner_route)
                model_name = planner_response.get("model")
                if isinstance(model_name, str) and model_name:
                    span.set_attribute(SpanAttributes.LLM_MODEL_NAME, model_name)

                prompt_tokens = planner_response.get("prompt_tokens")
                completion_tokens = planner_response.get("completion_tokens")
                total_tokens = planner_response.get("total_tokens")
                if isinstance(prompt_tokens, int):
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_PROMPT, prompt_tokens
                    )
                if isinstance(completion_tokens, int):
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, completion_tokens
                    )
                if isinstance(total_tokens, int):
                    span.set_attribute(
                        SpanAttributes.LLM_TOKEN_COUNT_TOTAL, total_tokens
                    )
                if isinstance(weight, (int, float)):
                    span.set_attribute("planner_weight", float(weight))

                if gateway_snapshot:
                    for key in ("motor_temp", "belt_load", "battery"):
                        value = gateway_snapshot.get(key)
                        if isinstance(value, (int, float)):
                            span.set_attribute(f"gateway_{key}", float(value))

                # Span status indicates telemetry/instrumentation health only.
                # Business outcomes (ok/planner_error) are captured in attributes.
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix planner event failed: {exc}")

    def emit_guard_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        guard_input: Dict[str, Any],
        outcome: str,
        gateway_snapshot: Dict[str, Any],
        failed_rule_name: Optional[str] = None,
        violation_message: Optional[str] = None,
        evaluated_rules: Optional[list[str]] = None,
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span("guard_check") as span:
                if outcome == "blocked":
                    output_summary = (
                        "## Guard Blocked\n\n"
                        f"- **Rule**: {failed_rule_name or 'unknown'}\n"
                        f"- **Reason**: {violation_message or 'unknown'}"
                    )
                else:
                    output_summary = "## Guard Passed\n\n- **Result**: All rules passed"
                span.set_attribute(
                    SpanAttributes.OPENINFERENCE_SPAN_KIND,
                    OpenInferenceSpanKindValues.GUARDRAIL.value,
                )
                span.set_attribute(
                    SpanAttributes.INPUT_VALUE,
                    json.dumps(guard_input, sort_keys=True, default=str),
                )
                span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, "application/json")
                span.set_attribute(SpanAttributes.OUTPUT_VALUE, output_summary)
                span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "text/plain")
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute("guardrail_outcome", outcome)
                span.set_attribute("guardrail_enforced", True)
                if outcome == "blocked":
                    if failed_rule_name:
                        span.set_attribute("failed_rule", failed_rule_name)
                    if violation_message:
                        span.set_attribute("block_reason", violation_message)
                elif evaluated_rules:
                    span.set_attribute("evaluated_rules", ",".join(evaluated_rules))
                for key in ("motor_temp", "belt_load", "battery"):
                    value = gateway_snapshot.get(key)
                    if isinstance(value, (int, float)):
                        span.set_attribute(f"gateway_{key}", float(value))
                # Guard block is expected control behavior, not a program error.
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix guard event failed: {exc}")

    def emit_verdict_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        planner_status: str,
        planner_route: str,
        planner_reason: str,
        block_reason: Optional[str],
        is_contradiction: bool,
        failed_rule: Optional[str] = None,
    ) -> None:
        if not self.enabled or self._tracer is None:
            return

        try:
            with self._tracer.start_as_current_span("contradiction_verdict") as span:
                output_summary = "## Evaluator Verdict\n\n" + (
                    "- **Result**: Contradiction detected"
                    if is_contradiction
                    else "- **Result**: No contradiction"
                )
                if failed_rule:
                    output_summary += f"\n- **Rule**: {failed_rule}"
                span.set_attribute(
                    SpanAttributes.OPENINFERENCE_SPAN_KIND,
                    OpenInferenceSpanKindValues.EVALUATOR.value,
                )
                span.set_attribute(
                    SpanAttributes.INPUT_VALUE,
                    json.dumps(
                        {
                            "planner_status": planner_status,
                            "planner_route": planner_route,
                            "planner_reason": planner_reason,
                            "block_reason": block_reason,
                        },
                        sort_keys=True,
                        default=str,
                    ),
                )
                span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, "application/json")
                span.set_attribute(SpanAttributes.OUTPUT_VALUE, output_summary)
                span.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, "text/plain")
                span.set_attribute("scenario_id", scenario_id)
                span.set_attribute("package_id", package_id)
                span.set_attribute("attempt_index", attempt_index)
                span.set_attribute("planner_status", planner_status)
                span.set_attribute("planner_route", planner_route)
                span.set_attribute("planner_reason", planner_reason)
                span.set_attribute("is_contradiction", is_contradiction)
                span.set_attribute(
                    "verdict_outcome",
                    "contradiction" if is_contradiction else "no_contradiction",
                )
                if block_reason:
                    span.set_attribute("block_reason", block_reason)
                if failed_rule:
                    span.set_attribute("failed_rule", failed_rule)
                # Contradiction is a business verdict, not instrumentation failure.
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:  # pragma: no cover
            print(f"Phoenix verdict event failed: {exc}")


_TRACER = PhoenixTracer()


def get_phoenix_tracer() -> PhoenixTracer:
    return _TRACER
