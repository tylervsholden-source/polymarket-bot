import pytest
from core.dashboard import Dashboard

@pytest.fixture
def dashboard():
    return Dashboard()

def test_dashboard_initial_state(dashboard):
    assert dashboard.state["signal"]["status"] == "bekliyor"
    assert len(dashboard.state["decisions"]) == 0

def test_dashboard_update(dashboard):
    dashboard.update("signal", status="test", market="BTC")
    assert dashboard.state["signal"]["status"] == "test"
    assert dashboard.state["signal"]["market"] == "BTC"
    assert dashboard.state["signal"]["last_at"] != "—"

def test_add_decision(dashboard):
    dashboard.add_decision("AgentA", "MarketX", "BUY", 100.0, 0.5)
    assert len(dashboard.state["decisions"]) == 1
    assert dashboard.state["decisions"][0]["agent"] == "AgentA"
    assert dashboard.state["decisions"][0]["market"] == "MarketX"

def test_update_decision_result(dashboard):
    dashboard.add_decision("AgentA", "MarketX", "BUY", 100.0, 0.5)
    dashboard.update_decision_result("MarketX", "WIN")
    assert dashboard.state["decisions"][0]["result"] == "WIN"

def test_decisions_limit(dashboard):
    # Add 12 decisions, check if it limits to 10
    for i in range(12):
        dashboard.add_decision("Agent", f"Market{i}", "BUY", 10.0, 0.1)
    
    assert len(dashboard.state["decisions"]) == 10
    assert dashboard.state["decisions"][0]["market"] == "Market11"
