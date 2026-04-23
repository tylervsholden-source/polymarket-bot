import { useState, useEffect } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, BarChart, Bar, CartesianGrid,
} from "recharts";
import {
  Activity, TrendingUp, TrendingDown, DollarSign, Eye, Zap,
  Clock, BarChart3, Target, Shield, Wallet, Radio, CircleDot,
  ArrowUpRight, ArrowDownRight, RefreshCw,
} from "lucide-react";

// ─── STYLE CONSTANTS ───────────────────────────
const COLORS = {
  bg: "#0a0e17",
  card: "rgba(15, 23, 42, 0.8)",
  cardBorder: "rgba(56, 189, 248, 0.15)",
  cyan: "#38bdf8",
  cyanGlow: "0 0 20px rgba(56, 189, 248, 0.3)",
  green: "#22c55e",
  red: "#ef4444",
  yellow: "#eab308",
  purple: "#a855f7",
  text: "#e2e8f0",
  textDim: "#64748b",
  textMuted: "#475569",
};

const glassCard = {
  background: COLORS.card,
  backdropFilter: "blur(20px)",
  border: `1px solid ${COLORS.cardBorder}`,
  borderRadius: "16px",
  padding: "20px",
  transition: "all 0.3s ease",
};

const GlowDot = ({ color, size = 8 }) => (
  <span style={{
    display: "inline-block", width: size, height: size, borderRadius: "50%",
    background: color, boxShadow: `0 0 ${size * 2}px ${color}`,
    animation: "pulse 2s ease-in-out infinite",
  }} />
);

const StatCard = ({ icon: Icon, label, value, subValue, color = COLORS.cyan, trend }) => (
  <div style={{ ...glassCard, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <div style={{
        width: 36, height: 36, borderRadius: 10,
        background: `${color}15`, display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Icon size={18} color={color} />
      </div>
      {trend !== undefined && (
        <div style={{
          display: "flex", alignItems: "center", gap: 4,
          color: trend >= 0 ? COLORS.green : COLORS.red, fontSize: 13, fontWeight: 600,
        }}>
          {trend >= 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
          {Math.abs(trend).toFixed(1)}%
        </div>
      )}
    </div>
    <div style={{ color: COLORS.textDim, fontSize: 12, fontWeight: 500, textTransform: "uppercase", letterSpacing: 1 }}>
      {label}
    </div>
    <div style={{ color: COLORS.text, fontSize: 24, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>
      {value}
    </div>
    {subValue && <div style={{ color: COLORS.textMuted, fontSize: 12 }}>{subValue}</div>}
  </div>
);

const SectionHeader = ({ icon: Icon, title, badge, color = COLORS.cyan }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
    <div style={{
      width: 32, height: 32, borderRadius: 8, background: `${color}15`,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <Icon size={16} color={color} />
    </div>
    <span style={{ color: COLORS.text, fontSize: 15, fontWeight: 600 }}>{title}</span>
    {badge && (
      <span style={{
        background: `${color}20`, color: color, fontSize: 11, fontWeight: 600,
        padding: "2px 8px", borderRadius: 20, marginLeft: "auto",
      }}>{badge}</span>
    )}
  </div>
);

const TickerItem = ({ symbol, price, change }) => (
  <div style={{
    display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
    padding: "6px 14px", borderRadius: 10, background: "rgba(255,255,255,0.03)",
    minWidth: 80, flexShrink: 0,
  }}>
    <span style={{ color: COLORS.textDim, fontSize: 10, fontWeight: 600, letterSpacing: 1 }}>{symbol}</span>
    <span style={{ color: COLORS.text, fontSize: 13, fontWeight: 700, fontFamily: "monospace" }}>
      {typeof price === "number" ? (price >= 100 ? price.toLocaleString("en-US", { maximumFractionDigits: 0 }) : price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })) : price}
    </span>
    <span style={{
      fontSize: 10, fontWeight: 600,
      color: change >= 0 ? COLORS.green : COLORS.red,
    }}>
      {change >= 0 ? "+" : ""}{change.toFixed(2)}%
    </span>
  </div>
);

// ─── HELPERS ───────────────────────────
const COIN_MAP = { bitcoin: "BTC", btc: "BTC", ethereum: "ETH", eth: "ETH", solana: "SOL", sol: "SOL", xrp: "XRP", ripple: "XRP", dogecoin: "DOGE", doge: "DOGE", bnb: "BNB", hyperliquid: "HYPE", hype: "HYPE" };
const detectCoin = (q) => { const lc = (q || "").toLowerCase(); for (const [kw, sym] of Object.entries(COIN_MAP)) { if (lc.includes(kw)) return sym; } return null; };

// ─── MAIN DASHBOARD ───────────────────────────
export default function App() {
  const [data, setData] = useState(null);
  const [equityHistory, setEquityHistory] = useState([]);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [isLive, setIsLive] = useState(true);
  const [connectionError, setConnectionError] = useState(false);
  const apiBase = "";

  // Clock
  useEffect(() => {
    const t = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // Live data fetching
  useEffect(() => {
    if (!isLive) return;
    const fetchData = async () => {
      try {
        const res = await fetch(`${apiBase}/api/status`);
        if (res.ok) {
          const json = await res.json();
          setData(json);
          setConnectionError(false);
          setEquityHistory(prev => {
            const next = [...prev];
            next.push({
              time: new Date().toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
              value: json.capital || 0,
            });
            if (next.length > 120) next.shift();
            return next;
          });
        }
      } catch {
        setConnectionError(true);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 3000);
    return () => clearInterval(interval);
  }, [isLive]);

  if (!data) {
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        background: COLORS.bg, color: COLORS.text, fontFamily: "Inter, sans-serif",
      }}>
        <div style={{ textAlign: "center" }}>
          <Zap size={48} color={COLORS.cyan} style={{ marginBottom: 16 }} />
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            {connectionError ? "Bot'a bağlanılamıyor..." : "Bağlanıyor..."}
          </div>
          <div style={{ fontSize: 13, color: COLORS.textDim, marginTop: 8 }}>
            /api/status (proxy → localhost:8080)
          </div>
        </div>
      </div>
    );
  }

  // ─── DERIVED DATA ───────────────────────────
  const positions = data.positions ? Object.entries(data.positions) : [];
  const closed = data.closed || [];
  const wins = closed.filter(t => t.result === "WIN").length;
  const losses = closed.filter(t => t.result === "LOSS").length;
  const totalTrades = wins + losses;
  const winRate = totalTrades > 0 ? ((wins / totalTrades) * 100) : 0;
  const totalPnl = closed.reduce((s, t) => s + (t.pnl || 0), 0);
  const unrealizedPnl = positions.reduce((s, [, p]) => s + (p.unrealized_pnl || 0), 0);
  const totalExposure = positions.reduce((s, [, p]) => s + (p.amount || 0), 0);
  const portfolioValue = (data.capital || 0) + totalExposure + unrealizedPnl;

  const pieData = [
    { name: "WIN", value: wins || 0, color: COLORS.green },
    { name: "LOSS", value: losses || 0, color: COLORS.red },
  ].filter(d => d.value > 0);

  const tradeHistory = closed.slice().reverse().map((t, i) => ({
    name: `#${closed.length - i}`,
    pnl: t.pnl || 0,
    fill: (t.pnl || 0) >= 0 ? COLORS.green : COLORS.red,
  }));

  const decisions = data.decisions || [];
  const crypto = data.crypto || {};
  const indices = data.indices || {};

  return (
    <div style={{
      minHeight: "100vh",
      background: `linear-gradient(135deg, ${COLORS.bg} 0%, #0f172a 50%, #0a0e17 100%)`,
      color: COLORS.text,
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
      padding: 0, margin: 0,
    }}>
      <div style={{
        position: "fixed", inset: 0, zIndex: 0, opacity: 0.03,
        backgroundImage: `linear-gradient(${COLORS.cyan} 1px, transparent 1px), linear-gradient(90deg, ${COLORS.cyan} 1px, transparent 1px)`,
        backgroundSize: "60px 60px",
      }} />

      <style>{`
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes slideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${COLORS.textMuted}; border-radius: 3px; }
      `}</style>

      <div style={{ position: "relative", zIndex: 1 }}>

        {/* ═══ TOP BAR ═══ */}
        <div style={{
          ...glassCard, borderRadius: 0, padding: "12px 24px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          borderBottom: `1px solid ${COLORS.cardBorder}`,
          background: "rgba(10, 14, 23, 0.95)",
          position: "sticky", top: 0, zIndex: 100,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{
              width: 38, height: 38, borderRadius: 10,
              background: `linear-gradient(135deg, ${COLORS.cyan}, ${COLORS.purple})`,
              display: "flex", alignItems: "center", justifyContent: "center",
              boxShadow: COLORS.cyanGlow,
            }}>
              <Zap size={20} color="#fff" />
            </div>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: 0.5 }}>
                POLYMARKET <span style={{ color: COLORS.cyan }}>AI</span> TRADING
              </div>
              <div style={{ fontSize: 11, color: COLORS.textDim }}>
                Autonomous Multi-Agent Engine v3
              </div>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <GlowDot color={data.running ? COLORS.green : COLORS.red} />
              <span style={{ fontSize: 12, color: data.running ? COLORS.green : COLORS.red, fontWeight: 600 }}>
                {data.running ? "LIVE" : "OFFLINE"}
              </span>
            </div>

            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "rgba(255,255,255,0.05)", padding: "6px 12px", borderRadius: 8,
            }}>
              <Clock size={14} color={COLORS.textDim} />
              <span style={{ fontSize: 13, fontFamily: "monospace", color: COLORS.text }}>
                {currentTime.toLocaleTimeString("tr-TR")}
              </span>
            </div>

            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "rgba(255,255,255,0.05)", padding: "6px 12px", borderRadius: 8,
            }}>
              <Radio size={14} color={COLORS.cyan} />
              <span style={{ fontSize: 12, color: COLORS.textDim }}>Cycle</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: COLORS.cyan, fontFamily: "monospace" }}>
                #{data.cycle}
              </span>
            </div>

            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              background: isLive ? "rgba(34,197,94,0.1)" : "rgba(255,255,255,0.05)",
              border: `1px solid ${isLive ? COLORS.green : COLORS.textMuted}`,
              color: isLive ? COLORS.green : COLORS.textDim,
              padding: "6px 14px", borderRadius: 8,
              fontSize: 12, fontWeight: 600,
            }}>
              <RefreshCw size={13} style={isLive ? { animation: "pulse 1s linear infinite" } : {}} />
              LIVE FEED
            </div>
          </div>
        </div>

        {/* ═══ CRYPTO TICKER BAR ═══ */}
        {(Object.keys(crypto).length > 0 || Object.keys(indices).length > 0) && (
          <div style={{
            background: "rgba(10, 14, 23, 0.7)", borderBottom: `1px solid ${COLORS.cardBorder}`,
            padding: "8px 24px", overflow: "hidden",
          }}>
            <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
              {Object.entries(crypto).map(([sym, d]) => (
                <TickerItem key={sym} symbol={sym} price={d.price} change={d.change_pct || 0} />
              ))}
              {Object.keys(crypto).length > 0 && Object.keys(indices).length > 0 && (
                <div style={{ width: 1, background: COLORS.cardBorder, margin: "4px 8px", flexShrink: 0 }} />
              )}
              {Object.entries(indices).map(([sym, d]) => (
                <TickerItem key={sym} symbol={sym} price={d.price} change={d.change_pct || 0} />
              ))}
            </div>
          </div>
        )}

        {/* ═══ MAIN CONTENT ═══ */}
        <div style={{ padding: "20px 24px", maxWidth: 1600, margin: "0 auto" }}>

          {/* ─── STAT CARDS ROW ─── */}
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 16, marginBottom: 20, animation: "slideIn 0.5s ease",
          }}>
            <StatCard
              icon={Wallet} label="Portfoy Degeri" color={COLORS.cyan}
              value={`$${portfolioValue.toFixed(2)}`}
              subValue={`Baslangic: $${data.initial_capital}`}
              trend={((portfolioValue - data.initial_capital) / data.initial_capital * 100)}
            />
            <StatCard
              icon={DollarSign} label="Kullanilabilir" color={COLORS.purple}
              value={`$${(data.capital || 0).toFixed(2)}`}
              subValue={`Kilitli: $${totalExposure.toFixed(2)}`}
            />
            <StatCard
              icon={TrendingUp} label="Gerceklesen P&L" color={totalPnl >= 0 ? COLORS.green : COLORS.red}
              value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`}
              subValue={`Unrealized: ${unrealizedPnl >= 0 ? "+" : ""}$${unrealizedPnl.toFixed(2)}`}
            />
            <StatCard
              icon={Target} label="Win Rate"
              color={winRate >= 50 ? COLORS.green : COLORS.yellow}
              value={`${winRate.toFixed(0)}%`}
              subValue={`${wins}W / ${losses}L (${totalTrades} trade)`}
            />
            <StatCard
              icon={Eye} label="Taranan Market" color={COLORS.cyan}
              value={(data.scanned || 0).toLocaleString()}
              subValue={`${data.candidates || 0} aday`}
            />
            <StatCard
              icon={BarChart3} label="Acik Pozisyon" color={COLORS.yellow}
              value={`${positions.length} / ${data.max_positions || 7}`}
              subValue={`Mode: ${data.signal_mode || "arbitrage"}`}
            />
          </div>

          {/* ─── MAIN GRID: 3 COLUMNS ─── */}
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr 380px",
            gap: 16, animation: "slideIn 0.6s ease",
          }}>

            {/* ═══ LEFT: EQUITY CURVE ═══ */}
            <div style={glassCard}>
              <SectionHeader icon={TrendingUp} title="Equity Curve" badge="LIVE" color={COLORS.cyan} />
              <div style={{ height: 260 }}>
                {equityHistory.length < 2 ? (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: COLORS.textMuted, fontSize: 13 }}>
                    Veri toplanıyor...
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={equityHistory}>
                      <defs>
                        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={COLORS.cyan} stopOpacity={0.4} />
                          <stop offset="100%" stopColor={COLORS.cyan} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                      <XAxis dataKey="time" tick={{ fill: COLORS.textMuted, fontSize: 10 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: COLORS.textMuted, fontSize: 10 }} tickLine={false} axisLine={false} domain={["auto", "auto"]} />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(15,23,42,0.95)", border: `1px solid ${COLORS.cardBorder}`,
                          borderRadius: 8, fontSize: 12, color: COLORS.text,
                        }}
                        formatter={(v) => [`$${Number(v).toFixed(2)}`, "Sermaye"]}
                      />
                      <Area
                        type="monotone" dataKey="value" stroke={COLORS.cyan} strokeWidth={2}
                        fill="url(#eqGrad)" dot={false} activeDot={{ r: 5, fill: COLORS.cyan, stroke: "#fff" }}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            {/* ═══ CENTER: TRADE P&L BAR + PIE ═══ */}
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div style={glassCard}>
                <SectionHeader icon={BarChart3} title="Trade P&L" badge={`${totalTrades} trade`} color={COLORS.purple} />
                <div style={{ height: 130 }}>
                  {tradeHistory.length === 0 ? (
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: COLORS.textMuted, fontSize: 13 }}>
                      Henuz trade yok
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={tradeHistory}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                        <XAxis dataKey="name" tick={{ fill: COLORS.textMuted, fontSize: 10 }} tickLine={false} axisLine={false} />
                        <YAxis tick={{ fill: COLORS.textMuted, fontSize: 10 }} tickLine={false} axisLine={false} />
                        <Tooltip
                          contentStyle={{
                            background: "rgba(15,23,42,0.95)", border: `1px solid ${COLORS.cardBorder}`,
                            borderRadius: 8, fontSize: 12,
                          }}
                          formatter={(v) => [`$${Number(v).toFixed(2)}`, "P&L"]}
                        />
                        <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                          {tradeHistory.map((e, i) => (
                            <Cell key={i} fill={e.fill} fillOpacity={0.8} />
                          ))}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>

              <div style={{ ...glassCard, display: "flex", alignItems: "center", gap: 20 }}>
                <div style={{ width: 120, height: 120 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={pieData.length > 0 ? pieData : [{ name: "N/A", value: 1, color: COLORS.textMuted }]}
                        cx="50%" cy="50%" innerRadius={32} outerRadius={50}
                        paddingAngle={4} dataKey="value" strokeWidth={0}
                      >
                        {(pieData.length > 0 ? pieData : [{ color: COLORS.textMuted }]).map((e, i) => (
                          <Cell key={i} fill={e.color} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: COLORS.text }}>Win / Loss</div>
                  <div style={{ display: "flex", gap: 20 }}>
                    <div>
                      <div style={{ fontSize: 28, fontWeight: 800, color: COLORS.green, fontFamily: "monospace" }}>{wins}</div>
                      <div style={{ fontSize: 11, color: COLORS.textDim }}>Kazanc</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 28, fontWeight: 800, color: COLORS.red, fontFamily: "monospace" }}>{losses}</div>
                      <div style={{ fontSize: 11, color: COLORS.textDim }}>Kayip</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 28, fontWeight: 800, color: COLORS.yellow, fontFamily: "monospace" }}>
                        {winRate.toFixed(0)}<span style={{ fontSize: 14 }}>%</span>
                      </div>
                      <div style={{ fontSize: 11, color: COLORS.textDim }}>Oran</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* ═══ RIGHT: SIGNAL FEED ═══ */}
            <div style={{ ...glassCard, maxHeight: 400, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <SectionHeader icon={Zap} title="Sinyal Akisi" badge="LIVE" color={COLORS.yellow} />
              <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
                {decisions.length === 0 ? (
                  <div style={{ color: COLORS.textMuted, fontSize: 13, textAlign: "center", padding: 40 }}>
                    Sinyal bekleniyor...
                  </div>
                ) : decisions.slice(0, 10).map((d, i) => (
                  <div key={i} style={{
                    background: d.action === "ORDER" ? "rgba(34,197,94,0.06)" : "rgba(255,255,255,0.02)",
                    border: `1px solid ${d.action === "ORDER" ? "rgba(34,197,94,0.2)" : "rgba(255,255,255,0.05)"}`,
                    borderRadius: 10, padding: "10px 12px",
                  }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, letterSpacing: 1,
                        color: d.action === "ORDER" ? COLORS.green : d.action === "SKIP" ? COLORS.textMuted : COLORS.yellow,
                      }}>
                        {d.action}
                      </span>
                      <span style={{ fontSize: 10, color: COLORS.textMuted, fontFamily: "monospace" }}>{d.time}</span>
                    </div>
                    <div style={{ fontSize: 12, color: COLORS.text, fontWeight: 500, marginBottom: 6 }}>
                      {d.market}
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {[
                        { label: "Edge", value: d.edge?.toFixed(3), color: (d.edge || 0) > 0.1 ? COLORS.green : COLORS.yellow },
                        { label: "Prob", value: d.prob?.toFixed(2), color: COLORS.cyan },
                        { label: "Price", value: d.price?.toFixed(2), color: COLORS.text },
                        { label: "Conf", value: d.confidence, color: d.confidence === "HIGH" ? COLORS.green : COLORS.textDim },
                      ].map((tag, j) => (
                        <span key={j} style={{
                          fontSize: 10, color: tag.color, background: `${tag.color}10`,
                          padding: "2px 6px", borderRadius: 4, fontFamily: "monospace",
                        }}>
                          {tag.label}:{tag.value}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ─── POSITIONS TABLE ─── */}
          <div style={{ ...glassCard, marginTop: 16, animation: "slideIn 0.7s ease" }}>
            <SectionHeader icon={CircleDot} title="Acik Pozisyonlar" badge={`${positions.length} aktif`} color={COLORS.cyan} />
            {positions.length === 0 ? (
              <div style={{ textAlign: "center", padding: 40, color: COLORS.textMuted }}>Acik pozisyon yok</div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["Market", "Yon", "Spot Price", "Strateji", "Yatirim", "Giris", "Guncel", "Deger", "P&L", "P&L %"].map(h => (
                        <th key={h} style={{
                          textAlign: "left", padding: "10px 12px", fontSize: 11,
                          color: COLORS.textDim, fontWeight: 600, letterSpacing: 0.5,
                          borderBottom: `1px solid ${COLORS.cardBorder}`, textTransform: "uppercase",
                        }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map(([id, p], i) => {
                      const pnlPct = p.amount > 0 ? (p.unrealized_pnl / p.amount * 100) : 0;
                      const pnlColor = p.unrealized_pnl >= 0 ? COLORS.green : COLORS.red;
                      const coin = detectCoin(p.question);
                      const spotPrice = coin && (data.spot_prices || {})[coin];
                      return (
                        <tr key={id} style={{
                          background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.01)",
                        }}>
                          <td style={{ padding: "12px", fontSize: 13, fontWeight: 500, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {p.question}
                          </td>
                          <td style={{ padding: "12px" }}>
                            <span style={{
                              fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 6,
                              background: p.outcome === "YES" ? `${COLORS.green}20` : `${COLORS.red}20`,
                              color: p.outcome === "YES" ? COLORS.green : COLORS.red,
                            }}>{p.outcome}</span>
                          </td>
                          <td style={{ padding: "12px" }}>
                            {spotPrice ? (
                              <span style={{
                                fontSize: 13, fontFamily: "monospace", fontWeight: 700, color: COLORS.yellow,
                                background: `${COLORS.yellow}15`, padding: "3px 8px", borderRadius: 6,
                              }}>
                                ${spotPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: spotPrice >= 100 ? 0 : 4 })}
                              </span>
                            ) : (
                              <span style={{ fontSize: 11, color: COLORS.textMuted }}>--</span>
                            )}
                          </td>
                          <td style={{ padding: "12px", fontSize: 12, color: COLORS.textDim }}>{p.strategy}</td>
                          <td style={{ padding: "12px", fontSize: 13, fontFamily: "monospace", fontWeight: 600 }}>${p.amount?.toFixed(2)}</td>
                          <td style={{ padding: "12px", fontSize: 13, fontFamily: "monospace" }}>{p.entry_price?.toFixed(2)}</td>
                          <td style={{ padding: "12px", fontSize: 13, fontFamily: "monospace", color: COLORS.cyan }}>{p.current_price?.toFixed(2)}</td>
                          <td style={{ padding: "12px", fontSize: 13, fontFamily: "monospace" }}>${p.current_value?.toFixed(2)}</td>
                          <td style={{ padding: "12px", fontSize: 13, fontFamily: "monospace", fontWeight: 700, color: pnlColor }}>
                            {p.unrealized_pnl >= 0 ? "+" : ""}${p.unrealized_pnl?.toFixed(2)}
                          </td>
                          <td style={{ padding: "12px" }}>
                            <span style={{
                              fontSize: 12, fontWeight: 700, fontFamily: "monospace", color: pnlColor,
                              background: `${pnlColor}15`, padding: "3px 8px", borderRadius: 6,
                            }}>
                              {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* ─── CLOSED TRADES ─── */}
          <div style={{ ...glassCard, marginTop: 16, animation: "slideIn 0.8s ease" }}>
            <SectionHeader icon={Shield} title="Kapanmis Trade'ler" badge={`${closed.length} trade`} color={COLORS.purple} />
            {closed.length === 0 ? (
              <div style={{ textAlign: "center", padding: 40, color: COLORS.textMuted }}>Henuz kapanmis trade yok</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {closed.slice().reverse().map((t, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
                    borderRadius: 10,
                    background: t.result === "WIN" ? "rgba(34,197,94,0.04)" : "rgba(239,68,68,0.04)",
                    border: `1px solid ${t.result === "WIN" ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)"}`,
                  }}>
                    <div style={{
                      width: 36, height: 36, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center",
                      background: t.result === "WIN" ? `${COLORS.green}20` : `${COLORS.red}20`,
                    }}>
                      {t.result === "WIN" ? <TrendingUp size={16} color={COLORS.green} /> : <TrendingDown size={16} color={COLORS.red} />}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 13, fontWeight: 500, color: COLORS.text }}>{t.question}</div>
                      <div style={{ fontSize: 11, color: COLORS.textDim, marginTop: 2 }}>
                        {t.outcome} · ${t.amount?.toFixed(2)} · Giris: {t.entry_price?.toFixed(2)} · {t.strategy}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{
                        fontSize: 16, fontWeight: 800, fontFamily: "monospace",
                        color: t.result === "WIN" ? COLORS.green : COLORS.red,
                      }}>
                        {(t.pnl || 0) >= 0 ? "+" : ""}${(t.pnl || 0).toFixed(2)}
                      </div>
                      <div style={{
                        fontSize: 11, fontWeight: 700, marginTop: 2,
                        color: t.result === "WIN" ? COLORS.green : COLORS.red,
                      }}>
                        {t.result}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ─── FOOTER ─── */}
          <div style={{
            marginTop: 24, padding: "16px 0", textAlign: "center",
            borderTop: `1px solid ${COLORS.cardBorder}`,
          }}>
            <span style={{ fontSize: 11, color: COLORS.textMuted }}>
              Polymarket AI Trading Bot v3 · Autonomous Multi-Agent Engine · Son guncelleme: {data.updated}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
