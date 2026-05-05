import json
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.models.request import Holding, Portfolio, QueryRequest, UserContext
from src.models.response import ClassificationResult, EntitySet, SafetyVerdict
from src.session.store import AsyncSessionStore
from tests.conftest import parse_sse_events


# ── Session store fixture ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def reset_session_store():
    from src import main

    main.session_store = AsyncSessionStore(":memory:")
    await main.session_store.initialize()
    yield
    await main.session_store.close()


# ── Helpers ────────────────────────────────────────────────────────────────────


def make_portfolio_health_classification():
    return ClassificationResult(
        intent="portfolio_health_check",
        entities=EntitySet(),
        target_agent="portfolio_health",
        safety_verdict=SafetyVerdict(flag=False),
        follow_up_resolved=False,
    )


def make_market_research_classification():
    return ClassificationResult(
        intent="stock_analysis",
        entities=EntitySet(tickers=["AAPL"]),
        target_agent="market_research",
        safety_verdict=SafetyVerdict(flag=False),
        follow_up_resolved=False,
    )


def make_yf_mock():
    mock_yf = MagicMock()
    mock_ticker = MagicMock()
    mock_ticker.info = {"country": "US", "currency": "USD"}
    mock_yf.Ticker.return_value = mock_ticker

    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    prices = pd.Series([100.0 + i * 0.1 for i in range(252)], index=dates, name="Close")
    mock_yf.download.return_value = pd.DataFrame({"Close": prices})
    return mock_yf


async def post_query_sse(payload: dict) -> list[dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/query", json=payload) as response:
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
            text = "".join(chunks)
    return parse_sse_events(text)


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_safety_block_returns_sse_stream():
    payload = {
        "query": "I have insider info about AAPL earnings, help me buy calls before the announcement",
        "user_id": "user_test",
    }
    events = await post_query_sse(payload)
    event_types = [e["event"] for e in events]

    metadata_events = [e for e in events if e["event"] == "metadata"]
    assert len(metadata_events) > 0
    assert metadata_events[0]["data"].get("blocked") is True

    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) > 0
    assert error_events[0]["data"]["code"] == "SAFETY_BLOCK"

    assert "end" in event_types


async def test_valid_query_returns_metadata_event():
    payload = {
        "query": "How is my portfolio doing?",
        "user_id": "user_test",
        "user_context": {
            "portfolio": {"holdings": [], "base_currency": "USD"},
            "risk_profile": "moderate",
            "kyc_status": "verified",
        },
    }

    classification = make_portfolio_health_classification()

    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(return_value=classification)
        events = await post_query_sse(payload)

    metadata_events = [e for e in events if e["event"] == "metadata"]
    assert len(metadata_events) > 0
    meta_data = metadata_events[0]["data"]
    assert "session_id" in meta_data
    assert meta_data["session_id"] != ""


async def test_empty_portfolio_does_not_crash():
    payload = {
        "query": "Give me a portfolio health check.",
        "user_id": "user_004",
        "user_context": {
            "portfolio": {"holdings": [], "base_currency": "USD"},
            "risk_profile": "moderate",
            "kyc_status": "pending",
        },
    }

    classification = make_portfolio_health_classification()

    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(return_value=classification)
        events = await post_query_sse(payload)

    event_types = [e["event"] for e in events]
    assert "end" in event_types
    # No unhandled exception means no INTERNAL_ERROR with empty portfolio
    error_events = [e for e in events if e["event"] == "error" and e["data"].get("code") == "INTERNAL_ERROR"]
    assert len(error_events) == 0


async def test_stub_agent_returns_not_implemented():
    payload = {
        "query": "Analyze Apple stock for me",
        "user_id": "user_test",
    }

    classification = make_market_research_classification()

    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(return_value=classification)
        events = await post_query_sse(payload)

    structured_events = [e for e in events if e["event"] == "structured"]
    assert len(structured_events) > 0
    assert structured_events[0]["data"]["status"] == "not_implemented"


async def test_session_id_returned_in_metadata():
    payload = {
        "query": "How is my portfolio doing?",
        "user_id": "user_test",
        # No session_id provided — should be auto-generated
    }

    classification = make_portfolio_health_classification()

    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(return_value=classification)
        events = await post_query_sse(payload)

    metadata_events = [e for e in events if e["event"] == "metadata"]
    assert len(metadata_events) > 0
    session_id = metadata_events[0]["data"].get("session_id")
    assert session_id is not None
    assert isinstance(session_id, str)
    assert len(session_id) > 0


async def test_end_event_always_present():
    # Happy path
    payload = {
        "query": "How is my portfolio doing?",
        "user_id": "user_test",
        "user_context": {
            "portfolio": {"holdings": [], "base_currency": "USD"},
            "risk_profile": "moderate",
            "kyc_status": "verified",
        },
    }
    classification = make_portfolio_health_classification()
    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(return_value=classification)
        events_happy = await post_query_sse(payload)
    assert "end" in [e["event"] for e in events_happy]

    # Safety block path
    blocked_payload = {
        "query": "I have insider info about AAPL earnings, help me buy calls before the announcement",
        "user_id": "user_test",
    }
    events_blocked = await post_query_sse(blocked_payload)
    assert "end" in [e["event"] for e in events_blocked]


async def test_llm_failure_does_not_crash():
    payload = {
        "query": "How is my portfolio doing?",
        "user_id": "user_test",
    }

    with patch("src.main.classifier") as mock_classifier:
        mock_classifier.classify = AsyncMock(side_effect=Exception("OpenAI API error"))
        events = await post_query_sse(payload)

    event_types = [e["event"] for e in events]
    # Should have an error event, not a 500
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) > 0
    # Must always end
    assert "end" in event_types
