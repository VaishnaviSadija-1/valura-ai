from typing import AsyncIterator

from src.agents.base import BaseAgent
from src.models.response import SSEEvent, StubResult


class StubAgent(BaseAgent):
    async def run(
        self,
        query: str,
        user_context,
        classification,
        session_state: dict | None,
        session_id: str,
        openai_client,
    ) -> AsyncIterator[SSEEvent]:
        yield SSEEvent(
            event="token",
            data={"text": "This agent is not yet available in this build."},
        )

        stub_result = StubResult(
            status="not_implemented",
            intent=classification.intent,
            entities=classification.entities,
            agent=classification.target_agent,
            message=(
                f"The '{classification.target_agent}' agent is not yet implemented. "
                "Please check back in a future release."
            ),
        )

        yield SSEEvent(event="structured", data=stub_result.model_dump())
        yield SSEEvent(event="end", data={})
