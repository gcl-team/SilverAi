"""
Evaluator for contradiction detection in SilverAi demo.

A contradiction occurs when:
- Planner intent (status='ok') contradicts actual safety constraints (guard blocks)

This evaluator processes scenario results and emits verdicts that stakeholders
can use as proof that safety logic is working correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from phoenix_trace import get_phoenix_tracer


@dataclass
class AttemptVerdictV1:
    """Evaluation result for a single planner attempt."""

    scenario_id: str
    package_id: str
    attempt_index: int
    planner_status: str
    planner_reason: str
    planner_route: str

    # Guard outcomes
    guard_blocked: bool
    failed_rule: Optional[str] = None
    block_reason: Optional[str] = None

    # Verdict
    is_contradiction: bool = False
    contradiction_reason: Optional[str] = None


class SilverAiEvaluator:
    """
    Evaluator that analyzes execution results for contradictions.
    """

    def __init__(self):
        self.tracer = get_phoenix_tracer()

    def evaluate_attempt(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        planner_response: Dict[str, Any],
        guard_response: Optional[Dict[str, Any]],
        failed_rule: Optional[str] = None,
    ) -> AttemptVerdictV1:
        """
        Evaluate a single planner attempt against guard outcome.

        Args:
            scenario_id: Scenario identifier
            package_id: Package identifier
            attempt_index: 1 for first plan, 2 for replan, etc.
            planner_response: Dict with status, reason, route, package_weight
            guard_response: Dict with status, reason
                (error dict from guard, or None if passed)
            failed_rule: Which rule triggered the guard block (if any)

        Returns:
            AttemptVerdictV1 with contradiction analysis
        """
        planner_status = planner_response.get("status", "unknown")
        guard_blocked = (
            guard_response is not None and guard_response.get("status") == "error"
        )

        # Contradiction: planner said "ok" but guard blocked
        is_contradiction = planner_status == "ok" and guard_blocked

        contradiction_reason = None
        if is_contradiction:
            blocked_reason = (
                guard_response.get("reason") if guard_response else "unknown"
            )
            contradiction_reason = (
                f"Planner intended '{planner_response.get('route')}' "
                f"(reason: {planner_response.get('reason')}) "
                f"but guard blocked: {blocked_reason}"
            )

        verdict = AttemptVerdictV1(
            scenario_id=scenario_id,
            package_id=package_id,
            attempt_index=attempt_index,
            planner_status=planner_status,
            planner_reason=str(planner_response.get("reason", "n/a")),
            planner_route=str(planner_response.get("route", "n/a")),
            guard_blocked=guard_blocked,
            failed_rule=failed_rule,
            block_reason=guard_response.get("reason") if guard_response else None,
            is_contradiction=is_contradiction,
            contradiction_reason=contradiction_reason,
        )

        # Emit verdict event
        self.tracer.emit_verdict_event(
            scenario_id=scenario_id,
            package_id=package_id,
            attempt_index=attempt_index,
            planner_status=str(planner_status),
            planner_route=str(planner_response.get("route", "n/a")),
            planner_reason=str(planner_response.get("reason", "n/a")),
            block_reason=guard_response.get("reason") if guard_response else None,
            is_contradiction=is_contradiction,
            failed_rule=failed_rule,
        )

        return verdict

    def evaluate_scenario(
        self,
        scenario_id: str,
        package_id: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Evaluate entire scenario result for contradictions.

        Args:
            scenario_id: Scenario identifier
            package_id: Package identifier
            result: Full result dict from propose_and_execute

        Returns:
            Evaluation summary with per-attempt verdicts
        """
        verdicts: List[AttemptVerdictV1] = []

        # Evaluate first attempt
        first_plan = result.get("first_plan") or result.get("plan")
        first_block = result.get("first_block")

        if first_plan:
            verdict1 = self.evaluate_attempt(
                scenario_id=scenario_id,
                package_id=package_id,
                attempt_index=1,
                planner_response=first_plan,
                guard_response=first_block,
                failed_rule=None,  # Would come from trace
            )
            verdicts.append(verdict1)

        # Evaluate second attempt (replan)
        replan = result.get("replan")
        retry_block = result.get("retry_block")

        if replan:
            verdict2 = self.evaluate_attempt(
                scenario_id=scenario_id,
                package_id=package_id,
                attempt_index=2,
                planner_response=replan,
                guard_response=retry_block,
                failed_rule=None,  # Would come from trace
            )
            verdicts.append(verdict2)

        # Summarize: scenario contradicts if ANY attempt has contradiction
        has_contradiction = any(v.is_contradiction for v in verdicts)

        return {
            "scenario_id": scenario_id,
            "package_id": package_id,
            "scenario_status": result.get("status", "unknown"),
            "has_contradiction": has_contradiction,
            "attempt_verdicts": verdicts,
        }


def print_evaluator_report(
    scenario_name: str,
    eval_summary: Dict[str, Any],
) -> None:
    """
    Print a stakeholder-friendly evaluator report.

    Args:
        scenario_name: Human-readable scenario name
        eval_summary: Output from evaluate_scenario
    """
    print(f"\n📊 Evaluator Report: {scenario_name}")
    print(f"   Package: {eval_summary['package_id']}")
    print(f"   Scenario Status: {eval_summary['scenario_status']}")
    print(f"   Contradictions Detected: {eval_summary['has_contradiction']}")

    for verdict in eval_summary["attempt_verdicts"]:
        print(f"\n   Attempt {verdict.attempt_index}:")
        print(
            f"     Planner: {verdict.planner_status} (reason: {verdict.planner_reason})"
        )
        print(f"     Route: {verdict.planner_route}")
        print(f"     Guard Blocked: {verdict.guard_blocked}")
        if verdict.block_reason:
            print(f"     Block Reason: {verdict.block_reason}")
        if verdict.is_contradiction:
            print(f"     ⚠️  CONTRADICTION: {verdict.contradiction_reason}")
        else:
            print("     ✅ No contradiction")
