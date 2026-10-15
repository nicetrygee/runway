"""The one LLM interface every AI feature calls.

Features call `complete(...)` and never a provider SDK directly. The provider
lives behind an adapter; Phase 0 has one, LM Studio over its OpenAI-compatible
API. Every call — success or failure — is traced via `tracing.record`.

Tests swap the provider with `monkeypatch.setattr(llm, "adapter", fake)`.
"""
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import openai
from pydantic import BaseModel

import tracing

DEFAULT_BASE_URL = "http://localhost:1234/v1"
DEFAULT_MODEL = "qwen3-8b"

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMUnavailable(Exception):
    """The provider couldn't be reached (not running, connection refused, timeout)."""


@dataclass(frozen=True)
class ModelSettings:
    temperature: float = 0.0
    max_tokens: int = 1024
    # Recorded, not enforced: thinking is switched off in the prompt itself
    # (Qwen3's `/no_think`), so this flag says what the caller asked for.
    thinking: bool = False


@dataclass
class AdapterResponse:
    raw_request: dict
    raw_response: dict
    text: str
    input_tokens: int | None
    output_tokens: int | None
    model: str


@dataclass
class LLMResult:
    parsed: Any
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    model: str
    request_id: str


def strip_think(text):
    """Drop any <think>…</think> reasoning blocks a model emits before its answer."""
    return _THINK_RE.sub("", text).strip()


class LMStudioAdapter:
    """LM Studio via its OpenAI-compatible API. Config from LLM_BASE_URL / LLM_MODEL."""

    def __init__(self, client=None, model=None):
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.client = client or openai.OpenAI(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key="lm-studio",  # LM Studio ignores it, but the SDK requires one
            timeout=60,
            max_retries=0,  # fail fast when LM Studio isn't running
        )

    def send(self, messages, schema, settings):
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


class _LazyAdapter:
    """Builds the real adapter on first use, so importing llm never needs config."""

    _instance: LMStudioAdapter | None = None

    def _get(self):
        if self._instance is None:
            self._instance = LMStudioAdapter()
        return self._instance

    @property
    def model(self):
        return self._get().model

    def send(self, messages, schema, settings):
        return self._get().send(messages, schema, settings)


adapter: Any = _LazyAdapter()


def complete(
    messages,
    *,
    feature,
    prompt_version,
    schema: type[BaseModel] | None = None,
    settings: ModelSettings = ModelSettings(),  # noqa: B008 -- frozen, safe to share
):
    """Send `messages` to the configured model and return an LLMResult.

    With `schema` (a Pydantic model), the model is constrained to it and the
    reply is parsed into `result.parsed`; a reply that doesn't validate raises
    pydantic.ValidationError. Provider unreachable raises LLMUnavailable.
    Either way the call is traced before the error propagates.
    """
    request_id = str(uuid.uuid4())
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
    )
    start = time.perf_counter()
    try:
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
