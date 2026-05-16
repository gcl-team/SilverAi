from __future__ import annotations

from typing import Any, Dict, List, cast

from planner_client import PlannerClient
from sorter_rules import MaxLoad, StateGate

from silver_ai import rules
from silver_ai.core import guard


class SorterAgent:
    def __init__(self, gateway: Any):
        self.gateway = gateway
        self.state = gateway.state
        self.planner = PlannerClient()

    @property
    def planner_trace_log(self) -> List[Dict[str, Any]]:
        return self.planner.trace_log

    def _planner_request(self, prompt: str) -> Dict[str, Any]:
        return self.planner.request(prompt)

    def _build_openai_endpoint(self) -> str:
        return self.planner._build_openai_endpoint()

    def _extract_planner_json(self, content: str) -> Dict[str, Any]:
        return self.planner._extract_planner_json(content)

    def _coerce_package_weight(self, raw_weight: Any) -> float:
        return self.planner._coerce_package_weight(raw_weight)

    @staticmethod
    def _safe_json_dumps(payload: Dict[str, Any]) -> str:
        return PlannerClient.safe_json_dumps(payload)

    def _execute_guarded_with_projection(
        self,
        package_id: str,
        route: str,
        package_weight: float,
    ) -> Dict[str, Any]:
        try:
            current_load = float(self.state.get("belt_load", 0.0))
        except (TypeError, ValueError, OverflowError):
            return {
                "status": "error",
                "reason": "Belt overload: invalid telemetry for belt_load.",
                "suggestion": (
                    "Restore valid numeric belt_load telemetry before retrying."
                ),
                "dry_run": False,
            }
        self.state["projected_belt_load"] = current_load + float(package_weight)
        try:
            return cast(
                Dict[str, Any],
                self._execute_guarded(package_id, route, package_weight),
            )
        finally:
            self.state.pop("projected_belt_load", None)

    @guard(
        rules=[
            MaxLoad(100.0),
            rules.BatteryMin(20),
            StateGate("motor_temp", max_value=80.0),
        ]
    )
    def _execute_guarded(
        self,
        package_id: str,
        route: str,
        package_weight: float,
    ) -> Dict[str, Any]:
        return self.gateway.execute_sort_command(
            package_id=package_id,
            route=route,
            package_weight=package_weight,
        )

    def propose_and_execute(
        self,
        package_id: str,
        package_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = (
            f"Package id: {package_id}. "
            f"Metadata: {self._safe_json_dumps(package_metadata)}. "
            "Choose a safe sorting route."
        )
        planned = self._planner_request(prompt)
        if planned.get("status") != "ok":
            return planned

        result = self._execute_guarded_with_projection(
            package_id,
            planned["route"],
            float(planned["package_weight"]),
        )

        if result.get("status") != "error":
            return {
                "status": "success",
                "plan": planned,
                "execution": result,
            }

        feedback_prompt = (
            f"Previous plan blocked. Reason: {result.get('reason')}. "
            f"Suggestion: {result.get('suggestion')}. "
            f"Telemetry: {self._safe_json_dumps(self.gateway.snapshot())}. "
            "Propose one alternative route as strict JSON."
        )

        replanned = self._planner_request(feedback_prompt)
        if replanned.get("status") != "ok":
            return {
                "status": "blocked",
                "first_plan": planned,
                "first_block": result,
                "block": result,
                "replan": replanned,
            }

        retry_result = self._execute_guarded_with_projection(
            package_id,
            replanned["route"],
            float(replanned["package_weight"]),
        )
        final_status = "success" if retry_result.get("status") != "error" else "blocked"
        response = {
            "status": final_status,
            "first_plan": planned,
            "first_block": result,
            "replan": replanned,
            "execution": retry_result,
        }
        if final_status == "blocked":
            response["retry_block"] = retry_result
            response["block"] = retry_result
        return response
