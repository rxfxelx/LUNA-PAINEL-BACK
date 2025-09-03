# app/services/lead_status.py
from __future__ import annotations
from typing import Optional, Dict, Any
from app.pg import get_conn  # use o seu helper de conexão

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lead_status (
  instance_id TEXT NOT NULL,
  chatid      TEXT NOT NULL,
  stage       TEXT,
  last_msg_ts BIGINT DEFAULT 0,
  updated_at  TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (instance_id, chatid)
);
-- migrações idempotentes (caso a tabela já existisse sem instance_id)
ALTER TABLE lead_status ADD COLUMN IF NOT EXISTS instance_id TEXT;
ALTER TABLE lead_status DROP CONSTRAINT IF EXISTS lead_status_pkey;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE tablename = 'lead_status' AND indexname = 'lead_status_pkey'
  ) THEN
    BEGIN
      ALTER TABLE lead_status ADD PRIMARY KEY (instance_id, chatid);
    EXCEPTION WHEN others THEN
      -- no-op
      NULL;
    END;
  END IF;
END$$;
"""

def ensure_table():
    with get_conn() as cur:
        cur.execute(TABLE_SQL)

def getCachedLeadStatus(instance_id: str, chatid: str) -> Optional[Dict[str, Any]]:
    ensure_table()
    with get_conn() as cur:
        cur.execute(
            "SELECT instance_id, chatid, stage, last_msg_ts, updated_at "
            "FROM lead_status WHERE instance_id=%s AND chatid=%s",
            (instance_id, chatid),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "instance_id": row[0],
            "chatid": row[1],
            "stage": row[2],
            "last_msg_ts": int(row[3] or 0),
            "updated_at": row[4],
        }

def upsertLeadStatus(instance_id: str, chatid: str, stage: str, last_msg_ts: int):
    ensure_table()
    with get_conn() as cur:
        cur.execute(
            """
            INSERT INTO lead_status (instance_id, chatid, stage, last_msg_ts)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (instance_id, chatid)
            DO UPDATE SET
              stage = EXCLUDED.stage,
              last_msg_ts = GREATEST(lead_status.last_msg_ts, EXCLUDED.last_msg_ts),
              updated_at = now()
            """,
            (instance_id, chatid, stage, int(last_msg_ts or 0)),
        )
