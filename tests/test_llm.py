from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest
from conftest import read_traces
from pydantic import BaseModel, ValidationError

import llm


class Answer(BaseModel):
    city: str
    count: int


MESSAGES = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
]


def _complete(**kwargs):
    kwargs.setdefault("feature", "test_feature")
    kwargs.setdefault("prompt_version", "test.v1")
    return llm.complete(MESSAGES, **kwargs)


# --- think-block stripping ---

@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', '{"a": 1}'),
        ('<think>hmm</think>{"a": 1}', '{"a": 1}'),
        ('<think>\nline one\nline two\n</think>\n\n{"a": 1}', '{"a": 1}'),
        ("<think></think>\n\nhello", "hello"),
        ("<think>a</think>x<THINK>b</THINK>y", "xy"),
        ("  plain  ", "plain"),
    ],
)
def test_strip_think(raw, expected):
    assert llm.strip_think(raw) == expected


# --- normalisation ---

def test_complete_parses_schema_output(fake_llm):
    fake_llm.text = '<think>\nreasoning\n</think>\n{"city": "Leeds", "count": 3}'

    result = _complete(schema=Answer)

    assert isinstance(result.parsed, Answer)
    assert result.parsed == Answer(city="Leeds", count=3)
    assert result.text == '{"city": "Leeds", "count": 3}'
    assert result.input_tokens == 12
    assert result.output_tokens == 8
    assert result.model == "fake-model"
    assert result.latency_ms >= 0
    assert result.request_id
    assert fake_llm.calls[0]["schema"] is Answer
    assert fake_llm.calls[0]["messages"] == MESSAGES


def test_complete_without_schema_returns_text_only(fake_llm):
    fake_llm.text = "<think>x</think>Just text."

    result = _complete()

    assert result.parsed is None
    assert result.text == "Just text."


def test_complete_passes_settings_to_adapter(fake_llm):
    fake_llm.text = "ok"
    settings = llm.ModelSettings(temperature=0.3, max_tokens=50)

    _complete(settings=settings)

    assert fake_llm.calls[0]["settings"] == settings


@pytest.mark.parametrize("bad", ["not json", '{"city": "Leeds"}', '{"city": 1, "count": "x"}'])
def test_complete_raises_on_unparseable_output(fake_llm, bad):
    fake_llm.text = bad

    with pytest.raises(ValidationError):
        _complete(schema=Answer)

    (row,) = read_traces()
    assert row["success"] == 0
    assert row["error"]


def test_complete_reraises_adapter_errors(fake_llm):
    fake_llm.error = llm.LLMUnavailable("down")

    with pytest.raises(llm.LLMUnavailable):
        _complete()

    (row,) = read_traces()
    assert row["success"] == 0
    assert "down" in row["error"]


# --- LM Studio adapter (fake OpenAI client, no network) ---

def _fake_openai_client(content='{"city": "Leeds", "count": 3}'):
    response = MagicMock()
    response.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
    response.usage = SimpleNamespace(prompt_tokens=40, completion_tokens=9)
    response.model = "qwen3-8b"
    response.model_dump.return_value = {"id": "chatcmpl-1", "model": "qwen3-8b"}
    client = MagicMock()
    client.chat.completions.create.return_value = response
    return client


def test_lm_studio_adapter_builds_request_and_normalises_response():
    client = _fake_openai_client()
    adapter = llm.LMStudioAdapter(client=client, model="qwen3-8b")

    resp = adapter.send(MESSAGES, Answer, llm.ModelSettings())

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["model"] == "qwen3-8b"
    assert kwargs["messages"] == MESSAGES
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 1024
    fmt = kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Answer"
    assert fmt["json_schema"]["schema"] == Answer.model_json_schema()
    assert resp.raw_request == kwargs
    assert resp.raw_response == {"id": "chatcmpl-1", "model": "qwen3-8b"}
    assert resp.text == '{"city": "Leeds", "count": 3}'
    assert (resp.input_tokens, resp.output_tokens) == (40, 9)
    assert resp.model == "qwen3-8b"


def test_lm_studio_adapter_omits_response_format_without_schema():
    client = _fake_openai_client(content="hello")
    adapter = llm.LMStudioAdapter(client=client, model="m")

    adapter.send(MESSAGES, None, llm.ModelSettings())

    _, kwargs = client.chat.completions.create.call_args
    assert "response_format" not in kwargs


@pytest.mark.parametrize("exc_type", [openai.APIConnectionError, openai.APITimeoutError])
def test_lm_studio_adapter_maps_connection_errors(exc_type):
    client = MagicMock()
    client.chat.completions.create.side_effect = exc_type(request=MagicMock())
    adapter = llm.LMStudioAdapter(client=client, model="m")

    with pytest.raises(llm.LLMUnavailable):
        adapter.send(MESSAGES, None, llm.ModelSettings())


def test_lm_studio_adapter_reads_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "some/model")

    adapter = llm.LMStudioAdapter()

    assert adapter.model == "some/model"
    assert str(adapter.client.base_url).startswith("http://example.test:9999/v1")


def test_lm_studio_adapter_defaults(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    adapter = llm.LMStudioAdapter()

    assert adapter.model == "qwen3-8b"
    assert str(adapter.client.base_url).startswith("http://localhost:1234/v1")
