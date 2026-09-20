"""
core/web_server.py used a plain (single-threaded) HTTPServer. /api/chamber/*
(handle_summary et al.) reads every shadow_journal_*.jsonl file in full before
truncating to max_records (operator_layer/ledgers.py::read_journal_records) —
these files grow every day the bot runs and the frontend polls
/api/chamber/summary every 5 seconds (see operator_layer/api.py::handle_summary
docstring). With a single-threaded server, a slow chamber read blocks EVERY
other request on the same server — including POST /api/control, the endpoint
used to flip live_trading off. Fixed by switching to ThreadingHTTPServer so a
slow GET can't starve a concurrent control request.
"""
import threading
import time

import httpx

import core.web_server as web_server


def test_slow_chamber_request_does_not_block_control_endpoint(monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def _slow_handle_summary():
        entered.set()
        release.wait(timeout=5)
        return b'{"ok": true}'

    monkeypatch.setattr(
        "operator_layer.api.handle_summary", _slow_handle_summary
    )

    server = web_server.start(port=0)
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        slow_result = {}

        def _fire_slow_request():
            slow_result["resp"] = httpx.get(f"{base}/api/chamber/summary", timeout=10)

        t = threading.Thread(target=_fire_slow_request)
        t.start()
        assert entered.wait(timeout=2), "slow chamber handler never started"

        # While the slow request is still blocked inside handle_summary, a
        # concurrent request to a different endpoint must still complete
        # promptly — proving the server isn't single-threaded.
        start = time.monotonic()
        control_resp = httpx.get(f"{base}/api/control", timeout=2)
        elapsed = time.monotonic() - start

        assert control_resp.status_code == 200
        assert elapsed < 1.0, (
            f"/api/control took {elapsed:.2f}s while a slow chamber request "
            "was in flight — the server is blocking on it"
        )

        release.set()
        t.join(timeout=5)
        assert slow_result["resp"].status_code == 200
    finally:
        release.set()
        server.shutdown()
        server.server_close()
