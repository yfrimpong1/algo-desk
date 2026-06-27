"""Decision core — the full desk pipeline for ONE symbol.

    analysts (parallel)  ->  bull/bear debate  ->  risk gate  ->  portfolio manager  ->  Order?

This is the heart of the system. Phase 6 wraps it in a scheduler over the whole universe and
sends the resulting Order to the broker; Phase 7's GUI renders the artifacts this produces.

Every decision is logged to runs/ as JSON so it can be audited, replayed, and displayed.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from src.agents.analyst import run_all_analysts
from src.agents.portfolio_manager import run_portfolio_manager
from src.agents.researcher_bull_bear import run_debate
from src.agents.risk_manager import run_risk_check
from src.layer.datafeed import get_bars
from src.layer.indicators import add_indicators
from src.layer.portfolio import Portfolio
from src.layer.risk import RiskLimits
from src.schemas import Order, Signal
from src.settings import load_settings

load_dotenv()

RUNS_DIR = os.path.join(os.path.dirname(__file__), "..", "runs")


def _proposed_direction(signals: list[Signal]) -> str:
    """Confidence-weighted vote across analysts -> 'buy' | 'sell' | 'hold'."""
    score = 0.0
    for s in signals:
        if s.direction.value == "buy":
            score += s.confidence
        elif s.direction.value == "sell":
            score -= s.confidence
    if score > 0.15:
        return "buy"
    if score < -0.15:
        return "sell"
    return "hold"


@dataclass
class DecisionResult:
    symbol: str
    timestamp: str
    price: float
    atr: float
    signals: list[Signal]
    debate: dict
    proposed_direction: str
    risk_approved: bool
    risk_reason: str
    order: Optional[Order]

    def log(self) -> str:
        os.makedirs(os.path.abspath(RUNS_DIR), exist_ok=True)
        safe = self.symbol.replace("/", "-")
        path = os.path.abspath(os.path.join(RUNS_DIR, f"decision_{safe}_{self.timestamp}.json"))
        payload = {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "price": self.price,
            "atr": self.atr,
            "proposed_direction": self.proposed_direction,
            "risk_approved": self.risk_approved,
            "risk_reason": self.risk_reason,
            "signals": [s.model_dump(mode="json") for s in self.signals],
            "debate": self.debate,
            "order": self.order.model_dump(mode="json") if self.order else None,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        return path


async def decide(
    symbol: str,
    portfolio: Portfolio,
    *,
    timeframe: str = "1d",
    debate_rounds: int = 1,
) -> DecisionResult:
    settings = load_settings()
    limits = RiskLimits.from_settings(settings)
    mode = settings["execution"]["mode"]

    # Market context (deterministic): latest price + ATR for sizing.
    bars = add_indicators(get_bars(symbol, timeframe, start="2025-06-01"))
    price = float(bars["close"].iloc[-1])
    atr = float(bars["atr"].iloc[-1])
    prices = {symbol: price}
    portfolio.update_peak(prices)
    current_dd = portfolio.drawdown(prices)

    # 1) Analysts (parallel) -> signals
    signals = await run_all_analysts(symbol)

    # 2) Bull/bear debate over the signals
    debate = await run_debate(symbol, signals, rounds=debate_rounds)

    # 3) Proposed direction (confidence-weighted vote) -> risk gate
    proposed = _proposed_direction(signals)
    verdict, sizing = await run_risk_check(
        symbol, proposed, debate,
        equity=portfolio.equity(prices), price=price, atr=atr,
        current_drawdown=current_dd, limits=limits,
    )

    # 4) Portfolio manager -> final Order (or None)
    order = await run_portfolio_manager(
        symbol, signals, debate, verdict, sizing,
        current_position_qty=portfolio.position_qty(symbol), price=price, mode=mode,
    )

    result = DecisionResult(
        symbol=symbol,
        timestamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        price=price, atr=atr, signals=signals, debate=debate,
        proposed_direction=proposed, risk_approved=verdict.approved,
        risk_reason=verdict.reason, order=order,
    )
    result.log()
    return result


if __name__ == "__main__":
    import sys

    import anyio

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — check algo-desk/.env")

    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC/USD"

    async def main():
        settings = load_settings()
        pf = Portfolio.new(settings["risk"]["starting_cash"])
        print(f"\n=== DESK DECISION CYCLE: {symbol} ===\n")
        r = await decide(symbol, pf)

        print("ANALYST SIGNALS:")
        for s in r.signals:
            print(f"  {s.analyst:12s} {s.direction.value.upper():4s} conf={s.confidence:.2f}")
        print(f"\nPROPOSED (weighted vote): {r.proposed_direction.upper()}")
        print(f"\nBULL:\n  {r.debate['bull'][:280]}...")
        print(f"\nBEAR:\n  {r.debate['bear'][:280]}...")
        print(f"\nRISK GATE: {'APPROVED' if r.risk_approved else 'REJECTED'} — {r.risk_reason}")
        if r.order:
            o = r.order
            print(f"\nFINAL ORDER: {o.side.value.upper()} {o.quantity:.6f} {o.symbol} "
                  f"@ ~${r.price:,.2f}  (approval_required={o.requires_approval})")
            print(f"  rationale: {o.rationale}")
        else:
            print("\nFINAL ORDER: none (HOLD / stand pat)")
        print(f"\nLogged to runs/. Price=${r.price:,.2f}  ATR=${r.atr:,.2f}")

    anyio.run(main)
