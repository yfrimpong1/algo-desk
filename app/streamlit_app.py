"""AI Trading Desk — Streamlit GUI (Phase 7).

This is a viewer over the desk's state (runs/desk_state.json) and decision logs
(runs/decision_*.json), plus the control surface: run a cycle, toggle paper/live, flip the
kill-switch, and — the safety centerpiece — APPROVE/REJECT live orders parked for sign-off.

Streamlit reruns this whole script on every interaction, so we just re-read the JSON each
time; there is no long-lived in-memory state to corrupt.

Run:  cd algo-desk && .venv/bin/python -m streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import glob
import json
import os

import anyio
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # pulls ANTHROPIC_API_KEY and DESK_SHARE_PASSWORD from .env

from src.agents.execution import Executor
from src.layer.datafeed import get_quote
from src.schemas import Order, OrderSide
from src.settings import load_settings, set_execution_mode
from src.state import DeskState, KILL_SWITCH_PATH, kill_switch_active

st.set_page_config(page_title="AI Trading Desk", page_icon="📈", layout="wide")
RUNS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "runs"))


def cfg(key: str, default: str | None = None) -> str | None:
    """Read config from env (local/.env) first, then Streamlit Cloud st.secrets."""
    v = os.getenv(key)
    if v is not None:
        return v
    try:
        return st.secrets[key]  # type: ignore[index]
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Access gate — protects your API budget when the app is shared via a tunnel.
# If DESK_SHARE_PASSWORD is unset (normal local use), the app is open.
# --------------------------------------------------------------------------- #
def require_password() -> None:
    expected = cfg("DESK_SHARE_PASSWORD")
    if not expected:
        return  # no password configured -> open (local dev)
    if st.session_state.get("authed"):
        return
    st.title("🔒 AI Trading Desk")
    st.caption("This shared demo is password-protected.")
    pw = st.text_input("Access password", type="password")
    if pw and pw == expected:
        st.session_state["authed"] = True
        st.rerun()
    elif pw:
        st.error("Incorrect password.")
    st.stop()


require_password()


# --------------------------------------------------------------------------- #
# Loaders (re-read each rerun)
# --------------------------------------------------------------------------- #
def load_state() -> DeskState:
    return DeskState.load(load_settings()["risk"]["starting_cash"])


def latest_decisions() -> dict[str, dict]:
    """Most recent decision_*.json per symbol."""
    out: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(RUNS, "decision_*.json"))):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        out[d["symbol"]] = d  # later files overwrite earlier -> keeps the newest
    return out


def held_prices(state: DeskState, decisions: dict) -> dict[str, float]:
    prices = {sym: d["price"] for sym, d in decisions.items()}
    for sym in state.portfolio.positions:
        if sym not in prices:
            try:
                prices[sym] = get_quote(sym).price
            except Exception:
                prices[sym] = 0.0
    return prices


# --------------------------------------------------------------------------- #
# Sidebar — controls
# --------------------------------------------------------------------------- #
settings = load_settings()
mode = settings["execution"]["mode"]

# Viewer mode: a read-only public demo. Hides every control that spends API budget or
# mutates state, and never imports/triggers the agent stack. Toggle with DESK_VIEWER_MODE=1.
VIEWER = (cfg("DESK_VIEWER_MODE", "") or "").lower() in ("1", "true", "yes")

if VIEWER:
    st.sidebar.title("👀 Viewer Mode")
    st.sidebar.caption("Read-only demo — open-source multi-agent trading desk")
    st.sidebar.info("This is a live read-only view. Controls (run cycle, approvals, mode) are "
                    "disabled. The desk is driven privately by the developer.")
    st.sidebar.write(f"**Mode:** {mode.upper()}")
    st.sidebar.write(f"**Execution:** {'🛑 HALTED' if kill_switch_active() else '✅ active'}")
    new_mode = mode
else:
    st.sidebar.title("⚙️ Desk Controls")
    st.sidebar.caption("Open-source multi-agent trading desk")

    # Paper / Live toggle
    new_mode = st.sidebar.radio(
        "Execution mode", ["paper", "live"], index=0 if mode == "paper" else 1,
        help="Paper = fully autonomous simulation. Live = every order needs your approval below "
             "(and real execution is intentionally unwired).",
    )
    if new_mode != mode:
        set_execution_mode(new_mode)
        st.rerun()
    if new_mode == "live":
        st.sidebar.warning("LIVE: orders park for your approval. Real execution is unwired (safe).")
    else:
        st.sidebar.success("PAPER: autonomous, no real money.")

    # Kill-switch
    kill_on = st.sidebar.toggle("🛑 Kill-switch (halt all execution)", value=kill_switch_active())
    if kill_on and not kill_switch_active():
        open(KILL_SWITCH_PATH, "w").close(); st.rerun()
    if not kill_on and kill_switch_active():
        os.remove(KILL_SWITCH_PATH); st.rerun()

    st.sidebar.divider()

    # Run a cycle now
    universe = list(settings["universe"].get("crypto", [])) + list(settings["universe"].get("equities", []))
    chosen = st.sidebar.multiselect("Symbols for next cycle", universe, default=universe[:1])
    if st.sidebar.button("▶️ Run decision cycle now", type="primary", width="stretch"):
        from src.orchestrator import run_cycle
        with st.spinner(f"Running the desk on {chosen} … (agents are thinking)"):
            anyio.run(lambda: run_cycle(chosen, load_state()))
        st.success("Cycle complete.")
        st.rerun()

    # Demo helper: park a sample live order so the approval queue is easy to demonstrate.
    if st.sidebar.button("🧪 Add demo live order (for approval)", width="stretch"):
        state = load_state()
        px = get_quote("BTC/USD").price
        demo = Order(symbol="BTC/USD", side=OrderSide.BUY, quantity=0.05,
                     rationale="Demo order to exercise the approval queue.", requires_approval=True)
        state.pending_approvals.append(demo.model_dump(mode="json"))
        state.save(); st.rerun()


# --------------------------------------------------------------------------- #
# Main — dashboard
# --------------------------------------------------------------------------- #
state = load_state()
decisions = latest_decisions()
prices = held_prices(state, decisions)
equity = state.portfolio.equity(prices)
ret_pct = (equity / state.portfolio.starting_cash - 1.0) * 100.0

st.title("📈 AI Multi-Agent Trading Desk")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Equity", f"${equity:,.0f}", f"{ret_pct:+.2f}%")
c2.metric("Cash", f"${state.portfolio.cash:,.0f}")
c3.metric("Open positions", len(state.portfolio.positions))
c4.metric("Mode", new_mode.upper(), "HALTED" if kill_switch_active() else "")

# Equity curve
st.subheader("Equity curve")
if state.equity_history:
    eq = pd.DataFrame(state.equity_history)
    eq["t"] = pd.to_datetime(eq["t"])
    fig = go.Figure(go.Scatter(x=eq["t"], y=eq["equity"], mode="lines+markers", name="Equity"))
    fig.add_hline(y=state.portfolio.starting_cash, line_dash="dash", line_color="gray",
                  annotation_text="starting cash")
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="USD")
    st.plotly_chart(fig, width="stretch")
else:
    st.info("No cycles run yet." if VIEWER else
            "No cycles run yet. Use **Run decision cycle now** in the sidebar.")

# Pending approvals — the human-in-the-loop centerpiece
st.subheader("🔔 Pending approvals")
if state.pending_approvals:
    executor = None if VIEWER else Executor(state, settings)
    for appr in list(state.pending_approvals):
        if VIEWER:
            # Read-only: show the parked order, no action buttons.
            st.write(f"⏳ **{appr['side'].upper()} {appr['quantity']:.6f} {appr['symbol']}** "
                     f"— awaiting developer approval — {appr.get('rationale','')}")
            continue
        cols = st.columns([3, 1, 1])
        cols[0].write(f"**{appr['side'].upper()} {appr['quantity']:.6f} {appr['symbol']}** "
                      f"— {appr.get('rationale','')}")
        if cols[1].button("✅ Approve", key=f"ap_{appr['id']}"):
            px = prices.get(appr["symbol"]) or get_quote(appr["symbol"]).price
            executor.approve(appr["id"], px); state.save(); st.rerun()
        if cols[2].button("❌ Reject", key=f"rj_{appr['id']}"):
            executor.reject(appr["id"]); state.save(); st.rerun()
else:
    st.caption("No orders awaiting approval.")

# Per-symbol latest decision with full agent drilldown
st.subheader("🧠 Latest agent decisions")
if decisions:
    for sym, d in decisions.items():
        order = d.get("order")
        badge = (f"{order['side'].upper()}" if order else "HOLD")
        with st.expander(f"{sym} — proposed {d['proposed_direction'].upper()} · "
                         f"risk {'✅' if d['risk_approved'] else '⛔'} · action {badge} "
                         f"(${d['price']:,.2f})"):
            st.markdown("**Analyst signals**")
            st.dataframe(pd.DataFrame(d["signals"])[["analyst", "direction", "confidence", "rationale"]],
                         hide_index=True, width="stretch")
            cc = st.columns(2)
            cc[0].markdown("**🐂 Bull case**"); cc[0].write(d["debate"]["bull"])
            cc[1].markdown("**🐻 Bear case**"); cc[1].write(d["debate"]["bear"])
            st.markdown(f"**Risk verdict:** {'APPROVED ✅' if d['risk_approved'] else 'REJECTED ⛔'} "
                        f"— {d['risk_reason']}")
            if order:
                st.markdown(f"**Order:** {order['side'].upper()} {order['quantity']:.6f} {sym} "
                            f"· approval_required={order['requires_approval']}")
else:
    st.caption("No decisions logged yet.")

# Recent fills
st.subheader("📜 Recent fills")
if state.fills:
    rows = [{"symbol": f["order"]["symbol"], "side": f["order"]["side"],
             "qty": round(f["filled_quantity"], 6), "price": round(f["fill_price"], 2),
             "fees": round(f["fees"], 2), "paper": f["is_paper"], "time": f["timestamp"]}
            for f in state.fills]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
else:
    st.caption("No fills yet.")
