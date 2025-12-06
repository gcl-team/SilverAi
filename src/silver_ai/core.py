import functools
import logging
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Protocol,
    TypedDict,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

DRY_RUN_FLAG = "_silver_ai_dry_run"


FailureMode = Literal["return_dict", "raise"]


@runtime_checkable
class GuardRule(Protocol):
    """
    Blueprint for any safety rule.
    """

    def check(self, state: Dict[str, Any]) -> bool:
        """Returns True if safety check passes, False if it fails."""
        ...

    def violation_message(self, state: Dict[str, Any]) -> str:
        """Human-readable explanation of why it failed."""
        ...

    def suggestion(self) -> str:
        """How the Agent should fix this."""
        ...


class GuardResult(TypedDict):
    status: str
    reason: str
    suggestion: str | None
    dry_run: bool


class GuardViolationError(Exception):
    """Raised when on_fail='raise' and a rule fails."""

    pass


def guard(
    rules: List[GuardRule],
    state_key: str = "state",
    on_fail: FailureMode = "return_dict",
) -> Callable:
    """
    The Safety Decorator.

    Args:
        rules: List of objects implementing GuardRule.
        state_key: The attribute name on 'self' to inspect (default: "state").
        on_fail: Behavior when a rule fails.
            - "return_dict": Return a Dict with error details (Zero-Crash Policy).
            - "raise": Raise GuardViolationError exception.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # --- Context Extraction ---
            # We assume the decorated function is a method: func(self, ...)
            # So args[0] is 'self'.
            if not args:
                logger.warning(
                    f"SilverAi: @guard ignored on {func.__name__}. "
                    "No 'self' context found. Is this a static method?"
                )
                # If there are no arguments at all...
                # We cannot possibly find 'self', so we cannot check state.
                # Just run the function and get out to avoid crashing.
                # It prevents your library from crashing if a user accidentally puts
                # @guard on a static function or a plain function that has no arguments.
                # Should ideally raise a config error, but let's be safe
                return func(*args, **kwargs)

            instance = args[0]

            current_state = getattr(instance, state_key, {})

            # --- Rule Validation ---
            for rule in rules:
                if not rule.check(current_state):
                    msg = rule.violation_message(current_state)
                    logger.warning(f"Guard blocked execution: {msg}")

                    # ON-FAIL BEHAVIOR: Raise exception if user requested it
                    # This does not affect ZERO-CRASH POLICY below because
                    # raising exception is an explicit user choice.
                    if on_fail == "raise":
                        raise GuardViolationError(msg)

                    # ZERO-CRASH POLICY: Return a Dict, don't throw Exception
                    # We are deliberately choosing NOT to raise exception, which would
                    # be the Pythonic norm. Instead, we are converting the logic
                    # failure into a data payload.
                    # This way, the caller can handle it gracefully.
                    return {
                        "status": "error",
                        "reason": msg,
                        "suggestion": rule.suggestion(),
                        "dry_run": False,
                    }

            # --- Dry Run Check ---
            # Check if the user activated Dry Run globally or on the instance
            is_dry_run = getattr(instance, DRY_RUN_FLAG, False)

            if is_dry_run:
                logger.info(f"Dry Run: {func.__name__} passed checks but was skipped.")
                return {
                    "status": "success",
                    "reason": "Checks passed, but Dry Run is active.",
                    "suggestion": None,
                    "dry_run": True,
                }

            # --- Execution ---
            # If we are here, everything is safe.
            # It is safe and not Dry Run, so we can proceed to execute the function.
            return func(*args, **kwargs)

        return wrapper

    return decorator
