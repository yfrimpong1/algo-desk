"""Risk management — deterministic guardrails the LLM cannot override.

The risk-manager AGENT (src/agents/risk_manager.py) adds *judgment* on top of these
functions, but these hard limits always win. Inputs come from config/settings.yaml ->
risk: { max_position_pct, per_trade_risk_pct, max_drawdown_halt_pct }.

Core idea — size by RISK, not by dollars:
  risk_$        = equity * per_trade_risk_pct      (what we're willing to lose)
  stop_distance = stop_atr_mult * ATR              (where the stop sits, in price units)
  quantity      = risk_$ / stop_distance           (so a stop-out loses ~risk_$)
then cap quantity so position value <= equity * max_position_pct.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskLimits:
    max_position_pct: float
    per_trade_risk_pct: float
    max_drawdown_halt_pct: float

    @classmethod
    def from_settings(cls, settings: dict) -> "RiskLimits":
        r = settings["risk"]
        return cls(
            max_position_pct=float(r["max_position_pct"]),
            per_trade_risk_pct=float(r["per_trade_risk_pct"]),
            max_drawdown_halt_pct=float(r["max_drawdown_halt_pct"]),
        )


@dataclass
class SizingResult:
    quantity: float
    position_value: float
    risk_dollars: float
    stop_distance: float
    capped_by_max_position: bool
    note: str = ""


def drawdown_halt(current_drawdown: float, limits: RiskLimits) -> bool:
    """True if losses have hit the halt threshold (drawdown is negative)."""
    return current_drawdown <= -abs(limits.max_drawdown_halt_pct)


def size_position(
    equity: float,
    price: float,
    atr: float,
    limits: RiskLimits,
    stop_atr_mult: float = 2.0,
) -> SizingResult:
    """Compute a long position size from ATR-based risk, capped by max position %."""
    if price <= 0 or equity <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, False, "non-positive price/equity")

    risk_dollars = equity * limits.per_trade_risk_pct
    # Fall back to a 5% price move if ATR is missing/zero (e.g. warm-up window).
    stop_distance = stop_atr_mult * atr if atr and atr > 0 else 0.05 * price

    qty = risk_dollars / stop_distance if stop_distance > 0 else 0.0

    max_value = equity * limits.max_position_pct
    capped = False
    if qty * price > max_value:
        qty = max_value / price
        capped = True

    return SizingResult(
        quantity=qty,
        position_value=qty * price,
        risk_dollars=risk_dollars,
        stop_distance=stop_distance,
        capped_by_max_position=capped,
        note="sized by ATR risk" + (" (capped by max position %)" if capped else ""),
    )


if __name__ == "__main__":
    from src.settings import load_settings

    limits = RiskLimits.from_settings(load_settings())
    print("Limits:", limits)
    # Example: $100k equity, BTC at 60,600 with ATR ~2,300.
    s = size_position(equity=100_000, price=60_600, atr=2_300, limits=limits)
    print(f"\nBTC sizing: qty={s.quantity:.4f}  value=${s.position_value:,.0f}  "
          f"risk=${s.risk_dollars:,.0f}  stop_dist=${s.stop_distance:,.0f}  {s.note}")
    # Calm asset (small ATR) -> bigger position, but capped by max position %.
    s2 = size_position(equity=100_000, price=284, atr=4, limits=limits)
    print(f"AAPL sizing: qty={s2.quantity:.2f}  value=${s2.position_value:,.0f}  "
          f"risk=${s2.risk_dollars:,.0f}  stop_dist=${s2.stop_distance:,.2f}  {s2.note}")
    print("\nDrawdown halt at -25%?", drawdown_halt(-0.25, limits))
    print("Drawdown halt at -10%?", drawdown_halt(-0.10, limits))
