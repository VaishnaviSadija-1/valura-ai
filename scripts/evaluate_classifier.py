"""
Offline classifier accuracy evaluation against the gold-labeled fixture set.

Requires a real API key (ANTHROPIC_API_KEY or OPENAI_API_KEY in .env).
Results are printed and optionally saved to results/classifier_eval.json.

Usage:
    python scripts/evaluate_classifier.py [--save]

Thresholds (from spec):
    - Routing accuracy: >= 85%
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.classifier.classifier import IntentClassifier
from src.config import settings

FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "test_queries" / "intent_classification.json"


def _normalize_agent(name: str) -> str:
    return name.strip().lower()


def _normalize_ticker(t: str) -> str:
    return t.upper().split(".")[0]


def _entities_subset_match(expected: dict, actual: dict) -> bool:
    """Return True if actual contains every item in expected (subset match with normalization)."""
    for key, exp_values in expected.items():
        if not exp_values:
            continue
        act_values = actual.get(key, [])
        if key == "tickers":
            exp_norm = {_normalize_ticker(t) for t in exp_values}
            act_norm = {_normalize_ticker(t) for t in act_values}
            if not exp_norm.issubset(act_norm):
                return False
        elif key == "amounts":
            # Numeric match within ±5%
            for ev in exp_values:
                matched = any(
                    abs(av - ev) / max(abs(ev), 1e-9) <= 0.05
                    for av in act_values
                )
                if not matched:
                    return False
        elif isinstance(exp_values, list):
            exp_norm = {str(v).lower() for v in exp_values}
            act_norm = {str(v).lower() for v in act_values}
            if not exp_norm.issubset(act_norm):
                return False
    return True


async def evaluate(save: bool = False) -> None:
    with open(FIXTURES_PATH) as f:
        queries = json.load(f)

    if not settings.anthropic_api_key and not settings.openai_api_key:
        print("ERROR: No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")
        sys.exit(1)

    if settings.anthropic_api_key:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        backend = f"Anthropic ({settings.anthropic_model})"
    else:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        backend = f"OpenAI ({settings.openai_model})"

    classifier = IntentClassifier()

    print(f"\nValura AI — Classifier Accuracy Evaluation")
    print(f"Backend: {backend}")
    print(f"Queries: {len(queries)}")
    print("-" * 60)

    routing_correct = 0
    entity_correct = 0
    failures = []
    results = []

    for i, q in enumerate(queries):
        result = await classifier.classify(q["query"], None, client)
        actual_agent = _normalize_agent(result.target_agent)
        expected_agent = _normalize_agent(q["expected_agent"])

        agent_match = actual_agent == expected_agent
        entity_match = _entities_subset_match(
            q.get("expected_entities", {}),
            result.entities.model_dump(),
        )

        if agent_match:
            routing_correct += 1
        if entity_match:
            entity_correct += 1

        if not agent_match:
            failures.append({
                "query": q["query"],
                "expected": expected_agent,
                "actual": actual_agent,
            })

        results.append({
            "query": q["query"],
            "expected_agent": expected_agent,
            "actual_agent": actual_agent,
            "agent_match": agent_match,
            "entity_match": entity_match,
        })

        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(queries)} done — routing so far: {routing_correct/(i+1):.1%}")

    routing_accuracy = routing_correct / len(queries)
    entity_accuracy = entity_correct / len(queries)

    print(f"\n{'=' * 60}")
    print(f"RESULTS")
    print(f"{'=' * 60}")
    print(f"Routing accuracy: {routing_correct}/{len(queries)} = {routing_accuracy:.1%}  (threshold: ≥85%)")
    print(f"Entity accuracy:  {entity_correct}/{len(queries)} = {entity_accuracy:.1%}")
    status = "PASS" if routing_accuracy >= 0.85 else "FAIL"
    print(f"Status: {status}")

    if failures:
        print(f"\nRouting failures ({len(failures)}):")
        for f in failures[:10]:
            print(f"  Q: {f['query'][:60]}...")
            print(f"     expected={f['expected']}  actual={f['actual']}")

    if save:
        out_path = Path(__file__).parent.parent / "results" / "classifier_eval.json"
        out_path.parent.mkdir(exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({
                "backend": backend,
                "total": len(queries),
                "routing_accuracy": routing_accuracy,
                "entity_accuracy": entity_accuracy,
                "pass": routing_accuracy >= 0.85,
                "failures": failures,
                "results": results,
            }, f, indent=2)
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="Save results to results/classifier_eval.json")
    args = parser.parse_args()
    asyncio.run(evaluate(save=args.save))
