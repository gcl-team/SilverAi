import contextvars
import functools
import logging
from contextlib import contextmanager
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

_REDACTED_VALUE = "<redacted>"
_SENSITIVE_KEY_MARKERS = (
    "token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "session",
    "credential",
    "bearer",
)

# Optional Phoenix tracing (for demo instrumentation)
# Uses contextvars for thread-safety and async-safety.
# Each thread/async context can have its own tracer.
_guard_tracer_var: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "guard_tracer", default=None
)
_guard_trace_scenario_id_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("guard_trace_scenario_id", default=None)
)
_guard_trace_package_id_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("guard_trace_package_id", default=None)
)
_guard_trace_attempt_index_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "guard_trace_attempt_index", default=1
)


def set_guard_tracer(tracer: Optional[Any]) -> None:
    """
    Inject a Phoenix tracer for guard instrumentation.
    Called by demo to enable tracing without coupling core to Phoenix.
    Thread-safe and async-safe via contextvars.ContextVar.
    """
    _guard_tracer_var.set(tracer)


@contextmanager
def guard_trace_context(
    scenario_id: Optional[str],
    package_id: Optional[str],
    attempt_index: int = 1,
):
    """Set per-invocation trace context for guard and planner tracing."""
    scenario_token = _guard_trace_scenario_id_var.set(scenario_id)
    package_token = _guard_trace_package_id_var.set(package_id)
    attempt_token = _guard_trace_attempt_index_var.set(attempt_index)
    try:
        yield
    finally:
        _guard_trace_scenario_id_var.reset(scenario_token)
        _guard_trace_package_id_var.reset(package_token)
        _guard_trace_attempt_index_var.reset(attempt_token)


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


def _type_summary(value: Any) -> str:
    return f"<{type(value).__name__}>"


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _safe_guard_input(func_name: str, args: tuple, kwargs: dict) -> Dict[str, Any]:
    safe_kwargs: Dict[str, str] = {}
    for key, value in kwargs.items():
        if _is_sensitive_key(key):
            safe_kwargs[str(key)] = _REDACTED_VALUE
        else:
            safe_kwargs[str(key)] = _type_summary(value)

    return {
        "function": func_name,
        # Positional arguments have no semantic names here; keep type-only
        # summaries to reduce accidental data leakage in trace payloads.
        "args": [_type_summary(arg) for arg in args[1:]],
        "kwargs": safe_kwargs,
    }


def _sanitize_trace_snapshot_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            string_key = str(key)
            if _is_sensitive_key(string_key):
                sanitized[string_key] = _REDACTED_VALUE
            else:
                sanitized[string_key] = _sanitize_trace_snapshot_value(item)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_trace_snapshot_value(item) for item in value]

    if isinstance(value, tuple):
        return [_sanitize_trace_snapshot_value(item) for item in value]

    if isinstance(value, set):
        return [_sanitize_trace_snapshot_value(item) for item in sorted(value, key=str)]

    return value


def _safe_gateway_snapshot(
    instance: Any, current_state: Dict[str, Any]
) -> Dict[str, Any]:
    try:
        gateway = getattr(instance, "gateway", None)
        if gateway is not None and hasattr(gateway, "snapshot"):
            snapshot = gateway.snapshot()
            if isinstance(snapshot, dict):
                return _sanitize_trace_snapshot_value(snapshot)
    except Exception:
        logger.exception("Failed to capture gateway snapshot for tracing")
    return _sanitize_trace_snapshot_value(current_state.copy())


def _resolve_guard_trace_context(
    instance: Any,
) -> tuple[Optional[str], Optional[str], int]:
    scenario_id = _guard_trace_scenario_id_var.get()
    package_id = _guard_trace_package_id_var.get()
    attempt_index = _guard_trace_attempt_index_var.get()

    if scenario_id is None:
        scenario_id = getattr(instance, "_trace_scenario_id", None)
    if package_id is None:
        package_id = getattr(instance, "_trace_package_id", None)

    instance_attempt_index = getattr(instance, "_trace_attempt_index", None)
    if attempt_index == 1 and isinstance(instance_attempt_index, int):
        attempt_index = instance_attempt_index

    return scenario_id, package_id, attempt_index


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

            scenario_id, package_id, attempt_index = _resolve_guard_trace_context(
                instance
            )

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
                            guard_input = _safe_guard_input(func.__name__, args, kwargs)
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
                        except Exception:
                            logger.exception("Failed to emit guard trace")

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
                except Exception:
                    logger.exception("Failed to emit guard pass trace")

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
