"""
core/web_server.py::_send_status() merged position_meta.json (target_price,
asset, gamma_id) into status["positions"] BEFORE reading positions.json and
overwriting status["positions"] wholesale with the fresher pm_data["positions"]
dict (comment: "positions.json'dan güncel ... al, status.json bot durduğunda
stale kalır"). Whenever positions.json actually has open positions — the
normal case while the bot runs — that override discarded the meta merge
entirely, so /api/status never surfaced target_price/asset/gamma_id from
position_meta.json to a live dashboard. Fixed by moving the meta merge to run
after the positions.json override, against whichever positions dict won.
"""
import json

import core.web_server as web_server


def _make_handler():
    handler = web_server._Handler.__new__(web_server._Handler)
    return handler


def test_position_meta_merge_survives_positions_json_override(tmp_path, monkeypatch):
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({"positions": {}}))

    meta_file = tmp_path / "position_meta.json"
    meta_file.write_text(json.dumps({
        "mkt1": {"target_price": 1.3944, "asset": "XRP/USD", "gamma_id": "1574501"},
    }))

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "positions.json").write_text(json.dumps({
        "capital": 42.0,
        "closed": [],
        "positions": {
            "mkt1": {
                "question": "XRP Up or Down",
                "outcome": "YES",
                "amount": 5.0,
                "entry_price": 0.5,
                "status": "matched",
            },
        },
    }))

    monkeypatch.setattr(web_server, "STATUS_FILE", str(status_file))
    monkeypatch.setattr(web_server, "META_FILE", str(meta_file))
    monkeypatch.setattr(web_server, "BASE_DIR", str(tmp_path))

    handler = _make_handler()
    sent = {}
    handler._send_json_data = lambda data: sent.setdefault("body", json.loads(data))

    handler._send_status()

    positions = sent["body"]["positions"]
    assert "mkt1" in positions
    pos = positions["mkt1"]
    # From positions.json (the "fresher" source):
    assert pos["question"] == "XRP Up or Down"
    # From position_meta.json — must survive the positions.json override:
    assert pos["target_price"] == 1.3944
    assert pos["asset"] == "XRP/USD"
    assert pos["gamma_id"] == "1574501"


def test_position_meta_merge_still_works_without_positions_json_override(tmp_path, monkeypatch):
    """Regression guard: the no-positions.json path (falls back to status.json)
    must keep merging meta the way it always did."""
    status_file = tmp_path / "status.json"
    status_file.write_text(json.dumps({
        "positions": {
            "mkt1": {"question": "XRP Up or Down", "outcome": "YES", "amount": 5.0},
        },
    }))

    meta_file = tmp_path / "position_meta.json"
    meta_file.write_text(json.dumps({
        "mkt1": {"target_price": 1.3944, "asset": "XRP/USD"},
    }))

    # No data/positions.json under this BASE_DIR → FileNotFoundError branch.
    monkeypatch.setattr(web_server, "STATUS_FILE", str(status_file))
    monkeypatch.setattr(web_server, "META_FILE", str(meta_file))
    monkeypatch.setattr(web_server, "BASE_DIR", str(tmp_path))

    handler = _make_handler()
    sent = {}
    handler._send_json_data = lambda data: sent.setdefault("body", json.loads(data))

    handler._send_status()

    pos = sent["body"]["positions"]["mkt1"]
    assert pos["target_price"] == 1.3944
    assert pos["asset"] == "XRP/USD"
