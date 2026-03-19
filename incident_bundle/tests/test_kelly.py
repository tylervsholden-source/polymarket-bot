import pytest
from strategies.kelly_criterion import KellyCriterion


@pytest.fixture
def kelly():
    return KellyCriterion()


def test_positive_edge(kelly):
    size = kelly.position_size(edge=0.15, price=0.40, capital=1000)
    assert size > 0
    assert size <= 200  # max %20


def test_zero_edge(kelly):
    size = kelly.position_size(edge=0.0, price=0.50, capital=1000)
    assert size == 0.0


def test_negative_edge(kelly):
    size = kelly.position_size(edge=-0.10, price=0.60, capital=1000)
    assert size == 0.0


def test_max_position_cap(kelly):
    # Çok yüksek edge bile %20'yi geçmemeli
    size = kelly.position_size(edge=0.50, price=0.30, capital=1000)
    assert size <= 200


def test_should_enter(kelly):
    assert kelly.should_enter(edge=0.06) is True
    assert kelly.should_enter(edge=0.04) is False
