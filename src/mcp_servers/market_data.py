"""Market-data MCP server (Phase 1).

This is the reusable *tool surface* every analyst agent will share. Agents never touch
ccxt/yfinance or raw HTTP — they call these scoped tools. Two design rules:

  1. Agent-facing tools return COMPACT summaries, not raw candle dumps. Feeding an agent
     178 rows is wasteful (tokens) and useless (it can't reason over a table). We pre-chew
     the data into the few numbers an analyst actually uses.
  2. Heavy/deterministic computation (the full OHLCV frame, and later indicators) lives in
     Python; the agent only sees the result.

`get_indicators` is added in Phase 2 once indicators exist. For now: `get_quote`,
`get_recent_bars`.

Other modules import `market_data_server` and attach it to an agent's ClaudeAgentOptions.
"""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.layer.datafeed import get_bars, get_quote

# How many days each timeframe's "lookback" covers, so the agent can say "last N days".
_DEFAULT_LOOKBACK_DAYS = 90


def _yf_symbol(symbol: str) -> str:
    """Map our symbol to a yfinance ticker (crypto 'BTC/USD' -> 'BTC-USD')."""
    return symbol.replace("/", "-") if "/" in symbol else symbol


@tool(
    "get_quote",
    "Get the latest price for one symbol. Crypto pairs look like 'BTC/USD'; stocks like 'AAPL'.",
    {"symbol": str},
)
async def get_quote_tool(args):
    q = get_quote(args["symbol"])
    return {
        "content": [
            {"type": "text", "text": f"{q.symbol} latest price = {q.price:,.4f} (source={q.source})"}
        ]
    }


@tool(
    "get_recent_bars",
    "Summarize recent OHLCV price history for one symbol over a lookback window. "
    "Returns bar count, date range, period high/low, percent change, average volume, "
    "and the last several closing prices. Use this to assess trend and momentum. "
    "symbol: 'BTC/USD' or 'AAPL'. timeframe: '1d' or '1h'. lookback_days: how far back.",
    {"symbol": str, "timeframe": str, "lookback_days": int},
)
async def get_recent_bars_tool(args):
    from datetime import datetime, timedelta, timezone

    symbol = args["symbol"]
    timeframe = args.get("timeframe") or "1d"
    lookback = int(args.get("lookback_days") or _DEFAULT_LOOKBACK_DAYS)

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=lookback)
    bars = get_bars(symbol, timeframe, start=start, end=end)

    if bars.empty:
        return {"content": [{"type": "text", "text": f"No bars for {symbol}."}], "is_error": True}

    first_close = float(bars["close"].iloc[0])
    last_close = float(bars["close"].iloc[-1])
    pct = (last_close / first_close - 1.0) * 100.0
    last_closes = ", ".join(f"{c:,.2f}" for c in bars["close"].tail(5))

    summary = (
        f"{symbol} [{timeframe}] over last {lookback}d: "
        f"{len(bars)} bars {bars.index.min().date()}..{bars.index.max().date()}. "
        f"close {first_close:,.2f} -> {last_close:,.2f} ({pct:+.2f}%). "
        f"period high {bars['high'].max():,.2f}, low {bars['low'].min():,.2f}. "
        f"avg volume {bars['volume'].mean():,.0f}. "
        f"last 5 closes: {last_closes}."
    )
    return {"content": [{"type": "text", "text": summary}]}


@tool(
    "get_fundamentals",
    "Get fundamental/valuation data for a symbol: market cap, P/E, price/book, profit "
    "margin, revenue growth, 52-week range, sector. Best for stocks (e.g. 'AAPL'); "
    "crypto pairs return limited data. Use to judge whether an asset is cheap or expensive.",
    {"symbol": str},
)
async def get_fundamentals_tool(args):
    import yfinance as yf

    symbol = args["symbol"]
    yf_sym = _yf_symbol(symbol)
    try:
        info = yf.Ticker(yf_sym).info
    except Exception as e:  # network / parse hiccup
        return {"content": [{"type": "text", "text": f"Fundamentals unavailable for {symbol}: {e}"}],
                "is_error": True}

    def fmt(v, pct=False):
        if v is None:
            return "n/a"
        return f"{v*100:.1f}%" if pct else (f"{v:,.2f}" if isinstance(v, (int, float)) else str(v))

    parts = [
        f"name={info.get('longName') or info.get('shortName', symbol)}",
        f"sector={info.get('sector', 'n/a')}",
        f"market_cap={fmt(info.get('marketCap'))}",
        f"trailing_PE={fmt(info.get('trailingPE'))}",
        f"forward_PE={fmt(info.get('forwardPE'))}",
        f"price_to_book={fmt(info.get('priceToBook'))}",
        f"profit_margin={fmt(info.get('profitMargins'), pct=True)}",
        f"revenue_growth={fmt(info.get('revenueGrowth'), pct=True)}",
        f"52w_range={fmt(info.get('fiftyTwoWeekLow'))}..{fmt(info.get('fiftyTwoWeekHigh'))}",
    ]
    return {"content": [{"type": "text", "text": f"{symbol} fundamentals: " + ", ".join(parts)}]}


@tool(
    "get_news",
    "Get recent news headlines for a symbol to gauge market sentiment. Works for stocks "
    "('AAPL') and crypto ('BTC/USD'). Returns the latest headlines with publisher.",
    {"symbol": str},
)
async def get_news_tool(args):
    import yfinance as yf

    symbol = args["symbol"]
    try:
        items = yf.Ticker(_yf_symbol(symbol)).news or []
    except Exception as e:
        return {"content": [{"type": "text", "text": f"News unavailable for {symbol}: {e}"}],
                "is_error": True}

    headlines = []
    for it in items[:6]:
        # yfinance v1.4 nests fields under 'content'; older versions are flat.
        content = it.get("content", it)
        title = content.get("title")
        publisher = (
            (content.get("provider") or {}).get("displayName")
            if isinstance(content.get("provider"), dict)
            else content.get("publisher", "")
        )
        if title:
            headlines.append(f"- {title}" + (f" ({publisher})" if publisher else ""))

    if not headlines:
        return {"content": [{"type": "text", "text": f"No recent news for {symbol}."}]}
    return {"content": [{"type": "text", "text": f"Recent {symbol} headlines:\n" + "\n".join(headlines)}]}


# The server object other modules import. Tool names become mcp__market_data__<tool>.
market_data_server = create_sdk_mcp_server(
    name="market_data",
    version="0.2.0",
    tools=[get_quote_tool, get_recent_bars_tool, get_fundamentals_tool, get_news_tool],
)

# Full surface, plus per-analyst scoped subsets (Phase 4).
MARKET_DATA_TOOLS = [
    "mcp__market_data__get_quote",
    "mcp__market_data__get_recent_bars",
    "mcp__market_data__get_fundamentals",
    "mcp__market_data__get_news",
]
TECHNICAL_TOOLS = ["mcp__market_data__get_quote", "mcp__market_data__get_recent_bars"]
FUNDAMENTAL_TOOLS = ["mcp__market_data__get_fundamentals", "mcp__market_data__get_quote"]
SENTIMENT_TOOLS = ["mcp__market_data__get_news"]


async def _demo() -> None:
    """Prove the reusable tool surface works: an agent answers using BOTH tools."""
    import os

    from dotenv import load_dotenv

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        query,
    )

    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — check algo-desk/.env")

    options = ClaudeAgentOptions(
        mcp_servers={"market_data": market_data_server},
        allowed_tools=MARKET_DATA_TOOLS,
        model="claude-sonnet-4-6",
        system_prompt=(
            "You are a market-data analyst. Use the tools to gather facts, then give a "
            "2-3 sentence read on trend and momentum. Do not invent numbers."
        ),
    )
    prompt = "How has BTC/USD trended over the last 60 days, and what's the current price?"
    print(f"\n[USER] {prompt}\n")
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[TOOL CALL] {block.name}({block.input})")
                elif isinstance(block, TextBlock):
                    print(f"[AGENT] {block.text}")
        elif isinstance(message, ResultMessage):
            cost = getattr(message, "total_cost_usd", None)
            if cost is not None:
                print(f"\n[run cost] ${cost:.4f}")


if __name__ == "__main__":
    import anyio

    anyio.run(_demo)
