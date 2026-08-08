"""
backend/db.py
=============
Connection pool MySQL per l'API FastAPI (main.py).

A differenza di web/db.py (che usa la cache per-sessione di Streamlit,
un solo utente alla volta di fatto), qui serve un vero pool: uvicorn
esegue le funzioni sync degli endpoint in un threadpool, quindi più
richieste concorrenti possono arrivare mentre un'altra è ancora a metà
query — una singola connessione condivisa tra thread andrebbe corrotta
(stato del cursore) sotto concorrenza reale.
"""

from __future__ import annotations

import os
from typing import Optional

from mysql.connector import pooling

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "db"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "database": os.environ.get("DB_NAME", "lotto_statistics"),
    "user": os.environ.get("DB_USER", "statistics_user"),
    "password": os.environ.get("DB_PASSWORD", "CHANGE_ME_STRONG_PASSWORD"),
}

# autocommit=True per ogni connessione del pool: per difesa in profondità,
# non ci si affida al solo reset implicito del pool (pool_reset_session,
# di default True, fa ROLLBACK alla restituzione) — vedi il bug reale
# documentato in web/db.py, causato esattamente da una connessione con
# autocommit=False (default di mysql-connector-python) mai committata.
#
# use_pure=True: lotto_stat_web si riavviava da solo con exit code 134
# (SIGABRT dall'estensione C _mysql_connector, non un'eccezione Python —
# vedi indagine in web/db.py/CLAUDE.md). uvicorn esegue gli endpoint sync
# in un threadpool: più connessioni C create/usate da thread diversi è
# esattamente lo scenario in cui quell'estensione è nota per essere
# fragile, quindi lo stesso fix va applicato qui per difesa, anche se
# backend non ha (ancora) mostrato il crash.
_pool = pooling.MySQLConnectionPool(pool_name="lotto_api_pool", pool_size=5, autocommit=True, use_pure=True, **DB_CONFIG)


def query(sql: str, params: Optional[tuple] = None) -> list[dict]:
    """Esegue una SELECT su una connessione presa dal pool e la ritorna
    subito dopo (il pool la marca come libera, non la chiude davvero)."""
    conn = _pool.get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
        finally:
            cursor.close()
    finally:
        conn.close()
