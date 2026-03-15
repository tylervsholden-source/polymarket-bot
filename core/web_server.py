"""
Web Dashboard Server — Port 8080'de çalışır.
Arka plan thread'inde HTTP sunucu başlatır.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from loguru import logger

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS_FILE  = os.path.join(BASE_DIR, "data", "status.json")
CONTROL_FILE = os.path.join(BASE_DIR, "data", "control.json")
META_FILE    = os.path.join(BASE_DIR, "data", "position_meta.json")
HTML_FILE    = os.path.join(BASE_DIR, "web", "index.html")


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
        elif self.path.startswith("/api/status"):
            self._send_status()
        elif self.path.startswith("/api/control"):
            self._send_json_data(json.dumps(_read_control()).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/control"):
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            try:
                patch = json.loads(body)
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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
        self._send_json_data(json.dumps(status, ensure_ascii=False).encode())

    def _send_json_data(self, data: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
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
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Web dashboard aktif → http://localhost:{port}")
    return server
