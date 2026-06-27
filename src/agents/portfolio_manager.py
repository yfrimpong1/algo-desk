"""Portfolio manager — the final decider. Emits one sized Order (or None = stand pat).

The PM is the judge of the whole desk. It sees the analyst signals, the bull/bear debate,
and the risk verdict, then makes ONE call: buy, sell, or hold this symbol.

Authority rules baked in:
  * If the risk manager REJECTED, the PM may only hold or reduce/exit an existing position —
    it cannot open or add risk. This is enforced in code after the agent decides, so a
    misbehaving PM still can't override the risk gate.
  * Quantity comes from the deterministic ATR sizing (for buys) or the current position
    (for sells/exits). The agent decides DIRECTION; Python decides SIZE.
  * requires_approval is set True in live mode so the execution layer (Phase 6) holds the
    order for human sign-off in the GUI.
"""

from __future__ import annotations

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)

from src.agents.researcher_bull_bear import format_signals
from src.layer.risk import SizingResult
from src.schemas import Order, OrderSide, RiskVerdict, Signal


async def run_portfolio_manager(
    symbol: str,
    signals: list[Signal],
    debate: dict,
    risk_verdict: RiskVerdict,
    sizing: SizingResult,
    *,
    current_position_qty: float,
    price: float,
    mode: str = "paper",
    model: str = "claude-opus-4-8",
) -> Order | None:
    """Decide the final action and return an Order (or None to stand pat)."""
    captured: dict[str, str] = {}

    async def _submit(args):
        captured["action"] = str(args["action"]).lower()
        captured["rationale"] = str(args.get("rationale", ""))
        return {"content": [{"type": "text", "text": "Decision recorded."}]}

    submit_decision = tool(
        "submit_decision",
        "Record your final decision. action must be 'buy', 'sell', or 'hold'. "
        "rationale is one or two sentences. Choose 'hold' to stand pat.",
        {"action": str, "rationale": str},
    )(_submit)

    server = create_sdk_mcp_server(name="pm", version="0.1.0", tools=[submit_decision])

    options = ClaudeAgentOptions(
        mcp_servers={"pm": server},
        allowed_tools=["mcp__pm__submit_decision"],
        model=model,
        system_prompt=(
            "You are the PORTFOLIO MANAGER — the final decision maker on the desk. Weigh the "
            "analyst signals, the bull/bear debate, and the risk verdict, then decide: buy, sell, "
            "or hold this one symbol. Respect the risk verdict: if risk REJECTED the trade, do not "
            "buy. Prefer 'hold' when the edge is unclear — not trading is a valid, often wise choice."
        ),
    )

    held = "holding" if current_position_qty > 0 else "flat (no position)"
    prompt = (
        f"Decision for {symbol} (currently {held}, price ${price:,.2f}).\n\n"
        f"Analyst signals:\n{format_signals(signals)}\n\n"
        f"BULL:\n{debate.get('bull','')}\n\nBEAR:\n{debate.get('bear','')}\n\n"
        f"RISK VERDICT: {'APPROVED' if risk_verdict.approved else 'REJECTED'} — {risk_verdict.reason}\n"
        f"(If approved, a buy would be ~{sizing.quantity:.6f} units / ${sizing.position_value:,.0f}.)\n\n"
        f"Call submit_decision with buy, sell, or hold."
    )

    async for _ in query(prompt=prompt, options=options):
        pass

    action = captured.get("action", "hold")
    rationale = captured.get("rationale", "")

    # Enforce the risk gate in code: a rejected trade can never become a buy.
    if action == "buy" and not risk_verdict.approved:
        action = "hold"
        rationale = f"[Overridden by risk gate] {rationale}"

    if action == "buy":
        return Order(symbol=symbol, side=OrderSide.BUY, quantity=sizing.quantity,
                     rationale=rationale, requires_approval=(mode == "live"))
    if action == "sell":
        # Exit the existing position (long-only desk); nothing to sell if flat.
        if current_position_qty <= 0:
            return None
        return Order(symbol=symbol, side=OrderSide.SELL, quantity=current_position_qty,
                     rationale=rationale, requires_approval=(mode == "live"))
    return None  # hold
