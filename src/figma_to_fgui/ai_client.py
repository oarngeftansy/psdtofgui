from __future__ import annotations

import base64
import json
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from threading import BoundedSemaphore
from typing import Literal, cast

import httpx
from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator

from figma_to_fgui.models import FrozenModel
from figma_to_fgui.semantic_models import SemanticResponse

MAX_SUMMARY_NODES = 500
MAX_SUMMARY_DEPTH = 32
MAX_STRING_BYTES = 512
MAX_SUMMARY_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 1_500 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_SCREENSHOT_BYTES = 1_000 * 1024
_RETRY_BACKOFF_BASE_SECONDS = 0.25
_RETRY_BACKOFF_MAX_SECONDS = 2.0

_SYSTEM_PROMPT = (
    "Classify only the supplied Figma node structure. Return a JSON object matching the semantic "
    "response schema: version=1, decisions with node_id, semantic_type, confidence, and optional "
    "fgui_name, children_roles, state_pages, reparent, risks, screenshot_recommended, "
    "screenshot_reason. Do not invent node ids."
)

_LOGGER = logging.getLogger(__name__)


class AIClientConfig(FrozenModel):
    provider: Literal["openai", "openai_compatible"]
    base_url: AnyHttpUrl
    model: str = Field(min_length=1, max_length=120)
    api_key: SecretStr
    timeout_seconds: float = Field(default=20, ge=1, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_concurrency: int = Field(default=4, ge=1, le=32)

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("AI base URL must use HTTPS")
        return value


class AIReasonCode(StrEnum):
    DISABLED = "ai.disabled"
    HTTP_STATUS = "ai.http_status"
    INTERNAL = "ai.internal"
    REQUEST_INVALID = "ai.request_invalid"
    REQUEST_TOO_LARGE = "ai.request_too_large"
    RESPONSE_JSON = "ai.response_json"
    RESPONSE_SCHEMA = "ai.response_schema"
    RESPONSE_TOO_LARGE = "ai.response_too_large"
    RESPONSE_VALIDATION = "ai.response_validation"
    SCREENSHOT_TOO_LARGE = "ai.screenshot_too_large"
    TIMEOUT = "ai.timeout"
    TRANSPORT = "ai.transport"


class AIAnalysisError(Exception):
    """A safe, stable error that intentionally excludes remote and user-supplied content."""

    def __init__(self, code: AIReasonCode) -> None:
        self.code = code if isinstance(code, AIReasonCode) else AIReasonCode.INTERNAL
        super().__init__(f"AI semantic analysis failed ({self.code})")


class _RetryableRequest(Exception):
    def __init__(self, code: AIReasonCode) -> None:
        self.code = code


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AIAnalysisError(AIReasonCode.REQUEST_INVALID) from None


def _check_summary_value(value: object) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 0, False)]
    active_containers: set[int] = set()
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        if depth > MAX_SUMMARY_DEPTH:
            raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
        if isinstance(current, str):
            try:
                encoded_length = len(current.encode("utf-8"))
            except UnicodeError:
                raise AIAnalysisError(AIReasonCode.REQUEST_INVALID) from None
            if encoded_length > MAX_STRING_BYTES:
                raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
            continue
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active_containers:
                raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
            active_containers.add(identity)
            stack.append((current, depth, True))
            for key, item in current.items():
                if not isinstance(key, str):
                    raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
                try:
                    encoded_key_length = len(key.encode("utf-8"))
                except UnicodeError:
                    raise AIAnalysisError(AIReasonCode.REQUEST_INVALID) from None
                if encoded_key_length > MAX_STRING_BYTES:
                    raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
                stack.append((item, depth + 1, False))
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (bytes, bytearray, memoryview)
        ):
            identity = id(current)
            if identity in active_containers:
                raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
            active_containers.add(identity)
            stack.append((current, depth, True))
            for item in current:
                stack.append((item, depth + 1, False))
            continue
        if current is None or isinstance(current, bool | int):
            continue
        if isinstance(current, float) and math.isfinite(current):
            continue
        raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)


def _validated_summary_json(summary: dict[str, object]) -> str:
    nodes = summary.get("nodes")
    if not isinstance(nodes, list) or len(nodes) > MAX_SUMMARY_NODES:
        raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
    _check_summary_value(summary)
    encoded = _canonical_json(summary)
    if len(encoded) > MAX_SUMMARY_BYTES:
        raise AIAnalysisError(AIReasonCode.REQUEST_TOO_LARGE)
    return encoded.decode("utf-8")


def build_chat_completion_payload(
    model: str, summary: dict[str, object], screenshot: bytes | None = None
) -> dict[str, object]:
    """Create the smallest compatible chat-completions request for structured semantics."""
    if not model or len(model) > 120:
        raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
    summary_json = _validated_summary_json(summary)
    user_content: str | list[dict[str, object]] = summary_json
    if screenshot is not None:
        if len(screenshot) > MAX_SCREENSHOT_BYTES:
            raise AIAnalysisError(AIReasonCode.SCREENSHOT_TOO_LARGE)
        user_content = [
            {"type": "text", "text": summary_json},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii")
                },
            },
        ]
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    if len(_canonical_json(payload)) > MAX_REQUEST_BYTES:
        raise AIAnalysisError(AIReasonCode.REQUEST_TOO_LARGE)
    return payload


def _extract_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise AIAnalysisError(AIReasonCode.RESPONSE_SCHEMA)
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise AIAnalysisError(AIReasonCode.RESPONSE_SCHEMA)
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise AIAnalysisError(AIReasonCode.RESPONSE_SCHEMA)
    return cast(str, message["content"])


class OpenAICompatibleSemanticClient:
    def __init__(
        self,
        config: AIClientConfig,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._request_slots = BoundedSemaphore(config.max_concurrency)
        self.http = httpx.Client(
            base_url=str(config.base_url),
            transport=transport,
            timeout=httpx.Timeout(config.timeout_seconds),
            follow_redirects=False,
        )

    def close(self) -> None:
        self.http.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
            "Accept": "application/json",
        }

    def analyze(
        self, summary: dict[str, object], screenshot: bytes | None = None
    ) -> SemanticResponse:
        if not self._request_slots.acquire(timeout=self.config.timeout_seconds):
            error = AIAnalysisError(AIReasonCode.TIMEOUT)
            _LOGGER.warning("ai.semantic_request outcome=fallback reason=%s", error.code)
            raise error
        try:
            try:
                result = self._analyze(summary, screenshot)
            except AIAnalysisError as error:
                _LOGGER.warning("ai.semantic_request outcome=fallback reason=%s", error.code)
                raise
        finally:
            self._request_slots.release()
        _LOGGER.info("ai.semantic_request outcome=success")
        return result

    def _retry_delay(self, retry: int) -> float:
        return min(
            _RETRY_BACKOFF_BASE_SECONDS * float(2**retry),
            _RETRY_BACKOFF_MAX_SECONDS,
        )

    def _request(self, payload: dict[str, object]) -> bytes:
        for attempt in range(self.config.max_retries + 1):
            retry_reason: AIReasonCode
            try:
                with self.http.stream(
                    "POST", "/chat/completions", json=payload, headers=self._headers()
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        if response.status_code == 429 or response.status_code >= 500:
                            raise _RetryableRequest(AIReasonCode.HTTP_STATUS)
                        raise AIAnalysisError(AIReasonCode.HTTP_STATUS)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise AIAnalysisError(AIReasonCode.RESPONSE_TOO_LARGE)
                return bytes(body)
            except _RetryableRequest as error:
                retry_reason = error.code
            except httpx.TimeoutException:
                retry_reason = AIReasonCode.TIMEOUT
            except httpx.HTTPError:
                retry_reason = AIReasonCode.TRANSPORT
            except (ValueError, UnicodeError):
                retry_reason = AIReasonCode.TRANSPORT
            if attempt >= self.config.max_retries:
                raise AIAnalysisError(retry_reason) from None
            self._sleep(self._retry_delay(attempt))
        raise AIAnalysisError(AIReasonCode.INTERNAL)

    def _analyze(
        self, summary: dict[str, object], screenshot: bytes | None = None
    ) -> SemanticResponse:
        payload = build_chat_completion_payload(self.config.model, summary, screenshot)
        body = self._request(payload)
        try:
            response_payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise AIAnalysisError(AIReasonCode.RESPONSE_JSON) from None
        try:
            return SemanticResponse.model_validate_json(_extract_content(response_payload))
        except AIAnalysisError:
            raise
        except (ValidationError, ValueError, TypeError, UnicodeDecodeError, RecursionError):
            raise AIAnalysisError(AIReasonCode.RESPONSE_VALIDATION) from None
