"""Risk manager — a gate with veto power. Python limits + agent judgment.

Order of authority (hard to soft):
  1. Python drawdown halt: if the account has fallen past the halt threshold, the trade is
     REJECTED before the agent is even consulted. The LLM cannot un-halt the desk.
  2. Python ATR sizing: the position size and its max value are computed deterministically
     (src/layer/risk.size_position). The agent never gets to inflate it.
  3. Agent judgment: given the proposed direction, the sized trade, and the bull/bear debate,
     the risk-manager agent APPROVES or REJECTS with a reason (e.g. "thesis too thin",
     "already over-exposed"). It may reject; it may not increase risk.

Returns a RiskVerdict(approved, reason, max_position_value).
"""

from __future__ import annotations

from claude_agent_sdk import (
    ClaudeAgentOptions,
    create_sdk_mcp_server,
    query,
    tool,
)

from src.layer.risk import RiskLimits, SizingResult, drawdown_halt, size_position
from src.schemas import RiskVerdict


async def run_risk_check(
    symbol: str,
    proposed_direction: str,
    debate: dict,
    *,
    equity: float,
    price: float,
    atr: float,
    current_drawdown: float,
    limits: RiskLimits,
    model: str = "claude-opus-4-8",
) -> tuple[RiskVerdict, SizingResult]:
    """Gate a proposed trade. Returns (verdict, sizing)."""
    sizing = size_position(equity, price, atr, limits)

    # (1) Hard halt — no agent call.
    if drawdown_halt(current_drawdown, limits):
        return (
            RiskVerdict(
                approved=False,
                reason=f"Max-drawdown halt active ({current_drawdown:.1%}). All new risk blocked.",
                max_position_value=0.0,
            ),
            sizing,
        )

    # A buy into a zero-sized position is pointless; reject cheaply without the agent.
    if proposed_direction == "buy" and sizing.quantity <= 0:
        return (
            RiskVerdict(approved=False, reason="Computed position size is zero.", max_position_value=0.0),
            sizing,
        )

    # (3) Agent judgment, capped by the Python sizing.
    captured: dict[str, RiskVerdict] = {}

    async def _submit(args):
        approved = str(args["decision"]).lower().startswith("approve")
        captured["verdict"] = RiskVerdict(
            approved=approved,
            reason=str(args.get("reason", "")),
            max_position_value=sizing.position_value if approved else 0.0,
        )
        return {"content": [{"type": "text", "text": "Risk verdict recorded."}]}

    submit_verdict = tool(
        "submit_risk_verdict",
        "Record your risk decision. decision must be 'approve' or 'reject'. reason is one "
        "sentence. You may reject a thin or reckless thesis; you cannot increase position size.",
        {"decision": str, "reason": str},
    )(_submit)

    server = create_sdk_mcp_server(name="risk", version="0.1.0", tools=[submit_verdict])

    options = ClaudeAgentOptions(
        mcp_servers={"risk": server},
        allowed_tools=["mcp__risk__submit_risk_verdict"],
        model=model,
        system_prompt=(
            "You are the RISK MANAGER on a trading desk — the last line of defense before a trade. "
            "You are conservative. Approve a trade only if the bull/bear debate shows a coherent edge "
            "for the proposed direction and the sizing is reasonable. Reject thin, contradictory, or "
            "reckless theses. Position sizing is already fixed by the desk's risk rules; your job is a "
            "go/no-go judgment, not sizing."
        ),
    )

    prompt = (
        f"Proposed trade on {symbol}: direction={proposed_direction}.\n"
        f"Sized position: qty {sizing.quantity:.6f}, value ${sizing.position_value:,.0f} "
        f"(risking ${sizing.risk_dollars:,.0f}; {sizing.note}).\n"
        f"Account equity ${equity:,.0f}, current drawdown {current_drawdown:.1%}.\n\n"
        f"BULL case:\n{debate.get('bull','(none)')}\n\nBEAR case:\n{debate.get('bear','(none)')}\n\n"
        f"Call submit_risk_verdict with approve or reject."
    )

    async for _ in query(prompt=prompt, options=options):
        pass

    verdict = captured.get(
        "verdict",
        RiskVerdict(approved=False, reason="Risk manager produced no verdict; defaulting to reject.",
                    max_position_value=0.0),
    )
    return verdict, sizing
