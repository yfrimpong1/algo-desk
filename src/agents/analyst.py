"""Analyst agents — parallel specialists that each emit a structured Signal.

The reliability trick
---------------------
We do NOT ask the agent to "reply in JSON" and hope. Instead each analyst is given a
`submit_signal` tool whose INPUT SCHEMA *is* the Signal. The agent reads data with its
scoped tools, reasons, then CALLS submit_signal(direction, confidence, rationale). The
SDK validates the call against the schema, and we capture it. Structure guaranteed.

Each analyst gets a different `data_tools` scope (technical sees bars, fundamental sees
financials, sentiment sees news) — they physically cannot stray outside their lens.

`run_all_analysts(symbol)` fans the three out IN PARALLEL (they're independent) and returns
their three Signals. Parallelism trades a little extra concurrent cost for ~3x lower latency.
"""

from __future__ import annotations

import os

import anyio
from dotenv import load_dotenv

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from src.mcp_servers.market_data import (
    FUNDAMENTAL_TOOLS,
    SENTIMENT_TOOLS,
    TECHNICAL_TOOLS,
    market_data_server,
)
from src.schemas import Direction, Signal

load_dotenv()


async def run_analyst(
    symbol: str,
    *,
    name: str,
    system_prompt: str,
    data_tools: list[str],
    model: str = "claude-sonnet-4-6",
    verbose: bool = False,
) -> Signal:
    """Run one analyst on `symbol` and return its validated Signal."""
    captured: dict[str, Signal] = {}

    # Per-call submit tool: a closure capturing `captured` so concurrent analysts don't
    # clobber each other. tool(...) is a decorator factory we apply manually here.
    async def _submit(args):
        direction = Direction(str(args["direction"]).lower())
        confidence = max(0.0, min(1.0, float(args.get("confidence", 0.0))))
        captured["signal"] = Signal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            rationale=str(args.get("rationale", "")),
            analyst=name,
        )
        return {"content": [{"type": "text", "text": "Signal recorded."}]}

    submit_signal = tool(
        "submit_signal",
        "Record your FINAL trading signal for the symbol. Call this exactly once when done. "
        "direction must be one of 'buy', 'sell', 'hold'. confidence is 0.0-1.0. "
        "rationale is one or two sentences citing the data you saw.",
        {"direction": str, "confidence": float, "rationale": str},
    )(_submit)

    submit_server = create_sdk_mcp_server(name="analyst", version="0.1.0", tools=[submit_signal])

    options = ClaudeAgentOptions(
        mcp_servers={"market_data": market_data_server, "analyst": submit_server},
        allowed_tools=data_tools + ["mcp__analyst__submit_signal"],
        model=model,
        system_prompt=system_prompt,
    )

    prompt = (
        f"Analyze {symbol} from your perspective. Use your tools to gather evidence, then "
        f"call submit_signal with your direction (buy/sell/hold), confidence, and a short "
        f"rationale citing what you saw."
    )

    async for message in query(prompt=prompt, options=options):
        if verbose and isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(f"  [{name}] {block.text}")

    return captured.get(
        "signal",
        Signal(symbol=symbol, direction=Direction.HOLD, confidence=0.0,
               rationale="No signal produced.", analyst=name),
    )


# --------------------------------------------------------------------------- #
# The three analyst configurations (lens + tool scope).
# --------------------------------------------------------------------------- #
ANALYSTS = {
    "technical": {
        "system_prompt": (
            "You are a TECHNICAL analyst. Judge a symbol purely on price action: trend, "
            "momentum, and recent moves from the bar history and latest quote. Ignore news "
            "and fundamentals. Be decisive but honest about uncertainty."
        ),
        "data_tools": TECHNICAL_TOOLS,
    },
    "fundamental": {
        "system_prompt": (
            "You are a FUNDAMENTAL analyst. Judge a symbol on valuation and business quality: "
            "P/E, margins, growth, market cap, 52-week range. For crypto, fundamentals are "
            "limited — weight that uncertainty into a lower confidence. Ignore short-term price."
        ),
        "data_tools": FUNDAMENTAL_TOOLS,
    },
    "sentiment": {
        "system_prompt": (
            "You are a SENTIMENT analyst. Judge a symbol on the tone of recent news headlines: "
            "are they bullish, bearish, or mixed? Ignore price and fundamentals. If headlines "
            "are sparse or neutral, say so and keep confidence low."
        ),
        "data_tools": SENTIMENT_TOOLS,
    },
}


async def run_all_analysts(symbol: str, verbose: bool = False) -> list[Signal]:
    """Fan out all three analysts in parallel; return their Signals.

    Resilient by design: if one analyst errors (e.g. a transient SDK/CLI hiccup), it degrades
    to a neutral HOLD signal instead of crashing the cycle. The desk keeps running on the
    remaining analysts' evidence.
    """
    results: dict[str, Signal] = {}

    async def _one(name: str, cfg: dict):
        try:
            results[name] = await run_analyst(
                symbol, name=name, system_prompt=cfg["system_prompt"],
                data_tools=cfg["data_tools"], verbose=verbose,
            )
        except Exception as e:  # transient API/CLI error -> neutral, never crash the group
            print(f"  [analyst:{name}] failed for {symbol}: {type(e).__name__}: {str(e)[:120]}")
            results[name] = Signal(
                symbol=symbol, direction=Direction.HOLD, confidence=0.0,
                rationale=f"Analyst unavailable ({type(e).__name__}).", analyst=name,
            )

    async with anyio.create_task_group() as tg:
        for name, cfg in ANALYSTS.items():
            tg.start_soon(_one, name, cfg)

    # Stable order: technical, fundamental, sentiment.
    return [results[name] for name in ANALYSTS]


if __name__ == "__main__":
    import sys

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — check algo-desk/.env")

    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

    async def main():
        print(f"\nRunning 3 analysts on {symbol} in parallel...\n")
        signals = await run_all_analysts(symbol)
        for s in signals:
            print(f"  {s.analyst:12s} {s.direction.value.upper():4s} "
                  f"conf={s.confidence:.2f}  {s.rationale}")

    anyio.run(main)
