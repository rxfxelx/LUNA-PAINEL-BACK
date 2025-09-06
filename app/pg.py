import os
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row  # <- cada fetch* já vem como dict

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """
    Singleton do pool de conexões (autocommit ligado, row_factory=dict_row).
    """
    global _pool
    if _pool is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL não definido no ambiente em runtime")

        size = int(os.getenv("PGPOOL_SIZE", "5"))

        def _configure(conn):
            conn.autocommit = True

        _pool = ConnectionPool(
            conninfo=dsn,
            max_size=size,
            configure=_configure,
            kwargs={"row_factory": dict_row},  # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
        )
    return _pool


# helper opcional (uso: with get_conn() as con: con.execute(...))
def get_conn():
    return get_pool().connection()


def init_schema():
    """
    - Tabela lead_status -> garante migrações/índices
    - Tabela billing_accounts -> trial + cobrança
    - Tabela users -> login por e-mail/senha
    """
    sql = """
    -- =========================================
    -- LEAD STATUS
    -- =========================================
    CREATE TABLE IF NOT EXISTS lead_status (
      instance_id   TEXT NOT NULL,
      chatid        TEXT NOT NULL,
      stage         TEXT NOT NULL DEFAULT 'contatos',
      updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_msg_ts   BIGINT NOT NULL DEFAULT 0,
      last_from_me  BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY (instance_id, chatid)
    );

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

    UPDATE lead_status
       SET instance_id = COALESCE(instance_id, 'legacy')
     WHERE instance_id IS NULL;

    ALTER TABLE lead_status
      ALTER COLUMN stage SET DEFAULT 'contatos',
      ALTER COLUMN updated_at SET DEFAULT NOW(),
      ALTER COLUMN last_msg_ts SET DEFAULT 0,
      ALTER COLUMN last_from_me SET DEFAULT FALSE;

    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'lead_status'::regclass AND contype = 'p'
      ) THEN
        ALTER TABLE lead_status DROP CONSTRAINT IF EXISTS lead_status_pkey;
      END IF;
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

    CREATE INDEX IF NOT EXISTS idx_lead_status_stage          ON lead_status(stage);
    CREATE INDEX IF NOT EXISTS idx_lead_status_updated_at     ON lead_status(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_lead_status_last_msg_ts    ON lead_status(last_msg_ts DESC);
    CREATE INDEX IF NOT EXISTS idx_lead_status_inst_stage     ON lead_status(instance_id, stage);
    CREATE INDEX IF NOT EXISTS idx_lead_status_inst_last_ts   ON lead_status(instance_id, last_msg_ts DESC);

    -- =========================================
    -- BILLING / ASSINATURAS
    -- =========================================
    CREATE TABLE IF NOT EXISTS billing_accounts (
      id                  SERIAL PRIMARY KEY,
      billing_key         TEXT UNIQUE NOT NULL,
      instance_id         TEXT,
      host                TEXT,
      created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      trial_started_at    TIMESTAMPTZ,
      trial_ends_at       TIMESTAMPTZ,
      paid_until          TIMESTAMPTZ,
      plan                TEXT,
      last_payment_status TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_billing_paid_until ON billing_accounts(paid_until DESC);
    CREATE INDEX IF NOT EXISTS idx_billing_trial_ends ON billing_accounts(trial_ends_at DESC);

    -- =========================================
    -- USERS (login por e-mail/senha)
    -- =========================================
    CREATE TABLE IF NOT EXISTS users (
      id              SERIAL PRIMARY KEY,
      email           TEXT NOT NULL UNIQUE,
      password_hash   TEXT NOT NULL,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_login_at   TIMESTAMPTZ NULL
    );
    """
    with get_pool().connection() as con:
        con.execute(sql)
