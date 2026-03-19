"""
Canlı Terminal Dashboard — Açık Ofis Görünümü

Her ajan kendi "masasında" çalışır:
  🧠 Signal Agent   |  🐋 Whale Tracker
  ⚡ BTC Arb Agent  |  🎯 Orchestrator
         💰 Portföy
         📋 Son Kararlar

Kullanım:
  from core.dashboard import dashboard
  dashboard.update("signal", status="analiz ediyor", market="BTC Up?")
  dashboard.add_decision("Signal", "BTC Up?", "BUY", 31.0, 0.58)
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ── Paylaşılan durum ─────────────────────────────────────────────────────────

_DEFAULT_STATE: dict[str, Any] = {
    "signal": {
        "status": "bekliyor",
        "market": "—",
        "prob": "—",
        "conf": "—",
        "reasoning": "—",
        "last_at": "—",
    },
    "whale": {
        "status": "izliyor",
        "market": "—",
        "direction": "—",
        "buys": 0,
        "sells": 0,
        "volume": 0,
        "last_at": "—",
    },
    "btc_arb": {
        "btc_price": "—",
        "last_move_pct": "—",
        "status": "bekleniyor",
        "last_trade": "—",
        "last_at": "—",
    },
    "orchestrator": {
        "cycle": 0,
        "scanned": 0,
        "candidates": 0,
        "open_pos": 0,
        "max_pos": 5,
        "next_in": "—",
        "last_at": "—",
    },
    "portfolio": {
        "capital": 0.0,
        "initial": 0.0,
        "open": 0,
        "wins": 0,
        "losses": 0,
    },
    "onchain": {
        "alert_level": "NEUTRAL",
        "message": "İzleniyor",
        "exchange_netflow_btc": 0.0,
        "large_transfer_usd": 0.0,
        "timestamp": "—",
    },
    "decisions": [],  # list[dict]
}


class Dashboard:
    """Singleton dashboard — tüm ajanlar bu nesneye yazar."""

    def __init__(self):
        import copy
        self.state: dict[str, Any] = copy.deepcopy(_DEFAULT_STATE)
        self._running = False

    def update(self, section: str, **kwargs):
        """Bir masanın verilerini güncelle."""
        if section in self.state:
            self.state[section].update(kwargs)
            self.state[section]["last_at"] = datetime.now().strftime("%H:%M:%S")

    def add_decision(
        self,
        agent: str,
        market: str,
        action: str,
        size: float,
        price: float,
        edge: float = 0.0,
        result: str = "…",
    ):
        """Son kararlar tablosuna satır ekle."""
        entry = {
            "time": datetime.now().strftime("%H:%M"),
            "agent": agent,
            "market": market[:32],
            "action": action,
            "size": size,
            "price": price,
            "edge": edge,
            "result": result,
        }
        self.state["decisions"].insert(0, entry)
        self.state["decisions"] = self.state["decisions"][:10]

    def update_decision_result(self, market: str, result: str):
        """Sonuç belli olduğunda güncelle (WIN/LOSS)."""
        for d in self.state["decisions"]:
            if d["market"] in market or market in d["market"]:
                d["result"] = result
                break

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self) -> Layout:
        s = self.state
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="desks", size=14),
            Layout(name="onchain_bar", size=4),
            Layout(name="portfolio", size=4),
            Layout(name="decisions"),
        )

        layout["desks"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["left"].split_column(
            Layout(name="signal", size=7),
            Layout(name="btc_arb", size=7),
        )
        layout["right"].split_column(
            Layout(name="whale", size=7),
            Layout(name="orch", size=7),
        )

        # ── Header ────────────────────────────────────────────────────────────
        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        layout["header"].update(
            Panel(
                Text(f"🤖  POLYMARKET AI BOT — CANLI KONTROL ODASI          {now}",
                     justify="center", style="bold white on dark_blue"),
                style="bold blue",
            )
        )

        # ── Signal Agent ──────────────────────────────────────────────────────
        sig = s["signal"]
        status_color = {
            "analiz ediyor": "yellow",
            "karar verdi": "green",
            "bekliyor": "dim",
        }.get(sig["status"], "white")
        sig_text = Text()
        sig_text.append(f"  Durum      : ", style="dim")
        sig_text.append(f"{sig['status']}\n", style=status_color)
        sig_text.append(f"  Market     : ", style="dim")
        sig_text.append(f"{sig['market']}\n", style="cyan")
        sig_text.append(f"  AI Prob    : ", style="dim")
        sig_text.append(f"{sig['prob']}\n", style="green bold")
        sig_text.append(f"  Güven      : ", style="dim")
        conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(str(sig["conf"]), "white")
        sig_text.append(f"{sig['conf']}\n", style=conf_color)
        sig_text.append(f"  Gerekçe    : ", style="dim")
        sig_text.append(f"{str(sig['reasoning'])[:45]}\n", style="italic white")
        sig_text.append(f"  Son güncelleme: {sig['last_at']}", style="dim")
        layout["signal"].update(Panel(sig_text, title="🧠  Signal Agent", border_style="green"))

        # ── Whale Tracker ─────────────────────────────────────────────────────
        wh = s["whale"]
        dir_color = {"BULLISH": "green", "BEARISH": "red", "BUY": "green", "SELL": "red"}.get(
            str(wh["direction"]), "white"
        )
        wh_text = Text()
        wh_text.append(f"  Durum      : ", style="dim")
        wh_text.append(f"{wh['status']}\n", style="yellow")
        wh_text.append(f"  Market     : ", style="dim")
        wh_text.append(f"{wh['market']}\n", style="cyan")
        wh_text.append(f"  Yön        : ", style="dim")
        wh_text.append(f"{wh['direction']}\n", style=f"bold {dir_color}")
        wh_text.append(f"  Büyük Alım : ", style="dim")
        wh_text.append(f"{wh['buys']}\n", style="green")
        wh_text.append(f"  Büyük Satış: ", style="dim")
        wh_text.append(f"{wh['sells']}\n", style="red")
        wh_text.append(f"  Hacim      : ", style="dim")
        wh_text.append(f"${float(wh['volume']):,.0f}\n" if wh['volume'] != "—" else "—\n", style="white")
        layout["whale"].update(Panel(wh_text, title="🐋  Whale Tracker", border_style="cyan"))

        # ── BTC Arb Agent ─────────────────────────────────────────────────────
        ba = s["btc_arb"]
        arb_status_color = {"TETİKLENDİ": "yellow bold", "EMİR VERİLDİ": "green bold", "bekleniyor": "dim"}.get(
            str(ba["status"]), "white"
        )
        ba_text = Text()
        ba_text.append(f"  BTC Fiyat  : ", style="dim")
        price_str = f"${float(ba['btc_price']):,.0f}" if ba["btc_price"] != "—" else "—"
        ba_text.append(f"{price_str}\n", style="white bold")
        ba_text.append(f"  Son Hareket: ", style="dim")
        move = ba["last_move_pct"]
        if move != "—" and move != 0:
            move_f = float(move)
            move_color = "green" if move_f > 0 else "red"
            ba_text.append(f"%{move_f:+.2f}\n", style=f"bold {move_color}")
        else:
            ba_text.append("—\n", style="dim")
        ba_text.append(f"  Durum      : ", style="dim")
        ba_text.append(f"{ba['status']}\n", style=arb_status_color)
        ba_text.append(f"  Son İşlem  : ", style="dim")
        ba_text.append(f"{ba['last_trade']}\n", style="cyan")
        ba_text.append(f"  Son güncelleme: {ba['last_at']}", style="dim")
        layout["btc_arb"].update(Panel(ba_text, title="⚡  BTC Arb Agent", border_style="yellow"))

        # ── Orchestrator ──────────────────────────────────────────────────────
        oc = s["orchestrator"]
        oc_text = Text()
        oc_text.append(f"  Döngü #    : ", style="dim")
        oc_text.append(f"{oc['cycle']}\n", style="white bold")
        oc_text.append(f"  Taranan    : ", style="dim")
        oc_text.append(f"{oc['scanned']} market\n", style="white")
        oc_text.append(f"  AI analizine: ", style="dim")
        oc_text.append(f"{oc['candidates']} market\n", style="cyan")
        oc_text.append(f"  Pozisyon   : ", style="dim")
        open_p = oc["open_pos"]
        max_p = oc["max_pos"]
        pos_color = "green" if open_p < max_p else "red"
        oc_text.append(f"{open_p}/{max_p}\n", style=f"bold {pos_color}")
        oc_text.append(f"  Sonraki    : ", style="dim")
        oc_text.append(f"{oc['next_in']}\n", style="white")
        layout["orch"].update(Panel(oc_text, title="🎯  Orchestrator", border_style="magenta"))

        # ── Onchain Alert Bar ─────────────────────────────────────────────────
        oc = s["onchain"]
        alert_colors = {
            "DANGER":  "bold white on red",
            "BEARISH": "bold red",
            "BULLISH": "bold green",
            "NEUTRAL": "dim",
        }
        alert_level = str(oc.get("alert_level", "NEUTRAL"))
        alert_style = alert_colors.get(alert_level, "white")
        alert_icon = {"DANGER": "🚨", "BEARISH": "🔴", "BULLISH": "🟢", "NEUTRAL": "🟡"}.get(alert_level, "⚪")

        netflow = float(oc.get("exchange_netflow_btc", 0))
        net_str = f"{netflow:+,.0f} BTC" if netflow != 0 else "—"
        max_tx = float(oc.get("large_transfer_usd", 0))
        max_tx_str = f"${max_tx/1e6:.1f}M" if max_tx >= 1_000_000 else ("$" + f"{max_tx:,.0f}" if max_tx > 0 else "—")

        oc_text = Text(justify="left")
        oc_text.append(f"  {alert_icon} On-Chain Durum: ", style="dim")
        oc_text.append(f"{alert_level}  ", style=alert_style)
        oc_text.append(f"  Mesaj: ", style="dim")
        oc_text.append(f"{oc.get('message', '—')}  ", style="white")
        oc_text.append(f"  Exchange Netflow: ", style="dim")
        netflow_color = "red" if netflow > 500 else ("green" if netflow < -500 else "white")
        oc_text.append(f"{net_str}  ", style=netflow_color)
        oc_text.append(f"  En büyük transfer: ", style="dim")
        oc_text.append(f"{max_tx_str}  ", style="yellow" if max_tx >= 100_000_000 else "white")
        oc_text.append(f"  Son kontrol: {oc.get('timestamp', '—')}", style="dim")

        layout["onchain_bar"].update(
            Panel(oc_text, title="🔗  On-Chain Whale Monitor", border_style="red" if alert_level == "DANGER" else "dim white")
        )

        # ── Portfolio ─────────────────────────────────────────────────────────
        pf = s["portfolio"]
        cap = float(pf["capital"])
        ini = float(pf["initial"]) or cap
        pnl = cap - ini
        pnl_pct = (pnl / ini * 100) if ini > 0 else 0
        pnl_color = "green" if pnl >= 0 else "red"
        pnl_sign = "+" if pnl >= 0 else ""

        pf_text = Text(justify="center")
        pf_text.append(f"  Sermaye: ", style="dim")
        pf_text.append(f"${cap:,.2f}  ", style="white bold")
        pf_text.append(f"  PnL: ", style="dim")
        pf_text.append(f"{pnl_sign}${pnl:,.2f} ({pnl_sign}{pnl_pct:.1f}%)  ", style=f"bold {pnl_color}")
        pf_text.append(f"  Açık Pozisyon: ", style="dim")
        pf_text.append(f"{pf['open']}  ", style="cyan")
        pf_text.append(f"  Kazanılan: ", style="dim")
        pf_text.append(f"{pf['wins']}  ", style="green")
        pf_text.append(f"  Kaybedilen: ", style="dim")
        pf_text.append(f"{pf['losses']}", style="red")

        layout["portfolio"].update(
            Panel(pf_text, title="💰  Portföy", border_style="white")
        )

        # ── Recent Decisions ──────────────────────────────────────────────────
        tbl = Table(show_header=True, header_style="bold dim", expand=True, show_edge=False)
        tbl.add_column("Saat", width=6)
        tbl.add_column("Ajan", width=10)
        tbl.add_column("Market", min_width=30)
        tbl.add_column("İşlem", width=6)
        tbl.add_column("$Büyüklük", width=10, justify="right")
        tbl.add_column("Fiyat", width=7, justify="right")
        tbl.add_column("Edge", width=7, justify="right")
        tbl.add_column("Sonuç", width=8)

        for d in s["decisions"]:
            result = d["result"]
            result_style = {
                "WIN": "green bold", "LOSS": "red bold",
                "…": "dim", "EMİR": "yellow",
            }.get(result, "white")
            edge_f = float(d.get("edge", 0))
            tbl.add_row(
                d["time"],
                d["agent"],
                d["market"],
                f"[cyan]{d['action']}[/cyan]",
                f"${d['size']:.1f}",
                f"{d['price']:.3f}",
                f"[{'green' if edge_f >= 0 else 'red'}]{edge_f:+.3f}[/]",
                f"[{result_style}]{result}[/{result_style}]",
            )

        if not s["decisions"]:
            tbl.add_row("—", "—", "Henüz karar alınmadı", "—", "—", "—", "—", "—")

        layout["decisions"].update(
            Panel(tbl, title="📋  Son Kararlar", border_style="blue")
        )

        return layout

    # ── Async run loop ────────────────────────────────────────────────────────

    async def run(self):
        """Dashboard'u canlı olarak göster (asyncio task olarak çalışır)."""
        self._running = True
        console = Console()
        with Live(
            self._render(),
            console=console,
            refresh_per_second=2,
            screen=True,
        ) as live:
            while self._running:
                live.update(self._render())
                await asyncio.sleep(0.5)

    def stop(self):
        self._running = False


# Singleton — tüm modüller bunu import eder
dashboard = Dashboard()
