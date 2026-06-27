"""Broker adapters: paper (simulated) and live (stubbed, approval-gated).

Both implement the same `place_order(order, ref_price)` -> Fill interface, so the executor
doesn't care which is active. Swapping to a real exchange later (ccxt/Alpaca) means writing
one new adapter with this signature — nothing upstream changes.

Despite living under mcp_servers/, these are plain Python (the executor calls them directly).
The name reflects their role as the desk's "execution venue"; we can expose them as MCP tools
later if an agent ever needs to query broker state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.schemas import Fill, Order, OrderSide


class ApprovalRequired(Exception):
    """Raised by the live broker when an order has no human approval token."""


class PaperBroker:
    """Fills orders against a reference price with slippage + fees. No real money."""

    def __init__(self, fee_bps: float = 10.0, slippage_bps: float = 5.0):
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def place_order(self, order: Order, ref_price: float) -> Fill:
        slip = self.slippage_bps / 10_000.0
        # You buy a touch above and sell a touch below the reference — that's slippage.
        if order.side == OrderSide.BUY:
            fill_price = ref_price * (1.0 + slip)
        else:
            fill_price = ref_price * (1.0 - slip)
        fees = order.quantity * fill_price * (self.fee_bps / 10_000.0)
        return Fill(
            order=order,
            filled_quantity=order.quantity,
            fill_price=fill_price,
            timestamp=datetime.now(timezone.utc),
            fees=fees,
            is_paper=True,
        )


class LiveBroker:
    """Stub for real execution. Refuses to trade without an approval token, and even then
    is intentionally NOT wired to a real exchange yet — going live is a deliberate, separate
    step (write a ccxt/Alpaca adapter here) so you can never accidentally trade real money."""

    def __init__(self, fee_bps: float = 10.0, slippage_bps: float = 5.0):
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def place_order(self, order: Order, ref_price: float, approval_token: str | None = None) -> Fill:
        if not approval_token:
            raise ApprovalRequired(f"Order {order.id} for {order.symbol} needs human approval.")
        raise NotImplementedError(
            "Live trading is not wired. Approval was given, but no real exchange adapter is "
            "configured — implement a ccxt/Alpaca adapter here before trading real capital."
        )


def make_broker(mode: str, fee_bps: float, slippage_bps: float):
    return (LiveBroker if mode == "live" else PaperBroker)(fee_bps, slippage_bps)
