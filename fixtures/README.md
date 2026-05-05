# Valura AI Fixtures

This directory contains test fixtures used by the Valura AI test suite.

## Structure

### users/
Sample user profiles with portfolio data for testing different scenarios:
- `user_001_aggressive.json` — Alex Chen: aggressive risk profile, diversified tech portfolio with strong returns
- `user_002_concentrated.json` — Jordan Smith: moderate risk, single-stock concentrated portfolio (AAPL)
- `user_003_global.json` — Maria Santos: moderate risk, globally diversified portfolio (US + EU + India)
- `user_004_empty.json` — Sam Rivera: pending KYC, empty portfolio (onboarding scenario)
- `user_005_retiree.json` — Eleanor Vance: conservative risk, dividend-focused retiree portfolio

### test_queries/
Query fixtures for unit testing components:
- `safety_pairs.json` — 45 (query, expected) pairs for testing the SafetyGuard
- `intent_classification.json` — 60 queries with expected agent routing for testing the IntentClassifier

### conversations/
Multi-turn conversation fixtures for testing session/follow-up resolution:
- `conv_001.json` — Follow-up ticker resolution (Microsoft → Apple context)
- `conv_002.json` — Topic switch handling (portfolio health → financial calculator)
- `conv_003.json` — Portfolio check then recommendations continuation

## Usage

Fixtures are loaded in `tests/conftest.py` and made available as pytest fixtures.
They can also be used directly for manual testing via `curl` or the OpenAPI docs at `/docs`.
