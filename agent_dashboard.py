"""
Polymarket Bot — Agent Intelligence Dashboard
Kullanıcının ajan kararlarını takip etmesi için (Mevcut dashboard ezilmeden)
"""
import json
import re
from datetime import datetime
from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Agent Intelligence Hub", page_icon="🕵️", layout="wide")

# --- Polymarket-style light/clean theme + Agent Hub Styles ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    .stApp { background: #f7f7f8; font-family: 'Inter', sans-serif; }
    header[data-testid="stHeader"] { background: #fff; border-bottom: 1px solid #e5e7eb; }
    #MainMenu, footer { visibility: hidden; }

    /* Top nav bar */
    .topbar {
        background: #fff; padding: 16px 32px; border-bottom: 1px solid #e5e7eb;
        display: flex; align-items: center; justify-content: space-between;
        margin: -2rem -2rem 24px -2rem;
    }
    .topbar-logo { font-size: 22px; font-weight: 800; color: #1a1a2e; display: flex; align-items: center; gap: 12px; }
    .topbar-right { display: flex; align-items: center; gap: 20px; font-size: 14px; }
    
    /* Section title */
    .section-title {
        font-size: 20px; font-weight: 800; color: #1a1a2e; margin: 32px 0 16px 0;
        display: flex; align-items: center; gap: 8px;
    }

    /* Decision Card */
    .agent-card {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 20px; margin-bottom: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .agent-card:hover { transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.05); }

    .ac-header {
        display: flex; justify-content: space-between; align-items: flex-start;
        border-bottom: 1px solid #f3f4f6; padding-bottom: 12px; margin-bottom: 16px;
    }
    .ac-title { font-size: 16px; font-weight: 700; color: #111827; flex: 1; margin-right: 16px; }
    .ac-time { font-size: 12px; color: #6b7280; font-weight: 500; white-space: nowrap; }
    
    .ac-metrics {
        display: flex; gap: 24px; margin-bottom: 16px; flex-wrap: wrap;
    }
    .ac-metric {
        display: flex; flex-direction: column; gap: 4px;
    }
    .ac-metric-label { font-size: 11px; text-transform: uppercase; color: #6b7280; font-weight: 600; letter-spacing: 0.5px; }
    .ac-metric-val { font-size: 16px; font-weight: 700; color: #1f2937; }
    
    .ac-reasoning-box {
        background: #f9fafb; border-radius: 8px; padding: 12px 16px;
        border-left: 4px solid #3b82f6; 
    }
    .ac-reasoning-title { font-size: 12px; font-weight: 700; color: #374151; margin-bottom: 4px; display: flex; align-items: center; gap: 6px; }
    .ac-reasoning-text { font-size: 14px; color: #4b5563; line-height: 1.5; }

    /* Badges */
    .badge {
        display: inline-flex; align-items: center; padding: 4px 12px;
        border-radius: 20px; font-size: 12px; font-weight: 700;
    }
    .badge-execution { background: #dcfce7; color: #16a34a; }
    .badge-sim { background: #fef08a; color: #ca8a04; }
    .badge-skip { background: #f3f4f6; color: #6b7280; }
    .badge-block { background: #fee2e2; color: #dc2626; }
    
    .badge-high-conf { background: #dbeafe; color: #2563eb; }
    .badge-med-conf { background: #e0e7ff; color: #4f46e5; }
    
    .badge-edge-good { color: #16a34a; }
    .badge-edge-bad { color: #dc2626; }

    /* Stats grid */
    .stat-grid {
        display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px;
    }
    .stat-item {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 20px; text-align: center;
    }
    .stat-label { font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }
    .stat-value { font-size: 32px; font-weight: 800; color: #1a1a2e; margin-top: 8px; }

    /* Live indicator */
    .live-badge {
        display: inline-flex; align-items: center; gap: 8px;
        background: #dcfce7; color: #16a34a; padding: 6px 16px;
        border-radius: 20px; font-size: 13px; font-weight: 700;
    }
    .live-dot-sm {
        width: 8px; height: 8px; background: #16a34a; border-radius: 50%;
        animation: pulse 2s infinite;
    }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
</style>
""", unsafe_allow_html=True)

# Auto-refresh every 10 seconds
st.markdown('<meta http-equiv="refresh" content="10">', unsafe_allow_html=True)

# --- Data Loading ---
@st.cache_data(ttl=5)
def load_status():
    try:
        with open("data/status.json") as f:
            return json.load(f)
    except Exception:
        return {}

status_data = load_status()
decisions = status_data.get("decisions", [])
cycle = status_data.get("cycle", "?")

# --- TOP NAV ---
st.markdown(
    f'<div class="topbar">'
    f'<div class="topbar-logo">'
    f'🕵️ Agent Intelligence Hub</div>'
    f'<div class="topbar-right">'
    f'<span class="live-badge"><span class="live-dot-sm"></span> LISTENING to AGENTS</span>'
    f'<span style="color: #6b7280; font-weight: 600;">Cycle #{cycle}</span>'
    f'</div></div>',
    unsafe_allow_html=True
)

if not decisions:
    st.info("Henüz yeni ajan kararı yok. Bot çalışmaya devam ettikçe veriler buraya akacaktır...", icon="📡")
    st.stop()

# --- Quick Stats ---
executes = sum(1 for d in decisions if "ORDER" in str(d.get("action", "")).upper() or "EXEC" in str(d.get("action", "")).upper())
skips = sum(1 for d in decisions if "SKIP" in str(d.get("action", "")).upper())
sims = sum(1 for d in decisions if "SIM" in str(d.get("action", "")).upper())

st.markdown(f'<div class="section-title">📊 Agent Decision Metrics (Son {len(decisions)} Karar)</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="stat-grid">'
    f'<div class="stat-item"><div class="stat-label">Toplam Karar</div><div class="stat-value">{len(decisions)}</div></div>'
    f'<div class="stat-item"><div class="stat-label">Execution (Gerçek Onay)</div><div class="stat-value" style="color: #16a34a;">{executes}</div></div>'
    f'<div class="stat-item"><div class="stat-label">Simulasyon İşlemi</div><div class="stat-value" style="color: #ca8a04;">{sims}</div></div>'
    f'<div class="stat-item"><div class="stat-label">Skip / Red (Atlanan)</div><div class="stat-value" style="color: #dc2626;">{skips}</div></div>'
    f'</div>',
    unsafe_allow_html=True
)

st.markdown('<div class="section-title">🔍 Canlı Karar Akışı</div>', unsafe_allow_html=True)

# --- Render Decisions ---
for idx, d in enumerate(decisions):
    market = d.get("market", "Unknown Market")
    action = str(d.get("action", "UNKNOWN")).upper()
    time_str = d.get("time", "")
    category = d.get("category", "N/A")
    prob_val = d.get("prob", 0)
    edge_val = d.get("edge", 0)
    conf = d.get("confidence", "N/A").upper()
    reasoning = d.get("reasoning", "No reasoning provided.")
    size = d.get("size", 0.0)
    
    # Resolve badges
    if action == "ORDER":
        act_badge = "badge-execution"
        act_text = f"EXECUTED (${size})"
    elif action == "SIM_BUY" or "SIM" in action:
        act_badge = "badge-sim"
        act_text = f"SIMULATION (${size})"
    elif action == "SKIP":
        act_badge = "badge-skip"
        act_text = "SKIPPED"
    else:
        act_badge = "badge-block"
        act_text = action
        
    conf_badge = "badge-high-conf" if conf == "HIGH" else "badge-med-conf"
    edge_class = "badge-edge-good" if edge_val > 0 else "badge-edge-bad"
    
    ui_html = f'''
    <div class="agent-card">
        <div class="ac-header">
            <div class="ac-title">{market}</div>
            <div class="ac-time">{time_str}</div>
        </div>
        
        <div class="ac-metrics">
            <div class="ac-metric">
                <span class="ac-metric-label">Action</span>
                <span class="badge {act_badge}">{act_text}</span>
            </div>
            <div class="ac-metric">
                <span class="ac-metric-label">Signal Source</span>
                <span class="ac-metric-val">{category}</span>
            </div>
            <div class="ac-metric">
                <span class="ac-metric-label">Bayesian Prob.</span>
                <span class="ac-metric-val">{prob_val:.2f}</span>
            </div>
            <div class="ac-metric">
                <span class="ac-metric-label">Detected Edge</span>
                <span class="ac-metric-val {edge_class}">{edge_val:+.3f}</span>
            </div>
            <div class="ac-metric">
                <span class="ac-metric-label">Confidence</span>
                <span class="badge {conf_badge}">{conf}</span>
            </div>
        </div>
        
        <div class="ac-reasoning-box">
            <div class="ac-reasoning-title">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                Reviewer / Reason
            </div>
            <div class="ac-reasoning-text">
                {reasoning}
            </div>
        </div>
    </div>
    '''
    st.markdown(ui_html, unsafe_allow_html=True)

st.markdown(
    '<div style="text-align:center;color:#9ca3af;font-size:12px;margin-top:32px;padding:20px;">'
    f'Auto-refreshes every 10s &bull; Last update: {datetime.now().strftime("%H:%M:%S")}'
    '</div>',
    unsafe_allow_html=True
)
