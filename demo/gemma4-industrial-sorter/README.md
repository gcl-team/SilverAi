# Gemma 4 Industrial Sorter Demo

This demo shows how probabilistic LLM planning can be constrained by deterministic SilverAi safety checks before any hardware-like action is executed.

## Why this exists

Gemma proposes a route based on package context.
SilverAi validates the action against real-time gateway telemetry.
If safety constraints are violated, execution is blocked and structured feedback is returned for re-planning.

This closes the reliability gap between:
- Probabilistic reasoning from an LLM
- Binary industrial safety requirements

## Demo architecture

1. Planner proposes route and package weight.
2. SilverAi guard validates rules against gateway state.
3. If safe, command executes on the simulated gateway.
4. If unsafe, SilverAi returns structured error details.
5. Planner can retry once using the safety feedback.

## Safety policy in this demo

The sorter agent applies these checks before execution:
- MaxLoad 100.0: blocks if belt_load exceeds physical capacity
- BatteryMin 20: blocks when backup power is critical
- StateGate on motor_temp <= 80.0: blocks overheating actions

## Files

- demo.py: runnable demo flow with safe and dangerous scenarios
- warehouse_gateway.py: simulated PLC/gateway telemetry and command execution
- sorter_agent.py: planner integration, SilverAi guarded execution, one-retry re-plan
- tests/test_sorter_agent.py: demo integration tests

## Run

From repo root:

```bash
poetry run python demo/gemma4-industrial-sorter/demo.py
```

Expected behavior:
- Scenario 1 executes successfully under healthy telemetry.
- Scenario 2 forces motor_temp to 85 and gets blocked by SilverAi, including a blocked retry.

## Demo scenarios

The demo script now runs four scenarios in sequence:
- Scenario 1: Healthy state (expected success)
- Scenario 2: Overheat state with one re-plan attempt (expected blocked)
- Scenario 3: Low battery below BatteryMin threshold (expected blocked)
- Scenario 4: Belt load above MaxLoad threshold (expected blocked)

## Run with a real OpenAI-compatible endpoint and show proof

Enable live mode to call an OpenAI-compatible endpoint directly instead of scripted planner output:

```bash
OPENAI_RAW_PREVIEW_CHARS=0 OPENAI_PARSED_PREVIEW_CHARS=0 DEMO_USE_LIVE_OPENAI=1 DEMO_TRACE_VERBOSE=1 poetry run python demo/gemma4-industrial-sorter/demo.py
```

In live mode, the demo prints a planner trace section showing:
- endpoint and model used
- HTTP status from the endpoint
- raw response preview from the API
- parsed response preview used by the sorter agent

This output demonstrates the request/response path came from an OpenAI-compatible endpoint.

## Run tests for this demo only

```bash
poetry run pytest demo/gemma4-industrial-sorter/tests
```

## Endpoint note

The current demo script uses scripted planner responses to keep CI deterministic.
The sorter agent also includes a real OpenAI-compatible endpoint path for runtime integration.
LM Studio is one example; Ollama or other compatible servers can work too.

Environment variables supported by the agent:
- OPENAI_BASE_URL (default: http://127.0.0.1:1234)
- OPENAI_MODEL (default: google/gemma-4-e4b)
- OPENAI_TIMEOUT (default: 20)
