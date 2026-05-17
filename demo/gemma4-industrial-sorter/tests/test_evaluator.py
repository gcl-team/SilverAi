"""
Tests for Phoenix evaluator and contradiction detection.
"""

import sys
import os

# Add demo folder to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from evaluator import SilverAiEvaluator, AttemptVerdictV1


class TestEvaluator:
    """Test contradiction detection logic."""
    
    def test_contradiction_detected_overheat(self):
        """Contradiction: planner=ok but guard blocked by StateGate(motor_temp)."""
        evaluator = SilverAiEvaluator()
        
        result = {
            "status": "blocked",
            "first_plan": {
                "status": "ok",
                "route": "Express Belt",
                "package_weight": 4.0,
                "reason": "Urgent package route.",
            },
            "first_block": {
                "status": "error",
                "reason": "State gate blocked: motor_temp=85.00. Limit: 80.00.",
                "suggestion": "Wait for telemetry to return to safe limits.",
            },
            "replan": {
                "status": "ok",
                "route": "Buffer Belt",
                "package_weight": 4.0,
                "reason": "Alternative route after safety feedback.",
            },
            "retry_block": {
                "status": "error",
                "reason": "State gate blocked: motor_temp=85.00. Limit: 80.00.",
                "suggestion": "Wait for telemetry to return to safe limits.",
            },
        }
        
        eval_summary = evaluator.evaluate_scenario("scenario-1", "PKG-200", result)
        
        # Should detect contradictions on both attempts
        assert eval_summary["has_contradiction"] is True
        assert len(eval_summary["attempt_verdicts"]) == 2
        
        # Both attempts should have contradictions
        for verdict in eval_summary["attempt_verdicts"]:
            assert verdict.is_contradiction is True
            assert verdict.planner_status == "ok"
            assert verdict.guard_blocked is True
    
    def test_no_contradiction_safe_scenario(self):
        """No contradiction: planner=ok and guard allowed execution."""
        evaluator = SilverAiEvaluator()
        
        result = {
            "status": "success",
            "plan": {
                "status": "ok",
                "route": "Express Belt",
                "package_weight": 5.0,
                "reason": "Fragile and high-priority package.",
            },
            "execution": {
                "status": "success",
                "route": "Express Belt",
                "package_weight": 5.0,
            },
        }
        
        eval_summary = evaluator.evaluate_scenario("scenario-1", "PKG-100", result)
        
        # Should NOT detect contradiction
        assert eval_summary["has_contradiction"] is False
        assert len(eval_summary["attempt_verdicts"]) == 1
        assert eval_summary["attempt_verdicts"][0].is_contradiction is False
    
    def test_no_contradiction_blocked_by_guard_first_attempt_only(self):
        """No contradiction: first planner failed (error), guard didn't need to block."""
        evaluator = SilverAiEvaluator()
        
        result = {
            "status": "error",
            "first_plan": {
                "status": "planner_error",
                "reason": "Failed to parse response.",
                "suggestion": "Check endpoint and model.",
                "route": "n/a",
                "package_weight": 0.0,
            },
            "first_block": None,
        }
        
        eval_summary = evaluator.evaluate_scenario("scenario-1", "PKG-300", result)
        
        # Should NOT detect contradiction (planner didn't say "ok")
        assert eval_summary["has_contradiction"] is False
        assert len(eval_summary["attempt_verdicts"]) == 1
        assert eval_summary["attempt_verdicts"][0].is_contradiction is False
        assert eval_summary["attempt_verdicts"][0].planner_status == "planner_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
