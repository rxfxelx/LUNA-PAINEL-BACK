import os
from psycopg_pool import ConnectionPool

_pool = None


def get_pool() -> ConnectionPool:
    """
    Cria (singleton) um pool de conexões.
    - Usa configure para ligar autocommit de forma correta.
    """
    global _pool
    if _pool is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL não definido no ambiente em runtime")
        size = int(os.getenv("PGPOOL_SIZE", "5"))

        def _configure(conn):
            conn.autocommit = True

        _pool = ConnectionPool(conninfo=dsn, max_size=size, configure=_configure)
    return _pool


def init_schema():
    """
    Cria/atualiza a tabela lead_status para suportar multi-instância.
    - adiciona instance_id (se não existir)
    - migra PK para (instance_id, chatid)
    - cria índices úteis
    """
    sql = """
    -- cria tabela caso não exista (estrutura nova)
    CREATE TABLE IF NOT EXISTS lead_status (
      instance_id   TEXT NOT NULL,
      chatid        TEXT NOT NULL,
      stage         TEXT NOT NULL DEFAULT 'contatos',
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_msg_ts   BIGINT NOT NULL DEFAULT 0,
      last_from_me  BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY (instance_id, chatid)
    );

    -- migração: adicionar coluna instance_id se a tabela antiga existir
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'lead_status' AND column_name = 'instance_id'
      ) THEN
        ALTER TABLE lead_status ADD COLUMN instance_id TEXT;
      END IF;
    END$$;

    -- backfill: se veio de schema antigo, garante valor para instance_id
    UPDATE lead_status
       SET instance_id = COALESCE(instance_id, 'legacy')
     WHERE instance_id IS NULL;

    -- garante defaults (idempotente)
    ALTER TABLE lead_status
      ALTER COLUMN stage SET DEFAULT 'contatos',
      ALTER COLUMN updated_at SET DEFAULT NOW(),
      ALTER COLUMN last_msg_ts SET DEFAULT 0,
      ALTER COLUMN last_from_me SET DEFAULT FALSE;

    -- migração da PK: ajusta para (instance_id, chatid)
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'lead_status'::regclass AND contype = 'p'
      ) THEN
        ALTER TABLE lead_status DROP CONSTRAINT IF EXISTS lead_status_pkey;
      END IF;

      -- pode falhar se houver linhas sem instance_id (tratado pelo backfill acima)
      BEGIN
        ALTER TABLE lead_status ALTER COLUMN instance_id SET NOT NULL;
      EXCEPTION WHEN others THEN
        NULL;
      END;

      BEGIN
        ALTER TABLE lead_status ADD PRIMARY KEY (instance_id, chatid);
      EXCEPTION WHEN others THEN
        NULL;
      END;
    END$$;

    -- índices
    CREATE INDEX IF NOT EXISTS idx_lead_status_stage          ON lead_status(stage);
    CREATE INDEX IF NOT EXISTS idx_lead_status_updated_at     ON lead_status(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_lead_status_last_msg_ts    ON lead_status(last_msg_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_lead_status_inst_stage     ON lead_status(instance_id, stage);
    CREATE INDEX IF NOT EXISTS idx_lead_status_inst_last_ts   ON lead_status(instance_id, last_msg_ts DESC);
    """
    with get_pool().connection() as con:
        con.execute(sql)
