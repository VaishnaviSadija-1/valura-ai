from abc import ABC, abstractmethod
from typing import AsyncIterator

from src.models.response import SSEEvent


class BaseAgent(ABC):
    @abstractmethod
    async def run(
        self,
        query: str,
        user_context,
        classification,
        session_state: dict | None,
        session_id: str,
        openai_client,
    ) -> AsyncIterator[SSEEvent]:
        ...
