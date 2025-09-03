from __future__ import annotations
from typing import Optional, Dict, Any
from app.pg import get_pool

def _row_to_dict(r) -> Dict[str, Any]:
    return {
        "chatid": r[0],
        "stage": r[1],
        "updated_at": r[2].isoformat() if r[2] else None,
        "last_msg_ts": int(r[3]) if r[3] is not None else 0,
        "last_from_me": bool(r[4]) if r[4] is not None else False,
    }

def getCachedLeadStatus(chatid: str) -> Optional[Dict[str, Any]]:
    sql = "SELECT chatid, stage, updated_at, last_msg_ts, last_from_me FROM lead_status WHERE chatid = %s"
    with get_pool().connection() as con:
        row = con.execute(sql, (chatid,)).fetchone()
    return _row_to_dict(row) if row else None

def upsertLeadStatus(chatid: str, *, stage: str | None, last_msg_ts: int | None, last_from_me: bool | None) -> Dict[str, Any]:
    sql = """
    INSERT INTO lead_status (chatid, stage, last_msg_ts, last_from_me)
    VALUES (%s, COALESCE(%s, 'contatos'), COALESCE(%s, 0), COALESCE(%s, FALSE))
    ON CONFLICT (chatid) DO UPDATE SET
        stage        = COALESCE(EXCLUDED.stage, lead_status.stage),
        last_msg_ts  = COALESCE(EXCLUDED.last_msg_ts, lead_status.last_msg_ts),
        last_from_me = COALESCE(EXCLUDED.last_from_me, lead_status.last_from_me),
        updated_at   = NOW()
    RETURNING chatid, stage, updated_at, last_msg_ts, last_from_me
    """
    with get_pool().connection() as con:
        row = con.execute(sql, (chatid, stage, last_msg_ts, last_from_me)).fetchone()
    return _row_to_dict(row)

def needsReclassify(chatid: str, new_last_msg_ts: int, new_last_from_me: bool) -> bool:
    cur = getCachedLeadStatus(chatid)
    if not cur:
        return True
    cur_ts = int(cur.get("last_msg_ts") or 0)
    return int(new_last_msg_ts or 0) > cur_ts
