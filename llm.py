"""The one LLM interface every AI feature calls.

Features call `complete(...)` and never a provider SDK directly. The provider
lives behind an adapter chosen by LLM_PROVIDER: `lmstudio` (default; LM Studio
over its OpenAI-compatible API) or `anthropic` (the Claude API, always hosted).
Every call — success or failure — is traced via `tracing.record`.

`/no_think` (D30): Qwen-only, so adapters strip a leading `/no_think` line from
the system prompt for other model families (LM Studio by model ID, Anthropic
always). Templates and their hashes don't change; `raw_request` shows what was sent.

Hosted guard (D22): every adapter declares `hosted`. A call to a hosted
adapter — for LM Studio, any LLM_BASE_URL whose host isn't loopback — is
refused before anything is sent unless RUNWAY_LAB=1, and the refusal is
traced. Lab data must be synthetic (D6).

Tests swap the provider with `monkeypatch.setattr(llm, "adapter", fake)`.
"""
import ipaddress
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

import anthropic
import openai
from pydantic import BaseModel

import tracing

# Chat messages in OpenAI shape: [{"role": ..., "content": ...}, ...].
Messages = list[dict[str, Any]]

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen3-8b"
DEFAULT_PROVIDER = "lmstudio"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
NO_THINK = "/no_think\n"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMUnavailable(Exception):
    """The provider couldn't be reached (not running, connection refused, timeout)."""


class HostedCallBlocked(LLMUnavailable):
    """The adapter is hosted and RUNWAY_LAB isn't 1, so nothing was sent."""


class LLMRefusal(ValueError):
    """The model declined to answer (Anthropic `stop_reason: refusal`). Traced, not a crash."""


def is_loopback_url(url: str) -> bool:
    """True if `url`'s host is this machine. Anything unparseable counts as hosted."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def lab_mode() -> bool:
    """RUNWAY_LAB=1 allows hosted calls. Read per call so tests can set it."""
    return os.environ.get("RUNWAY_LAB") == "1"


@dataclass(frozen=True)
class ModelSettings:
    temperature: float = 0.0
    max_tokens: int = 1024
    # LM Studio: recorded, not enforced (thinking is switched off in the prompt
    # itself, Qwen3's `/no_think`). Anthropic: mapped to the `thinking` param.
    thinking: bool = False


@dataclass
class AdapterResponse:
    raw_request: dict
    raw_response: dict
    text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str
    # Set when the model declined; complete() traces the reply, then raises LLMRefusal.
    refusal: str | None = None


@dataclass
class LLMResult:
    parsed: Any
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    model: str
    request_id: str


def strip_think(text: str) -> str:
    """Drop any <think>…</think> reasoning blocks a model emits before its answer."""
    return _THINK_RE.sub("", text).strip()


def strip_no_think(messages: Messages) -> Messages:
    """Drop a leading `/no_think` line from system messages (D30).

    Returns `messages` itself, untouched, when there's nothing to strip.
    """
    if not any(_has_no_think(m) for m in messages):
        return messages
    return [
        {**m, "content": m["content"][len(NO_THINK):]} if _has_no_think(m) else m
        for m in messages
    ]


def _has_no_think(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "system"
        and isinstance(content, str)
        and content.startswith(NO_THINK)
    )


def is_qwen(model: str) -> bool:
    """Qwen-family model IDs are the only ones that understand `/no_think`."""
    return "qwen" in model.lower()


class LMStudioAdapter:
    """LM Studio via its OpenAI-compatible API. Config from LLM_BASE_URL / LLM_MODEL.

    `hosted` comes from the configured LLM_BASE_URL, even when a test injects
    its own `client`.
    """

    def __init__(self, client: openai.OpenAI | None = None, model: str | None = None) -> None:
        base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.hosted: bool = not is_loopback_url(base_url)
        self.client = client or openai.OpenAI(
            base_url=base_url,
            api_key="lm-studio",  # LM Studio ignores it, but the SDK requires one
            timeout=60,
            max_retries=0,  # fail fast when LM Studio isn't running
        )

    def send(
        self, messages: Messages, schema: type[BaseModel] | None, settings: ModelSettings
    ) -> AdapterResponse:
        if not is_qwen(self.model):
            messages = strip_no_think(messages)
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
        }
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        try:
            response = self.client.chat.completions.create(**request)
        except openai.APIConnectionError as e:  # includes APITimeoutError
            raise LLMUnavailable(f"Can't reach LLM at {self.client.base_url}: {e}") from e
        usage = response.usage
        return AdapterResponse(
            raw_request=request,
            raw_response=response.model_dump(),
            text=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            model=response.model or self.model,
        )


class AnthropicAdapter:
    """The Claude API via the official SDK. Model from ANTHROPIC_MODEL.

    Always hosted, so `complete()`'s guard blocks it unless RUNWAY_LAB=1. The
    SDK client (credentials from the environment) is built on the first send,
    so a blocked call never even constructs one.
    """

    hosted: bool = True

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Low retry cap: the SDK retries 429/5xx/connection errors; we add none.
            self._client = anthropic.Anthropic(timeout=60, max_retries=2)
        return self._client

    def send(
        self, messages: Messages, schema: type[BaseModel] | None, settings: ModelSettings
    ) -> AdapterResponse:
        messages = strip_no_think(messages)
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": settings.max_tokens,
            "messages": [m for m in messages if m["role"] != "system"],
            # No temperature: Sonnet 5.5 (and API version 2026-10-21) rejects it.
            # `between_tools` is Sonnet 5.5's thinking-off; `disabled` is a 400.
            "thinking": {"type": "adaptive" if settings.thinking else "between_tools"},
        }
        if system:
            request["system"] = system
        if schema is not None:
            request["output_config"] = {
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
            }
        try:
            response = self.client.messages.create(**request)
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            raise LLMUnavailable(f"Can't reach the Anthropic API: {e}") from e
        refusal = None
        if response.stop_reason == "refusal":
            details = response.stop_details
            refusal = (details.category if details else None) or "unspecified"
        usage = response.usage
        return AdapterResponse(
            raw_request=request,
            raw_response=response.model_dump(),
            text="".join(b.text for b in response.content if b.type == "text"),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=response.model or self.model,
            refusal=refusal,
        )


_ADAPTERS: dict[str, type[LMStudioAdapter] | type[AnthropicAdapter]] = {
    "lmstudio": LMStudioAdapter,
    "anthropic": AnthropicAdapter,
}


class _LazyAdapter:
    """Builds the real adapter on first use, so importing llm never needs config.

    LLM_PROVIDER picks the class; an unknown value fails loudly.
    """

    _instance: LMStudioAdapter | AnthropicAdapter | None = None

    def _get(self) -> LMStudioAdapter | AnthropicAdapter:
        if self._instance is None:
            provider = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
            if provider not in _ADAPTERS:
                raise ValueError(
                    f"Unknown LLM_PROVIDER {provider!r}; expected one of {sorted(_ADAPTERS)}"
                )
            self._instance = _ADAPTERS[provider]()
        return self._instance

    @property
    def model(self) -> str:
        return self._get().model

    @property
    def hosted(self) -> bool:
        return self._get().hosted

    def send(
        self, messages: Messages, schema: type[BaseModel] | None, settings: ModelSettings
    ) -> AdapterResponse:
        return self._get().send(messages, schema, settings)


adapter: Any = _LazyAdapter()


def complete(
    messages: Messages,
    *,
    feature: str,
    prompt_version: str,
    schema: type[BaseModel] | None = None,
    settings: ModelSettings = ModelSettings(),  # noqa: B008 -- frozen, safe to share
) -> LLMResult:
    """Send `messages` to the configured model and return an LLMResult.

    With `schema` (a Pydantic model), the model is constrained to it and the
    reply is parsed into `result.parsed`; a reply that doesn't validate raises
    pydantic.ValidationError, and a model refusal raises LLMRefusal. Provider
    unreachable raises LLMUnavailable; a hosted adapter without RUNWAY_LAB=1
    raises HostedCallBlocked before sending. Either way the call is traced
    before the error propagates.
    """
    request_id = str(uuid.uuid4())
    hosted = bool(adapter.hosted)
    trace: dict[str, Any] = dict(
        request_id=request_id,
        feature=feature,
        model=getattr(adapter, "model", None),
        model_settings=asdict(settings),
        prompt_version=prompt_version,
        input=messages,
        output=None,
        raw_request=None,
        raw_response=None,
        input_tokens=None,
        output_tokens=None,
        latency_ms=None,
        success=False,
        hosted=hosted,
    )
    start = time.perf_counter()
    try:
        if hosted and not lab_mode():
            raise HostedCallBlocked(
                f"Hosted LLM call blocked ({feature}, model {trace['model']}): "
                "set RUNWAY_LAB=1 to allow; lab data must be synthetic"
            )
        resp = adapter.send(messages, schema, settings)
        latency_ms = (time.perf_counter() - start) * 1000
        trace.update(
            model=resp.model,
            raw_request=resp.raw_request,
            raw_response=resp.raw_response,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            latency_ms=latency_ms,
        )
        if resp.refusal is not None:
            raise LLMRefusal(f"Model {resp.model} declined ({feature}): {resp.refusal}")
        text = strip_think(resp.text)
        trace["output"] = text
        parsed = schema.model_validate_json(text) if schema is not None else None
        if parsed is not None:
            trace["output"] = parsed.model_dump()
        trace["success"] = True
        return LLMResult(
            parsed=parsed,
            text=text,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            latency_ms=latency_ms,
            model=resp.model,
            request_id=request_id,
        )
    except Exception as e:
        if trace["latency_ms"] is None:
            trace["latency_ms"] = (time.perf_counter() - start) * 1000
        trace["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        tracing.record(**trace)
