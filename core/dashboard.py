"""
Canli Terminal Dashboard v2 — Pozisyon & Crypto Odakli

Layout:
  [Header]
  [Crypto Ticker (Binance Feed)]  |  [Orchestrator + Portfolio]
  [Acik Pozisyonlar Tablosu — YES/NO, entry, current, PnL]
  [Son Kararlar + Kapanan Trade'ler]

Kullanim:
  from core.dashboard import dashboard
  dashboard.update("orchestrator", cycle=5, scanned=100)
  dashboard.update_positions(positions_dict)
  dashboard.update_crypto({"BTC": {"price": 70000, "change_pct": -1.2}, ...})
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POSITIONS_FILE = os.path.join(_BASE_DIR, "data", "positions.json")
_STATUS_FILE = os.path.join(_BASE_DIR, "data", "status.json")


def _coin_from_question(q: str) -> str:
    """Extract coin name from question like 'Bitcoin Up or Down - March 24, 4:45PM'."""
    q_lower = q.lower()
    for coin, name in [
        ("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana"),
        ("XRP", "xrp"), ("DOGE", "doge"), ("BNB", "bnb"), ("HYPE", "hype"),
    ]:
        if name in q_lower or coin.lower() in q_lower:
            return coin
    # Non-crypto — show first 20 chars
    return q[:20]


def _time_window(q: str) -> str:
    """Extract time window like '4:45PM-5:00PM' from question."""
    import re
    m = re.search(r'(\d{1,2}:\d{2}[AP]M\s*-\s*\d{1,2}:\d{2}[AP]M)', q, re.IGNORECASE)
    return m.group(1) if m else ""


class Dashboard:
    """Singleton dashboard — tum ajanlar bu nesneye yazar."""

    def __init__(self):
        self.state: dict[str, Any] = {
            "orchestrator": {
                "cycle": 0, "scanned": 0, "candidates": 0,
                "open_pos": 0, "max_pos": 5, "next_in": "—", "last_at": "—",
            },
            "portfolio": {
                "capital": 0.0, "initial": 0.0, "open": 0, "wins": 0, "losses": 0,
            },
            "crypto": {},      # {"BTC": {"price": 70000, "change_pct": -1.2}, ...}
            "positions": {},   # from positions.json
            "closed": [],      # from positions.json
            "decisions": [],
            "signal": {
                "status": "bekliyor", "market": "—", "prob": "—",
                "conf": "—", "reasoning": "—", "last_at": "—",
            },
            "whale": {
                "status": "izliyor", "market": "—", "direction": "—",
                "buys": 0, "sells": 0, "volume": 0, "last_at": "—",
            },
            "btc_arb": {
                "btc_price": "—", "last_move_pct": "—", "status": "bekleniyor",
                "last_trade": "—", "last_at": "—",
            },
            "onchain": {
                "alert_level": "NEUTRAL", "message": "Izleniyor",
                "exchange_netflow_btc": 0.0, "large_transfer_usd": 0.0, "timestamp": "—",
            },
            "daily_pnl": 0.0,
        }
        self._running = False

    def update(self, section: str, **kwargs):
        if section in self.state:
            if isinstance(self.state[section], dict):
                self.state[section].update(kwargs)
                if "last_at" in self.state[section]:
                    self.state[section]["last_at"] = datetime.now().strftime("%H:%M:%S")

    def update_positions(self, positions: dict):
        self.state["positions"] = positions

    def update_closed(self, closed: list):
        self.state["closed"] = closed

    def update_crypto(self, data: dict):
        self.state["crypto"] = data

    def add_decision(self, agent: str, market: str, action: str,
                     size: float, price: float, edge: float = 0.0, result: str = "…"):
        entry = {
            "time": datetime.now().strftime("%H:%M"),
            "agent": agent, "market": market[:40], "action": action,
            "size": size, "price": price, "edge": edge, "result": result,
        }
        self.state["decisions"].insert(0, entry)
        self.state["decisions"] = self.state["decisions"][:15]

    def update_decision_result(self, market: str, result: str):
        for d in self.state["decisions"]:
            if d["market"] in market or market in d["market"]:
                d["result"] = result
                break

    def _load_live_data(self):
        """Read positions.json and status.json for fresh data."""
        try:
            with open(_POSITIONS_FILE) as f:
                pm = json.load(f)
            self.state["positions"] = pm.get("positions", {})
            self.state["closed"] = pm.get("closed", [])
            self.state["portfolio"]["capital"] = pm.get("capital", 0)
            self.state["daily_pnl"] = pm.get("daily", {}).get("pnl", 0)
        except Exception:
            pass

        try:
            with open(_STATUS_FILE) as f:
                st = json.load(f)
            if st.get("crypto"):
                self.state["crypto"] = st["crypto"]
        except Exception:
            pass

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self) -> Layout:
        self._load_live_data()
        s = self.state
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="top_row", size=9),
            Layout(name="positions"),
            Layout(name="bottom"),
        )

        # Top row: crypto ticker left, orchestrator+portfolio right
        layout["top_row"].split_row(
            Layout(name="crypto_panel", ratio=3),
            Layout(name="status_panel", ratio=2),
        )

        # Bottom: decisions + closed trades
        layout["bottom"].split_row(
            Layout(name="decisions_panel", ratio=3),
            Layout(name="closed_panel", ratio=2),
        )

        # ── Header ────────────────────────────────────────────────────────────
        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        pf = s["portfolio"]
        cap = float(pf["capital"])
        daily = float(s.get("daily_pnl", 0))
        daily_color = "green" if daily >= 0 else "red"
        daily_sign = "+" if daily >= 0 else ""

        # Count wins/losses from closed
        wins = sum(1 for c in s.get("closed", []) if c.get("result") == "WIN")
        losses = sum(1 for c in s.get("closed", []) if c.get("result") == "LOSS")

        header_text = Text(justify="center")
        header_text.append(f"  POLYMARKET TRADING BOT  ", style="bold white on dark_blue")
        header_text.append(f"  {now}  ", style="white")
        header_text.append(f"  Sermaye: ", style="dim")
        header_text.append(f"${cap:,.2f}", style="white bold")
        header_text.append(f"  Gunluk: ", style="dim")
        header_text.append(f"{daily_sign}${daily:,.2f}", style=f"bold {daily_color}")
        header_text.append(f"  W/L: ", style="dim")
        header_text.append(f"{wins}", style="green bold")
        header_text.append(f"/", style="dim")
        header_text.append(f"{losses}", style="red bold")

        layout["header"].update(Panel(header_text, style="bold blue"))

        # ── Crypto Ticker ─────────────────────────────────────────────────────
        crypto = s.get("crypto", {})
        crypto_tbl = Table(show_header=True, header_style="bold cyan", expand=True,
                           show_edge=False, pad_edge=False)
        crypto_tbl.add_column("Coin", width=6, style="bold white")
        crypto_tbl.add_column("Fiyat", width=12, justify="right")
        crypto_tbl.add_column("%24h", width=8, justify="right")
        crypto_tbl.add_column("Sinyal", width=10, justify="center")

        for coin in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE"]:
            data = crypto.get(coin, {})
            price = data.get("price", 0)
            change = data.get("change_pct", 0)
            if price == 0:
                crypto_tbl.add_row(coin, "—", "—", "—")
                continue
            change_color = "green" if change >= 0 else "red"
            change_sign = "+" if change >= 0 else ""

            # Signal based on change
            if abs(change) < 0.3:
                signal = "[dim]FLAT[/dim]"
            elif change > 1.5:
                signal = "[green bold]STRONG UP[/green bold]"
            elif change > 0:
                signal = "[green]UP[/green]"
            elif change < -1.5:
                signal = "[red bold]STRONG DN[/red bold]"
            else:
                signal = "[red]DOWN[/red]"

            price_str = f"${price:,.2f}" if price >= 1 else f"${price:.6f}"
            crypto_tbl.add_row(
                f"[bold]{coin}[/bold]",
                f"[white bold]{price_str}[/white bold]",
                f"[{change_color}]{change_sign}{change:.2f}%[/{change_color}]",
                signal,
            )

        layout["crypto_panel"].update(
            Panel(crypto_tbl, title="Binance Feed - Canli Fiyatlar", border_style="cyan")
        )

        # ── Status Panel (Orchestrator + Info) ────────────────────────────────
        oc = s["orchestrator"]
        positions = s.get("positions", {})
        dir_count = sum(1 for p in positions.values() if p.get("strategy") == "directional")
        bond_count = sum(1 for p in positions.values() if p.get("strategy") == "bond")

        status_text = Text()
        status_text.append(f"  Dongu    : ", style="dim")
        status_text.append(f"#{oc['cycle']}\n", style="white bold")
        status_text.append(f"  Taranan  : ", style="dim")
        status_text.append(f"{oc['scanned']} market\n", style="white")
        status_text.append(f"  Aday     : ", style="dim")
        status_text.append(f"{oc['candidates']} market\n", style="cyan")
        status_text.append(f"  Sonraki  : ", style="dim")
        status_text.append(f"{oc['next_in']}\n", style="yellow")
        status_text.append(f"  Pozisyon : ", style="dim")
        status_text.append(f"{dir_count} dir", style="green bold")
        status_text.append(f" + ", style="dim")
        status_text.append(f"{bond_count} bond\n", style="yellow")
        # Total unrealized PnL
        total_upnl = sum(float(p.get("unrealized_pnl", 0)) for p in positions.values())
        upnl_color = "green" if total_upnl >= 0 else "red"
        upnl_sign = "+" if total_upnl >= 0 else ""
        status_text.append(f"  Unr. PnL : ", style="dim")
        status_text.append(f"{upnl_sign}${total_upnl:,.2f}", style=f"bold {upnl_color}")

        layout["status_panel"].update(
            Panel(status_text, title="Orchestrator", border_style="magenta")
        )

        # ── Open Positions Table ──────────────────────────────────────────────
        pos_tbl = Table(show_header=True, header_style="bold white", expand=True,
                        show_edge=False, row_styles=["", "dim"])
        pos_tbl.add_column("#", width=3, justify="right")
        pos_tbl.add_column("Coin", width=8)
        pos_tbl.add_column("Yon", width=5, justify="center")
        pos_tbl.add_column("Strateji", width=10)
        pos_tbl.add_column("Giris", width=8, justify="right")
        pos_tbl.add_column("Simdi", width=8, justify="right")
        pos_tbl.add_column("Tutar", width=8, justify="right")
        pos_tbl.add_column("Deger", width=8, justify="right")
        pos_tbl.add_column("PnL", width=10, justify="right")
        pos_tbl.add_column("Pencere", width=18)

        sorted_positions = sorted(
            positions.items(),
            key=lambda x: x[1].get("created_at", ""),
            reverse=True,
        )

        for i, (mid, pos) in enumerate(sorted_positions, 1):
            q = pos.get("question", "")
            coin = _coin_from_question(q)
            outcome = pos.get("outcome", "?")
            strategy = pos.get("strategy", "?")
            entry_p = float(pos.get("entry_price", 0))
            current_p = float(pos.get("current_price", 0))
            amount = float(pos.get("amount", 0))
            value = float(pos.get("current_value", amount))
            upnl = float(pos.get("unrealized_pnl", 0))
            window = _time_window(q)

            # Direction color
            if outcome == "YES":
                dir_style = "[green bold]YES[/green bold]"
            else:
                dir_style = "[red bold]NO[/red bold]"

            # Strategy style
            strat_style = "[yellow]bond[/yellow]" if strategy == "bond" else "[cyan]direct[/cyan]"

            # PnL color
            pnl_color = "green" if upnl >= 0 else "red"
            pnl_sign = "+" if upnl >= 0 else ""
            pnl_pct = ((upnl / amount) * 100) if amount > 0 else 0
            pnl_str = f"[{pnl_color} bold]{pnl_sign}${upnl:.2f} ({pnl_sign}{pnl_pct:.0f}%)[/{pnl_color} bold]"

            # Current price change indicator
            if current_p > entry_p:
                price_style = f"[green]{current_p:.3f}[/green]"
            elif current_p < entry_p:
                price_style = f"[red]{current_p:.3f}[/red]"
            else:
                price_style = f"[white]{current_p:.3f}[/white]"

            pos_tbl.add_row(
                str(i),
                f"[bold]{coin}[/bold]",
                dir_style,
                strat_style,
                f"{entry_p:.3f}",
                price_style,
                f"${amount:.2f}",
                f"${value:.2f}",
                pnl_str,
                window or "—",
            )

        if not positions:
            pos_tbl.add_row("—", "—", "—", "—", "—", "—", "—", "—", "[dim]Pozisyon yok[/dim]", "—")

        layout["positions"].update(
            Panel(pos_tbl, title=f"Acik Pozisyonlar ({len(positions)})", border_style="green")
        )

        # ── Recent Decisions ──────────────────────────────────────────────────
        dec_tbl = Table(show_header=True, header_style="bold dim", expand=True, show_edge=False)
        dec_tbl.add_column("Saat", width=6)
        dec_tbl.add_column("Market", min_width=20)
        dec_tbl.add_column("Yon", width=5, justify="center")
        dec_tbl.add_column("$", width=6, justify="right")
        dec_tbl.add_column("Fiyat", width=7, justify="right")
        dec_tbl.add_column("Edge", width=7, justify="right")
        dec_tbl.add_column("Sonuc", width=8)

        for d in s["decisions"][:8]:
            result = d.get("result", "…")
            result_style = {"WIN": "green bold", "LOSS": "red bold", "…": "dim", "EMIR": "yellow"}.get(result, "white")
            edge_f = float(d.get("edge", 0))
            action = d.get("action", "?")
            dec_tbl.add_row(
                d["time"],
                d["market"],
                f"[cyan]{action}[/cyan]",
                f"${d['size']:.1f}",
                f"{d['price']:.3f}",
                f"[{'green' if edge_f >= 0 else 'red'}]{edge_f:+.3f}[/]",
                f"[{result_style}]{result}[/{result_style}]",
            )

        if not s["decisions"]:
            dec_tbl.add_row("—", "[dim]Henuz karar alinmadi[/dim]", "—", "—", "—", "—", "—")

        layout["decisions_panel"].update(
            Panel(dec_tbl, title="Son Kararlar", border_style="blue")
        )

        # ── Closed Trades ─────────────────────────────────────────────────────
        closed = s.get("closed", [])
        cl_tbl = Table(show_header=True, header_style="bold dim", expand=True, show_edge=False)
        cl_tbl.add_column("Coin", width=6)
        cl_tbl.add_column("Yon", width=5, justify="center")
        cl_tbl.add_column("Giris", width=7, justify="right")
        cl_tbl.add_column("PnL", width=10, justify="right")
        cl_tbl.add_column("Sonuc", width=8, justify="center")

        for c in reversed(closed[-8:]):
            q = c.get("question", "")
            coin = _coin_from_question(q)
            outcome = c.get("outcome", "?")
            entry_p = float(c.get("entry_price", 0))
            pnl = float(c.get("pnl", 0))
            result = c.get("result", "?")

            dir_style = "[green]YES[/green]" if outcome == "YES" else "[red]NO[/red]"
            pnl_color = "green" if pnl >= 0 else "red"
            pnl_sign = "+" if pnl >= 0 else ""
            res_style = "green bold" if result == "WIN" else "red bold"

            cl_tbl.add_row(
                f"[bold]{coin}[/bold]",
                dir_style,
                f"{entry_p:.3f}",
                f"[{pnl_color} bold]{pnl_sign}${pnl:.2f}[/{pnl_color} bold]",
                f"[{res_style}]{result}[/{res_style}]",
            )

        if not closed:
            cl_tbl.add_row("—", "—", "—", "[dim]—[/dim]", "[dim]—[/dim]")

        layout["closed_panel"].update(
            Panel(cl_tbl, title=f"Kapanan ({wins}W/{losses}L)", border_style="yellow")
        )

        return layout

    # ── Async run loop ────────────────────────────────────────────────────────

    async def run(self):
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


# Singleton
dashboard = Dashboard()


if __name__ == "__main__":
    import signal
    import sys

    def _stop(sig, frame):
        dashboard.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    print("Dashboard baslatiliyor... (Ctrl+C ile kapat)")
    asyncio.run(dashboard.run())
