# app/services/lead_status.py
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool
from app.pg import get_pool

# Tabela esperada:
# lead_status(
#   instance_id TEXT, chatid TEXT,
#   stage TEXT, updated_at TIMESTAMPTZ,
#   last_msg_ts BIGINT, last_from_me BOOLEAN,
#   PRIMARY KEY(instance_id, chatid)
# )

# ---------- helpers sync (psycopg) ----------

def _row_to_dict(row: Tuple) -> Dict[str, Any]:
    # row order must match SELECT order below
    if not row:
        return {}
    instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me = row
    return {
        "instance_id": instance_id,
        "chatid": chatid,
        "stage": stage,
        "updatedAt": updated_at.isoformat() if updated_at else None,
        "last_msg_ts": int(last_msg_ts or 0),
        "last_from_me": bool(last_from_me),
    }

def _normalize_stage(stage: str) -> str:
    s = (stage or "").strip().lower()
    if s.startswith("contato"):
        return "contatos"
    if "lead_quente" in s or "quente" in s:
        return "lead_quente"
    if s == "lead":
        return "lead"
    return "contatos"

# ---------- SYNC core (usa psycopg pool) ----------

def _get_lead_status_sync(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    pool = get_pool()  # psycopg.ConnectionPool (sync)
    with pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
                  FROM lead_status
                 WHERE instance_id = %s AND chatid = %s
                """,
                (instance_id, chatid),
            )
            row = cur.fetchone()
    return _row_to_dict(row) if row else None

def _get_many_lead_status_sync(instance_id: str, chatids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = [c for c in dict.fromkeys(chatids) if c]  # dedup + remove vazios
    if not ids:
        return []
    placeholders = ", ".join(["%s"] * len(ids))
    params: Tuple[Any, ...] = (instance_id, *ids)

    pool = get_pool()
    with pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(
                f"""
                SELECT instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
                  FROM lead_status
                 WHERE instance_id = %s AND chatid IN ({placeholders})
                """,
                params,
            )
            rows = cur.fetchall() or []
    return [_row_to_dict(r) for r in rows]

def _upsert_lead_status_sync(
    instance_id: str,
    chatid: str,
    stage: str,
    last_msg_ts: int = 0,
    last_from_me: bool = False,
) -> Dict[str, Any]:
    s = _normalize_stage(stage)
    pool = get_pool()
    with pool.connection() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_status (instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me)
                VALUES (%s, %s, %s, NOW(), %s, %s)
                ON CONFLICT (instance_id, chatid)
                DO UPDATE SET
                  stage = EXCLUDED.stage,
                  updated_at = NOW(),
                  last_msg_ts = GREATEST(lead_status.last_msg_ts, EXCLUDED.last_msg_ts),
                  last_from_me = EXCLUDED.last_from_me
                RETURNING instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
                """,
                (instance_id, chatid, s, int(last_msg_ts or 0), bool(last_from_me)),
            )
            row = cur.fetchone()
    return _row_to_dict(row)

def _should_reclassify_sync(
    instance_id: str,
    chatid: str,
    last_msg_ts: Optional[int] = None,
    last_from_me: Optional[bool] = None,
) -> bool:
    cur = _get_lead_status_sync(instance_id, chatid)
    if not cur:
        return True
    if last_msg_ts and int(last_msg_ts) > int(cur.get("last_msg_ts") or 0):
        return True
    if last_from_me is not None and bool(last_from_me) != bool(cur.get("last_from_me")):
        return True
    return False

# ---------- API assíncrona (wrappers) ----------

async def get_lead_status(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    return await run_in_threadpool(_get_lead_status_sync, instance_id, chatid)

async def get_many_lead_status(instance_id: str, chatids: Iterable[str]) -> List[Dict[str, Any]]:
    return await run_in_threadpool(_get_many_lead_status_sync, instance_id, chatids)

async def upsert_lead_status(
    instance_id: str,
    chatid: str,
    stage: str,
    last_msg_ts: int = 0,
    last_from_me: bool = False,
) -> Dict[str, Any]:
    return await run_in_threadpool(
        _upsert_lead_status_sync, instance_id, chatid, stage, last_msg_ts, last_from_me
    )

async def should_reclassify(
    instance_id: str,
    chatid: str,
    last_msg_ts: Optional[int] = None,
    last_from_me: Optional[bool] = None,
) -> bool:
    return await run_in_threadpool(
        _should_reclassify_sync, instance_id, chatid, last_msg_ts, last_from_me
    )

# ---------- Aliases de compatibilidade ----------

async def getCachedLeadStatus(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    return await get_lead_status(instance_id, chatid)

async def getCachedLeadStatusBulk(instance_id: str, chatids: Iterable[str]) -> List[Dict[str, Any]]:
    return await get_many_lead_status(instance_id, chatids)

async def upsertLeadStatus(
    instance_id: str,
    chatid: str,
    stage: str,
    last_msg_ts: int = 0,
    last_from_me: bool = False,
) -> Dict[str, Any]:
    return await upsert_lead_status(instance_id, chatid, stage, last_msg_ts, last_from_me)

async def needsReclassify(
    instance_id: str,
    chatid: str,
    last_msg_ts: Optional[int] = None,
    last_from_me: Optional[bool] = None,
) -> bool:
    return await should_reclassify(instance_id, chatid, last_msg_ts, last_from_me)

__all__ = [
    # oficiais
    "get_lead_status", "get_many_lead_status", "upsert_lead_status", "should_reclassify",
    # compat + bulk
    "getCachedLeadStatus", "getCachedLeadStatusBulk", "upsertLeadStatus", "needsReclassify",
]
