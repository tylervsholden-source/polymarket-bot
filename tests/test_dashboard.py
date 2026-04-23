import pytest
from core.dashboard import Dashboard

@pytest.fixture
def dash():
    return Dashboard()

def test_dashboard_initial_state(dash):
    assert dash.state["orchestrator"]["cycle"] == 0
    assert len(dash.state["decisions"]) == 0

def test_dashboard_update(dash):
    dash.update("orchestrator", cycle=5, scanned=100)
    assert dash.state["orchestrator"]["cycle"] == 5
    assert dash.state["orchestrator"]["scanned"] == 100

def test_add_decision(dash):
    dash.add_decision("AgentA", "MarketX", "BUY", 100.0, 0.5)
    assert len(dash.state["decisions"]) == 1
    assert dash.state["decisions"][0]["agent"] == "AgentA"
    assert dash.state["decisions"][0]["market"] == "MarketX"

def test_update_decision_result(dash):
    dash.add_decision("AgentA", "MarketX", "BUY", 100.0, 0.5)
    dash.update_decision_result("MarketX", "WIN")
    assert dash.state["decisions"][0]["result"] == "WIN"

def test_decisions_limit(dash):
    for i in range(20):
        dash.add_decision("Agent", f"Market{i}", "BUY", 10.0, 0.1)
    assert len(dash.state["decisions"]) == 15
    assert dash.state["decisions"][0]["market"] == "Market19"

def test_update_positions(dash):
    positions = {"abc": {"question": "Bitcoin Up?", "outcome": "YES", "amount": 5.0}}
    dash.update_positions(positions)
    assert "abc" in dash.state["positions"]

def test_update_crypto(dash):
    dash.update_crypto({"BTC": {"price": 70000, "change_pct": -1.2}})
    assert dash.state["crypto"]["BTC"]["price"] == 70000
