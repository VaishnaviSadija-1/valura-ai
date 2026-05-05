import asyncio
import json
import logging
import uuid

from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

from src.classifier.classifier import IntentClassifier
from src.config import settings
from src.models.request import QueryRequest
from src.models.response import SSEEvent
from src.router.router import route
from src.safety.guard import SafetyGuard
from src.session.store import AsyncSessionStore


def _make_llm_client():
    if settings.anthropic_api_key:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=settings.anthropic_api_key)
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=settings.openai_api_key or "sk-placeholder")

logger = logging.getLogger(__name__)

app = FastAPI(title="Valura AI")

safety_guard = SafetyGuard()
session_store = AsyncSessionStore(settings.database_path)
classifier = IntentClassifier()


@app.on_event("startup")
async def startup() -> None:
    await session_store.initialize()


@app.on_event("shutdown")
async def shutdown() -> None:
    await session_store.close()


@app.post("/query")
async def query_endpoint(request: QueryRequest):
    return EventSourceResponse(pipeline(request))


async def pipeline(request: QueryRequest):
    session_id = request.session_id or str(uuid.uuid4())
    openai_client = _make_llm_client()

    try:
        async def _run():
            # 1. Safety guard
            safety_result = safety_guard.check(request.query)
            if safety_result.blocked:
                yield SSEEvent(
                    event="metadata",
                    data={
                        "session_id": session_id,
                        "blocked": True,
                        "category": safety_result.category,
                    },
                )
                yield SSEEvent(
                    event="error",
                    data={
                        "code": "SAFETY_BLOCK",
                        "message": safety_result.response,
                    },
                )
                yield SSEEvent(event="end", data={})
                return

            # 2. Load session state
            session_state = await session_store.get(session_id)

            # 3. Classify
            classification = await classifier.classify(
                request.query, session_state, openai_client
            )

            # 4. Emit metadata
            yield SSEEvent(
                event="metadata",
                data={
                    "session_id": session_id,
                    "intent": classification.intent,
                    "agent": classification.target_agent,
                    "safety_verdict": classification.safety_verdict.model_dump(),
                    "follow_up_resolved": classification.follow_up_resolved,
                },
            )

            # 5. Update session state
            await session_store.upsert(
                session_id=session_id,
                user_id=request.user_id,
                last_intent=classification.intent,
                last_entities=classification.entities.model_dump(),
                conversation_summary=f"User asked: {request.query[:200]}",
            )

            # 6. Route to agent
            async for event in route(
                target_agent=classification.target_agent,
                query=request.query,
                user_context=request.user_context,
                classification=classification,
                session_state=session_state,
                session_id=session_id,
                openai_client=openai_client,
            ):
                yield event

        def _sse(event: SSEEvent) -> dict:
            return {"event": event.event, "data": json.dumps(event.data)}

        # Apply timeout (Python 3.11+)
        async with asyncio.timeout(settings.pipeline_timeout_seconds):
            async for event in _run():
                yield _sse(event)

    except asyncio.TimeoutError:
        yield {"event": "error", "data": json.dumps({
            "code": "TIMEOUT",
            "message": (
                f"Response timed out after {settings.pipeline_timeout_seconds}s. "
                "Any partial results above may be incomplete."
            ),
        })}
        yield {"event": "end", "data": json.dumps({})}
    except Exception as exc:
        logger.error("Unhandled pipeline error: %s", exc, exc_info=True)
        yield {"event": "error", "data": json.dumps({
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again.",
        })}
        yield {"event": "end", "data": json.dumps({})}
