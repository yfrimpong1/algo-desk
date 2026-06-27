"""Typed contracts shared across the whole desk.

Why this file exists
--------------------
Agents are non-deterministic text generators. To build a *reliable* system on top
of them, every agent must hand the next stage a **structured, validated object** —
not free-form prose. These pydantic models are those contracts. They let us:
  * validate an agent actually produced the fields we need (or fail loudly),
  * pass state cleanly between agents (analyst -> researcher -> risk -> PM -> exec),
  * log every decision to runs/ as JSON for audit/replay,
  * render the data in the Streamlit GUI without guesswork.

Phase 0 only uses `Bar` and `Quote`. The decision-stage models (Signal, Order, ...)
are defined now so the contract is visible from the start; later phases fill them in.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Market data (Phase 0–1)
# --------------------------------------------------------------------------- #
class Bar(BaseModel):
    """A single OHLCV candle for one symbol at one timestamp."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Quote(BaseModel):
    """A lightweight 'latest price' snapshot for a symbol."""

    symbol: str
    price: float
    timestamp: datetime
    source: str = Field(description="e.g. 'ccxt:binance' or 'yfinance'")


# --------------------------------------------------------------------------- #
# Decision pipeline (filled in from Phase 2 onward — defined now as the contract)
# --------------------------------------------------------------------------- #
class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Signal(BaseModel):
    """An analyst agent's opinion on one symbol. Produced in Phase 4."""

    symbol: str
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0, description="0..1 conviction")
    rationale: str
    analyst: str = Field(description="which analyst produced this, e.g. 'technical'")


class RiskVerdict(BaseModel):
    """Risk manager's gate decision on a proposed trade. Produced in Phase 5."""

    approved: bool
    reason: str
    max_position_value: Optional[float] = None


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Order(BaseModel):
    """A concrete, sized order emitted by the portfolio manager. Phase 5–6."""

    id: str = Field(default_factory=lambda: uuid4().hex, description="stable id for idempotency")
    symbol: str
    side: OrderSide
    quantity: float
    order_type: str = "market"
    rationale: str = ""
    requires_approval: bool = False  # True when mode == 'live'


class Fill(BaseModel):
    """The broker's confirmation that an order executed. Phase 6."""

    order: Order
    filled_quantity: float
    fill_price: float
    timestamp: datetime
    fees: float = 0.0
    is_paper: bool = True
