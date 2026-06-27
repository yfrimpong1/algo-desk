"""Persistent desk state (runs/desk_state.json).

The orchestrator is a long-running loop that must survive restarts: it has to remember
cash, open positions, which orders it already filled (idempotency), any live orders waiting
for human approval, and the equity curve over time. All of that lives in one JSON file the
GUI also reads.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from src.layer.portfolio import Portfolio

RUNS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "runs"))
STATE_PATH = os.path.join(RUNS_DIR, "desk_state.json")
KILL_SWITCH_PATH = os.path.join(RUNS_DIR, "KILL_SWITCH")


@dataclass
class DeskState:
    portfolio: Portfolio
    processed_order_ids: list[str] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)  # serialized Orders awaiting sign-off
    equity_history: list[dict] = field(default_factory=list)     # [{"t": iso, "equity": float}]
    fills: list[dict] = field(default_factory=list)              # executed fills, for the GUI

    @classmethod
    def load(cls, starting_cash: float) -> "DeskState":
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                d = json.load(f)
            pf = Portfolio(**d["portfolio"])
            return cls(
                portfolio=pf,
                processed_order_ids=d.get("processed_order_ids", []),
                pending_approvals=d.get("pending_approvals", []),
                equity_history=d.get("equity_history", []),
                fills=d.get("fills", []),
            )
        return cls(portfolio=Portfolio.new(starting_cash))

    def save(self) -> None:
        os.makedirs(RUNS_DIR, exist_ok=True)
        payload = {
            "portfolio": {
                "cash": self.portfolio.cash,
                "starting_cash": self.portfolio.starting_cash,
                "positions": self.portfolio.positions,
                "peak_equity": self.portfolio.peak_equity,
            },
            "processed_order_ids": self.processed_order_ids,
            "pending_approvals": self.pending_approvals,
            "equity_history": self.equity_history,
            "fills": self.fills,
        }
        with open(STATE_PATH, "w") as f:
            json.dump(payload, f, indent=2, default=str)


def kill_switch_active() -> bool:
    return os.path.exists(KILL_SWITCH_PATH)
