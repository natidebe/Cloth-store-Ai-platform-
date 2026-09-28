"""LLM service tests. OpenAI's servers are faked: no network, no cost."""
import json
import logging
from decimal import Decimal

import httpx2
import pytest

from app.services.llm_service import (
    FakeProvider,
    LLMError,
    LLMMessage,
    LLMResponse,
    OpenAIProvider,
    ToolCall,
    ToolDefinition,
    create_provider,
    estimate_cost,
)

pytestmark = pytest.mark.anyio
API_KEY = "sk-test-SECRET-KEY"

CHECK_STOCK = ToolDefinition(
    name="check_stock",
    description="Find products",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _completion(message: dict, finish_reason="stop", model="gpt-5-mini-2025-08-07",
                prompt_tokens=100, completion_tokens=20) -> dict:
    """A Chat Completions response body, as OpenAI sends it."""
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1790000000, "model": model,
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": None, **message}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }


class FakeOpenAI:
    """Answers with the given (status, body) pairs in order; records requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def handler(self, request):
        self.requests.append(json.loads(request.content))
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return httpx2.Response(status, json=body)

    def provider(self, **kwargs) -> OpenAIProvider:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
        return OpenAIProvider(API_KEY, "gpt-5-mini", http_client=client, **kwargs)


# --- OpenAI adapter ---------------------------------------------------------

async def test_request_is_translated_to_openai_format():
    fake = FakeOpenAI((200, _completion({"content": "Hello!"})))
    llm = fake.provider(reasoning_effort="low")
    history = [
        LLMMessage(role="user", content="Do you have AF1 in 42?"),
        LLMMessage(role="assistant", tool_calls=[
            ToolCall(id="call_1", name="check_stock", arguments={"query": "Air Force 1"})]),
        LLMMessage(role="tool", tool_call_id="call_1", content='{"results": []}'),
    ]
    await llm.complete("You are a shop assistant.", history, [CHECK_STOCK])

    sent = fake.requests[0]
    assert sent["model"] == "gpt-5-mini"
    assert sent["reasoning_effort"] == "low"
    assert sent["max_completion_tokens"] == 2000
    assert [m["role"] for m in sent["messages"]] == ["system", "user", "assistant", "tool"]
    assert sent["messages"][0]["content"] == "You are a shop assistant."
    assert sent["messages"][2]["tool_calls"][0]["function"] == {
        "name": "check_stock", "arguments": '{"query": "Air Force 1"}'}
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"results": []}'}
    assert sent["tools"][0] == {"type": "function", "function": CHECK_STOCK.model_dump()}


async def test_text_reply_with_usage_and_cost(caplog):
    fake = FakeOpenAI((200, _completion({"content": "Yes, we have it!"},
                                        prompt_tokens=1000, completion_tokens=200)))
    with caplog.at_level(logging.INFO):
        response = await fake.provider().complete("sys", [LLMMessage(role="user", content="hi")])

    assert response.text == "Yes, we have it!"
    assert response.tool_calls == []
    assert response.stop_reason == "stop"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (1000, 200)
    # 1000 x $0.25/M + 200 x $2.00/M
    assert response.usage.estimated_cost_usd == Decimal("0.00065")
    record = next(r for r in caplog.records if r.getMessage() == "llm call")
    assert (record.model, record.input_tokens, record.output_tokens) == ("gpt-5-mini-2025-08-07", 1000, 200)
    assert record.cost_usd == "0.000650"


async def test_tool_calls_are_parsed():
    fake = FakeOpenAI((200, _completion({"tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "check_stock", "arguments": '{"query": "Air Force 1", "size": "42"}'}},
        {"id": "call_2", "type": "function",
         "function": {"name": "check_stock", "arguments": '{"query": broken'}},
    ]}, finish_reason="tool_calls")))
    response = await fake.provider().complete("sys", [LLMMessage(role="user", content="AF1 42?")], [CHECK_STOCK])

    assert response.text is None
    assert response.stop_reason == "tool_calls"
    good, bad = response.tool_calls
    assert (good.id, good.name, good.arguments) == ("call_1", "check_stock", {"query": "Air Force 1", "size": "42"})
    assert bad.arguments == {} and bad.arguments_error  # invalid JSON is reported, not crashed on


async def test_wrong_api_key_fails_fast_without_leaking_it():
    fake = FakeOpenAI((401, {"error": {"message": f"Incorrect API key provided: {API_KEY}", "type": "invalid_request_error"}}))
    with pytest.raises(LLMError) as error:
        await fake.provider().complete("sys", [LLMMessage(role="user", content="hi")])
    assert error.value.reason == "invalid API key"
    assert not error.value.retryable
    assert API_KEY not in str(error.value)
    assert error.value.__cause__ is None
    assert len(fake.requests) == 1  # no retries for a bad key


async def test_server_errors_and_rate_limits_are_retried():
    fake = FakeOpenAI((500, {"error": {"message": "oops"}}), (429, {"error": {"message": "slow down"}}),
                      (200, _completion({"content": "Recovered"})))
    response = await fake.provider().complete("sys", [LLMMessage(role="user", content="hi")])
    assert response.text == "Recovered"
    assert len(fake.requests) == 3


async def test_no_credit_is_reported_clearly():
    no_credit = {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota",
                           "code": "credit_balance_exhausted"}}
    fake = FakeOpenAI((429, no_credit))
    with pytest.raises(LLMError) as error:
        await fake.provider().complete("sys", [LLMMessage(role="user", content="hi")])
    assert "no credit" in error.value.reason
    assert not error.value.retryable


async def test_gives_up_after_retries():
    fake = FakeOpenAI((503, {"error": {"message": "down"}}))
    with pytest.raises(LLMError) as error:
        await fake.provider().complete("sys", [LLMMessage(role="user", content="hi")])
    assert error.value.reason == "HTTP 503" and error.value.retryable
    assert len(fake.requests) == 3  # first try + 2 retries


# --- Cost, fake provider, provider choice ------------------------------------

def test_estimate_cost():
    assert estimate_cost("gpt-5-mini", 1_000_000, 0) == Decimal("0.25")
    assert estimate_cost("gpt-5-mini-2025-08-07", 0, 1_000_000) == Decimal("2.00")
    assert estimate_cost("gpt-5", 1_000_000, 0) == Decimal("1.25")  # not confused with gpt-5-mini
    assert estimate_cost("some-other-model", 100, 100) is None
    assert estimate_cost("nvidia/nemotron-3-ultra-550b-a55b:free", 5000, 500) == Decimal(0)


async def test_fake_provider_returns_script_then_default():
    scripted = LLMResponse(text="first", model="fake")
    fake = FakeProvider([scripted])
    first = await fake.complete("sys", [LLMMessage(role="user", content="a")], [CHECK_STOCK])
    second = await fake.complete("sys", [LLMMessage(role="user", content="b")])
    assert first.text == "first"
    assert second.text == "(fake AI reply)"
    assert fake.requests[0]["tools"] == [CHECK_STOCK]
    assert fake.requests[1]["messages"][0].content == "b"


async def test_create_provider():
    assert isinstance(create_provider("fake", "anything", ""), FakeProvider)
    llm = create_provider("OpenAI", "gpt-5-mini", "sk-x")
    assert isinstance(llm, OpenAIProvider) and llm._reasoning_effort == "low"
    await llm.close()
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        create_provider("openai", "gpt-5-mini", "")

    router = create_provider("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", "sk-or-x")
    assert isinstance(router, OpenAIProvider)
    assert str(router._client.base_url).startswith("https://openrouter.ai/api/v1")
    assert router._reasoning_effort is None  # OpenAI-only setting, not sent to other models
    await router.close()
    with pytest.raises(ValueError, match="OpenRouter"):
        create_provider("openrouter", "some/model:free", "")

    gemini = create_provider("gemini", "gemini-model", "AIza-x")
    assert isinstance(gemini, OpenAIProvider)
    assert str(gemini._client.base_url).startswith("https://generativelanguage.googleapis.com/v1beta/openai")
    assert gemini._reasoning_effort is None
    await gemini.close()
    with pytest.raises(ValueError, match="Gemini"):
        create_provider("gemini", "gemini-model", "")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        create_provider("claude", "x", "key")
