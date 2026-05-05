"""
Load harness — measures p95 first-token and end-to-end latency.

Usage:
    # Start the server first:
    #   uvicorn src.main:app --host 0.0.0.0 --port 8000

    python scripts/benchmark.py [--n 200] [--url http://localhost:8000] [--concurrency 10]

Outputs:
    - p50/p95/p99 first-token latency
    - p50/p95/p99 end-to-end latency
    - Per-run summary suitable for README
"""

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

SAMPLE_PAYLOADS = [
    {
        "query": "How is my portfolio doing?",
        "user_id": "bench_user",
        "user_context": {
            "portfolio": {
                "holdings": [
                    {"ticker": "AAPL", "quantity": 50, "current_price": 182.0, "currency": "USD",
                     "cost_basis": 150.0, "purchase_date": "2023-01-15"},
                    {"ticker": "MSFT", "quantity": 30, "current_price": 415.0, "currency": "USD",
                     "cost_basis": 330.0, "purchase_date": "2023-01-15"},
                ],
                "base_currency": "USD",
            },
            "risk_profile": "moderate",
            "kyc_status": "verified",
        },
    },
    {
        "query": "Give me a portfolio health check.",
        "user_id": "bench_user_2",
        "user_context": {
            "portfolio": {"holdings": [], "base_currency": "USD"},
            "risk_profile": "moderate",
            "kyc_status": "verified",
        },
    },
]


@dataclass
class RequestResult:
    first_token_ms: Optional[float] = None
    end_to_end_ms: Optional[float] = None
    error: Optional[str] = None
    token_count: int = 0


async def measure_request(client: httpx.AsyncClient, base_url: str, payload: dict) -> RequestResult:
    result = RequestResult()
    start = time.perf_counter()

    try:
        async with client.stream(
            "POST",
            f"{base_url}/query",
            json=payload,
            timeout=60.0,
        ) as response:
            async for chunk in response.aiter_text():
                elapsed_ms = (time.perf_counter() - start) * 1000
                if chunk.strip():
                    if result.first_token_ms is None:
                        result.first_token_ms = elapsed_ms
                    result.token_count += 1

        result.end_to_end_ms = (time.perf_counter() - start) * 1000
    except Exception as exc:
        result.error = str(exc)

    return result


async def run_benchmark(base_url: str, n: int, concurrency: int) -> None:
    print(f"\nValura AI Load Harness")
    print(f"Target: {base_url}")
    print(f"Requests: {n}  Concurrency: {concurrency}")
    print("-" * 50)

    semaphore = asyncio.Semaphore(concurrency)
    results: list[RequestResult] = []

    async def bounded_request(payload: dict) -> RequestResult:
        async with semaphore:
            return await measure_request(client, base_url, payload)

    async with httpx.AsyncClient() as client:
        # Warm-up: 5 requests, discarded
        print("Warming up (5 requests)...")
        warmup_tasks = [
            bounded_request(SAMPLE_PAYLOADS[i % len(SAMPLE_PAYLOADS)])
            for i in range(5)
        ]
        await asyncio.gather(*warmup_tasks)

        # Main run
        print(f"Running {n} requests...")
        tasks = [
            bounded_request(SAMPLE_PAYLOADS[i % len(SAMPLE_PAYLOADS)])
            for i in range(n)
        ]

        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            completed += 1
            if completed % 20 == 0:
                print(f"  {completed}/{n} complete...")

    errors = [r for r in results if r.error]
    valid = [r for r in results if r.error is None]

    print(f"\n{'=' * 50}")
    print(f"Results ({len(valid)}/{n} successful, {len(errors)} errors)")
    print(f"{'=' * 50}")

    if valid:
        first_tokens = [r.first_token_ms for r in valid if r.first_token_ms is not None]
        end_to_ends = [r.end_to_end_ms for r in valid if r.end_to_end_ms is not None]

        if first_tokens:
            first_tokens.sort()
            print("\nFirst-token latency (ms):")
            print(f"  p50  = {statistics.median(first_tokens):.1f}")
            print(f"  p95  = {first_tokens[int(0.95 * len(first_tokens))]:.1f}  (target: < 2000)")
            print(f"  p99  = {first_tokens[int(0.99 * len(first_tokens))]:.1f}")
            print(f"  mean = {statistics.mean(first_tokens):.1f}")

        if end_to_ends:
            end_to_ends.sort()
            print("\nEnd-to-end latency (ms):")
            print(f"  p50  = {statistics.median(end_to_ends):.1f}")
            print(f"  p95  = {end_to_ends[int(0.95 * len(end_to_ends))]:.1f}  (target: < 6000)")
            print(f"  p99  = {end_to_ends[int(0.99 * len(end_to_ends))]:.1f}")
            print(f"  mean = {statistics.mean(end_to_ends):.1f}")

        p95_ft = first_tokens[int(0.95 * len(first_tokens))] if first_tokens else None
        p95_e2e = end_to_ends[int(0.95 * len(end_to_ends))] if end_to_ends else None

        print("\nTarget compliance:")
        if p95_ft is not None:
            status = "PASS" if p95_ft < 2000 else "FAIL"
            print(f"  First-token p95 < 2s: {status} ({p95_ft:.0f}ms)")
        if p95_e2e is not None:
            status = "PASS" if p95_e2e < 6000 else "FAIL"
            print(f"  End-to-end  p95 < 6s: {status} ({p95_e2e:.0f}ms)")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for r in errors[:5]:
            print(f"  {r.error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Valura AI load harness")
    parser.add_argument("--n", type=int, default=200, help="Number of requests")
    parser.add_argument("--url", default="http://localhost:8000", help="Server base URL")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent requests")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.url, args.n, args.concurrency))
