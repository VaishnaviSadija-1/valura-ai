import json

from src.models.response import SSEEvent


def format_sse(event: SSEEvent) -> str:
    return f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"
