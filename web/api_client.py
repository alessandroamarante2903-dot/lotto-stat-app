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
    n_estrazioni: Optional[int], ampiezza_bin: int,
) -> dict:
    r = requests.get(
        f"{API_BASE_URL}/analisi/somme",
        params={
            "gioco": gioco, "ruota": ruota, "data_da": data_da, "data_a": data_a,
            "n_estrazioni": n_estrazioni, "ampiezza_bin": ampiezza_bin,
        },
        timeout=TIMEOUT_SECONDI,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60, show_spinner="Interrogo l'API...")
def analisi_tabellone(
    ruota: str, data_da: Optional[str], data_a: Optional[str], n_estrazioni: Optional[int],
    ritardo_min: Optional[int], ritardo_max: Optional[int],
) -> list[dict]:
    r = requests.get(
        f"{API_BASE_URL}/analisi/tabellone",
        params={
            "ruota": ruota, "data_da": data_da, "data_a": data_a, "n_estrazioni": n_estrazioni,
            "ritardo_min": ritardo_min, "ritardo_max": ritardo_max,
        },
        timeout=TIMEOUT_SECONDI,
    )
    r.raise_for_status()
    return r.json()


def analisi_backtest(numeri: list[int], ruota: str, data_da: Optional[str] = None) -> dict:
    # Non cachata: azione esplicita dell'utente (bottone "Simula"), non un
    # rerun passivo dei filtri globali.
    r = requests.post(
        f"{API_BASE_URL}/analisi/backtest",
        json={"numeri": numeri, "ruota": ruota, "data_da": data_da},
        timeout=TIMEOUT_SECONDI,
    )
    if r.status_code == 400:
        raise ValueError(r.json().get("detail", "Richiesta non valida"))
    r.raise_for_status()
    return r.json()
