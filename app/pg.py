# app/pg.py
import os
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Env DATABASE_URL ausente")

POOL_SIZE = int(os.getenv("PGPOOL_SIZE", "5"))

pool = ConnectionPool(conninfo=DATABASE_URL, max_size=POOL_SIZE, kwargs={"autocommit": True})

def init_schema():
    sql = """
    CREATE TABLE IF NOT EXISTS lead_status (
      chatid        TEXT PRIMARY KEY,
      stage         TEXT NOT NULL,
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_msg_ts   BIGINT NOT NULL DEFAULT 0,
      last_from_me  BOOLEAN NOT NULL DEFAULT FALSE
    );
    CREATE INDEX IF NOT EXISTS idx_lead_status_stage        ON lead_status(stage);
    CREATE INDEX IF NOT EXISTS idx_lead_status_updated_at   ON lead_status(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_lead_status_last_msg_ts  ON lead_status(last_msg_ts DESC);
    """
    with pool.connection() as con:
        con.execute(sql)
