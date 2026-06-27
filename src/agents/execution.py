"""Execution — deterministic on purpose (the one place we never let an LLM improvise).

Given an Order from the portfolio manager, the executor decides whether and how it actually
hits the broker, enforcing three safety rules in order:

  1. KILL-SWITCH: if runs/KILL_SWITCH exists, nothing executes. Full stop.
  2. IDEMPOTENCY: an order id already processed is never filled again (safe retries/restarts).
  3. APPROVAL GATE: an order flagged requires_approval (live mode) is parked in the pending
     queue for the GUI, NOT filled. Paper-mode orders auto-fill.

Successful fills update the Portfolio and are recorded to state for the GUI/audit trail.
"""

from __future__ import annotations

from src.mcp_servers.broker import make_broker
from src.schemas import Fill, Order
from src.state import DeskState, kill_switch_active


class Executor:
    def __init__(self, state: DeskState, settings: dict):
        self.state = state
        self.mode = settings["execution"]["mode"]
        bt = settings.get("backtest", {})
        self.broker = make_broker(
            self.mode,
            fee_bps=float(bt.get("fee_bps", 10.0)),
            slippage_bps=float(bt.get("slippage_bps", 5.0)),
        )

    def execute(self, order: Order | None, ref_price: float) -> Fill | None:
        """Run an order through the safety rules and (if clear) fill it. Returns the Fill."""
        if order is None:
            return None

        # (1) Kill-switch — global manual stop.
        if kill_switch_active():
            print(f"  [executor] KILL-SWITCH active — skipping {order.side.value} {order.symbol}")
            return None

        # (2) Idempotency — never fill the same order id twice.
        if order.id in self.state.processed_order_ids:
            print(f"  [executor] order {order.id[:8]} already processed — skipping")
            return None

        # (3) Approval gate — live orders wait for a human; paper orders proceed.
        if order.requires_approval:
            already = any(a.get("id") == order.id for a in self.state.pending_approvals)
            if not already:
                self.state.pending_approvals.append(order.model_dump(mode="json"))
                print(f"  [executor] LIVE order parked for approval: "
                      f"{order.side.value} {order.quantity:.6f} {order.symbol}")
            return None

        # Paper (or already-approved) path: fill it.
        return self._fill(order, ref_price)

    def _fill(self, order: Order, ref_price: float) -> Fill:
        fill = self.broker.place_order(order, ref_price)
        self.state.portfolio.apply_fill(fill)
        self.state.processed_order_ids.append(order.id)
        self.state.fills.append(fill.model_dump(mode="json"))
        print(f"  [executor] FILLED {order.side.value} {fill.filled_quantity:.6f} "
              f"{order.symbol} @ ${fill.fill_price:,.2f} (fees ${fill.fees:,.2f})")
        return fill

    # --- Approval flow used by the GUI (Phase 7) ---------------------------- #
    def approve(self, order_id: str, ref_price: float) -> Fill | None:
        """Approve a parked live order and execute it.

        Real live trading is intentionally unwired (LiveBroker raises), so the approved fill
        is SIMULATED via a paper broker — enough to demonstrate the human-in-the-loop flow
        without touching real money. Wiring a ccxt/Alpaca LiveBroker is the only change needed
        to make this route real capital."""
        from src.mcp_servers.broker import PaperBroker

        match = next((a for a in self.state.pending_approvals if a.get("id") == order_id), None)
        if not match:
            return None
        self.state.pending_approvals = [
            a for a in self.state.pending_approvals if a.get("id") != order_id
        ]
        order = Order(**match)
        # Simulated approved fill (clearly marked is_paper=True).
        fill = PaperBroker(self.broker.fee_bps, self.broker.slippage_bps).place_order(order, ref_price)
        self.state.portfolio.apply_fill(fill)
        self.state.processed_order_ids.append(order.id)
        self.state.fills.append(fill.model_dump(mode="json"))
        return fill

    def reject(self, order_id: str) -> None:
        """Reject (discard) a parked live order."""
        self.state.pending_approvals = [
            a for a in self.state.pending_approvals if a.get("id") != order_id
        ]
