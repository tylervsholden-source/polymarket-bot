import pytest
from agents.signal_agent import SignalAgent


@pytest.fixture
def agent():
    return SignalAgent.__new__(SignalAgent)  # __init__ çalıştırma (API key gerekmez)


def test_parse_clean_json(agent):
    text = '{"probability": 0.65, "confidence": "HIGH", "reasoning": "test"}'
    result = agent._parse_response(text)
    assert result is not None
    assert result["probability"] == 0.65


def test_parse_json_in_codeblock(agent):
    text = '```json\n{"probability": 0.72, "confidence": "MEDIUM", "reasoning": "ok"}\n```'
    result = agent._parse_response(text)
    assert result is not None
    assert result["probability"] == 0.72


def test_parse_json_with_preamble(agent):
    text = 'İşte analizim:\n{"probability": 0.55, "confidence": "LOW", "reasoning": "belirsiz"}'
    result = agent._parse_response(text)
    assert result is not None
    assert result["probability"] == 0.55


def test_parse_invalid_probability_zero(agent):
    text = '{"probability": 0.0, "confidence": "LOW", "reasoning": "test"}'
    assert agent._parse_response(text) is None


def test_parse_invalid_probability_one(agent):
    text = '{"probability": 1.0, "confidence": "HIGH", "reasoning": "test"}'
    assert agent._parse_response(text) is None


def test_parse_garbage(agent):
    assert agent._parse_response("Bu bir tahmin değil.") is None


def test_parse_missing_probability(agent):
    text = '{"confidence": "HIGH", "reasoning": "eksik alan"}'
    assert agent._parse_response(text) is None
