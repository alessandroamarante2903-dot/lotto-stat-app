"""
web/api_client.py
==================
Client HTTP verso lotto_stat_backend (backend/main.py) per i moduli
parametrici della nuova Piattaforma di Controllo (web/pages/*.py).

A differenza delle vecchie tab di web/app.py (query dirette a MySQL via
web/db.py, invariate), i moduli di analisi qui sotto chiamano l'API
FastAPI: la logica di analisi (backend/analytics.py) resta unica,
condivisa con qualunque altro consumer HTTP dell'API, invece di essere
duplicata in Python lato Streamlit. Raggiungibile come 'backend' sulla
rete Podman interna (lotto_stat_net), niente a che fare col reverse
proxy nginx (quello serve solo il traffico da fuori, vedi nginx.conf).
"""

from __future__ import annotations

import os
from typing import Optional

import requests
import streamlit as st

API_BASE_URL = os.environ.get("BACKEND_API_URL", "http://backend:8000")
TIMEOUT_SECONDI = 30


@st.cache_data(ttl=60, show_spinner="Interrogo l'API...")
def analisi_somme(
    gioco: str, ruota: str, data_da: Optional[str], data_a: Optional[str],
    n_estrazioni: Optional[int], ampiezza_bin: int, posizioni: Optional[tuple[str, ...]] = None,
) -> dict:
    # posizioni è una tuple (non list) nella firma: gli argomenti di una
    # funzione @st.cache_data devono essere hashabili per la chiave di
    # cache, una list non lo è.
    params = {
        "gioco": gioco, "ruota": ruota, "data_da": data_da, "data_a": data_a,
        "n_estrazioni": n_estrazioni, "ampiezza_bin": ampiezza_bin,
    }
    if posizioni:
        params["posizioni"] = list(posizioni)  # requests ripete il parametro per ogni elemento
    r = requests.get(f"{API_BASE_URL}/analisi/somme", params=params, timeout=TIMEOUT_SECONDI)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60, show_spinner="Interrogo l'API...")
def analisi_tabellone(
    ruota: str, data_da: Optional[str], data_a: Optional[str], n_estrazioni: Optional[int],
    ritardo_min: Optional[int], ritardo_max: Optional[int],
    stato: str = "Tutti", passo_k: int = 5,
) -> list[dict]:
    r = requests.get(
        f"{API_BASE_URL}/analisi/tabellone",
        params={
            "ruota": ruota, "data_da": data_da, "data_a": data_a, "n_estrazioni": n_estrazioni,
            "ritardo_min": ritardo_min, "ritardo_max": ritardo_max, "stato": stato, "passo_k": passo_k,
        },
        timeout=TIMEOUT_SECONDI,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60, show_spinner="Interrogo l'API...")
def analisi_isocronia() -> dict:
    r = requests.get(f"{API_BASE_URL}/analisi/isocronia", timeout=TIMEOUT_SECONDI)
    r.raise_for_status()
    return r.json()


def invalidare_cache_backend() -> bool:
    """Da chiamare dopo un refresh riuscito lanciato da qui (web/app.py,
    subprocess) — il backend (processo separato, container lotto_stat_backend)
    non lo vede altrimenti: vedi backend/main.py, POST /analisi/cache/invalidare.
    Best-effort: se il backend non risponde non blocca il pannello scraper
    (i dati grezzi sono comunque già salvati), ritorna solo True/False."""
    try:
        r = requests.post(f"{API_BASE_URL}/analisi/cache/invalidare", timeout=10)
        r.raise_for_status()
        return True
    except requests.RequestException:
        return False


def analisi_spia(
    numero_spia: int, ruota_spia: str, ruota_target: str, orizzonte_h: int = 5,
    data_da: Optional[str] = None,
) -> dict:
    # Non cachata: azione esplicita dell'utente (bottone "Analizza").
    r = requests.post(
        f"{API_BASE_URL}/analisi/spia",
        json={
            "numero_spia": numero_spia, "ruota_spia": ruota_spia, "ruota_target": ruota_target,
            "orizzonte_h": orizzonte_h, "data_da": data_da,
        },
        timeout=TIMEOUT_SECONDI,
    )
    if r.status_code in (400, 422):
        detail = r.json().get("detail", "Richiesta non valida")
        raise ValueError(detail if isinstance(detail, str) else str(detail))
    r.raise_for_status()
    return r.json()


def analisi_backtest(numeri: list[int], ruota: str, sorte: str = "Ambo", data_da: Optional[str] = None) -> dict:
    # Non cachata: azione esplicita dell'utente (bottone "Simula"), non un
    # rerun passivo dei filtri globali.
    r = requests.post(
        f"{API_BASE_URL}/analisi/backtest",
        json={"numeri": numeri, "ruota": ruota, "sorte": sorte, "data_da": data_da},
        timeout=TIMEOUT_SECONDI,
    )
    if r.status_code == 400:
        raise ValueError(r.json().get("detail", "Richiesta non valida"))
    r.raise_for_status()
    return r.json()
