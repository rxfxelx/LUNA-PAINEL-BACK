# app/services/lead_status.py
from __future__ import annotations
from typing import Iterable, List, Optional, Dict, Any
from app.pg import get_pool

def _row_to_dict(row) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "instance_id": row["instance_id"],
        "chatid": row["chatid"],
        "stage": row["stage"],
        "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
        "last_msg_ts": int(row["last_msg_ts"] or 0),
        "last_from_me": bool(row["last_from_me"]),
    }

# -------- API assíncrona "oficial" --------
async def get_lead_status(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """
            SELECT instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
            FROM lead_status
            WHERE instance_id=$1 AND chatid=$2
            """,
            instance_id, chatid,
        )
    return _row_to_dict(row) if row else None

async def get_many_lead_status(instance_id: str, chatids: Iterable[str]) -> List[Dict[str, Any]]:
    ids = [c for c in set(chatids) if c]
    if not ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
            FROM lead_status
            WHERE instance_id=$1 AND chatid = ANY($2)
            """,
            instance_id, ids,
        )
    return [_row_to_dict(r) for r in rows]

async def upsert_lead_status(
    instance_id: str,
    chatid: str,
    stage: str,
    last_msg_ts: int = 0,
    last_from_me: bool = False,
) -> Dict[str, Any]:
    s = (stage or "").strip().lower()
    if s.startswith("contato"):
        s = "contatos"
    elif "lead_quente" in s or "quente" in s:
        s = "lead_quente"
    elif s != "lead":
        s = "contatos"

    pool = await get_pool()
    async with pool.acquire() as con:
        row = await con.fetchrow(
            """
            INSERT INTO lead_status (instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me)
            VALUES ($1, $2, $3, NOW(), $4, $5)
            ON CONFLICT (instance_id, chatid)
            DO UPDATE SET
              stage = EXCLUDED.stage,
              updated_at = NOW(),
              last_msg_ts = GREATEST(lead_status.last_msg_ts, EXCLUDED.last_msg_ts),
              last_from_me = EXCLUDED.last_from_me
            RETURNING instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
            """,
            instance_id, chatid, s, int(last_msg_ts or 0), bool(last_from_me),
        )
    return _row_to_dict(row)

async def should_reclassify(
    instance_id: str,
    chatid: str,
    last_msg_ts: Optional[int] = None,
    last_from_me: Optional[bool] = None,
) -> bool:
    cur = await get_lead_status(instance_id, chatid)
    if not cur:
        return True
    if last_msg_ts and int(last_msg_ts) > int(cur.get("last_msg_ts") or 0):
        return True
    if last_from_me is not None and bool(last_from_me) != bool(cur.get("last_from_me")):
        return True
    return False

# -------- Aliases de compatibilidade (mantém imports antigos) --------
async def getCachedLeadStatus(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    return await get_lead_status(instance_id, chatid)

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
    "get_lead_status", "get_many_lead_status", "upsert_lead_status", "should_reclassify",
    "getCachedLeadStatus", "upsertLeadStatus", "needsReclassify",
]
