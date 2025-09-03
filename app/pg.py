import os
from psycopg_pool import ConnectionPool

_pool = None

def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL não definido no ambiente em runtime")
        size = int(os.getenv("PGPOOL_SIZE", "5"))
        _pool = ConnectionPool(conninfo=dsn, max_size=size, kwargs={"autocommit": True})
    return _pool

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
    with get_pool().connection() as con:
        con.execute(sql)
