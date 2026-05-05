import os

# Must be set before any src imports so pydantic-settings picks it up
os.environ.setdefault("OPENAI_API_KEY", "sk-fake-for-testing")

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.main import app
from src.models.request import Holding, Portfolio, QueryRequest, UserContext
from src.models.response import (
    ClassificationResult,
    EntitySet,
    SafetyResult,
    SafetyVerdict,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def safety_pairs():
    with open(FIXTURES_DIR / "test_queries" / "safety_pairs.json") as f:
        return json.load(f)


@pytest.fixture
def intent_queries():
    with open(FIXTURES_DIR / "test_queries" / "intent_classification.json") as f:
        return json.load(f)


@pytest.fixture
def user_001():
    with open(FIXTURES_DIR / "users" / "user_001_aggressive.json") as f:
        return json.load(f)


@pytest.fixture
def user_004():
    with open(FIXTURES_DIR / "users" / "user_004_empty.json") as f:
        return json.load(f)


@pytest.fixture
def sample_portfolio_request():
    return QueryRequest(
        query="How is my portfolio doing?",
        user_id="user_001",
        user_context=UserContext(
            portfolio=Portfolio(
                holdings=[
                    Holding(
                        ticker="NVDA",
                        quantity=100,
                        current_price=875.0,
                        currency="USD",
                        cost_basis=220.0,
                        purchase_date="2022-06-15",
                    ),
                    Holding(
                        ticker="AAPL",
                        quantity=50,
                        current_price=182.0,
                        currency="USD",
                        cost_basis=150.0,
                        purchase_date="2022-06-15",
                    ),
                ],
                base_currency="USD",
            ),
            risk_profile="aggressive",
        ),
    )


@pytest.fixture
def empty_portfolio_request():
    return QueryRequest(
        query="Give me a portfolio health check.",
        user_id="user_004",
        user_context=UserContext(
            portfolio=Portfolio(holdings=[], base_currency="USD"),
            risk_profile="moderate",
        ),
    )


@pytest.fixture
def mock_classification():
    return ClassificationResult(
        intent="portfolio_health_check",
        entities=EntitySet(),
        target_agent="portfolio_health",
        safety_verdict=SafetyVerdict(flag=False),
        follow_up_resolved=False,
    )


@pytest.fixture
def mock_openai_client():
    client = AsyncMock()
    return client


def parse_sse_events(text: str) -> list[dict]:
    events = []
    current_event: dict = {}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("event:"):
            current_event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            try:
                current_event["data"] = json.loads(line[5:].strip())
            except Exception:
                current_event["data"] = line[5:].strip()
        elif line == "" and current_event:
            events.append(current_event.copy())
            current_event = {}
    return events
