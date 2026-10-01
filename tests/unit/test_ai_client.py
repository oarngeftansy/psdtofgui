from __future__ import annotations

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from figma_to_fgui.ai_client import (
    MAX_SUMMARY_DEPTH,
    AIAnalysisError,
    AIClientConfig,
    AIReasonCode,
    OpenAICompatibleSemanticClient,
    build_chat_completion_payload,
)


def _config(
    provider: str = "openai_compatible",
    *,
    max_retries: int = 0,
    max_concurrency: int = 4,
) -> AIClientConfig:
    return AIClientConfig(
        provider=provider,
        base_url="https://ai.example.test/v1",
        model="semantic-model",
        api_key=SecretStr("secret-value"),
        max_retries=max_retries,
        max_concurrency=max_concurrency,
    )


def _valid_response() -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "version": 1,
                            "decisions": [
                                {
                                    "node_id": "button",
                                    "semantic_type": "Button",
                                    "confidence": 0.9,
                                }
                            ],
                        }
                    )
                }
            }
        ]
    }


def test_client_uses_bearer_token_without_exposing_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_valid_response())

    client = OpenAICompatibleSemanticClient(_config(), transport=httpx.MockTransport(handler))

    result = client.analyze({"version": 1, "nodes": []})

    assert result.version == 1
    assert requests[0].headers["Authorization"] == "Bearer secret-value"
    body = json.loads(requests[0].content)
    assert body["model"] == "semantic-model"
    assert requests[0].url.path == "/v1/chat/completions"


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_failures_raise_redacted_error(status: int) -> None:
    client = OpenAICompatibleSemanticClient(
        _config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="server included secret-value")
        ),
    )

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"version": 1, "nodes": []})

    assert error.value.code == "ai.http_status"
    assert "secret-value" not in str(error.value)


def test_transport_and_invalid_response_fail_with_stable_redacted_codes() -> None:
    transport_client = OpenAICompatibleSemanticClient(
        _config(),
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("secret-value"))),
    )
    response_client = OpenAICompatibleSemanticClient(
        _config(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not json secret-value")),
    )

    with pytest.raises(AIAnalysisError, match="ai.transport") as transport_error:
        transport_client.analyze({"version": 1, "nodes": []})
    with pytest.raises(AIAnalysisError, match="ai.response_json") as response_error:
        response_client.analyze({"version": 1, "nodes": []})

    assert "secret-value" not in str(transport_error.value)
    assert "secret-value" not in str(response_error.value)


def test_client_rejects_oversized_summary_response_and_screenshot() -> None:
    client = OpenAICompatibleSemanticClient(
        _config(), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 300_000))
    )

    with pytest.raises(AIAnalysisError, match="ai.request_invalid"):
        client.analyze({"nodes": ["x" * 1_000]})
    with pytest.raises(AIAnalysisError, match="ai.response_too_large"):
        client.analyze({"version": 1, "nodes": []})
    with pytest.raises(AIAnalysisError, match="ai.screenshot_too_large"):
        client.analyze({"version": 1, "nodes": []}, screenshot=b"x" * 2_000_001)


def test_payload_is_minimal_and_only_embeds_screenshot_when_present() -> None:
    without_screenshot = build_chat_completion_payload("model", {"version": 1, "nodes": []})
    with_screenshot = build_chat_completion_payload("model", {"version": 1, "nodes": []}, b"png")

    assert without_screenshot["messages"][1]["content"] == '{"nodes":[],"version":1}'
    assert without_screenshot["response_format"] == {"type": "json_object"}
    assert isinstance(with_screenshot["messages"][1]["content"], list)


@pytest.mark.parametrize("bad_string", ["\ud800", "prefix\udfff"])
@pytest.mark.parametrize("location", ["key", "value"])
def test_payload_rejects_isolated_surrogates_with_stable_error(
    bad_string: str, location: str
) -> None:
    node = {bad_string: "safe"} if location == "key" else {"name": bad_string}

    with pytest.raises(AIAnalysisError) as error:
        build_chat_completion_payload("model", {"nodes": [node]})

    assert error.value.code is AIReasonCode.REQUEST_INVALID
    assert bad_string not in str(error.value)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_payload_rejects_non_finite_numbers(non_finite: float) -> None:
    with pytest.raises(AIAnalysisError) as error:
        build_chat_completion_payload("model", {"nodes": [{"x": non_finite}]})

    assert error.value.code is AIReasonCode.REQUEST_INVALID


def test_config_rejects_plain_http_before_sending_the_key() -> None:
    requests: list[httpx.Request] = []

    with pytest.raises(ValidationError):
        config = AIClientConfig(
            provider="openai",
            base_url="http://ai.example.test/v1",
            model="model",
            api_key=SecretStr("must-not-be-sent"),
        )
        OpenAICompatibleSemanticClient(
            config,
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200)
            ),
        )

    assert requests == []


@pytest.mark.parametrize("provider", ["openai", "openai_compatible"])
def test_supported_providers_use_the_compatible_endpoint(provider: str) -> None:
    requests: list[httpx.Request] = []
    client = OpenAICompatibleSemanticClient(
        _config(provider),
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json=_valid_response())
        ),
    )

    assert client.analyze({"nodes": []}).version == 1
    assert requests[0].url.path == "/v1/chat/completions"


def test_timeout_has_a_stable_reason_code() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout details", request=request)

    client = OpenAICompatibleSemanticClient(_config(), transport=httpx.MockTransport(timeout))

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.TIMEOUT
    assert "private timeout details" not in str(error.value)


def test_invalid_semantic_response_schema_has_a_stable_reason_code() -> None:
    remote = {"choices": [{"message": {"content": '{"version":1,"decisions":[{}]}'}}]}
    client = OpenAICompatibleSemanticClient(
        _config(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=remote))
    )

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.RESPONSE_VALIDATION


@pytest.mark.parametrize("exception", [ValueError("private value"), UnicodeError("private unicode")])
def test_transport_value_errors_are_redacted(exception: Exception) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exception

    client = OpenAICompatibleSemanticClient(_config(), transport=httpx.MockTransport(fail))

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.TRANSPORT
    assert "private" not in str(error.value)


@pytest.mark.parametrize("container_type", ["dict", "list"])
def test_payload_rejects_self_referential_containers(container_type: str) -> None:
    if container_type == "dict":
        cycle: dict[str, object] | list[object] = {}
        cycle["self"] = cycle
        summary = {"nodes": [cycle]}
    else:
        cycle = []
        cycle.append(cycle)
        summary = {"nodes": cycle}

    with pytest.raises(AIAnalysisError) as error:
        build_chat_completion_payload("model", summary)

    assert error.value.code is AIReasonCode.REQUEST_INVALID
    assert "cycle" not in str(error.value).lower()


def test_payload_rejects_summary_beyond_explicit_depth_limit() -> None:
    nested: object = "leaf"
    for _ in range(MAX_SUMMARY_DEPTH + 1):
        nested = {"child": nested}

    with pytest.raises(AIAnalysisError) as error:
        build_chat_completion_payload("model", {"nodes": [nested]})

    assert error.value.code is AIReasonCode.REQUEST_INVALID


def test_overdeep_response_json_has_a_stable_redacted_reason() -> None:
    overdeep_json = "[" * 5_000 + '"private response"' + "]" * 5_000
    assert len(overdeep_json.encode("utf-8")) < 256 * 1024
    client = OpenAICompatibleSemanticClient(
        _config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=overdeep_json.encode("utf-8"))
        ),
    )

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.RESPONSE_JSON
    assert "private response" not in str(error.value)


@pytest.mark.parametrize("response_kind", ["success", "429", "invalid_json", "500"])
def test_ai_request_logs_never_contain_sensitive_request_or_response_content(
    response_kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    response_marker = "RAW_MODEL_RESPONSE_PRIVATE"
    screenshot = b"SCREENSHOT_BYTES_PRIVATE"

    def handler(_request: httpx.Request) -> httpx.Response:
        if response_kind == "success":
            payload = _valid_response()
            payload["choices"][0]["message"]["content"] = json.dumps(
                {
                    "version": 1,
                    "decisions": [],
                    "screenshot_recommended": True,
                    "screenshot_reason": response_marker,
                }
            )
            return httpx.Response(200, json=payload)
        if response_kind == "invalid_json":
            return httpx.Response(200, text=f"not-json-{response_marker}")
        return httpx.Response(int(response_kind), text=response_marker)

    client = OpenAICompatibleSemanticClient(
        _config(), transport=httpx.MockTransport(handler)
    )
    caplog.set_level(logging.INFO, logger="figma_to_fgui.ai_client")
    summary = {
        "version": 1,
        "nodes": [{"id": "private-id", "name": "PRIVATE_NODE_TEXT"}],
    }

    if response_kind == "success":
        client.analyze(summary, screenshot=screenshot)
    else:
        with pytest.raises(AIAnalysisError):
            client.analyze(summary, screenshot=screenshot)

    captured = caplog.text
    assert "ai.semantic_request" in captured
    for forbidden in (
        "secret-value",
        "Authorization",
        "Bearer",
        "PRIVATE_NODE_TEXT",
        "private-id",
        "Classify only the supplied Figma node structure",
        "SCREENSHOT_BYTES_PRIVATE",
        base64.b64encode(screenshot).decode("ascii"),
        response_marker,
    ):
        assert forbidden not in captured


@pytest.mark.parametrize("failure", [429, 500, 503, "transport", "timeout"])
def test_retryable_failures_use_bounded_exponential_backoff(
    failure: int | str,
) -> None:
    attempts = 0
    backoffs: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            return httpx.Response(200, json=_valid_response())
        if failure == "transport":
            raise httpx.ConnectError("private transport detail", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        return httpx.Response(int(failure), text="private response detail")

    client = OpenAICompatibleSemanticClient(
        _config(max_retries=2),
        transport=httpx.MockTransport(handler),
        sleep=backoffs.append,
    )

    assert client.analyze({"nodes": []}).version == 1
    assert attempts == 3
    assert backoffs == [0.25, 0.5]


@pytest.mark.parametrize("status", [400, 401, 403, 408])
def test_non_retryable_http_statuses_are_attempted_once(status: int) -> None:
    attempts = 0
    backoffs: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="private response detail")

    client = OpenAICompatibleSemanticClient(
        _config(max_retries=5),
        transport=httpx.MockTransport(handler),
        sleep=backoffs.append,
    )

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.HTTP_STATUS
    assert attempts == 1
    assert backoffs == []


def test_response_schema_failures_are_not_retried() -> None:
    attempts = 0
    backoffs: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"choices": []})

    client = OpenAICompatibleSemanticClient(
        _config(max_retries=5),
        transport=httpx.MockTransport(handler),
        sleep=backoffs.append,
    )

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": []})

    assert error.value.code is AIReasonCode.RESPONSE_SCHEMA
    assert attempts == 1
    assert backoffs == []


def test_exhausted_retries_are_bounded_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    backoffs: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, text="RAW_PRIVATE_RETRY_RESPONSE")

    client = OpenAICompatibleSemanticClient(
        _config(max_retries=2),
        transport=httpx.MockTransport(handler),
        sleep=backoffs.append,
    )
    caplog.set_level(logging.INFO, logger="figma_to_fgui.ai_client")

    with pytest.raises(AIAnalysisError) as error:
        client.analyze({"nodes": [{"name": "PRIVATE_RETRY_NODE"}]})

    assert error.value.code is AIReasonCode.HTTP_STATUS
    assert attempts == 3
    assert backoffs == [0.25, 0.5]
    assert "RAW_PRIVATE_RETRY_RESPONSE" not in caplog.text
    assert "PRIVATE_RETRY_NODE" not in caplog.text


def test_shared_concurrency_limit_bounds_structure_and_screenshot_requests() -> None:
    active = 0
    peak = 0
    entered = Event()
    release = Event()
    lock = Lock()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
        assert release.wait(timeout=5)
        with lock:
            active -= 1
        return httpx.Response(200, json=_valid_response())

    client = OpenAICompatibleSemanticClient(
        _config(max_concurrency=2),
        transport=httpx.MockTransport(handler),
    )
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(
                client.analyze,
                {"nodes": []},
                b"png" if index % 2 else None,
            )
            for index in range(6)
        ]
        assert entered.wait(timeout=5)
        assert peak == 2
        release.set()
        assert all(future.result(timeout=5).version == 1 for future in futures)

    assert peak == 2
