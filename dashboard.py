"""
Polymarket Bot — Dashboard (Polymarket-style UI)
"""
import json
import re
import math
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import streamlit as st
import plotly.graph_objects as go
import requests

st.set_page_config(page_title="Polymarket Bot", page_icon="", layout="wide", initial_sidebar_state="collapsed")

# --- Polymarket-style light/clean theme ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    .stApp { background: #f7f7f8; font-family: 'Inter', sans-serif; }
    header[data-testid="stHeader"] { background: #fff; border-bottom: 1px solid #e5e7eb; }
    #MainMenu, footer { visibility: hidden; }

    /* Top nav bar */
    .topbar {
        background: #fff; padding: 12px 24px; border-bottom: 1px solid #e5e7eb;
        display: flex; align-items: center; justify-content: space-between;
        margin: -1rem -1rem 20px -1rem;
    }
    .topbar-logo { font-size: 20px; font-weight: 800; color: #1a1a2e; display: flex; align-items: center; gap: 8px; }
    .topbar-right { display: flex; align-items: center; gap: 16px; font-size: 13px; }
    .topbar-item { color: #6b7280; }
    .topbar-value { font-weight: 700; color: #1a1a2e; }
    .topbar-green { color: #16a34a; font-weight: 600; }

    /* Cards */
    .pm-card {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .pm-card-title { font-size: 13px; color: #6b7280; font-weight: 500; margin-bottom: 4px; }
    .pm-card-value { font-size: 36px; font-weight: 800; color: #1a1a2e; }
    .pm-card-sub { font-size: 14px; margin-top: 4px; }
    .pm-green { color: #16a34a; }
    .pm-red { color: #dc2626; }

    /* Available badge */
    .available-box {
        text-align: right;
    }
    .available-label { font-size: 12px; color: #6b7280; }
    .available-value { font-size: 28px; font-weight: 700; color: #1a1a2e; }

    /* PnL card */
    .pnl-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
    .pnl-title { font-size: 13px; color: #6b7280; display: flex; align-items: center; gap: 6px; }
    .pnl-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .pnl-tabs { display: flex; gap: 4px; }
    .pnl-tab {
        padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
        color: #6b7280; background: transparent; cursor: pointer;
    }
    .pnl-tab.active { background: #e5e7eb; color: #1a1a2e; }
    .pnl-value { font-size: 32px; font-weight: 800; }
    .pnl-period { font-size: 12px; color: #9ca3af; margin-top: 2px; }

    /* Claim card */
    .claim-card {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 16px 20px; display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 8px;
    }
    .claim-left { display: flex; align-items: center; gap: 12px; }
    .claim-icons {
        display: flex; gap: -4px;
    }
    .claim-icon {
        width: 36px; height: 36px; border-radius: 8px; display: inline-flex;
        align-items: center; justify-content: center; font-size: 16px; font-weight: 700;
    }
    .claim-text { font-size: 15px; font-weight: 600; color: #1a1a2e; }
    .claim-amount { font-weight: 800; }
    .claim-btn {
        background: #2563eb; color: #fff; padding: 8px 20px; border-radius: 8px;
        font-size: 13px; font-weight: 700; border: none; cursor: pointer;
    }

    /* Position rows */
    .pos-row {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
        padding: 14px 18px; margin-bottom: 6px;
        display: flex; align-items: center; justify-content: space-between;
    }
    .pos-left { display: flex; align-items: center; gap: 12px; }
    .pos-badge {
        padding: 3px 10px; border-radius: 6px; font-size: 11px; font-weight: 700;
    }
    .pos-badge-yes { background: #dcfce7; color: #16a34a; }
    .pos-badge-no { background: #fef2f2; color: #dc2626; }
    .pos-name { font-size: 14px; font-weight: 600; color: #1a1a2e; }
    .pos-detail { font-size: 12px; color: #9ca3af; }
    .pos-right { text-align: right; }
    .pos-pnl { font-size: 15px; font-weight: 700; }
    .pos-amount { font-size: 12px; color: #9ca3af; }

    /* Trade history */
    .trade-row {
        display: flex; align-items: center; padding: 10px 0;
        border-bottom: 1px solid #f3f4f6; font-size: 13px;
    }
    .trade-result {
        width: 50px; font-weight: 700; font-size: 12px;
    }
    .trade-dir { width: 40px; font-weight: 600; }
    .trade-market { flex: 1; color: #4b5563; }
    .trade-pnl { width: 70px; text-align: right; font-weight: 700; }

    /* Stats grid */
    .stat-grid {
        display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px;
    }
    .stat-item {
        background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px;
        padding: 14px; text-align: center;
    }
    .stat-label { font-size: 11px; color: #9ca3af; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }
    .stat-value { font-size: 22px; font-weight: 800; color: #1a1a2e; margin-top: 4px; }

    /* Section title */
    .section-title {
        font-size: 16px; font-weight: 700; color: #1a1a2e; margin: 24px 0 12px 0;
    }

    /* Hide default streamlit metric styling */
    div[data-testid="stMetric"] { display: none; }

    /* Buttons */
    .pm-btn {
        padding: 10px 24px; border-radius: 8px; font-size: 14px;
        font-weight: 700; border: none; cursor: pointer; display: inline-block;
    }
    .pm-btn-blue { background: #2563eb; color: #fff; }
    .pm-btn-outline { background: #fff; color: #1a1a2e; border: 1px solid #e5e7eb; }

    /* Live indicator */
    .live-badge {
        display: inline-flex; align-items: center; gap: 6px;
        background: #dcfce7; color: #16a34a; padding: 4px 12px;
        border-radius: 20px; font-size: 12px; font-weight: 600;
    }
    .live-dot-sm {
        width: 6px; height: 6px; background: #16a34a; border-radius: 50%;
        animation: pulse 2s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
</style>
""", unsafe_allow_html=True)

# Auto-refresh
st.markdown('<meta http-equiv="refresh" content="15">', unsafe_allow_html=True)

# --- Load data ---
@st.cache_data(ttl=8)
def load_positions():
    try:
        with open("data/positions.json") as f:
            return json.load(f)
    except Exception:
        return {"capital": 0, "positions": [], "closed": [], "daily": {}}

@st.cache_data(ttl=8)
def load_status():
    try:
        return requests.get("http://127.0.0.1:8080/api/status", timeout=3).json()
    except Exception:
        return {}

@st.cache_data(ttl=8)
def load_bot_log(n=400):
    try:
        p = Path("data/bot_log.txt")
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []

data = load_positions()
status = load_status()
all_closed = data.get("closed", [])
positions = data.get("positions", [])
capital = data.get("capital", 0)

# --- MILAT FILTER ---
MILAT_CAPITAL = 75.0

def is_after_milat(trade):
    q = trade.get("question", "")
    if "March 19" not in q:
        return False
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", q, re.IGNORECASE)
    if not m:
        return False
    h = int(m.group(1))
    ap = m.group(3).upper()
    if ap == "PM" and h != 12: h += 12
    elif ap == "AM" and h == 12: h = 0
    return h >= 6

closed = [t for t in all_closed if is_after_milat(t)]
ansi_clean = lambda s: re.sub(r'\x1b\[[0-9;]*m', '', s)

# --- Calculations ---
wins = [t for t in closed if t.get("result") == "WIN"]
losses = [t for t in closed if t.get("result") == "LOSS"]
neutrals = [t for t in closed if t.get("result") not in ("WIN", "LOSS")]
total_pnl = sum(t.get("pnl", 0) for t in closed)

# Portfolio calculation — same as Polymarket shows
# status API positions have current_value from live market prices
locked = 0
pos_current_value = 0
api_positions = status.get("positions", {}) if status else {}
for pid, p in api_positions.items():
    if isinstance(p, dict):
        locked += p.get("amount", 0)
        cv = p.get("current_value", 0)
        if cv > 0:
            pos_current_value += cv

available = capital - locked  # cash available to trade
current_equity = available + pos_current_value  # portfolio = cash + position market value
open_positions_list = [p for p in api_positions.values() if isinstance(p, dict)]

pnl_since_milat = current_equity - MILAT_CAPITAL
wr = len(wins) * 100 / (len(wins) + len(losses)) if (len(wins) + len(losses)) > 0 else 0
target_pct = min(100.0, current_equity / 300 * 100)

# Parse regime & fear from logs
log_lines = load_bot_log(400)
regime_text, regime_str, fng_value, fng_label = "NEUTRAL", 0, 0, "N/A"
for l in reversed(log_lines):
    cl = ansi_clean(l)
    if regime_text == "NEUTRAL":
        rm = re.search(r"REGIME:\s*(\w+)\s*\(str=([\d.]+)\)", cl)
        if rm:
            regime_text = rm.group(1)
            regime_str = float(rm.group(2))
    if fng_value == 0:
        fm = re.search(r"FEAR_GREED:\s*(\d+)\s*\(([^)]+)\)", cl)
        if fm:
            fng_value = int(fm.group(1))
            fng_label = fm.group(2)
    if regime_text != "NEUTRAL" and fng_value > 0:
        break

# Recent wins for claim cards
recent_wins = [t for t in closed if t.get("result") == "WIN"][-5:]

# =========================================================
#  TOP NAV BAR
# =========================================================
regime_colors = {"BULLISH": "#16a34a", "BEARISH": "#dc2626", "NEUTRAL": "#d97706"}
rc = regime_colors.get(regime_text, "#6b7280")

st.markdown(
    f'<div class="topbar">'
    f'<div class="topbar-logo">'
    f'<svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M3 3h18v18H3V3z" fill="#1a1a2e"/><path d="M7 12l3 3 7-7" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    f' Polymarket Bot</div>'
    f'<div class="topbar-right">'
    f'<span class="live-badge"><span class="live-dot-sm"></span> LIVE</span>'
    f'<span class="topbar-item">Cycle <span class="topbar-value">#{status.get("cycle", "?")}</span></span>'
    f'<span class="topbar-item">Regime <span style="color:{rc};font-weight:700;">{regime_text}</span></span>'
    f'<span class="topbar-item">F&G <span class="topbar-value">{fng_value}</span> <span style="font-size:11px;color:#9ca3af;">{fng_label}</span></span>'
    f'<span class="topbar-item">Portfolio <span class="topbar-green">${capital:.2f}</span></span>'
    f'<span class="topbar-item">Cash <span class="topbar-value">${available:.2f}</span></span>'
    f'</div></div>',
    unsafe_allow_html=True
)

# =========================================================
#  PORTFOLIO + PNL CARDS (like Polymarket)
# =========================================================
port_col, pnl_col = st.columns([1, 1])

with port_col:
    pnl_color = "pm-green" if pnl_since_milat >= 0 else "pm-red"
    pnl_sign = "+" if pnl_since_milat >= 0 else ""
    pnl_pct = pnl_since_milat / MILAT_CAPITAL * 100

    st.markdown(
        f'<div class="pm-card">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<div>'
        f'<div class="pm-card-title">Portfolio</div>'
        f'<div class="pm-card-value">${current_equity:.2f}</div>'
        f'<div class="pm-card-sub {pnl_color}">{pnl_sign}${pnl_since_milat:.2f} ({pnl_pct:+.1f}%) since milat</div>'
        f'</div>'
        f'<div class="available-box">'
        f'<div class="available-label">Available to trade</div>'
        f'<div class="available-value">${available:.2f}</div>'
        f'</div>'
        f'</div>'
        f'<div class="stat-grid">'
        f'<div class="stat-item"><div class="stat-label">Trades</div><div class="stat-value">{len(closed)}</div></div>'
        f'<div class="stat-item"><div class="stat-label">Win Rate</div><div class="stat-value">{wr:.0f}%</div></div>'
        f'<div class="stat-item"><div class="stat-label">Open</div><div class="stat-value">{len(open_positions_list)}</div></div>'
        f'<div class="stat-item"><div class="stat-label">Target</div><div class="stat-value">{target_pct:.0f}%</div></div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True
    )

with pnl_col:
    pnl_display_color = "#dc2626" if pnl_since_milat < 0 else "#16a34a"
    dot_color = "#dc2626" if pnl_since_milat < 0 else "#16a34a"

    st.markdown(
        f'<div class="pm-card">'
        f'<div class="pnl-header">'
        f'<div class="pnl-title"><span class="pnl-dot" style="background:{dot_color};"></span> Profit/Loss</div>'
        f'<div class="pnl-tabs">'
        f'<span class="pnl-tab active">Today</span>'
        f'</div></div>'
        f'<div class="pnl-value" style="color:{pnl_display_color};">{pnl_sign}${abs(pnl_since_milat):.2f}</div>'
        f'<div class="pnl-period">Since TR 13:00 (Milat)</div>'
        f'</div>',
        unsafe_allow_html=True
    )

    # PnL mini chart
    if closed:
        cumulative = []
        running = 0
        for t in closed:
            running += t.get("pnl", 0)
            cumulative.append(running)

        chart_color = "#16a34a" if running >= 0 else "#dc2626"
        fig_mini = go.Figure()
        fig_mini.add_trace(go.Scatter(
            y=cumulative, mode="lines",
            line=dict(color=chart_color, width=2, shape="spline"),
            fill="tozeroy",
            fillcolor=f"rgba({','.join(str(int(chart_color.lstrip('#')[i:i+2], 16)) for i in (0,2,4))},0.08)",
            hovertemplate="Trade #%{x}<br>PnL: $%{y:+.2f}<extra></extra>"
        ))
        fig_mini.add_hline(y=0, line_color="#e5e7eb", line_width=1)
        fig_mini.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            height=120, margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            showlegend=False,
        )
        st.plotly_chart(fig_mini, use_container_width=True)


# =========================================================
#  EQUITY CURVE (full)
# =========================================================
st.markdown('<div class="section-title">Equity Curve</div>', unsafe_allow_html=True)

if closed:
    cumulative_eq = []
    colors_eq = []
    running_eq = MILAT_CAPITAL
    for t in closed:
        running_eq += t.get("pnl", 0)
        cumulative_eq.append(running_eq)
        colors_eq.append(t.get("result", ""))

    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        y=cumulative_eq, mode="lines",
        line=dict(color="#2563eb", width=2.5, shape="spline"),
        fill="tozeroy", fillcolor="rgba(37,99,235,0.06)",
        hovertemplate="Trade #%{x}<br>Equity: $%{y:.2f}<extra></extra>"
    ))
    # Win/loss dots
    win_x = [i for i, c in enumerate(colors_eq) if c == "WIN"]
    win_y = [cumulative_eq[i] for i in win_x]
    loss_x = [i for i, c in enumerate(colors_eq) if c == "LOSS"]
    loss_y = [cumulative_eq[i] for i in loss_x]
    fig_eq.add_trace(go.Scatter(x=win_x, y=win_y, mode="markers",
                                 marker=dict(color="#16a34a", size=4), name="Win", hoverinfo="skip"))
    fig_eq.add_trace(go.Scatter(x=loss_x, y=loss_y, mode="markers",
                                 marker=dict(color="#dc2626", size=4, symbol="x"), name="Loss", hoverinfo="skip"))
    fig_eq.add_hline(y=300, line_dash="dash", line_color="#16a34a",
                     annotation_text="Target $300", annotation_font_color="#16a34a")
    fig_eq.add_hline(y=MILAT_CAPITAL, line_dash="dot", line_color="#d1d5db",
                     annotation_text=f"Start ${MILAT_CAPITAL:.0f}")
    fig_eq.update_layout(
        paper_bgcolor="#fff", plot_bgcolor="#fff",
        height=280, margin=dict(l=50, r=20, t=10, b=30),
        showlegend=False,
        xaxis=dict(showgrid=False, title=""),
        yaxis=dict(gridcolor="#f3f4f6", title=""),
        font=dict(family="Inter"),
    )
    st.plotly_chart(fig_eq, use_container_width=True)


# =========================================================
#  OPEN POSITIONS (from live API)
# =========================================================
open_positions_list = [p for p in api_positions.values() if isinstance(p, dict)]
if open_positions_list:
    st.markdown('<div class="section-title">Open Positions</div>', unsafe_allow_html=True)
    for p in open_positions_list:
        amt = p.get("amount", 0)
        cv = p.get("current_value", 0)
        pnl_val = cv - amt if cv > 0 else 0
        pnl_color = "#16a34a" if pnl_val >= 0 else "#dc2626"
        direction = p.get("direction", "YES").upper()
        badge_class = "pos-badge-yes" if direction == "YES" else "pos-badge-no"
        q = p.get("question", p.get("market_slug", ""))
        asset = ""
        for nm in ["Bitcoin","Ethereum","Solana","XRP","Dogecoin","BNB","Hype"]:
            if nm.lower() in q.lower():
                asset = nm; break
        if not asset:
            slug = p.get("market_slug", "")
            for nm in ["bitcoin","ethereum","solana","xrp","dogecoin","bnb","hype"]:
                if nm in slug.lower():
                    asset = nm.capitalize(); break
        time_match = re.findall(r"(\d{1,2}:\d{2}(?:AM|PM))", q, re.IGNORECASE)
        time_str = " - ".join(time_match[:2]) if time_match else ""

        st.markdown(
            f'<div class="pos-row">'
            f'<div class="pos-left">'
            f'<span class="pos-badge {badge_class}">{direction}</span>'
            f'<div><div class="pos-name">{asset} Up or Down</div>'
            f'<div class="pos-detail">{time_str} ET &bull; Entry ${p.get("entry_price",0):.3f} &bull; Current ${cv:.2f}</div></div>'
            f'</div>'
            f'<div class="pos-right">'
            f'<div class="pos-pnl" style="color:{pnl_color};">${pnl_val:+.2f}</div>'
            f'<div class="pos-amount">${amt:.2f} invested</div>'
            f'</div></div>',
            unsafe_allow_html=True
        )


# =========================================================
#  RECENT WINS (Claim-style cards)
# =========================================================
if recent_wins:
    st.markdown('<div class="section-title">Recent Wins</div>', unsafe_allow_html=True)
    for t in reversed(recent_wins):
        q = t.get("question", "")
        asset = ""
        for nm in ["Bitcoin","Ethereum","Solana","XRP","Dogecoin","BNB","Hype"]:
            if nm.lower() in q.lower():
                asset = nm[:3].upper(); break
        pnl_val = t.get("pnl", 0)
        out = t.get("outcome", "?")

        st.markdown(
            f'<div class="claim-card">'
            f'<div class="claim-left">'
            f'<div class="claim-icon" style="background:#dbeafe;color:#2563eb;">{asset[:1]}</div>'
            f'<div class="claim-text">You won <span class="claim-amount">${pnl_val:.2f}</span>'
            f' &bull; {asset} {out}</div>'
            f'</div>'
            f'<div style="color:#16a34a;font-weight:700;font-size:14px;">+${pnl_val:.2f}</div>'
            f'</div>',
            unsafe_allow_html=True
        )


# =========================================================
#  ANALYTICS: YES/NO + Assets + Hourly
# =========================================================
st.markdown('<div class="section-title">Analytics</div>', unsafe_allow_html=True)

a_col, b_col, c_col = st.columns(3)

# YES vs NO
with a_col:
    yes_trades = [t for t in closed if t.get("outcome", "").upper() in ("YES", "UP")]
    no_trades = [t for t in closed if t.get("outcome", "").upper() in ("NO", "DOWN")]
    yes_w = sum(1 for t in yes_trades if t.get("result") == "WIN")
    no_w = sum(1 for t in no_trades if t.get("result") == "WIN")
    yes_pnl = sum(t.get("pnl", 0) for t in yes_trades)
    no_pnl = sum(t.get("pnl", 0) for t in no_trades)
    yes_wr = yes_w * 100 / len(yes_trades) if yes_trades else 0
    no_wr = no_w * 100 / len(no_trades) if no_trades else 0

    fig_yn = go.Figure()
    fig_yn.add_trace(go.Bar(
        x=["YES", "NO"], y=[yes_wr, no_wr],
        marker_color=["#16a34a", "#dc2626"],
        text=[f"{yes_wr:.0f}%<br>${yes_pnl:+.0f}", f"{no_wr:.0f}%<br>${no_pnl:+.0f}"],
        textposition="auto", textfont=dict(size=12),
    ))
    fig_yn.update_layout(
        paper_bgcolor="#fff", plot_bgcolor="#fff", title="YES vs NO",
        height=260, margin=dict(l=30, r=10, t=40, b=30),
        yaxis=dict(range=[0, 100], gridcolor="#f3f4f6", title="WR %"),
        xaxis=dict(showgrid=False), showlegend=False, font=dict(family="Inter"),
    )
    st.plotly_chart(fig_yn, use_container_width=True)

# Asset performance
with b_col:
    assets = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
    for t in closed:
        q = t.get("question", "").lower()
        for name, sym in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL"),
                           ("xrp","XRP"),("dogecoin","DOGE"),("bnb","BNB"),("hype","HYPE")]:
            if name in q:
                if t.get("result") == "WIN": assets[sym]["w"] += 1
                else: assets[sym]["l"] += 1
                assets[sym]["pnl"] += t.get("pnl", 0)
                break

    if assets:
        sorted_a = sorted(assets.items(), key=lambda x: -x[1]["pnl"])
        names = [a[0] for a in sorted_a]
        pnls = [a[1]["pnl"] for a in sorted_a]

        fig_a = go.Figure()
        fig_a.add_trace(go.Bar(
            x=names, y=pnls,
            marker_color=["#16a34a" if p > 0 else "#dc2626" for p in pnls],
            text=[f"${p:+.0f}" for p in pnls],
            textposition="auto", textfont=dict(size=11),
        ))
        fig_a.update_layout(
            paper_bgcolor="#fff", plot_bgcolor="#fff", title="PnL by Asset",
            height=260, margin=dict(l=30, r=10, t=40, b=30),
            yaxis=dict(gridcolor="#f3f4f6", title="$"),
            xaxis=dict(showgrid=False), showlegend=False, font=dict(family="Inter"),
        )
        st.plotly_chart(fig_a, use_container_width=True)

# Hourly
with c_col:
    hour_stats = defaultdict(lambda: {"w": 0, "l": 0})
    for t in closed:
        m = re.search(r"(\d{1,2}):\d{2}\s*(AM|PM)", t.get("question", ""), re.IGNORECASE)
        if m:
            h = int(m.group(1))
            ap = m.group(2).upper()
            if ap == "PM" and h != 12: h += 12
            elif ap == "AM" and h == 12: h = 0
            if t.get("result") == "WIN": hour_stats[h]["w"] += 1
            else: hour_stats[h]["l"] += 1

    if hour_stats:
        hours = sorted(hour_stats.keys())
        h_wr = [hour_stats[h]["w"] * 100 / (hour_stats[h]["w"] + hour_stats[h]["l"])
                if (hour_stats[h]["w"] + hour_stats[h]["l"]) > 0 else 0 for h in hours]
        h_count = [hour_stats[h]["w"] + hour_stats[h]["l"] for h in hours]

        fig_h = go.Figure()
        fig_h.add_trace(go.Bar(
            x=[f"{h:02d}:00" for h in hours], y=h_wr,
            marker_color=["#16a34a" if w >= 60 else "#f59e0b" if w >= 45 else "#dc2626" for w in h_wr],
            text=[f"{w:.0f}%" for w in h_wr],
            textposition="auto", textfont=dict(size=10),
        ))
        fig_h.update_layout(
            paper_bgcolor="#fff", plot_bgcolor="#fff", title="WR by Hour (ET)",
            height=260, margin=dict(l=30, r=10, t=40, b=30),
            yaxis=dict(range=[0, 100], gridcolor="#f3f4f6"),
            xaxis=dict(showgrid=False), showlegend=False, font=dict(family="Inter"),
        )
        st.plotly_chart(fig_h, use_container_width=True)


# =========================================================
#  TRADE HISTORY
# =========================================================
st.markdown('<div class="section-title">Trade History</div>', unsafe_allow_html=True)

if closed:
    recent_trades = list(reversed(closed[-30:]))
    html = '<div class="pm-card" style="max-height:400px;overflow-y:auto;">'
    for t in recent_trades:
        res = t.get("result", "?")
        out = t.get("outcome", "?")
        pnl_val = t.get("pnl", 0)
        q = t.get("question", "")

        asset = ""
        for nm in ["Bitcoin","Ethereum","Solana","XRP","Dogecoin","BNB","Hype"]:
            if nm.lower() in q.lower():
                asset = nm[:3].upper(); break

        tm = re.search(r"(\d{1,2}:\d{2}(?:AM|PM))", q, re.IGNORECASE)
        time_str = tm.group(1) if tm else ""

        res_color = "#16a34a" if res == "WIN" else "#dc2626" if res == "LOSS" else "#9ca3af"
        dir_color = "#16a34a" if out.upper() in ("YES","UP") else "#dc2626"
        pnl_color = "#16a34a" if pnl_val >= 0 else "#dc2626"

        html += (
            f'<div class="trade-row">'
            f'<span class="trade-result" style="color:{res_color};">{res}</span>'
            f'<span class="trade-dir" style="color:{dir_color};">{out}</span>'
            f'<span class="trade-market">{asset} {time_str} ET</span>'
            f'<span class="trade-pnl" style="color:{pnl_color};">${pnl_val:+.2f}</span>'
            f'</div>'
        )
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


# =========================================================
#  LIVE SIGNAL LOG
# =========================================================
st.markdown('<div class="section-title">Live Signals</div>', unsafe_allow_html=True)

signal_keywords = ["SINYAL", "SUCCESS", "EMIR", "REGIME", "FEAR", "ML_BOOST", "CANDLE_YES", "ARB ["]
signal_lines = [l for l in log_lines if any(k in l for k in signal_keywords)]
if signal_lines:
    clean_lines = [ansi_clean(l) for l in signal_lines[-20:]]
    st.markdown(
        '<div class="pm-card" style="max-height:300px;overflow-y:auto;font-family:monospace;font-size:12px;color:#4b5563;line-height:1.8;">'
        + "<br>".join(reversed(clean_lines))
        + '</div>',
        unsafe_allow_html=True
    )

# Footer
st.markdown(
    '<div style="text-align:center;color:#d1d5db;font-size:11px;margin-top:40px;padding:20px;">'
    'Auto-refreshes every 15s &bull; Data from TR 13:00 March 19 &bull; '
    f'{datetime.now().strftime("%H:%M:%S")}</div>',
    unsafe_allow_html=True
)
