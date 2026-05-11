from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib import error, request
from urllib.parse import urlparse

from silver_ai import rules
from silver_ai.core import guard


class MaxLoad:
    """Fail if current belt load exceeds allowed limit."""

    def __init__(self, max_load: float):
        self.max_load = float(max_load)

    def check(self, state: Dict[str, Any]) -> bool:
        current = float(state.get("belt_load", 0.0))
        return current <= self.max_load

    def violation_message(self, state: Dict[str, Any]) -> str:
        current = float(state.get("belt_load", 0.0))
        return f"Belt overload: {current:.2f}. Limit: {self.max_load:.2f}."

    def suggestion(self) -> str:
        return "Re-route to a lower-load belt or wait for load to drop."


class StateGate:
    """Generic state threshold gate for numeric telemetry keys."""

    def __init__(self, key: str, *, max_value: Optional[float] = None):
        self.key = key
        self.max_value = max_value

    def check(self, state: Dict[str, Any]) -> bool:
        if self.max_value is None:
            return True

        raw = state.get(self.key, 999.0)
        value = float(raw)
        return value <= float(self.max_value)

    def violation_message(self, state: Dict[str, Any]) -> str:
        raw = state.get(self.key, 999.0)
        value = float(raw)
        limit = 0.0 if self.max_value is None else float(self.max_value)
        return f"State gate blocked: {self.key}={value:.2f}. " f"Limit: {limit:.2f}."

    def suggestion(self) -> str:
        return "Wait for telemetry to return to safe limits before retrying."


class SorterAgent:
    DEFAULT_BASE_URL = "http://127.0.0.1:1234"
    DEFAULT_MODEL = "google/gemma-4-e4b"
    DEFAULT_TIMEOUT_SECONDS = 20.0
    DEFAULT_RAW_PREVIEW_CHARS = 500
    DEFAULT_PARSED_PREVIEW_CHARS = 300

    def __init__(self, gateway: Any):
        self.gateway = gateway
        self.state = gateway.state
        self.base_url = os.getenv("OPENAI_BASE_URL", self.DEFAULT_BASE_URL)
        self.model = self.DEFAULT_MODEL
        self.timeout_seconds = self.DEFAULT_TIMEOUT_SECONDS
        self.raw_preview_chars = self.DEFAULT_RAW_PREVIEW_CHARS
        self.parsed_preview_chars = self.DEFAULT_PARSED_PREVIEW_CHARS
        self.allow_remote_openai = os.getenv("OPENAI_ALLOW_REMOTE", "0") == "1"
        self.planner_trace_log: List[Dict[str, Any]] = []

    def _build_openai_endpoint(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()

        if scheme not in {"http", "https"}:
            raise ValueError("OPENAI_BASE_URL must use http or https.")

        if not self.allow_remote_openai and not self._is_localhost(hostname):
            raise ValueError(
                "OPENAI_BASE_URL must point to localhost unless "
                "OPENAI_ALLOW_REMOTE=1 is set."
            )

        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    @staticmethod
    def _is_localhost(hostname: str) -> bool:
        if hostname in {"localhost", "::1"}:
            return True

        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return hostname.endswith(".localhost")

    def _extract_planner_json(self, content: str) -> Dict[str, Any]:
        text = content.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue

            try:
                candidate, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue

            if isinstance(candidate, dict):
                return candidate

        return json.loads(text)

    def _coerce_package_weight(self, raw_weight: Any) -> float:
        if isinstance(raw_weight, (int, float)):
            return float(raw_weight)

        text = str(raw_weight).strip().lower()
        match = re.search(r"[-+]?\d*\.?\d+", text)
        if match is None:
            qualitative_map = {
                "tiny": 0.25,
                "light": 0.75,
                "small": 1.0,
                "medium": 5.0,
                "large": 12.0,
                "heavy": 20.0,
            }
            for label, weight in qualitative_map.items():
                if label in text:
                    return weight

            raise ValueError(f"Could not parse package_weight: {raw_weight}")

        value = float(match.group(0))
        if "lb" in text or "lbs" in text or "pound" in text:
            return value * 0.45359237

        return value

    def _planner_request(self, prompt: str) -> Dict[str, Any]:
        endpoint = self._build_openai_endpoint()
        trace: Dict[str, Any] = {
            "source": "openai-compatible endpoint",
            "endpoint": endpoint,
            "model": self.model,
            "prompt_preview": prompt[:200],
        }
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a warehouse sorter planner. Return only raw JSON "
                        "with keys route, package_weight, reason. Do not use markdown "
                        "code fences."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(  # noqa: S310
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
                trace["http_status"] = getattr(response, "status", "unknown")
                if self.raw_preview_chars <= 0:
                    trace["raw_response_preview"] = raw
                else:
                    trace["raw_response_preview"] = raw[: self.raw_preview_chars]
        except (error.URLError, TimeoutError, ValueError) as exc:
            trace["error"] = str(exc)
            self.planner_trace_log.append(trace)
            return {
                "status": "planner_error",
                "source": "openai-compatible endpoint",
                "reason": f"OpenAI-compatible endpoint request failed: {exc}",
                "suggestion": (
                    "Ensure the endpoint is reachable and the model is loaded."
                ),
            }

        try:
            parsed = json.loads(raw)
            content = parsed["choices"][0]["message"]["content"]
            as_json = self._extract_planner_json(str(content))
            coerced_weight = self._coerce_package_weight(as_json["package_weight"])
            if self.parsed_preview_chars <= 0:
                trace["parsed_content_preview"] = content
            else:
                trace["parsed_content_preview"] = content[: self.parsed_preview_chars]
            trace["coerced_package_weight"] = coerced_weight
            self.planner_trace_log.append(trace)
            return {
                "status": "ok",
                "source": "openai-compatible endpoint",
                "route": str(as_json["route"]),
                "package_weight": coerced_weight,
                "reason": str(as_json["reason"]),
            }
        except (
            KeyError,
            IndexError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            trace["parse_error"] = str(exc)
            self.planner_trace_log.append(trace)
            return {
                "status": "planner_error",
                "source": "openai-compatible endpoint",
                "reason": f"Invalid planner response: {exc}",
                "suggestion": (
                    "Return raw JSON with route, package_weight, reason. "
                    "Weight can be numeric or include units."
                ),
            }

    @guard(
        rules=[
            MaxLoad(100.0),
            rules.BatteryMin(20),
            StateGate("motor_temp", max_value=80.0),
        ]
    )
    def _execute_guarded(
        self,
        package_id: str,
        route: str,
        package_weight: float,
    ) -> Dict[str, Any]:
        return self.gateway.execute_sort_command(
            package_id=package_id,
            route=route,
            package_weight=package_weight,
        )

    def propose_and_execute(
        self,
        package_id: str,
        package_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = (
            f"Package id: {package_id}. "
            f"Metadata: {json.dumps(package_metadata, sort_keys=True)}. "
            "Choose a safe sorting route."
        )
        planned = self._planner_request(prompt)
        if planned.get("status") != "ok":
            return planned

        result = self._execute_guarded(
            package_id,
            planned["route"],
            float(planned["package_weight"]),
        )

        if result.get("status") != "error":
            return {
                "status": "success",
                "plan": planned,
                "execution": result,
            }

        feedback_prompt = (
            f"Previous plan blocked. Reason: {result.get('reason')}. "
            f"Suggestion: {result.get('suggestion')}. "
            f"Telemetry: {json.dumps(self.gateway.snapshot(), sort_keys=True)}. "
            "Propose one alternative route as strict JSON."
        )

        replanned = self._planner_request(feedback_prompt)
        if replanned.get("status") != "ok":
            return {
                "status": "blocked",
                "first_plan": planned,
                "block": result,
                "replan": replanned,
            }

        retry_result = self._execute_guarded(
            package_id,
            replanned["route"],
            float(replanned["package_weight"]),
        )
        final_status = "success" if retry_result.get("status") != "error" else "blocked"
        return {
            "status": final_status,
            "first_plan": planned,
            "block": result,
            "replan": replanned,
            "execution": retry_result,
        }
