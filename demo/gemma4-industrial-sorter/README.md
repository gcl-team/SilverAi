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

## Run

From repo root:

```bash
poetry run python demo/gemma4-industrial-sorter/demo.py
```

## Demo scenarios

The demo script now runs four scenarios in sequence:
- Scenario 1: Healthy state (expected success)
- Scenario 2: Overheat state with one re-plan attempt (expected blocked)
- Scenario 3: Low battery below BatteryMin threshold (expected blocked)
- Scenario 4: Belt load above MaxLoad threshold (expected blocked)

## Run with a real OpenAI-compatible endpoint and show proof

By default, the demo runs in live endpoint mode and calls an OpenAI-compatible endpoint directly (LM Studio local endpoint by default):

```bash
poetry run python demo/gemma4-industrial-sorter/demo.py
```

To force scripted planner mode (useful for deterministic local checks), set:

```bash
DEMO_USE_LIVE_OPENAI=0 poetry run python demo/gemma4-industrial-sorter/demo.py
```

To point the demo at a cloud-hosted OpenAI-compatible model, set the base URL explicitly and opt in to remote access:

```bash
OPENAI_BASE_URL=https://your-cloud-endpoint.example OPENAI_ALLOW_REMOTE=1 poetry run python demo/gemma4-industrial-sorter/demo.py
```

If the endpoint requires authentication, also provide an API key. The demo client will send it as a Bearer token in the Authorization header:

```bash
OPENAI_BASE_URL=https://your-cloud-endpoint.example OPENAI_ALLOW_REMOTE=1 OPENAI_API_KEY=your-token poetry run python demo/gemma4-industrial-sorter/demo.py
```

For localhost runtimes (for example LM Studio), OPENAI_API_KEY is optional.

For non-localhost endpoints, HTTPS is required when OPENAI_ALLOW_REMOTE=1 is set.

In live mode, the demo prints a planner trace section showing:
- endpoint and model used
- HTTP status from the endpoint
- raw response preview from the API
- parsed response preview used by the sorter agent

Planner package_weight parsing assumptions:
- Numeric values without units are treated as kilograms (kg)
- Supported explicit units: kg, g, lb/lbs/pound, oz/ounce (including plural forms)
- Unsupported unit strings are rejected with a planner_error to avoid silent mis-scaling

This output demonstrates the request/response path came from an OpenAI-compatible endpoint.

## Run tests for this demo only

```bash
poetry run pytest demo/gemma4-industrial-sorter/tests
```

## Endpoint note

The demo defaults to localhost OpenAI-compatible runtime integration.
Use DEMO_USE_LIVE_OPENAI=0 when you need scripted planner responses for deterministic checks.
For remote endpoints, explicitly opt in with OPENAI_ALLOW_REMOTE=1 and use HTTPS.
LM Studio is one example; Ollama or other compatible servers can work too.
