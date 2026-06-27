"""Portfolio accountant — tracks cash, positions, equity, and drawdown.

Pure, deterministic bookkeeping. Agents NEVER mutate this directly; only the execution
layer applies fills (Phase 6). Risk sizing (risk.py) reads equity/exposure from here.

`peak_equity` is tracked so the max-drawdown halt (a hard safety limit) can fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.schemas import Fill, OrderSide


@dataclass
class Portfolio:
    cash: float
    starting_cash: float
    positions: dict[str, float] = field(default_factory=dict)  # symbol -> quantity
    peak_equity: float = 0.0

    @classmethod
    def new(cls, starting_cash: float) -> "Portfolio":
        return cls(cash=starting_cash, starting_cash=starting_cash, peak_equity=starting_cash)

    def position_qty(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def position_value(self, symbol: str, price: float) -> float:
        return self.position_qty(symbol) * price

    def equity(self, prices: dict[str, float]) -> float:
        """Total account value = cash + marked-to-market positions."""
        holdings = sum(qty * prices.get(sym, 0.0) for sym, qty in self.positions.items())
        return self.cash + holdings

    def update_peak(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        self.peak_equity = max(self.peak_equity, eq)
        return eq

    def drawdown(self, prices: dict[str, float]) -> float:
        """Current drawdown from peak as a negative fraction (e.g. -0.12 = down 12%)."""
        if self.peak_equity <= 0:
            return 0.0
        return self.equity(prices) / self.peak_equity - 1.0

    def apply_fill(self, fill: Fill) -> None:
        """Update cash and positions from an executed fill (used by execution, Phase 6)."""
        sym = fill.order.symbol
        qty = fill.filled_quantity
        cost = qty * fill.fill_price + fill.fees
        if fill.order.side == OrderSide.BUY:
            self.cash -= cost
            self.positions[sym] = self.position_qty(sym) + qty
        else:  # SELL
            self.cash += qty * fill.fill_price - fill.fees
            self.positions[sym] = self.position_qty(sym) - qty
        if abs(self.positions.get(sym, 0.0)) < 1e-12:
            self.positions.pop(sym, None)

    def snapshot(self, prices: dict[str, float]) -> dict:
        """Compact state for logging / the GUI."""
        return {
            "cash": round(self.cash, 2),
            "equity": round(self.equity(prices), 2),
            "drawdown": round(self.drawdown(prices), 4),
            "positions": {s: round(q, 6) for s, q in self.positions.items()},
        }
