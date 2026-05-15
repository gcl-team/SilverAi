from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import sys
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
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
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

        normalized = parsed._replace(path=parsed.path.rstrip("/"))
        if normalized.path.endswith("/v1"):
            normalized = normalized._replace(path=normalized.path[:-3])

        return f"{normalized.geturl().rstrip('/')}/v1/chat/completions"

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
        try:
            return json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError, OverflowError):
            return json.dumps(
                PlannerClient._make_json_safe(payload),
                sort_keys=True,
                default=str,
            )

    @staticmethod
    def _make_json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): PlannerClient._make_json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [PlannerClient._make_json_safe(item) for item in value]
        if isinstance(value, set):
            return [
                PlannerClient._make_json_safe(item)
                for item in sorted(value, key=str)
            ]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            max_digits = sys.get_int_max_str_digits()
            if max_digits > 0 and value.bit_length() > int(max_digits * 3.322):
                return f"<int:{value.bit_length()} bits>"
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        return value

    @staticmethod
    def _validate_non_negative_weight(value: float, raw_weight: Any) -> float:
        if not math.isfinite(value):
            raise ValueError(f"package_weight must be finite: {raw_weight}")
        if value < 0:
            raise ValueError(f"package_weight must be non-negative: {raw_weight}")
        return value

    @staticmethod
    def _qualitative_weight_from_text(text: str) -> float | None:
        qualitative_map = {
            "tiny": 0.25,
            "light": 0.75,
            "small": 1.0,
            "medium": 5.0,
            "large": 12.0,
            "heavy": 20.0,
        }
        tokens = re.findall(r"[a-z]+", text)
        for token in tokens:
            if token in qualitative_map:
                return qualitative_map[token]
        return None

    def _coerce_package_weight(self, raw_weight: Any) -> float:
        if isinstance(raw_weight, bool):
            raise ValueError(
                f"package_weight must be numeric, not boolean: {raw_weight}"
            )

        if isinstance(raw_weight, (int, float)):
            return self._validate_non_negative_weight(float(raw_weight), raw_weight)

        text = str(raw_weight).strip().lower()
        # Support decimal and scientific notation (e.g. 1e3, -2.5e-1).
        # Reject ambiguous strings containing multiple numeric tokens.
        pattern = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
        matches = list(re.finditer(pattern, text))
        if not matches:
            qualitative_weight = self._qualitative_weight_from_text(text)
            if qualitative_weight is not None:
                return qualitative_weight

            raise ValueError(f"Could not parse package_weight: {raw_weight}")

        if len(matches) > 1:
            raise ValueError(
                "Ambiguous package_weight: multiple numeric tokens found "
                f"in {raw_weight!r}."
            )

        value = float(matches[0].group(0))

        # Infer units from alphabetic tokens outside the numeric span.
        # This avoids treating scientific-notation exponent markers (e/E)
        # as unit tokens. Bare numbers default to kilograms.
        number_span = matches[0].span()
        text_without_number = f"{text[: number_span[0]]} {text[number_span[1] :]}"
        unit_tokens = re.findall(r"[a-z]+", text_without_number)
        if not unit_tokens:
            return self._validate_non_negative_weight(value, raw_weight)

        supported_units = {
            "kg",
            "kgs",
            "kilogram",
            "kilograms",
            "g",
            "gram",
            "grams",
            "lb",
            "lbs",
            "pound",
            "pounds",
            "oz",
            "ounce",
            "ounces",
        }
        matched_units = [token for token in unit_tokens if token in supported_units]
        unit_category = {
            "kg": "kg",
            "kgs": "kg",
            "kilogram": "kg",
            "kilograms": "kg",
            "g": "g",
            "gram": "g",
            "grams": "g",
            "lb": "lb",
            "lbs": "lb",
            "pound": "lb",
            "pounds": "lb",
            "oz": "oz",
            "ounce": "oz",
            "ounces": "oz",
        }

        if not matched_units:
            # If text contains words but none are recognized units, preserve
            # previous qualitative-label behavior and otherwise fail closed.
            if self._qualitative_weight_from_text(text_without_number) is not None:
                return self._validate_non_negative_weight(value, raw_weight)
            raise ValueError(
                "Unsupported package_weight unit. "
                "Use kg, g, lb, or oz (and common singular/plural forms)."
            )

        matched_categories = {unit_category[token] for token in matched_units}
        if len(matched_categories) > 1:
            raise ValueError(
                "Ambiguous package_weight unit: conflicting unit tokens found "
                f"in {raw_weight!r}."
            )

        if any(token in {"g", "gram", "grams"} for token in matched_units):
            value /= 1000.0
        elif any(token in {"lb", "lbs", "pound", "pounds"} for token in matched_units):
            value *= 0.45359237
        elif any(token in {"oz", "ounce", "ounces"} for token in matched_units):
            value *= 0.028349523125

        return self._validate_non_negative_weight(value, raw_weight)

    def request(self, prompt: str) -> Dict[str, Any]:
        trace: Dict[str, Any] = {
            "source": "openai-compatible endpoint",
            "base_url": self.base_url,
            "model": self.model,
            "prompt_preview": prompt[:200],
        }

        try:
            endpoint = self._build_openai_endpoint()
            trace["endpoint"] = endpoint
        except ValueError as exc:
            trace["error"] = str(exc)
            self.trace_log.append(trace)
            return {
                "status": "planner_error",
                "source": "openai-compatible endpoint",
                "reason": f"OpenAI-compatible endpoint request failed: {exc}",
                "suggestion": (
                    "Set a valid OPENAI_BASE_URL and, for remote hosts, "
                    "enable OPENAI_ALLOW_REMOTE=1 with HTTPS."
                ),
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
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(  # noqa: S310
            endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(  # noqa: S310
                req,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
                trace["http_status"] = getattr(response, "status", "unknown")
                if self.raw_preview_chars <= 0:
                    trace["raw_response_preview"] = raw
                else:
                    trace["raw_response_preview"] = raw[: self.raw_preview_chars]
        except (error.URLError, TimeoutError, UnicodeDecodeError, ValueError) as exc:
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
