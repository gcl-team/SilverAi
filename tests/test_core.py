from typing import Any, Dict, cast

import pytest

from silver_ai.core import (
    DRY_RUN_FLAG,
    GuardResult,
    GuardViolationError,
    _safe_gateway_snapshot,
    guard,
    set_guard_tracer,
)

# --- Mock Infrastructure and Mock Rules ---


class AlwaysTrueRule:
    def check(self, state):
        return True

    def violation_message(self, state):
        return ""

    def suggestion(self):
        return ""


class AlwaysFalseRule:
    def check(self, state):
        return False

    def violation_message(self, state):
        return "You shall not pass!"

    def suggestion(self):
        return "Go back."


class StatefulMessageRule:
    """
    A rule that uses the state to generate its error message.
    Used to verify that the message generation receives the correct context.
    """

    def check(self, state):
        return False  # Always fail

    def violation_message(self, state):
        # The message depends on the SPECIFIC robot's battery
        # If this was using 'self.stored_battery', it might be stale.
        # By using 'state['battery']', we prove it is reading live data.
        return f"Battery is {state.get('battery')}"

    def suggestion(self):
        return ""


class MockDevice:
    """A fake hardware class to test the 'self.state' access."""

    def __init__(self, state=None, dry_run=False):
        self.state = state if state else {}
        setattr(self, DRY_RUN_FLAG, dry_run)
        self.action_performed = False

    # Safe action that always passes
    @guard(rules=[AlwaysTrueRule()])
    def safe_action(self):
        self.action_performed = True
        return "Executed"

    # Critical action that always fails
    @guard(rules=[AlwaysFalseRule()])
    def dangerous_action(self):
        self.action_performed = True
        return "Should Not Happen"

    # Critical action that raises on failure
    @guard(rules=[AlwaysFalseRule()], on_fail="raise")
    def critical_action(self):
        self.action_performed = True
        return "Should Crash"

    @guard(rules=[AlwaysFalseRule()])
    def dangerous_action_with_payload(self, payload, **kwargs):
        self.action_performed = True
        return "Should Not Happen"


# The shared rule instance (Simulating the @guard instantiation)
# By doing this, we can create multiple devices sharing the same rule instance.
SHARED_RULE = StatefulMessageRule()


class SharedRuleDevice:
    def __init__(self, battery):
        self.state = {"battery": battery}

    @guard(rules=[SHARED_RULE])
    def run(self):
        pass


# --- Test Cases ---


def test_guard_allows_safe_execution():
    """If rules pass, the function should run."""
    device = MockDevice()
    result = device.safe_action()

    assert result == "Executed"
    assert device.action_performed is True


def test_guard_blocks_unsafe_execution():
    """If rules fail, the function should NOT run and return a dict."""
    device = MockDevice()
    result = device.dangerous_action()

    assert device.action_performed is False

    assert isinstance(result, dict)

    result_dict = cast(Dict[str, Any], result)

    assert result_dict["status"] == "error"
    assert result_dict["reason"] == "You shall not pass!"
    assert result_dict["dry_run"] is False


def test_dry_run_skips_execution_but_checks_rules():
    """
    If Dry Run is True:
    1. Rules must still be checked (and pass).
    2. Function must NOT be executed.
    3. Return status should be 'success' (simulated).
    """
    device = MockDevice(dry_run=True)

    result = device.safe_action()

    assert device.action_performed is False

    result_dict = cast(GuardResult, result)

    assert result_dict["status"] == "success"
    assert result_dict["dry_run"] is True
    assert "checks passed" in result_dict["reason"].lower()


def test_dry_run_still_fails_unsafe_rules():
    """
    If Dry Run is True but Rule is False, it should still FAIL.
    (Definition A: Simulation Mode)
    """
    device = MockDevice(dry_run=True)

    result = device.dangerous_action()

    result_dict = cast(GuardResult, result)

    assert result_dict["status"] == "error"
    assert result_dict["reason"] == "You shall not pass!"


def test_guard_missing_state_attribute():
    """
    Edge case: @guard on a class that has no 'self.state' attribute.
    Default behavior: State is empty {}, rules check against empty dict.
    """

    class BadDevice:
        # No self.state defined!
        @guard(rules=[AlwaysTrueRule()])
        def run(self):
            return "OK"

    device = BadDevice()

    assert device.run() == "OK"


def test_guard_on_plain_function_no_args():
    """
    Edge case: @guard on a function without arguments (no 'self').
    Default behavior: Should bypass checks and run.
    """

    @guard(rules=[AlwaysFalseRule()])
    def plain_func():
        return "I ran"

    assert plain_func() == "I ran"


def test_guard_raises_exception_when_requested():
    """
    If on_fail="raise" is set, the guard should raise GuardViolationError
    instead of returning a dictionary.
    """
    device = MockDevice()

    # The 'with' block asserts that the code inside IT causes an exception.
    with pytest.raises(GuardViolationError) as excinfo:
        device.critical_action()

    # 1. Verify the crash message matches the rule
    assert "You shall not pass!" in str(excinfo.value)

    # 2. Verify the hardware execution was blocked
    assert device.action_performed is False


def test_guard_rules_are_stateless_across_instances():
    """
    Verify that a single Rule instance shared across multiple objects
    correctly reads the unique state of each object.
    """
    # 1. Create two devices with different states
    robot_low = SharedRuleDevice(battery=10)
    robot_high = SharedRuleDevice(battery=99)

    # 2. Run Robot Low (Fails)
    result_low = robot_low.run()
    result_low = cast(Dict[str, Any], result_low)

    # 3. Run Robot High (Fails)
    result_high = robot_high.run()
    result_high = cast(Dict[str, Any], result_high)

    # 4. ASSERTION:
    # If the rule was stateful/buggy, 'self.current_level' might have been
    # overwritten by the last run or initialization.
    # We verify that EACH result contains its OWN battery level.

    assert result_low["reason"] == "Battery is 10"
    assert result_high["reason"] == "Battery is 99"


# --- Tracing Instrumentation Tests ---


class StubTracer:
    """Mock tracer for testing guard instrumentation."""

    def __init__(self, fail_on_emit: bool = False):
        self.blocked_events = []
        self.passed_events = []
        self.fail_on_emit = fail_on_emit

    def emit_guard_event(
        self,
        scenario_id: str,
        package_id: str,
        attempt_index: int,
        guard_input: Dict[str, Any],
        outcome: str,
        gateway_snapshot: Dict[str, Any],
        failed_rule_name: str = None,
        violation_message: str = None,
        evaluated_rules: list = None,
    ) -> None:
        if self.fail_on_emit:
            raise RuntimeError("Simulated tracer failure")

        if outcome == "blocked":
            self.blocked_events.append(
                {
                    "scenario_id": scenario_id,
                    "package_id": package_id,
                    "attempt_index": attempt_index,
                    "guard_input": guard_input,
                    "outcome": outcome,
                    "failed_rule_name": failed_rule_name,
                    "violation_message": violation_message,
                }
            )
        elif outcome == "passed":
            self.passed_events.append(
                {
                    "scenario_id": scenario_id,
                    "package_id": package_id,
                    "attempt_index": attempt_index,
                    "guard_input": guard_input,
                    "outcome": outcome,
                    "evaluated_rules": evaluated_rules,
                }
            )


def test_guard_emits_blocked_event_with_trace_ids():
    """
    Verify that when scenario_id and package_id are set on the instance,
    a blocked event is emitted to the tracer.
    """
    tracer = StubTracer()
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-123"
        device._trace_package_id = "pkg-456"
        device._trace_attempt_index = 1

        result = device.dangerous_action()

        # Verify guard blocked execution
        assert isinstance(result, dict)
        assert result["status"] == "error"

        # Verify tracer captured the blocked event
        assert len(tracer.blocked_events) == 1
        event = tracer.blocked_events[0]
        assert event["scenario_id"] == "scenario-123"
        assert event["package_id"] == "pkg-456"
        assert event["attempt_index"] == 1
        assert event["outcome"] == "blocked"
        assert event["failed_rule_name"] == "AlwaysFalseRule"
        assert "You shall not pass!" in event["violation_message"]

        # No passed events should be recorded
        assert len(tracer.passed_events) == 0

    finally:
        set_guard_tracer(None)


def test_guard_emits_passed_event_with_trace_ids():
    """
    Verify that when scenario_id and package_id are set and all rules pass,
    a passed event is emitted to the tracer.
    """
    tracer = StubTracer()
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-789"
        device._trace_package_id = "pkg-101"
        device._trace_attempt_index = 2

        result = device.safe_action()

        # Verify guard allowed execution
        assert result == "Executed"
        assert device.action_performed is True

        # Verify tracer captured the passed event
        assert len(tracer.passed_events) == 1
        event = tracer.passed_events[0]
        assert event["scenario_id"] == "scenario-789"
        assert event["package_id"] == "pkg-101"
        assert event["attempt_index"] == 2
        assert event["outcome"] == "passed"
        assert "AlwaysTrueRule" in event["evaluated_rules"]

        # No blocked events should be recorded
        assert len(tracer.blocked_events) == 0

    finally:
        set_guard_tracer(None)


def test_guard_no_trace_without_ids():
    """
    Verify that if scenario_id or package_id are missing,
    no trace events are emitted even if a tracer is registered.
    """
    tracer = StubTracer()
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        # Don't set scenario/package IDs

        device.dangerous_action()

        # No events should be recorded
        assert len(tracer.blocked_events) == 0
        assert len(tracer.passed_events) == 0

    finally:
        set_guard_tracer(None)


def test_guard_swallows_tracer_failure_on_blocked():
    """
    Verify that if the tracer fails during emit_guard_event (blocked path),
    the guard outcome is NOT affected. The error is logged and swallowed.
    """
    tracer = StubTracer(fail_on_emit=True)
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-123"
        device._trace_package_id = "pkg-456"

        # This should NOT raise an exception, despite tracer failure
        result = device.dangerous_action()

        # Verify guard still blocked execution correctly
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert result["reason"] == "You shall not pass!"
        assert device.action_performed is False

    finally:
        set_guard_tracer(None)


def test_guard_swallows_tracer_failure_on_passed():
    """
    Verify that if the tracer fails during emit_guard_event (passed path),
    the guard outcome is NOT affected. The error is logged and swallowed.
    """
    tracer = StubTracer(fail_on_emit=True)
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-789"
        device._trace_package_id = "pkg-101"

        # This should NOT raise an exception, despite tracer failure
        result = device.safe_action()

        # Verify guard still allowed execution correctly
        assert result == "Executed"
        assert device.action_performed is True

    finally:
        set_guard_tracer(None)


def test_guard_input_sanitizes_sensitive_kwargs_for_trace_events():
    tracer = StubTracer()
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-sensitive"
        device._trace_package_id = "pkg-sensitive"

        result = device.dangerous_action_with_payload(
            "secret payload value",
            token="sk-test-123",
            api_key="api-key-456",
            password="pw-789",
            request_id=123,
        )

        assert isinstance(result, dict)
        assert result["status"] == "error"

        assert len(tracer.blocked_events) == 1
        guard_input = tracer.blocked_events[0]["guard_input"]
        assert guard_input["args"] == ["<str>"]
        assert guard_input["kwargs"]["token"] == "<redacted>"
        assert guard_input["kwargs"]["api_key"] == "<redacted>"
        assert guard_input["kwargs"]["password"] == "<redacted>"
        assert guard_input["kwargs"]["request_id"] == "<int>"
    finally:
        set_guard_tracer(None)


def test_guard_input_uses_type_summaries_for_passed_trace_events():
    tracer = StubTracer()
    set_guard_tracer(tracer)

    try:
        device = MockDevice()
        device._trace_scenario_id = "scenario-pass"
        device._trace_package_id = "pkg-pass"

        result = device.safe_action()

        assert result == "Executed"
        assert len(tracer.passed_events) == 1
        guard_input = tracer.passed_events[0]["guard_input"]
        assert guard_input["args"] == []
        assert guard_input["kwargs"] == {}
    finally:
        set_guard_tracer(None)


def test_safe_gateway_snapshot_falls_back_for_none_snapshot():
    class _BadGateway:
        def snapshot(self):
            return None

    class _Device:
        gateway = _BadGateway()

    current_state = {"battery": 88}
    snapshot = _safe_gateway_snapshot(_Device(), current_state)

    assert snapshot == current_state
    assert snapshot is not current_state


def test_safe_gateway_snapshot_falls_back_for_non_dict_snapshot():
    class _BadGateway:
        def snapshot(self):
            return ["not", "a", "dict"]

    class _Device:
        gateway = _BadGateway()

    current_state = {"motor_temp": 42.0}
    snapshot = _safe_gateway_snapshot(_Device(), current_state)

    assert snapshot == current_state
    assert snapshot is not current_state
