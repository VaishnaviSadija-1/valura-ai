from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.agents.portfolio_health import PortfolioHealthAgent
from src.models.request import Holding, Portfolio, UserContext
from src.models.response import ClassificationResult, EntitySet, SafetyVerdict


def make_user_context(holdings, risk_profile="moderate", kyc_status="verified", base_currency="USD"):
    return UserContext(
        portfolio=Portfolio(holdings=holdings, base_currency=base_currency),
        risk_profile=risk_profile,
        kyc_status=kyc_status,
    )


def make_classification():
    return ClassificationResult(
        intent="portfolio_health_check",
        entities=EntitySet(),
        target_agent="portfolio_health",
        safety_verdict=SafetyVerdict(flag=False),
        follow_up_resolved=False,
    )


def make_price_df(start=100.0, end=110.0, periods=252):
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=periods, freq="B")
    prices = pd.Series(
        [start + (end - start) * i / (periods - 1) for i in range(periods)],
        index=dates,
        name="Close",
    )
    df = pd.DataFrame({"Close": prices})
    return df


def make_yf_mock(country="US", currency="USD", start_price=100.0, end_price=110.0):
    mock_yf = MagicMock()
    mock_ticker = MagicMock()
    mock_ticker.info = {"country": country, "currency": currency}
    mock_yf.Ticker.return_value = mock_ticker
    mock_yf.download.return_value = make_price_df(start_price, end_price)
    return mock_yf


async def collect_events(agent, **kwargs):
    events = []
    async for event in agent.run(**kwargs):
        events.append(event)
    return events


async def test_monitoring_mode_with_holdings():
    holdings = [
        Holding(ticker="NVDA", quantity=100, current_price=875.0, currency="USD", cost_basis=220.0, purchase_date="2022-06-15"),
        Holding(ticker="AAPL", quantity=50, current_price=182.0, currency="USD", cost_basis=150.0, purchase_date="2022-06-15"),
    ]
    user_context = make_user_context(holdings)
    agent = PortfolioHealthAgent()

    with patch("src.agents.portfolio_health.yf", make_yf_mock()):
        events = await collect_events(
            agent,
            query="How is my portfolio?",
            user_context=user_context,
            classification=make_classification(),
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )

    event_types = [e.event for e in events]
    assert "token" in event_types
    assert "structured" in event_types
    assert "end" in event_types

    structured = next(e for e in events if e.event == "structured")
    result = structured.data
    assert result["mode"] == "monitoring"
    assert result["concentration_risk"]["top_position_pct"] is not None
    assert result["disclaimer"] != ""


async def test_empty_portfolio_returns_onboarding_mode():
    user_context = make_user_context([])
    agent = PortfolioHealthAgent()

    events = await collect_events(
        agent,
        query="Give me a portfolio health check.",
        user_context=user_context,
        classification=make_classification(),
        session_state=None,
        session_id="test-session",
        openai_client=AsyncMock(),
    )

    structured = next(e for e in events if e.event == "structured")
    result = structured.data
    assert result["mode"] == "onboarding"
    assert result["concentration_risk"]["status"] == "not_applicable"
    assert len(result["next_steps"]) > 0


async def test_concentration_risk_high_flag():
    # One holding worth ~85% of portfolio
    holdings = [
        Holding(ticker="AAPL", quantity=1000, current_price=182.0, currency="USD", cost_basis=150.0, purchase_date="2022-01-01"),
        Holding(ticker="MSFT", quantity=10, current_price=415.0, currency="USD", cost_basis=300.0, purchase_date="2022-01-01"),
    ]
    user_context = make_user_context(holdings)
    agent = PortfolioHealthAgent()

    with patch("src.agents.portfolio_health.yf", make_yf_mock()):
        events = await collect_events(
            agent,
            query="Check my portfolio health",
            user_context=user_context,
            classification=make_classification(),
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )

    structured = next(e for e in events if e.event == "structured")
    result = structured.data
    assert result["concentration_risk"]["flag"] == "high"


async def test_concentration_risk_low_flag():
    # Five equally-weighted positions
    holdings = [
        Holding(ticker="AAPL", quantity=20, current_price=100.0, currency="USD", cost_basis=80.0, purchase_date="2022-01-01"),
        Holding(ticker="MSFT", quantity=20, current_price=100.0, currency="USD", cost_basis=80.0, purchase_date="2022-01-01"),
        Holding(ticker="GOOGL", quantity=20, current_price=100.0, currency="USD", cost_basis=80.0, purchase_date="2022-01-01"),
        Holding(ticker="AMZN", quantity=20, current_price=100.0, currency="USD", cost_basis=80.0, purchase_date="2022-01-01"),
        Holding(ticker="META", quantity=20, current_price=100.0, currency="USD", cost_basis=80.0, purchase_date="2022-01-01"),
    ]
    user_context = make_user_context(holdings)
    agent = PortfolioHealthAgent()

    with patch("src.agents.portfolio_health.yf", make_yf_mock()):
        events = await collect_events(
            agent,
            query="Check my portfolio health",
            user_context=user_context,
            classification=make_classification(),
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )

    structured = next(e for e in events if e.event == "structured")
    result = structured.data
    assert result["concentration_risk"]["flag"] in ("low", "medium")


async def test_disclaimer_always_present():
    agent = PortfolioHealthAgent()

    # Onboarding mode
    empty_context = make_user_context([])
    events_onboarding = await collect_events(
        agent,
        query="portfolio check",
        user_context=empty_context,
        classification=make_classification(),
        session_state=None,
        session_id="sess-1",
        openai_client=AsyncMock(),
    )
    structured = next(e for e in events_onboarding if e.event == "structured")
    assert structured.data["disclaimer"] != ""
    assert len(structured.data["disclaimer"]) > 10

    # Monitoring mode
    holdings = [
        Holding(ticker="AAPL", quantity=10, current_price=182.0, currency="USD", cost_basis=150.0, purchase_date="2022-01-01"),
    ]
    monitoring_context = make_user_context(holdings)
    with patch("src.agents.portfolio_health.yf", make_yf_mock()):
        events_monitoring = await collect_events(
            agent,
            query="portfolio check",
            user_context=monitoring_context,
            classification=make_classification(),
            session_state=None,
            session_id="sess-2",
            openai_client=AsyncMock(),
        )
    structured = next(e for e in events_monitoring if e.event == "structured")
    assert structured.data["disclaimer"] != ""
    assert len(structured.data["disclaimer"]) > 10


async def test_missing_cost_basis_uses_estimated_basis():
    holdings = [
        Holding(ticker="AAPL", quantity=10, current_price=182.0, currency="USD"),
        # No cost_basis, no purchase_date
    ]
    user_context = make_user_context(holdings)
    agent = PortfolioHealthAgent()

    with patch("src.agents.portfolio_health.yf", make_yf_mock(start_price=150.0, end_price=182.0)):
        events = await collect_events(
            agent,
            query="portfolio check",
            user_context=user_context,
            classification=make_classification(),
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )

    structured = next(e for e in events if e.event == "structured")
    result = structured.data
    perf = result["performance"]
    # Should use estimated since no cost_basis/purchase_date
    if perf.get("status") != "not_applicable":
        assert perf["return_basis"] == "estimated"


async def test_yields_end_event():
    agent = PortfolioHealthAgent()
    user_context = make_user_context([])

    events = await collect_events(
        agent,
        query="portfolio check",
        user_context=user_context,
        classification=make_classification(),
        session_state=None,
        session_id="test-session",
        openai_client=AsyncMock(),
    )

    assert events[-1].event == "end"


async def test_kyc_pending_adds_observation():
    user_context = make_user_context([], kyc_status="pending")
    agent = PortfolioHealthAgent()

    events = await collect_events(
        agent,
        query="portfolio check",
        user_context=user_context,
        classification=make_classification(),
        session_state=None,
        session_id="test-session",
        openai_client=AsyncMock(),
    )

    structured = next(e for e in events if e.event == "structured")
    observations = structured.data["observations"]
    kyc_obs = [o for o in observations if "kyc" in o["text"].lower() or "verification" in o["text"].lower()]
    assert len(kyc_obs) > 0
