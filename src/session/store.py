import json
import logging
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    last_intent TEXT,
    last_entities TEXT,
    conversation_summary TEXT,
    updated_at REAL
)
"""


class AsyncSessionStore:
    def __init__(self, database_path: str) -> None:
        self._database_path = database_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        try:
            self._conn = await aiosqlite.connect(self._database_path)
            await self._conn.execute(CREATE_TABLE_SQL)
            await self._conn.commit()
        except Exception as exc:
            logger.error("Failed to initialize session store: %s", exc)
            self._conn = None

    async def get(self, session_id: str) -> Optional[dict]:
        if self._conn is None:
            return None
        try:
            async with self._conn.execute(
                "SELECT session_id, user_id, last_intent, last_entities, conversation_summary, updated_at "
                "FROM sessions WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            session_id_val, user_id, last_intent, last_entities_raw, conversation_summary, updated_at = row
            last_entities: dict = {}
            if last_entities_raw:
                try:
                    last_entities = json.loads(last_entities_raw)
                except json.JSONDecodeError:
                    last_entities = {}
            return {
                "session_id": session_id_val,
                "user_id": user_id,
                "last_intent": last_intent,
                "last_entities": last_entities,
                "conversation_summary": conversation_summary,
                "updated_at": updated_at,
            }
        except Exception as exc:
            logger.error("Error fetching session %s: %s", session_id, exc)
            return None

    async def upsert(
        self,
        session_id: str,
        user_id: str,
        last_intent: str,
        last_entities: dict,
        conversation_summary: str,
    ) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, last_intent, last_entities, conversation_summary, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    last_intent = excluded.last_intent,
                    last_entities = excluded.last_entities,
                    conversation_summary = excluded.conversation_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    user_id,
                    last_intent,
                    json.dumps(last_entities),
                    conversation_summary,
                    time.time(),
                ),
            )
            await self._conn.commit()
        except Exception as exc:
            logger.error("Error upserting session %s: %s", session_id, exc)

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception as exc:
                logger.error("Error closing session store: %s", exc)
            finally:
                self._conn = None
