from __future__ import annotations

import ipaddress
import json
import math
import os
import re
from typing import Any, Dict, List
from urllib import error, request
from urllib.parse import urlparse


class PlannerClient:
    """OpenAI-compatible HTTP client for the warehouse sorter planner."""

    DEFAULT_BASE_URL = "http://127.0.0.1:1234"
    DEFAULT_MODEL = "google/gemma-4-e4b"
    DEFAULT_TIMEOUT_SECONDS = 20.0
    DEFAULT_RAW_PREVIEW_CHARS = 500
    DEFAULT_PARSED_PREVIEW_CHARS = 300

    @staticmethod
    def _int_env(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def __init__(self) -> None:
        self.base_url = os.getenv("OPENAI_BASE_URL", self.DEFAULT_BASE_URL)
        self.model = self.DEFAULT_MODEL
        self.timeout_seconds = self.DEFAULT_TIMEOUT_SECONDS
        self.raw_preview_chars = self._int_env(
            "OPENAI_RAW_PREVIEW_CHARS", self.DEFAULT_RAW_PREVIEW_CHARS
        )
        self.parsed_preview_chars = self._int_env(
            "OPENAI_PARSED_PREVIEW_CHARS", self.DEFAULT_PARSED_PREVIEW_CHARS
        )
        self.allow_remote_openai = os.getenv("OPENAI_ALLOW_REMOTE", "0") == "1"
        self.trace_log: List[Dict[str, Any]] = []

    def _build_openai_endpoint(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        is_localhost = self._is_localhost(hostname)

        if scheme not in {"http", "https"}:
            raise ValueError("OPENAI_BASE_URL must use http or https.")

        if not self.allow_remote_openai and not is_localhost:
            raise ValueError(
                "OPENAI_BASE_URL must point to localhost unless "
                "OPENAI_ALLOW_REMOTE=1 is set."
            )

        if self.allow_remote_openai and not is_localhost and scheme != "https":
            raise ValueError(
                "Remote OPENAI_BASE_URL must use https when "
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
            # Accept fenced payloads in both multi-line and single-line forms,
            # with an optional language tag (e.g. ```json ... ```).
            fenced = re.match(
                r"^```(?:[A-Za-z0-9_-]+)?\s*(.*?)\s*```$",
                text,
                re.DOTALL,
            )
            if fenced:
                text = fenced.group(1).strip()
            else:
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

    @staticmethod
    def safe_json_dumps(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, sort_keys=True, default=str)

    @staticmethod
    def _validate_non_negative_weight(value: float, raw_weight: Any) -> float:
        if not math.isfinite(value):
            raise ValueError(f"package_weight must be finite: {raw_weight}")
        if value < 0:
            raise ValueError(f"package_weight must be non-negative: {raw_weight}")
        return value

    def _coerce_package_weight(self, raw_weight: Any) -> float:
        if isinstance(raw_weight, (int, float)):
            return self._validate_non_negative_weight(float(raw_weight), raw_weight)

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
            value *= 0.45359237

        return self._validate_non_negative_weight(value, raw_weight)

    def request(self, prompt: str) -> Dict[str, Any]:
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
            self.trace_log.append(trace)
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
            self.trace_log.append(trace)
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
            self.trace_log.append(trace)
            return {
                "status": "planner_error",
                "source": "openai-compatible endpoint",
                "reason": f"Invalid planner response: {exc}",
                "suggestion": (
                    "Return raw JSON with route, package_weight, reason. "
                    "Weight can be numeric or include units."
                ),
            }
