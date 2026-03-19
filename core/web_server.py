"""
Web Dashboard Server — Port 8080'de çalışır.
Arka plan thread'inde HTTP sunucu başlatır.

Routes:
  GET  /                       → web/index.html  (existing dashboard)
  GET  /api/status             → data/status.json
  GET  /api/control            → data/control.json
  POST /api/control            → update control.json
  GET  /api/gate               → 10-point LiveGate status
  GET  /api/pending            → approval queue
  GET  /chamber                → architect_chamber/index.html (operator dashboard)
  GET  /api/chamber/summary    → full ChamberSummary JSON
  GET  /api/chamber/decisions  → recent decision feed
  GET  /api/chamber/positions  → open positions
  GET  /api/chamber/trades     → closed trades
  GET  /api/chamber/health     → health state
  GET  /api/chamber/readiness  → readiness state
  GET  /api/chamber/profile-comparison → profile comparison
  GET  /api/chamber/equity     → equity state
  GET  /api/chamber/decision/<id> → decision drilldown chain
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from loguru import logger

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE   = os.path.join(BASE_DIR, "data", "status.json")
CONTROL_FILE  = os.path.join(BASE_DIR, "data", "control.json")
META_FILE     = os.path.join(BASE_DIR, "data", "position_meta.json")
HTML_FILE     = os.path.join(BASE_DIR, "web", "index.html")
CHAMBER_FILE  = os.path.join(BASE_DIR, "architect_chamber", "index.html")
PENDING_FILE  = os.path.join(BASE_DIR, "data", "pending_orders.json")


def _read_control() -> dict:
    try:
        with open(CONTROL_FILE) as f:
            return json.load(f)
    except Exception:
        return {"live_trading": False}


def _write_control(data: dict) -> None:
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    with open(CONTROL_FILE, "w") as f:
        json.dump(data, f)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(HTML_FILE, "text/html; charset=utf-8")
        elif self.path in ("/chamber", "/chamber/"):
            self._send_file(CHAMBER_FILE, "text/html; charset=utf-8")
        elif self.path.startswith("/api/status"):
            self._send_status()
        elif self.path.startswith("/api/control"):
            self._send_json_data(json.dumps(_read_control()).encode())
        elif self.path.startswith("/api/gate"):
            self._send_gate_status()
        elif self.path.startswith("/api/pending"):
            self._send_pending_orders()
        elif self.path.startswith("/api/chamber/"):
            self._send_chamber(self.path)
        else:
            self.send_error(404)

    def _send_pending_orders(self):
        from core.approval_queue import get_pending, get_all
        pending = get_pending()
        all_orders = get_all()
        data = json.dumps({"pending": pending, "history": all_orders[-20:]}, ensure_ascii=False).encode()
        self._send_json_data(data)

    def _send_gate_status(self):
        """10-nokta LiveGate kontrolünü çalıştır ve JSON döndür."""
        try:
            from control_plane.live_gate import check_live_gate
            from control_plane.process_lock import ProcessLock

            lock = ProcessLock()
            # PositionManager'ı import etmeden positions.json'dan oku
            pos_data = {}
            try:
                pos_file = os.path.join(BASE_DIR, "data", "positions.json")
                with open(pos_file) as f:
                    pos_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass

            capital = pos_data.get("capital", 0)
            open_count = len(pos_data.get("positions", {}))

            result = check_live_gate(
                process_lock=lock,
                control_file=CONTROL_FILE,
                readiness_file=os.path.join(BASE_DIR, "data", "readiness_verdict.json"),
                daily_loss_exceeded=False,  # Snapshot — tam kontrol orchestrator'da
                open_position_count=open_count,
                max_open_positions=int(os.environ.get("MAX_OPEN_POSITIONS", "5")),
                max_orders_per_hour=int(os.environ.get("MAX_ORDERS_PER_HOUR", "3")),
                available_capital=capital,
                required_capital=0,
            )
            self._send_json_data(json.dumps(result.to_dict(), ensure_ascii=False).encode())
        except Exception as exc:
            self._send_json_data(json.dumps({"error": str(exc)}).encode())

    def do_POST(self):
        if self.path.startswith("/api/pending/approve"):
            self._handle_order_action("approve")
        elif self.path.startswith("/api/pending/reject"):
            self._handle_order_action("reject")
        elif self.path.startswith("/api/control"):
            length = int(self.headers.get("Content-Length", 0))
            if length > 4096:
                self.send_error(413, "Request too large")
                return
            body   = self.rfile.read(length)
            try:
                patch = json.loads(body)
                # Whitelist: sadece bilinen anahtarlar kabul edilir
                _ALLOWED_KEYS = {"live_trading", "simulation_running", "min_bet"}
                patch = {k: v for k, v in patch.items() if k in _ALLOWED_KEYS}
                if not patch:
                    self.send_error(400, "No valid keys")
                    return
                ctrl  = _read_control()
                ctrl.update(patch)
                _write_control(ctrl)
                live = ctrl.get("live_trading", False)
                if "min_bet" in patch:
                    logger.info(f"Min bet: ${patch['min_bet']}")
                else:
                    logger.info(f"Trading modu: {'CANLI' if live else 'SIMULASYON'}")
                self._send_json_data(json.dumps(ctrl).encode())
            except Exception:
                self.send_error(400)
        else:
            self.send_error(404)

    def _handle_order_action(self, action: str):
        length = int(self.headers.get("Content-Length", 0))
        if length > 4096:
            self.send_error(413, "Request too large")
            return
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            order_id = data.get("id", "")
            if not order_id:
                self.send_error(400, "Missing order id")
                return
            from core.approval_queue import approve, reject
            ok = approve(order_id) if action == "approve" else reject(order_id)
            result = {"ok": ok, "id": order_id, "action": action}
            self._send_json_data(json.dumps(result).encode())
        except Exception:
            self.send_error(400)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8080")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_chamber(self, path: str):
        """Route /api/chamber/* to operator_layer.api handlers."""
        try:
            from operator_layer import api as chamber_api
        except ImportError as e:
            self._send_json_data(json.dumps({"error": f"operator_layer not available: {e}"}).encode())
            return

        try:
            # Strip query string
            clean = path.split("?")[0].rstrip("/")

            if clean == "/api/chamber/summary":
                data = chamber_api.handle_summary()
            elif clean == "/api/chamber/decisions":
                data = chamber_api.handle_decisions()
            elif clean == "/api/chamber/positions":
                data = chamber_api.handle_positions()
            elif clean == "/api/chamber/trades":
                data = chamber_api.handle_trades()
            elif clean == "/api/chamber/health":
                data = chamber_api.handle_health()
            elif clean == "/api/chamber/readiness":
                data = chamber_api.handle_readiness()
            elif clean == "/api/chamber/profile-comparison":
                data = chamber_api.handle_profile_comparison()
            elif clean == "/api/chamber/equity":
                data = chamber_api.handle_equity()
            elif clean.startswith("/api/chamber/decision/"):
                record_id = clean.split("/api/chamber/decision/", 1)[1]
                data = chamber_api.handle_decision_chain(record_id)
            else:
                self.send_error(404)
                return

            self._send_json_data(data)
        except Exception as exc:
            logger.warning(f"Chamber API error for {path}: {exc}")
            self._send_json_data(json.dumps({"error": str(exc)}).encode())

    def _send_status(self):
        try:
            with open(STATUS_FILE) as f:
                status = json.load(f)
        except FileNotFoundError:
            status = {}
        # position_meta.json'dan ek alanları positions'a merge et
        try:
            with open(META_FILE) as f:
                meta = json.load(f)
            for market_id, extra in meta.items():
                if market_id in status.get("positions", {}):
                    status["positions"][market_id].update(extra)
        except FileNotFoundError:
            pass
        # positions.json'dan güncel capital ve closed trades'i al
        # (status.json bot durduğunda stale kalır)
        try:
            pos_file = os.path.join(BASE_DIR, "data", "positions.json")
            with open(pos_file) as f:
                pm_data = json.load(f)
            status["capital"] = pm_data.get("capital", status.get("capital", 0))
            status["closed"] = pm_data.get("closed", status.get("closed", []))
            # Açık pozisyonları da positions.json'dan al (daha güncel)
            if pm_data.get("positions"):
                status["positions"] = pm_data["positions"]
                status["open_positions"] = len(pm_data["positions"])
            elif not status.get("positions"):
                status["positions"] = {}
                status["open_positions"] = 0
        except FileNotFoundError:
            pass
        self._send_json_data(json.dumps(status, ensure_ascii=False).encode())

    def _send_json_data(self, data: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8080")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: str, ctype: str):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def start(port: int = 8080) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Web dashboard aktif → http://127.0.0.1:{port}")
    return server


if __name__ == "__main__":
    import signal
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(("127.0.0.1", port), _Handler)
    logger.info(f"Standalone dashboard → http://127.0.0.1:{port}")
    logger.info(f"Architect Chamber   → http://localhost:{port}/chamber")
    logger.info("Durdurmak için Ctrl+C")

    def _shutdown(sig, frame):
        logger.info("Dashboard kapatılıyor...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    server.serve_forever()
