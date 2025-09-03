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
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lead_status' AND column_name = 'instance_id'
      ) THEN
        ALTER TABLE lead_status ADD COLUMN instance_id TEXT;
      END IF;
    END$$;

    -- backfill: se veio de schema antigo, garante valor para instance_id
    UPDATE lead_status SET instance_id = COALESCE(instance_id, 'legacy') WHERE instance_id IS NULL;

    -- garante colunas antigas (idempotente)
    ALTER TABLE lead_status
      ALTER COLUMN stage SET DEFAULT 'contatos',
      ALTER COLUMN updated_at SET DEFAULT NOW(),
      ALTER COLUMN last_msg_ts SET DEFAULT 0,
      ALTER COLUMN last_from_me SET DEFAULT FALSE;

    -- migração da PK: troca PK antiga (somente chatid) pela composta
    DO $$
    DECLARE
      has_pkey BOOLEAN;
    BEGIN
      SELECT TRUE
      FROM pg_constraint
      WHERE conrelid = 'lead_status'::regclass
        AND contype = 'p'
        AND conkey::text IN (
          -- pk só chatid
          (SELECT array_agg(attnum)::text
             FROM pg_attribute
             WHERE attrelid = 'lead_status'::regclass
               AND attname IN ('chatid')
               AND attnum > 0),
          -- pk composta correta
          (SELECT array_agg(attnum)::text
             FROM pg_attribute
             WHERE attrelid = 'lead_status'::regclass
               AND attname IN ('instance_id','chatid')
               AND attnum > 0)
        )
      INTO has_pkey;

      -- sempre dropar a PK e recriar a correta (idempotente)
      IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'lead_status'::regclass AND contype='p') THEN
        ALTER TABLE lead_status DROP CONSTRAINT IF EXISTS lead_status_pkey;
      END IF;

      BEGIN
        ALTER TABLE lead_status ALTER COLUMN instance_id SET NOT NULL;
      EXCEPTION WHEN others THEN
        -- se tiver registros nulos que não deu pra backfill, mantém sem NOT NULL
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
