"""Send a few test messages to the AI and print the replies, tokens, and cost.

Run from the backend folder, with the virtual environment active:

    python -m scripts.try_llm            send the test messages
    python -m scripts.try_llm --models   list the models your key can use

Uses LLM_PROVIDER, LLM_MODEL, and LLM_API_KEY from backend/.env. Each run
makes 3 small AI calls (a fraction of a US cent with gpt-5-mini).
Set LLM_PROVIDER=fake to try it without an API key or cost.
"""
import asyncio
import sys
from decimal import Decimal

from app.core.config import get_settings
from app.services.llm_service import LLMError, LLMMessage, ToolDefinition, create_provider
from app.utils.logging import setup_logging

SYSTEM_PROMPT = (
    "You are the assistant of Selam Shoes, a shoe store in Addis Ababa. "
    "Reply briefly and in the same language the customer writes in. "
    "Never invent stock or prices: use the check_stock tool for those."
)

CHECK_STOCK = ToolDefinition(
    name="check_stock",
    description="Find products in the store's catalog, with stock and price.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Product name, brand, or category in English"},
            "color": {"type": "string"},
            "size": {"type": "string"},
        },
        "required": ["query"],
    },
)

TESTS = [
    ("English greeting", "Hi! What are your opening hours?"),
    ("Amharic greeting", "ሰላም! እንዴት ናችሁ? ጫማ መግዛት እፈልጋለሁ።"),
    ("Stock question (should ask to use check_stock)", "Do you have white Air Force 1 in size 42?"),
    ("Amharic Stock question(should ask to use check_stock)","ነጭ ኤር ፎርስ 1 በሳይዝ 42 አላችሁ?"),
    ("Amharic Stock question(should ask to use check_stock)","white AF1 ቁጥር 43 ስንት ነው?")
]


async def main() -> None:
    setup_logging("WARNING")  # keep the output readable
    settings = get_settings()
    try:
        llm = create_provider(settings.llm_provider, settings.llm_model,
                              settings.llm_api_key.get_secret_value())
    except ValueError as error:
        sys.exit(str(error))

    if "--models" in sys.argv:
        try:
            names = await llm.list_models()
        except LLMError as error:
            sys.exit(f"Could not list models: {error.reason}")
        finally:
            await llm.close()
        print(f"Models available to your {settings.llm_provider} key ({len(names)}):")
        for name in names:
            print(f"  {name}")
        return

    print(f"Provider: {settings.llm_provider}   Model: {llm.model}\n")
    total_cost = Decimal(0)
    try:
        for title, text in TESTS:
            print(f"--- {title}\nCustomer: {text}")
            try:
                response = await llm.complete(
                    SYSTEM_PROMPT, [LLMMessage(role="user", content=text)], [CHECK_STOCK]
                )
            except LLMError as error:
                print(f"AI call failed: {error.reason}\n")
                continue
            if response.text:
                print(f"AI:       {response.text}")
            for call in response.tool_calls:
                print(f"AI wants to run: {call.name}({call.arguments})")
            usage = response.usage
            cost = usage.estimated_cost_usd
            total_cost += cost or 0
            print(f"Tokens:   {usage.input_tokens} in, {usage.output_tokens} out   "
                  f"Cost: {'$%.6f' % cost if cost is not None else 'unknown'}\n")
    finally:
        await llm.close()
    print(f"Total estimated cost: ${total_cost:.6f}")


if __name__ == "__main__":
    asyncio.run(main())
