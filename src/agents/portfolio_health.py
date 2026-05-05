import asyncio
import logging
from datetime import date, datetime
from typing import AsyncIterator, List, Optional, Tuple

import yfinance as yf

from src.agents.base import BaseAgent
from src.models.response import (
    BenchmarkComparison,
    ConcentrationRisk,
    Observation,
    Performance,
    PortfolioHealthResult,
    SSEEvent,
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "This analysis is for informational purposes only and does not constitute investment advice. "
    "Past performance does not guarantee future results. "
    "Please consult a qualified financial adviser before making investment decisions."
)

SEVERITY_ORDER = {"warning": 0, "info": 1, "ok": 2}


def _select_benchmark(holdings_with_country: list) -> Tuple[str, str]:
    us_count = sum(1 for h in holdings_with_country if h.get("country") == "US")
    total = len(holdings_with_country)
    ratio = us_count / total if total > 0 else 0
    if ratio > 0.7:
        return "S&P 500", "^GSPC"
    return "MSCI ACWI", "ACWI"


def _years_held(purchase_date_str: str) -> float:
    try:
        purchase_date = datetime.strptime(purchase_date_str, "%Y-%m-%d").date()
        delta = date.today() - purchase_date
        return max(delta.days / 365.25, 1 / 365.25)
    except Exception:
        return 1.0


def _fetch_ticker_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


def _fetch_history(ticker: str, period: str = "1y") -> Optional[object]:
    try:
        hist = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        return hist
    except Exception:
        return None


def _fetch_fx_rate(from_currency: str, to_currency: str) -> Optional[float]:
    if from_currency == to_currency:
        return 1.0
    pair = f"{from_currency}{to_currency}=X"
    try:
        hist = yf.download(pair, period="1d", auto_adjust=True, progress=False)
        if hist is not None and len(hist) > 0:
            close = hist["Close"]
            if hasattr(close, "iloc"):
                val = close.iloc[-1]
                if hasattr(val, "iloc"):
                    val = val.iloc[0]
                return float(val)
    except Exception:
        pass
    return None


class PortfolioHealthAgent(BaseAgent):
    async def run(
        self,
        query: str,
        user_context,
        classification,
        session_state: dict | None,
        session_id: str,
        openai_client,
    ) -> AsyncIterator[SSEEvent]:
        holdings = user_context.portfolio.holdings if user_context.portfolio else []
        base_currency = (
            user_context.portfolio.base_currency
            if user_context.portfolio
            else "USD"
        )
        risk_profile = user_context.risk_profile
        kyc_status = user_context.kyc_status

        # ── ONBOARDING MODE ───────────────────────────────────────────────────────
        if not holdings:
            async for event in self._onboarding(risk_profile, kyc_status, session_id):
                yield event
            return

        # ── MONITORING MODE ───────────────────────────────────────────────────────
        async for event in self._monitoring(
            holdings, base_currency, risk_profile, kyc_status, session_id
        ):
            yield event

    # ──────────────────────────────────────────────────────────────────────────────
    # ONBOARDING
    # ──────────────────────────────────────────────────────────────────────────────

    async def _onboarding(
        self, risk_profile: str, kyc_status: str, session_id: str
    ) -> AsyncIterator[SSEEvent]:
        observations: List[Observation] = []
        if kyc_status == "pending":
            observations.append(
                Observation(
                    severity="info",
                    text="Your KYC verification is pending. Some features may be limited.",
                )
            )

        next_steps = self._onboarding_next_steps(risk_profile)

        result = PortfolioHealthResult(
            mode="onboarding",
            concentration_risk=ConcentrationRisk(
                status="not_applicable",
                reason="No holdings found. Add positions to enable concentration analysis.",
            ),
            performance=Performance(
                status="not_applicable",
                reason="No holdings found. Add positions to enable performance tracking.",
            ),
            benchmark_comparison=BenchmarkComparison(
                status="not_applicable",
                reason="No holdings found. Add positions to enable benchmark comparison.",
            ),
            observations=observations,
            next_steps=next_steps,
            disclaimer=DISCLAIMER,
            session_id=session_id,
        )

        narrative_parts = [
            "Welcome to Valura AI!\n\n",
            "It looks like you haven't added any holdings to your portfolio yet. "
            "Once you add your investments, I'll be able to provide a full health check "
            "including concentration risk, performance tracking, and benchmark comparison.\n\n",
            f"Based on your {risk_profile} risk profile, here are some suggested next steps to get started:\n\n",
        ]
        for step in next_steps:
            narrative_parts.append(f"• {step}\n")

        if kyc_status == "pending":
            narrative_parts.append(
                "\nNote: Your KYC verification is still pending. "
                "Some features may be limited until verification is complete.\n"
            )

        for part in narrative_parts:
            yield SSEEvent(event="token", data={"text": part})
            await asyncio.sleep(0)

        yield SSEEvent(event="structured", data=result.model_dump())
        yield SSEEvent(event="end", data={})

    def _onboarding_next_steps(self, risk_profile: str) -> List[str]:
        base = [
            "Add your current holdings by importing from your broker or entering them manually.",
            "Complete your KYC verification to unlock all features.",
            "Set your investment goals and time horizon in your profile.",
        ]
        if risk_profile == "conservative":
            base.append(
                "Consider starting with a diversified mix of bonds, dividend stocks, and low-volatility ETFs."
            )
            base.append(
                "Explore index funds like BND, SCHD, or VYM suited to conservative investors."
            )
        elif risk_profile == "aggressive":
            base.append(
                "Explore growth-oriented equities, sector ETFs, and international diversification."
            )
            base.append(
                "Consider a core-satellite strategy: broad index funds as core, thematic ETFs as satellites."
            )
        else:  # moderate
            base.append(
                "Consider a balanced portfolio: 60% equities, 40% fixed income as a starting point."
            )
            base.append(
                "Look into diversified ETFs like VTI, VXUS, and BND for broad exposure."
            )
        return base

    # ──────────────────────────────────────────────────────────────────────────────
    # MONITORING
    # ──────────────────────────────────────────────────────────────────────────────

    async def _monitoring(
        self,
        holdings,
        base_currency: str,
        risk_profile: str,
        kyc_status: str,
        session_id: str,
    ) -> AsyncIterator[SSEEvent]:
        observations: List[Observation] = []

        if kyc_status == "pending":
            observations.append(
                Observation(
                    severity="info",
                    text="Your KYC verification is pending. Some features may be limited.",
                )
            )

        # Fetch market data for all holdings concurrently
        ticker_infos = {}
        for holding in holdings:
            try:
                info = await asyncio.to_thread(_fetch_ticker_info, holding.ticker)
                ticker_infos[holding.ticker] = info
            except Exception as exc:
                logger.error("Could not fetch info for %s: %s", holding.ticker, exc)
                ticker_infos[holding.ticker] = {}
                observations.append(
                    Observation(
                        severity="info",
                        text=f"Could not fetch data for {holding.ticker} — excluded from analysis.",
                    )
                )

        # Fetch FX rates for non-base currencies
        fx_cache: dict[str, float] = {}
        for holding in holdings:
            currency = holding.currency.upper()
            base = base_currency.upper()
            if currency != base and currency not in fx_cache:
                rate = await asyncio.to_thread(_fetch_fx_rate, currency, base)
                fx_cache[currency] = rate if rate is not None else 1.0

        # Compute holding values in base currency
        holding_values: List[Tuple[str, float]] = []
        for holding in holdings:
            currency = holding.currency.upper()
            fx_rate = fx_cache.get(currency, 1.0)
            value = holding.quantity * holding.current_price * fx_rate
            holding_values.append((holding.ticker, value))

        total_value = sum(v for _, v in holding_values)
        if total_value == 0:
            total_value = 1.0  # avoid division by zero

        # Portfolio weights
        weights = {ticker: val / total_value for ticker, val in holding_values}

        # Concentration risk
        sorted_weights = sorted(weights.values(), reverse=True)
        top1 = sorted_weights[0] * 100 if sorted_weights else 0.0
        top3 = sum(sorted_weights[:3]) * 100

        if top1 > 40 or top3 > 70:
            conc_flag = "high"
        elif top1 >= 20 or top3 >= 50:
            conc_flag = "medium"
        else:
            conc_flag = "low"

        concentration_risk = ConcentrationRisk(
            top_position_pct=round(top1, 2),
            top_3_positions_pct=round(top3, 2),
            flag=conc_flag,
        )

        if conc_flag == "high":
            observations.append(
                Observation(
                    severity="warning",
                    text=f"High concentration risk: your top position represents {top1:.1f}% of portfolio value.",
                )
            )
        elif conc_flag == "medium":
            observations.append(
                Observation(
                    severity="info",
                    text=f"Moderate concentration: top position at {top1:.1f}%. Consider diversifying.",
                )
            )
        else:
            observations.append(
                Observation(severity="ok", text="Portfolio appears well-diversified across positions.")
            )

        # Performance
        performance = await self._calculate_performance(holdings, weights, fx_cache, observations)

        # Benchmark
        holdings_with_country = [
            {"ticker": h.ticker, "country": ticker_infos.get(h.ticker, {}).get("country", "US")}
            for h in holdings
        ]
        benchmark_name, benchmark_ticker = _select_benchmark(holdings_with_country)
        benchmark_comparison = await self._calculate_benchmark(
            benchmark_name, benchmark_ticker, performance.total_return_pct
        )

        # Sort and cap observations at 3
        observations.sort(key=lambda o: SEVERITY_ORDER.get(o.severity, 99))
        observations = observations[:3]

        # Next steps based on findings
        next_steps = self._monitoring_next_steps(conc_flag, performance, risk_profile)

        result = PortfolioHealthResult(
            mode="monitoring",
            concentration_risk=concentration_risk,
            performance=performance,
            benchmark_comparison=benchmark_comparison,
            observations=observations,
            next_steps=next_steps,
            disclaimer=DISCLAIMER,
            session_id=session_id,
        )

        # Narrative streaming
        total_value_str = f"{total_value:,.2f}"
        narrative_parts = [
            "Here's your portfolio health check.\n\n",
            f"Your portfolio currently holds {len(holdings)} position{'s' if len(holdings) != 1 else ''} "
            f"with a total value of {total_value_str} {base_currency}.\n\n",
            f"Concentration risk is flagged as **{conc_flag}** — "
            f"your largest position accounts for {top1:.1f}% of your portfolio.\n\n",
        ]

        if performance.total_return_pct is not None:
            narrative_parts.append(
                f"Overall portfolio return: {performance.total_return_pct:+.2f}% "
                f"({performance.return_basis} basis).\n\n"
            )
        else:
            narrative_parts.append("Performance data could not be calculated for all holdings.\n\n")

        if benchmark_comparison.alpha_pct is not None:
            sign = "+" if benchmark_comparison.alpha_pct >= 0 else ""
            narrative_parts.append(
                f"Compared to {benchmark_comparison.benchmark}, your portfolio alpha is "
                f"{sign}{benchmark_comparison.alpha_pct:.2f}%.\n\n"
            )

        narrative_parts.append("Key observations:\n")
        for obs in observations:
            narrative_parts.append(f"  [{obs.severity.upper()}] {obs.text}\n")

        for part in narrative_parts:
            yield SSEEvent(event="token", data={"text": part})
            await asyncio.sleep(0)

        yield SSEEvent(event="structured", data=result.model_dump())
        yield SSEEvent(event="end", data={})

    async def _calculate_performance(
        self, holdings, weights: dict, fx_cache: dict, observations: List[Observation]
    ) -> Performance:
        weighted_return = 0.0
        weighted_annualized = 0.0
        has_actual = False
        has_estimated = False
        total_weight_used = 0.0

        for holding in holdings:
            weight = weights.get(holding.ticker, 0.0)
            if weight == 0:
                continue

            if holding.cost_basis is not None and holding.purchase_date is not None:
                # Actual return
                total_ret = (holding.current_price - holding.cost_basis) / holding.cost_basis * 100
                years = _years_held(holding.purchase_date)
                annualized = ((1 + total_ret / 100) ** (1 / years) - 1) * 100
                weighted_return += weight * total_ret
                weighted_annualized += weight * annualized
                has_actual = True
                total_weight_used += weight
            else:
                # Estimated from 12-month history
                try:
                    hist = await asyncio.to_thread(_fetch_history, holding.ticker, "1y")
                    if hist is not None and len(hist) > 1:
                        close = hist["Close"]
                        if hasattr(close, "iloc"):
                            first_val = close.iloc[0]
                            last_val = close.iloc[-1]
                            if hasattr(first_val, "iloc"):
                                first_val = first_val.iloc[0]
                            if hasattr(last_val, "iloc"):
                                last_val = last_val.iloc[0]
                            first_price = float(first_val)
                            last_price = float(last_val)
                            if first_price > 0:
                                est_ret = (last_price - first_price) / first_price * 100
                                weighted_return += weight * est_ret
                                weighted_annualized += weight * est_ret  # ~1 year
                                has_estimated = True
                                total_weight_used += weight
                except Exception as exc:
                    logger.error("Could not fetch history for %s: %s", holding.ticker, exc)

        if total_weight_used == 0:
            return Performance(
                status="not_applicable",
                reason="Insufficient data to calculate performance.",
            )

        # Normalize to account for any excluded holdings
        scale = 1.0 / total_weight_used if total_weight_used > 0 else 1.0
        total_return = weighted_return * scale
        annualized_return = weighted_annualized * scale

        return_basis = "actual" if (has_actual and not has_estimated) else "estimated"

        return Performance(
            total_return_pct=round(total_return, 2),
            annualized_return_pct=round(annualized_return, 2),
            measurement_period="since purchase" if return_basis == "actual" else "12 months",
            return_basis=return_basis,
        )

    async def _calculate_benchmark(
        self,
        benchmark_name: str,
        benchmark_ticker: str,
        portfolio_return: Optional[float],
    ) -> BenchmarkComparison:
        try:
            hist = await asyncio.to_thread(_fetch_history, benchmark_ticker, "1y")
            if hist is not None and len(hist) > 1:
                close = hist["Close"]
                if hasattr(close, "iloc"):
                    first_val = close.iloc[0]
                    last_val = close.iloc[-1]
                    if hasattr(first_val, "iloc"):
                        first_val = first_val.iloc[0]
                    if hasattr(last_val, "iloc"):
                        last_val = last_val.iloc[0]
                    first_price = float(first_val)
                    last_price = float(last_val)
                    if first_price > 0:
                        bench_return = (last_price - first_price) / first_price * 100
                        alpha = (
                            (portfolio_return - bench_return)
                            if portfolio_return is not None
                            else None
                        )
                        return BenchmarkComparison(
                            benchmark=benchmark_name,
                            benchmark_ticker=benchmark_ticker,
                            portfolio_return_pct=round(portfolio_return, 2)
                            if portfolio_return is not None
                            else None,
                            benchmark_return_pct=round(bench_return, 2),
                            alpha_pct=round(alpha, 2) if alpha is not None else None,
                        )
        except Exception as exc:
            logger.error("Could not fetch benchmark %s: %s", benchmark_ticker, exc)

        return BenchmarkComparison(
            benchmark=benchmark_name,
            benchmark_ticker=benchmark_ticker,
            status="unavailable",
            reason="Could not fetch benchmark data.",
        )

    def _monitoring_next_steps(
        self, conc_flag: str, performance: Performance, risk_profile: str
    ) -> List[str]:
        steps = []
        if conc_flag == "high":
            steps.append(
                "Consider rebalancing to reduce concentration — no single position should exceed 20-25% for most investors."
            )
        elif conc_flag == "medium":
            steps.append("Review your largest positions and consider gradual diversification.")

        if performance.total_return_pct is not None and performance.total_return_pct < 0:
            steps.append(
                "Your portfolio shows a negative return. Review underperforming positions and consider tax-loss harvesting."
            )

        if risk_profile == "conservative":
            steps.append(
                "Ensure your portfolio includes adequate fixed-income exposure to match your conservative risk profile."
            )
        elif risk_profile == "aggressive":
            steps.append(
                "Continue monitoring sector concentration and ensure your high-growth positions align with your risk tolerance."
            )
        else:
            steps.append(
                "Consider a quarterly rebalancing schedule to maintain your target allocation."
            )

        steps.append("Review your investment thesis for each position at least annually.")
        return steps
