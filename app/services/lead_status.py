# app/services/lead_status.py
from __future__ import annotations
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.pg import get_pool


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def getCachedLeadStatus(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    """
    Lê do cache (tabela lead_status) escopado por instance_id + chatid.
    """
    pool = get_pool()
    sql = """
        SELECT instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
          FROM lead_status
         WHERE instance_id = %s AND chatid = %s
         LIMIT 1
    """
    with pool.connection() as con:
        row = con.execute(sql, (instance_id, chatid)).fetchone()
        if not row:
            return None
        return {
            "instance_id": row[0],
            "chatid": row[1],
            "stage": row[2],
            "updated_at": _iso(row[3]),
            "last_msg_ts": int(row[4] or 0),
            "last_from_me": bool(row[5]),
        }


def upsertLeadStatus(
    instance_id: str,
    chatid: str,
    stage: str,
    last_msg_ts: int = 0,
    last_from_me: bool = False,
) -> Dict[str, Any]:
    """
    Insere/atualiza o cache de lead status para (instance_id, chatid).
    Mantém updated_at = NOW(), e guarda last_msg_ts/last_from_me.
    """
    pool = get_pool()
    sql = """
        INSERT INTO lead_status (instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me)
        VALUES (%s, %s, %s, NOW(), %s, %s)
        ON CONFLICT (instance_id, chatid)
        DO UPDATE SET
            stage = EXCLUDED.stage,
            updated_at = NOW(),
            last_msg_ts = EXCLUDED.last_msg_ts,
            last_from_me = EXCLUDED.last_from_me
        RETURNING instance_id, chatid, stage, updated_at, last_msg_ts, last_from_me
    """
    with pool.connection() as con:
        row = con.execute(sql, (instance_id, chatid, stage, int(last_msg_ts or 0), bool(last_from_me))).fetchone()
        return {
            "instance_id": row[0],
            "chatid": row[1],
            "stage": row[2],
            "updated_at": _iso(row[3]),
            "last_msg_ts": int(row[4] or 0),
            "last_from_me": bool(row[5]),
        }


def needsReclassify(
    cached: Optional[Dict[str, Any]],
    observed_last_ts: Optional[int] = None,
    observed_from_me: Optional[bool] = None,
) -> bool:
    """
    Decide se precisamos reclassificar.
    Regra simples (compatível com usos típicos no media.py):
      - Se não há cache => True
      - Se o timestamp observado é mais recente que o do cache => True
      - Caso contrário => False
    """
    if not cached:
        return True
    try:
        cached_ts = int(cached.get("last_msg_ts") or 0)
        obs_ts = int(observed_last_ts or 0)
        if obs_ts > cached_ts:
            return True
    except Exception:
        # se algo der ruim ao parsear, força reclassificação para segurança
        return True
    return False
