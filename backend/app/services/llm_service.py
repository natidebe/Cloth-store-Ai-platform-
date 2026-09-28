"""The only module that talks to an AI model provider.

The rest of the app uses our own provider-agnostic shapes (LLMMessage,
ToolDefinition, LLMResponse) and the LLMProvider interface. Each provider is
a small adapter that translates to and from its own API. Switching provider
means changing LLM_PROVIDER / LLM_MODEL in .env (and adding an adapter if
it's a new provider).

Providers:
- "openai":     OpenAI Chat Completions (GPT-5 mini first).
- "openrouter": OpenRouter (https://openrouter.ai), which speaks the same
                format as OpenAI, so it reuses the OpenAI adapter. Gives
                access to many models, including free ones (ids ending
                in ":free") for testing.
- "fake":       scripted replies for tests and local development; costs nothing.
"""
import json
import logging
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Literal

import openai
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# How long one AI call may take before we give up, and how many times the
# SDK retries (with growing waits) on timeouts, rate limits, and server errors.
LLM_TIMEOUT_SECONDS = 30.0
LLM_MAX_RETRIES = 2
MAX_OUTPUT_TOKENS = 2000  # includes the model's hidden reasoning tokens

# Price in USD per 1 million tokens: (input, output).
# Launch prices; check https://openai.com/api/pricing and update here.
PRICES_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "gpt-5-nano": (Decimal("0.05"), Decimal("0.40")),
    "gpt-5": (Decimal("1.25"), Decimal("10.00")),
}


# ---------------------------------------------------------------------------
# Our own shapes (provider-agnostic)
# ---------------------------------------------------------------------------

class ToolDefinition(BaseModel):
    """A tool the AI may ask us to run. `parameters` is a JSON Schema."""
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    """The AI asking us to run a tool."""
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set when the AI sent arguments that weren't valid JSON.
    arguments_error: str | None = None


class LLMMessage(BaseModel):
    """One entry in the conversation sent to the AI."""
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)  # role == "assistant"
    tool_call_id: str | None = None  # role == "tool": which call this answers


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal | None = None  # None if the model's price is unknown


class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    model: str
    stop_reason: str | None = None  # e.g. "stop", "tool_calls", "length"


class LLMError(Exception):
    """The AI call failed after retries. Never contains the API key."""

    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    if model.endswith(":free"):  # OpenRouter's free models
        return Decimal(0)
    # Match "gpt-5-mini-2025-08-07" to "gpt-5-mini": longest known prefix wins.
    known = sorted((m for m in PRICES_PER_MILLION if model.startswith(m)), key=len, reverse=True)
    if not known:
        return None
    price_in, price_out = PRICES_PER_MILLION[known[0]]
    return (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)


# ---------------------------------------------------------------------------
# The interface every provider implements
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    model: str

    @abstractmethod
    async def _complete(
        self, system_prompt: str, messages: list[LLMMessage], tools: list[ToolDefinition]
    ) -> LLMResponse:
        """Provider-specific call. Raise LLMError on failure."""

    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Ask the AI. Returns text and/or tool calls, plus token usage.

        Every call is logged with model, tokens, and estimated cost (the log
        context adds store_id / telegram_id when called for a customer).
        """
        response = await self._complete(system_prompt, messages, tools or [])
        cost = response.usage.estimated_cost_usd
        logger.info("llm call", extra={
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cost_usd": f"{cost:.6f}" if cost is not None else "unknown",
            "tool_calls": len(response.tool_calls),
            "stop_reason": response.stop_reason,
        })
        return response

    async def close(self) -> None:
        """Release network connections (app shutdown)."""


# ---------------------------------------------------------------------------
# OpenAI (Chat Completions)
# ---------------------------------------------------------------------------

class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
        http_client: Any = None,  # tests pass a fake one
    ):
        self.model = model
        self._reasoning_effort = reasoning_effort
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=LLM_MAX_RETRIES,
            http_client=http_client,
        )

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _to_openai_messages(system_prompt: str, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            if message.role == "tool":
                converted.append({
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content or "",
                })
            elif message.role == "assistant" and message.tool_calls:
                converted.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [{
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    } for call in message.tool_calls],
                })
            else:
                converted.append({"role": message.role, "content": message.content or ""})
        return converted

    @staticmethod
    def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [{
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        } for t in tools]

    @staticmethod
    def _parse_tool_call(raw: Any) -> ToolCall:
        try:
            arguments = json.loads(raw.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments are not an object")
            return ToolCall(id=raw.id, name=raw.function.name, arguments=arguments)
        except ValueError as error:
            return ToolCall(id=raw.id, name=raw.function.name, arguments_error=str(error))

    async def _complete(self, system_prompt, messages, tools) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(system_prompt, messages),
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
        }
        if tools:
            request["tools"] = self._to_openai_tools(tools)
        if self._reasoning_effort:
            request["reasoning_effort"] = self._reasoning_effort

        try:
            completion = await self._client.chat.completions.create(**request)
        except openai.APITimeoutError:
            raise LLMError("timeout", retryable=True) from None
        except openai.APIConnectionError:
            raise LLMError("connection failed", retryable=True) from None
        except openai.RateLimitError as error:
            # OpenAI uses 429 both for "too fast" and for "no credit left".
            body = error.body if isinstance(error.body, dict) else {}
            if body.get("type") == "insufficient_quota" or body.get("code") == "insufficient_quota":
                raise LLMError("no credit left on the AI account (add credit / check billing)") from None
            raise LLMError("rate limited", retryable=True) from None
        except openai.AuthenticationError:
            raise LLMError("invalid API key") from None
        except openai.APIStatusError as error:
            raise LLMError(f"HTTP {error.status_code}", retryable=error.status_code >= 500) from None

        choice = completion.choices[0]
        usage = completion.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        return LLMResponse(
            text=choice.message.content or None,
            tool_calls=[self._parse_tool_call(c) for c in (choice.message.tool_calls or [])],
            usage=LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimate_cost(completion.model, input_tokens, output_tokens),
            ),
            model=completion.model,
            stop_reason=choice.finish_reason,
        )


# ---------------------------------------------------------------------------
# Fake provider (tests and local development)
# ---------------------------------------------------------------------------

class FakeProvider(LLMProvider):
    """Returns scripted responses in order and records every request.

    With no script, it answers every message with a fixed text.
    """

    def __init__(self, responses: list[LLMResponse] | None = None, model: str = "fake"):
        self.model = model
        self._responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []

    async def _complete(self, system_prompt, messages, tools) -> LLMResponse:
        self.requests.append({"system_prompt": system_prompt, "messages": messages, "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(text="(fake AI reply)", model=self.model, stop_reason="stop")


# ---------------------------------------------------------------------------
# Choosing the provider from settings
# ---------------------------------------------------------------------------

def create_provider(provider: str, model: str, api_key: str) -> LLMProvider:
    provider = provider.strip().lower()
    if provider == "fake":
        return FakeProvider()
    if provider == "openai":
        if not api_key:
            raise ValueError("LLM_API_KEY is not set in backend/.env")
        # GPT-5 models "think" before answering; low effort keeps chat replies fast and cheap.
        effort = "low" if model.startswith("gpt-5") else None
        return OpenAIProvider(api_key, model, reasoning_effort=effort)
    if provider == "openrouter":
        if not api_key:
            raise ValueError("LLM_API_KEY is not set in backend/.env (use your OpenRouter key)")
        return OpenAIProvider(api_key, model, base_url=OPENROUTER_BASE_URL)
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Supported: openai, openrouter, fake")
