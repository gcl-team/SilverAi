# 🛡️ SilverAi

**Deterministic State-Guardrails for Agentic Hardware & Critical Systems.**

[![SilverAi CI](https://github.com/gcl-team/SilverAi/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/gcl-team/SilverAi/actions/workflows/ci.yaml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

> **"You wouldn't let a drunk person drive a forklift. Why let a probabilistic LLM drive your physical hardware?"**

## 🚨 The Problem

Large Language Models (LLMs) like GPT-4, DeepSeek, and Claude are **Probabilistic Engines**. They are optimized for creativity, not safety.

When connecting Agents to **Physical Hardware (IoT/Robotics)** or **Financial Systems**, "99% accuracy" is not enough. A single hallucination can cause:
*   **Physical Damage:** Ignoring battery/thermal limits on a device.
*   **Operational Failure:** Attempting to control a disconnected device over BLE/MQTT.
*   **Financial Risk:** Hallucinating discounts or executing unauthorized transactions.

Existing solutions (Bedrock Guardrails, NeMo) focus on **Semantic Safety** (profanity, PII). They are blind to **State Safety**.

## ⚡ The Solution

**SilverAi** is a lightweight, dependency-free Python middleware that enforces **Deterministic Contracts** on your Agent's tools. It sits between the LLM's intent and your system's execution.

### ✨ Key Features
*   **🐍 Pythonic Decorators:** Clean, readable syntax using `@guard`.
*   **🔌 Connectivity Gates:** Prevents Agents from calling APIs when the device is offline (`BLE`, `WiFi`).
*   **🔋 State-Aware:** Validates against real-time telemetry (Battery, Heat) before execution.
*   **🧪 Dry-Run Mode:** Test your safety logic in CI/CD without requiring physical hardware or live APIs.

---

## 🚀 Quick Start

### Installation
```bash
pip install silver-ai
```

### Usage: Protecting a Robot

Prevent an Agent from moving a robot if the battery is critical or the connection is unstable.

```python
from silver_ai import guard, rules

class IndustrialRobot:
    def __init__(self):
        # In production, this state comes from live telemetry
        self.state = {
            "battery": 10, 
            "connection": "offline",
            "is_stuck": False
        }

    @guard(
        rules.BatteryMin(15),
        rules.RequireConnectivity(protocol="BLE"),
        on_fail="raise" # Make the script to crash (e.g., during unit tests).
    )
    def start_operation(self, zone: str):
        # 🛑 This code NEVER runs because battery (10) < 15
        # AND the device is offline.
        hardware_driver.move_to(zone)
```

The Agent receives this structured rejection (instead of crashing):

```json
{
  "status": "error",
  "code": "SAFETY_BLOCK",
  "reason": "Operation blocked: Battery level 10% is below minimum threshold 15%. Device is OFFLINE.",
  "suggestion": "Charge device and re-establish BLE connection."
}
```

## 🏛️ Architecture

SilverAi acts as the "Prefrontal Cortex" for your Agent—a logical check before impulsive actions.

```mermaid
graph LR
    A[User Request] --> B[LLM / Agent]
    B -->|Unsafe Intent| C{SilverAi Guard}
    C -- Fails Rules --> D[Block & Explain]
    D -->|Feedback Loop| B
    C -- Passes Rules --> E[Execute Hardware API]
```

## 🎯 Domain Modules

While architected for Robotics, the state machine of SilverAi is universal.

### 🤖 IoT / Robotics Module

```python
@guard(rules.MaxTemp(80), rules.DeviceIdle())
def firmware_update(self): ...
```

## 💸 FinTech / E-Commerce Module

Prevent Chatbots from hallucinating prices or authorizing large transactions.

```python
@guard(
    rules.TransactionLimit(50.00), 
    rules.AllowedDomains(["official-store.com"])
)
def generate_payment_link(self, amount): ...
```

## 🧪 Simulation & Testing (No Hardware Required)

One of the hardest parts of IoT development is testing failure states (e.g., "What happens if the battery dies halfway?"). SilverAi provides a DryRun harness to test safety logic instantly.

```python
from silver_ai.test import DryRun
from my_robot import IndustrialRobot

def test_safety_stops_low_battery():
    # 1. Mock a dangerous state
    dangerous_state = {"battery": 5, "connection": "online"}
    
    # 2. Run the function in "Dry Run" mode (skips real hardware)
    result = DryRun(IndustrialRobot.start_operation, state=dangerous_state)
    
    # 3. Assert that SilverAi caught it
    assert result['success'] is False
    assert "Battery" in result['reason']
```

## 🤝 Contributing
We welcome your contributions! Bug reports and feature suggestions are encouraged. 
Open issues or submit pull requests via [Project Issues](https://github.com/gcl-team/SilverAi/issues).