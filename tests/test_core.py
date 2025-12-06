from typing import Any, Dict, cast

import pytest

from silver_ai.core import DRY_RUN_FLAG, GuardResult, GuardViolationError, guard

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
