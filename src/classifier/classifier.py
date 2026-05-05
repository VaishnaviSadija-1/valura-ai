import json
import logging
from typing import Optional

from src.models.response import ClassificationResult, EntitySet, SafetyVerdict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial query classifier for Valura, a wealth management platform. \
Classify the user query and extract entities. Return ONLY a valid JSON object with no other text, \
markdown, or explanation. The JSON must have exactly these fields:
- intent: string describing what the user wants
- entities: object with tickers (list of strings), amounts (list of numbers), time_period (string or null), sectors (list of strings), topics (list of strings)
- target_agent: one of: portfolio_health, market_research, investment_strategy, financial_calculator, risk_analysis, recommendations, predictive_analysis, support
- safety_verdict: object with flag (boolean) and reason (string or null)
- follow_up_resolved: boolean, true if query references prior conversation

Agent routing rules:
- portfolio_health: portfolio health check, diversification, how is my portfolio, health check, am I diversified, portfolio overview, concentration risk
- market_research: stock analysis, company research, sector analysis, market trends, news about a company
- investment_strategy: investment plan, asset allocation, rebalancing strategy, long-term planning
- financial_calculator: compound interest, returns calculator, future value, mortgage, tax calculations
- risk_analysis: risk assessment, volatility, drawdown, beta, correlation
- recommendations: what should I buy, investment recommendations, suggest investments
- predictive_analysis: price prediction, forecast, future performance
- support: general questions, account help, how to use platform, other"""

FALLBACK_RESULT = ClassificationResult(
    intent="unknown",
    entities=EntitySet(),
    target_agent="support",
    safety_verdict=SafetyVerdict(),
    follow_up_resolved=False,
)


def _parse_response(raw: str) -> ClassificationResult:
    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    parsed = json.loads(raw)

    entities_data = parsed.get("entities", {})
    if not isinstance(entities_data, dict):
        entities_data = {}

    entities = EntitySet(
        tickers=entities_data.get("tickers", []),
        amounts=entities_data.get("amounts", []),
        time_period=entities_data.get("time_period"),
        sectors=entities_data.get("sectors", []),
        topics=entities_data.get("topics", []),
    )

    safety_data = parsed.get("safety_verdict", {})
    if not isinstance(safety_data, dict):
        safety_data = {}

    return ClassificationResult(
        intent=parsed.get("intent", "unknown"),
        entities=entities,
        target_agent=parsed.get("target_agent", "support"),
        safety_verdict=SafetyVerdict(
            flag=bool(safety_data.get("flag", False)),
            reason=safety_data.get("reason"),
        ),
        follow_up_resolved=bool(parsed.get("follow_up_resolved", False)),
    )


class IntentClassifier:
    async def classify(
        self,
        query: str,
        session_state: Optional[dict],
        client,
    ) -> ClassificationResult:
        user_message = query
        if session_state:
            user_message = f"{query}\n\nPrior context: {json.dumps(session_state)}"

        try:
            # Detect backend by client type name
            client_type = type(client).__name__

            if "Anthropic" in client_type:
                raw = await self._call_anthropic(client, user_message)
            else:
                raw = await self._call_openai(client, user_message)

            return _parse_response(raw)

        except Exception as exc:
            logger.error("Classifier error: %s", exc)
            return FALLBACK_RESULT

    async def _call_openai(self, client, user_message: str) -> str:
        from src.config import settings
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content

    async def _call_anthropic(self, client, user_message: str) -> str:
        from src.config import settings
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            temperature=0,
        )
        return response.content[0].text
