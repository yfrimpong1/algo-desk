"""Phase 0 — the first tool-calling agent.

Goal: prove the full chain works before we add any trading logic.

The chain we are validating
---------------------------
1. We define a Python function `get_market_price` and expose it to Claude as a TOOL.
2. We ask the agent a natural-language question ("what's the market doing?").
3. The agent DECIDES to call our tool, the SDK runs our Python code, and feeds the
   real price back to the model.
4. The agent writes a one-line summary using the tool's result.

That "model decides -> our code runs -> model continues" loop is the foundation every
later agent (analyst, risk manager, executioner) is built on. If this works, the
plumbing — Python env, API key, data libraries, and the Agent SDK — all work.

Run:  python -m src.agents.hello_agent
"""

from __future__ import annotations

import os

import anyio
from dotenv import load_dotenv

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from src.layer.datafeed import get_quote

# Load ANTHROPIC_API_KEY from .env into the process environment. The SDK's underlying
# Claude Code transport reads it from there.
load_dotenv()


# --------------------------------------------------------------------------- #
# 1) The tool: a thin wrapper over our data layer.
#    @tool(name, description, input_schema). The description is what the MODEL reads
#    to decide when to call it, so it is written for the model, not for humans.
# --------------------------------------------------------------------------- #
@tool(
    "get_market_price",
    "Get the latest market price for one symbol. Use crypto pairs like 'BTC/USD' "
    "or stock tickers like 'AAPL'. Returns the price and its data source.",
    {"symbol": str},
)
async def get_market_price(args):
    quote = get_quote(args["symbol"])
    text = (
        f"{quote.symbol}: {quote.price:,.2f} "
        f"(source={quote.source}, at {quote.timestamp.isoformat()})"
    )
    # Tools must return {"content": [...]} with content blocks.
    return {"content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------- #
# 2) Wrap the tool in an in-process MCP server (no subprocess — runs in this Python).
# --------------------------------------------------------------------------- #
market_server = create_sdk_mcp_server(
    name="market",
    version="0.1.0",
    tools=[get_market_price],
)


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — check algo-desk/.env")

    options = ClaudeAgentOptions(
        # Keep the agent narrow: it may ONLY use our market tool, nothing else
        # (no Bash, no file access). Tool name format: mcp__<server>__<tool>.
        mcp_servers={"market": market_server},
        allowed_tools=["mcp__market__get_market_price"],
        model="claude-sonnet-4-6",
        system_prompt=(
            "You are a market data assistant for a trading desk. When asked about a "
            "symbol's price, call the get_market_price tool and report the number "
            "clearly. Be concise — one or two sentences."
        ),
    )

    prompt = (
        "What are the latest prices for Bitcoin (BTC/USD) and Apple (AAPL)? "
        "Give me a one-line market summary."
    )

    print(f"\n[USER] {prompt}\n")

    tool_calls: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    tool_calls.append(block.name)
                    print(f"[TOOL CALL] {block.name}({block.input})")
                elif isinstance(block, TextBlock):
                    print(f"[AGENT] {block.text}")
        elif isinstance(message, ResultMessage):
            # ResultMessage carries cost/usage totals for the whole run.
            cost = getattr(message, "total_cost_usd", None)
            if cost is not None:
                print(f"\n[run cost] ${cost:.4f}")

    print(f"\n[tools the agent chose to call] {tool_calls or 'NONE'}")


if __name__ == "__main__":
    anyio.run(main)
