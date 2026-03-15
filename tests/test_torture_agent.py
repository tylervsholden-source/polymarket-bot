import pytest
import json
from agents.torture_agent import MrTortureAgent

@pytest.fixture
def agent():
    # Use __new__ to avoid API key requirement during init if possible,
    # but MrTortureAgent init actually requires os.getenv("ANTHROPIC_API_KEY") for the client.
    # We'll mock the internal state or just provide a dummy key in env for testing.
    import os
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-key"
    return MrTortureAgent()

def test_parse_valid_json(agent):
    text = """
    İşte analizim:
    {
        "inaccuracies": "Hata yok",
        "bottlenecks": "Düşük hacim",
        "improvements": "Limit emri kullan",
        "decision": "BUY",
        "reasoning": "Mantıklı görünüyor"
    }
    """
    result = agent._parse_response(text)
    assert result is not None
    assert result["decision"] == "BUY"
    assert result["inaccuracies"] == "Hata yok"

def test_parse_json_with_nested_content(agent):
    # Testing the fallback regex
    text = 'Bazı metinler... {"decision": "SELL", "reasoning": "Riskli {iç içe}"} ...daha fazla metin'
    result = agent._parse_response(text)
    assert result is not None
    assert result["decision"] == "SELL"

def test_parse_invalid_json(agent):
    text = "Bu bir JSON değil."
    result = agent._parse_response(text)
    assert result is None

def test_build_prompt(agent):
    asset_data = {"name": "Test Market", "details": {"price": 0.5}}
    agents_results = {"Signal": "BUY"}
    prompt = agent._build_prompt(asset_data, agents_results)
    assert "Test Market" in prompt
    assert "Signal" in prompt
    assert "BUY" in prompt
