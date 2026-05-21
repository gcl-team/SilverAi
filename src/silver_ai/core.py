import contextvars
import functools
import logging
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    ParamSpec,
    Protocol,
    TypedDict,
    TypeVar,
    Union,
    cast,
    overload,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

# Optional Phoenix tracing (for demo instrumentation)
# Uses contextvars for thread-safety and async-safety.
# Each thread/async context can have its own tracer.
_guard_tracer_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "guard_tracer", default=None
)


def set_guard_tracer(tracer: Optional[Any]) -> None:
    """
    Inject a Phoenix tracer for guard instrumentation.
    Called by demo to enable tracing without coupling core to Phoenix.
    Thread-safe and async-safe via contextvars.ContextVar.
    """
    _guard_tracer_var.set(tracer)


DRY_RUN_FLAG = "_silver_ai_dry_run"


FailureMode = Literal["return_dict", "raise"]
P = ParamSpec("P")
R = TypeVar("R")


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


class GuardErrorResult(TypedDict):
    status: Literal["error"]
    reason: str
    suggestion: str
    dry_run: Literal[False]


class GuardDryRunResult(TypedDict):
    status: Literal["success"]
    reason: str
    suggestion: None
    dry_run: Literal[True]


GuardResult = Union[GuardErrorResult, GuardDryRunResult]


class GuardViolationError(Exception):
    """Raised when on_fail='raise' and a rule fails."""

    pass


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return "<unrepresentable>"


def _safe_guard_input(
    func_name: str, args: tuple, kwargs: dict
) -> Dict[str, Any]:
    return {
        "function": func_name,
        "args": [_safe_repr(arg) for arg in args[1:]],
        "kwargs": {key: _safe_repr(value) for key, value in kwargs.items()},
    }


def _safe_gateway_snapshot(
    instance: Any, current_state: Dict[str, Any]
) -> Dict[str, Any]:
    try:
        gateway = getattr(instance, "gateway", None)
        if gateway is not None and hasattr(gateway, "snapshot"):
            return gateway.snapshot()
    except Exception as e:
        logger.warning(f"Failed to capture gateway snapshot for tracing: {e}")
    return current_state.copy()


@overload
def guard(
    rules: List[GuardRule],
    state_key: str = "state",
    on_fail: Literal["return_dict"] = "return_dict",
) -> Callable[[Callable[P, R]], Callable[P, Union[R, GuardResult]]]: ...


@overload
def guard(
    rules: List[GuardRule],
    state_key: str = "state",
    on_fail: Literal["raise"] = "raise",
) -> Callable[[Callable[P, R]], Callable[P, Union[R, GuardDryRunResult]]]: ...


def guard(
    rules: List[GuardRule],
    state_key: str = "state",
    on_fail: FailureMode = "return_dict",
) -> Callable[[Callable[P, R]], Callable[P, Union[R, GuardResult]]]:
    """
    The Safety Decorator.

    Args:
        rules: List of objects implementing GuardRule.
        state_key: The attribute name on 'self' to inspect (default: "state").
        on_fail: Behavior when a rule fails.
            - "return_dict": Return a Dict with error details (Zero-Crash Policy).
            - "raise": Raise GuardViolationError exception.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, Union[R, GuardResult]]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Union[R, GuardResult]:
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

            state_value = getattr(instance, state_key, {})
            current_state = cast(
                Dict[str, Any],
                state_value if isinstance(state_value, dict) else {},
            )

            scenario_id = getattr(instance, "_trace_scenario_id", None)
            package_id = getattr(instance, "_trace_package_id", None)
            attempt_index = getattr(instance, "_trace_attempt_index", 1)

            # --- Rule Validation ---
            for rule in rules:
                if not rule.check(current_state):
                    msg = rule.violation_message(current_state)
                    logger.warning(f"Guard blocked execution: {msg}")

                    # Emit guard trace event if tracer is available
                    tracer = _guard_tracer_var.get()
                    if tracer is not None and scenario_id and package_id:
                        try:
                            rule_name = getattr(
                                rule, "_trace_name", rule.__class__.__name__
                            )
                            guard_input = _safe_guard_input(
                                func.__name__, args, kwargs
                            )
                            tracer.emit_guard_event(
                                scenario_id=scenario_id,
                                package_id=package_id,
                                attempt_index=attempt_index,
                                guard_input=guard_input,
                                outcome="blocked",
                                gateway_snapshot=_safe_gateway_snapshot(
                                    instance, current_state
                                ),
                                failed_rule_name=rule_name,
                                violation_message=msg,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to emit guard trace: {e}")

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

            # Emit pass-path guard event so outcomes are observable for both
            # allowed and blocked attempts.
            tracer = _guard_tracer_var.get()
            if tracer is not None and scenario_id and package_id:
                try:
                    evaluated_rules = [
                        getattr(rule, "_trace_name", rule.__class__.__name__)
                        for rule in rules
                    ]
                    guard_input = _safe_guard_input(func.__name__, args, kwargs)
                    tracer.emit_guard_event(
                        scenario_id=scenario_id,
                        package_id=package_id,
                        attempt_index=attempt_index,
                        guard_input=guard_input,
                        outcome="passed",
                        gateway_snapshot=_safe_gateway_snapshot(
                            instance, current_state
                        ),
                        evaluated_rules=evaluated_rules,
                    )
                except Exception as e:
                    logger.warning(f"Failed to emit guard pass trace: {e}")

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
