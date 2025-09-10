# app/db.py

import os, sqlite3, hashlib, time
from threading import RLock

DB_PATH = os.getenv("LUNA_DB_PATH", os.path.join(os.getcwd(), "data", "luna.db"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
_lock = RLock()

def _connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)

    # Status de conversas/contatos no funil
    con.execute("""
        CREATE TABLE IF NOT EXISTS crm_status (
            instance   TEXT NOT NULL,
            chatid     TEXT NOT NULL,
            stage      TEXT NOT NULL,
            notes      TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (instance, chatid)
        )
    """)

    # >>> NOVA TABELA: instâncias Uazapi provisionadas pelo painel <<<
    # Guardamos o token de instância (para chamadas /connect, /status),
    # seu hash (para verificações/compat) e o host (subdomínio).
    con.execute("""
        CREATE TABLE IF NOT EXISTS uaz_instances (
            tenant      TEXT NOT NULL,
            host        TEXT NOT NULL,
            instance    TEXT NOT NULL,
            token       TEXT NOT NULL,
            token_hash  TEXT NOT NULL,
            status      TEXT NOT NULL,
            created_at  INTEGER NOT NULL,
            PRIMARY KEY (tenant, instance)
        )
    """)
    return con

_con = _connect()

def inst_key(token: str) -> str:
    # não guardamos o token em texto puro; usamos hash
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# --------------------------
# Helpers da tabela crm_status
# --------------------------
def upsert_status(instance: str, chatid: str, stage: str, notes: str | None = None):
    now = int(time.time())
    with _lock:
        _con.execute("""
            INSERT INTO crm_status (instance, chatid, stage, notes, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instance, chatid) DO UPDATE SET
              stage=excluded.stage, notes=excluded.notes, updated_at=excluded.updated_at
        """, (instance, chatid, stage, notes, now))
        _con.commit()

def get_status(instance: str, chatid: str) -> dict | None:
    cur = _con.execute("SELECT stage, notes, updated_at FROM crm_status WHERE instance=? AND chatid=?",
                       (instance, chatid))
    row = cur.fetchone()
    if not row: return None
    return {"stage": row[0], "notes": row[1], "updated_at": row[2]}

def list_by_stage(instance: str, stage: str, limit: int = 50, offset: int = 0):
    cur = _con.execute("""
        SELECT chatid, stage, notes, updated_at
        FROM crm_status
        WHERE instance=? AND stage=?
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
    """, (instance, stage, limit, offset))
    rows = cur.fetchall()
    return [{"chatid": r[0], "stage": r[1], "notes": r[2], "updated_at": r[3]}] if rows else []

def counts_by_stage(instance: str) -> dict:
    cur = _con.execute("""
        SELECT stage, COUNT(*) FROM crm_status
        WHERE instance=?
        GROUP BY stage
    """, (instance,))
    return {row[0]: row[1] for row in cur.fetchall()}

# --------------------------
# Helpers da tabela uaz_instances
# --------------------------
def save_uaz_instance(tenant: str, host: str, instance: str, token: str, status: str) -> None:
    """Cria/atualiza o registro de uma instância Uazapi para um tenant."""
    now = int(time.time())
    thash = inst_key(token)
    with _lock:
        _con.execute("""
            INSERT INTO uaz_instances (tenant, host, instance, token, token_hash, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant, instance) DO UPDATE SET
              host=excluded.host,
              token=excluded.token,
              token_hash=excluded.token_hash,
              status=excluded.status
        """, (tenant, host, instance, token, thash, status, now))
        _con.commit()

def get_uaz_instance(tenant: str, instance: str) -> dict | None:
    """Retorna os dados da instância (host, token, status, ...) ou None."""
    cur = _con.execute("""
        SELECT tenant, host, instance, token, token_hash, status, created_at
        FROM uaz_instances
        WHERE tenant = ? AND instance = ?
    """, (tenant, instance))
    row = cur.fetchone()
    if not row:
        return None
    keys = ["tenant", "host", "instance", "token", "token_hash", "status", "created_at"]
    return dict(zip(keys, row))
