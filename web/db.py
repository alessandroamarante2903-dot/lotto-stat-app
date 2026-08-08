"""
web/db.py
=========

Livello di accesso al database per l'interfaccia Streamlit: connessione
condivisa (cache_resource, sopravvive ai rerun) + helper per eseguire
query e restituire un pandas.DataFrame, con cache a breve TTL per non
martellare MySQL ad ogni rerun di Streamlit (che riesegue l'intero
script ad ogni interazione dell'utente: click, cambio di un selectbox, ecc.).

Si connette come 'statistics_user' (mai come root), stesse variabili
d'ambiente di backend/scraper.py.
"""

from __future__ import annotations

import os
from typing import Optional

import mysql.connector
import pandas as pd
import streamlit as st
from mysql.connector import Error as MySQLError

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "db"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "database": os.environ.get("DB_NAME", "lotto_statistics"),
    "user": os.environ.get("DB_USER", "statistics_user"),
    "password": os.environ.get("DB_PASSWORD", "CHANGE_ME_STRONG_PASSWORD"),
}

# Le viste "live" (frequenze, ritardi) riflettono l'ultima riga inserita
# in estrazioni_lotto/estrazioni_superenalotto; le tabelle cache_* vengono
# aggiornate solo dalla pipeline di scraping (scraper/update_pipeline.py).
# 60s è un compromesso fra reattività della dashboard e carico su MySQL
# durante una sessione interattiva con molti rerun ravvicinati.
DEFAULT_TTL_SECONDI = 60


@st.cache_resource(show_spinner=False)
def _connessione() -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(**DB_CONFIG)


def _connessione_viva() -> mysql.connector.MySQLConnection:
    """Ritorna la connessione condivisa, riconnettendo automaticamente se
    MySQL l'ha chiusa per inattività (wait_timeout) mentre la sessione
    Streamlit restava aperta in background senza eseguire query."""
    conn = _connessione()
    try:
        conn.ping(reconnect=True, attempts=3, delay=1)
    except MySQLError:
        _connessione.clear()
        conn = _connessione()
    return conn


@st.cache_data(ttl=DEFAULT_TTL_SECONDI, show_spinner=False)
def query_df(sql: str, params: Optional[tuple] = None) -> pd.DataFrame:
    """Esegue una SELECT e ritorna un DataFrame. La cache di Streamlit è
    chiavata anche su 'sql' e 'params': query diverse (es. ruota diversa
    selezionata in UI) non condividono risultati tra loro."""
    conn = _connessione_viva()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params or ())
        righe = cursor.fetchall()
        return pd.DataFrame(righe)
    finally:
        cursor.close()


def esegui_comando(sql: str, params: Optional[tuple] = None) -> int:
    """Esegue un comando che non ritorna righe (INSERT/UPDATE/CALL) sulla
    connessione condivisa e fa subito commit."""
    conn = _connessione_viva()
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        while cursor.nextset():
            pass
        conn.commit()
        return cursor.rowcount
    finally:
        cursor.close()


def svuota_cache_query() -> None:
    """Invalida la cache di query_df: da chiamare dopo un refresh riuscito
    delle cache statistiche, così la dashboard mostra subito i dati
    aggiornati invece di aspettare la scadenza naturale del TTL."""
    query_df.clear()
