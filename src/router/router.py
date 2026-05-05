from typing import AsyncIterator

from src.agents.portfolio_health import PortfolioHealthAgent
from src.agents.stub import StubAgent
from src.models.response import SSEEvent

AGENT_REGISTRY = {
    "portfolio_health": PortfolioHealthAgent,
}


async def route(target_agent: str, **kwargs) -> AsyncIterator[SSEEvent]:
    agent_class = AGENT_REGISTRY.get(target_agent, StubAgent)
    agent = agent_class()
    async for event in agent.run(**kwargs):
        yield event
