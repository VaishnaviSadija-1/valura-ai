import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.classifier.classifier import IntentClassifier
from src.models.response import ClassificationResult


def make_mock_client(response_json: dict):
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(response_json)
    client.chat.completions.create = AsyncMock(return_value=mock_response)
    return client


@pytest.fixture
def classifier():
    return IntentClassifier()


async def test_classifies_portfolio_health_query(classifier):
    payload = {
        "intent": "portfolio_health_check",
        "entities": {
            "tickers": [],
            "amounts": [],
            "time_period": None,
            "sectors": [],
            "topics": [],
        },
        "target_agent": "portfolio_health",
        "safety_verdict": {"flag": False, "reason": None},
        "follow_up_resolved": False,
    }
    client = make_mock_client(payload)
    result = await classifier.classify("How is my portfolio doing?", None, client)
    assert isinstance(result, ClassificationResult)
    assert result.target_agent == "portfolio_health"
    assert result.intent == "portfolio_health_check"


async def test_classifies_market_research_with_ticker(classifier):
    payload = {
        "intent": "stock_analysis",
        "entities": {
            "tickers": ["AAPL"],
            "amounts": [],
            "time_period": None,
            "sectors": [],
            "topics": [],
        },
        "target_agent": "market_research",
        "safety_verdict": {"flag": False, "reason": None},
        "follow_up_resolved": False,
    }
    client = make_mock_client(payload)
    result = await classifier.classify("Analyze Apple stock", None, client)
    assert result.target_agent == "market_research"
    assert "AAPL" in result.entities.tickers


async def test_classifies_financial_calculator(classifier):
    payload = {
        "intent": "compound_interest_calculation",
        "entities": {
            "tickers": [],
            "amounts": [10000],
            "time_period": "10 years",
            "sectors": [],
            "topics": ["compound interest"],
        },
        "target_agent": "financial_calculator",
        "safety_verdict": {"flag": False, "reason": None},
        "follow_up_resolved": False,
    }
    client = make_mock_client(payload)
    result = await classifier.classify(
        "What's the compound interest on $10,000 at 7% for 10 years?", None, client
    )
    assert result.target_agent == "financial_calculator"
    assert 10000 in result.entities.amounts


async def test_handles_malformed_json_gracefully(classifier):
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not valid json {{{"
    client.chat.completions.create = AsyncMock(return_value=mock_response)

    result = await classifier.classify("some query", None, client)
    assert result.target_agent == "support"
    assert result.intent == "unknown"


async def test_handles_openai_timeout(classifier):
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError())

    result = await classifier.classify("some query", None, client)
    assert result.target_agent == "support"
    assert result.intent == "unknown"


async def test_follow_up_resolution(classifier):
    payload = {
        "intent": "stock_analysis_follow_up",
        "entities": {
            "tickers": ["AAPL"],
            "amounts": [],
            "time_period": None,
            "sectors": [],
            "topics": [],
        },
        "target_agent": "market_research",
        "safety_verdict": {"flag": False, "reason": None},
        "follow_up_resolved": True,
    }
    client = make_mock_client(payload)
    session_state = {
        "last_intent": "stock_analysis",
        "last_entities": {"tickers": ["MSFT"]},
        "conversation_summary": "User asked about Microsoft stock",
    }
    result = await classifier.classify("What about Apple?", session_state, client)
    assert result.follow_up_resolved is True


async def test_session_state_included_in_prompt(classifier):
    payload = {
        "intent": "portfolio_health_check",
        "entities": {"tickers": [], "amounts": [], "time_period": None, "sectors": [], "topics": []},
        "target_agent": "portfolio_health",
        "safety_verdict": {"flag": False, "reason": None},
        "follow_up_resolved": False,
    }
    client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(payload)
    client.chat.completions.create = AsyncMock(return_value=mock_response)

    session_state = {
        "session_id": "sess-abc",
        "last_intent": "market_research",
        "last_entities": {"tickers": ["TSLA"]},
        "conversation_summary": "User asked about Tesla",
    }

    await classifier.classify("How is my portfolio?", session_state, client)

    call_args = client.chat.completions.create.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0] if call_args.args else call_args.kwargs["messages"]

    user_message_content = None
    for msg in messages:
        if msg["role"] == "user":
            user_message_content = msg["content"]
            break

    assert user_message_content is not None
    assert "Prior context" in user_message_content
    assert "Tesla" in user_message_content or "TSLA" in user_message_content
