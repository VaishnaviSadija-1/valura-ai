from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.request import Holding, Portfolio, UserContext
from src.models.response import ClassificationResult, EntitySet, SafetyVerdict
from src.router.router import route


def make_classification(intent="portfolio_health_check", target_agent="portfolio_health", entities=None):
    return ClassificationResult(
        intent=intent,
        entities=entities or EntitySet(),
        target_agent=target_agent,
        safety_verdict=SafetyVerdict(flag=False),
        follow_up_resolved=False,
    )


def make_user_context(holdings=None):
    return UserContext(
        portfolio=Portfolio(holdings=holdings or [], base_currency="USD"),
        risk_profile="moderate",
        kyc_status="verified",
    )


def make_yf_mock():
    import pandas as pd

    mock_yf = MagicMock()
    mock_ticker = MagicMock()
    mock_ticker.info = {"country": "US", "currency": "USD"}
    mock_yf.Ticker.return_value = mock_ticker

    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    prices = pd.Series([100.0 + i * 0.1 for i in range(252)], index=dates, name="Close")
    mock_yf.download.return_value = pd.DataFrame({"Close": prices})
    return mock_yf


async def collect_events(generator):
    events = []
    async for event in generator:
        events.append(event)
    return events


async def test_routes_portfolio_health_to_correct_agent():
    classification = make_classification(target_agent="portfolio_health")
    user_context = make_user_context()

    with patch("src.agents.portfolio_health.yf", make_yf_mock()):
        gen = route(
            target_agent="portfolio_health",
            query="How is my portfolio?",
            user_context=user_context,
            classification=classification,
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )
        events = await collect_events(gen)

    event_types = [e.event for e in events]
    assert "end" in event_types
    # portfolio_health agent should emit structured and end
    assert "structured" in event_types


async def test_unknown_agent_returns_stub():
    classification = make_classification(intent="unknown_intent", target_agent="nonexistent_agent")
    user_context = make_user_context()

    gen = route(
        target_agent="nonexistent_agent",
        query="What is this?",
        user_context=user_context,
        classification=classification,
        session_state=None,
        session_id="test-session",
        openai_client=AsyncMock(),
    )
    events = await collect_events(gen)

    assert events[-1].event == "end"
    structured_events = [e for e in events if e.event == "structured"]
    assert len(structured_events) > 0
    assert structured_events[0].data.get("status") == "not_implemented"


async def test_stub_includes_intent_and_entities():
    entities = EntitySet(tickers=["AAPL"], amounts=[1000.0])
    classification = make_classification(
        intent="stock_analysis",
        target_agent="market_research",
        entities=entities,
    )
    user_context = make_user_context()

    gen = route(
        target_agent="market_research",
        query="Analyze AAPL",
        user_context=user_context,
        classification=classification,
        session_state=None,
        session_id="test-session",
        openai_client=AsyncMock(),
    )
    events = await collect_events(gen)

    structured_events = [e for e in events if e.event == "structured"]
    assert len(structured_events) > 0
    stub_data = structured_events[0].data
    assert stub_data["intent"] == "stock_analysis"
    assert stub_data["status"] == "not_implemented"


async def test_all_stubs_terminate_with_end_event():
    unimplemented_agents = [
        "financial_calculator",
        "market_research",
        "investment_strategy",
        "risk_analysis",
        "recommendations",
        "predictive_analysis",
        "support",
    ]
    user_context = make_user_context()

    for agent_name in unimplemented_agents:
        classification = make_classification(
            intent="test_intent", target_agent=agent_name
        )
        gen = route(
            target_agent=agent_name,
            query="test query",
            user_context=user_context,
            classification=classification,
            session_state=None,
            session_id="test-session",
            openai_client=AsyncMock(),
        )
        events = await collect_events(gen)
        assert events[-1].event == "end", (
            f"Agent '{agent_name}' did not terminate with 'end' event. "
            f"Last event: {events[-1].event}"
        )
