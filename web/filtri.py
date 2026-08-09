"""
web/filtri.py
=============
Pannello Parametri globale (Modulo A della Piattaforma di Controllo),
condiviso dalle pagine in web/pages/*.py. Streamlit multipage nativo
mantiene st.session_state tra una pagina e l'altra nella stessa sessione
browser: i filtri impostati qui restano validi cambiando pagina, senza
bisogno di un router o di uno state manager esterno.

Uso in una pagina:
    import filtri
    f = filtri.pannello_parametri(gioco_default="lotto")
    # f.gioco, f.ruota, f.data_da, f.data_a, f.n_estrazioni,
    # f.somma_min, f.somma_max, f.ampiezza_bin, f.ritardo_min, f.ritardo_max
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import streamlit as st

import db

RUOTE_LOTTO = (
    "Tutte", "Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli",
    "Palermo", "Roma", "Torino", "Venezia", "Nazionale",
)

PRESET_N_ESTRAZIONI = ["Ultime 50", "Ultime 100", "Ultime 500", "Range di date"]


@dataclass
class Filtri:
    gioco: str
    ruota: str
    data_da: Optional[str]
    data_a: Optional[str]
    n_estrazioni: Optional[int]
    somma_min: Optional[int]
    somma_max: Optional[int]
    ampiezza_bin: int
    ritardo_min: Optional[int]
    ritardo_max: Optional[int]


@st.cache_data(ttl=300, show_spinner=False)
def _limiti_storico(gioco: str) -> tuple[date, date]:
    tabella = "estrazioni_lotto" if gioco == "lotto" else "estrazioni_superenalotto"
    df = db.query_df(f"SELECT MIN(data_estrazione) AS data_min, MAX(data_estrazione) AS data_max FROM {tabella}")
    if df.empty or df.iloc[0]["data_min"] is None:
        oggi = date.today()
        return oggi, oggi
    return df.iloc[0]["data_min"], df.iloc[0]["data_max"]


def pannello_parametri(gioco_default: str = "lotto") -> Filtri:
    st.sidebar.header("🎛️ Parametri di analisi")

    gioco = st.sidebar.radio(
        "Gioco", ["lotto", "superenalotto"],
        format_func=lambda g: "Lotto" if g == "lotto" else "SuperEnalotto",
        index=0 if gioco_default == "lotto" else 1, key="filtri_gioco",
    )

    ruota = "Tutte"
    if gioco == "lotto":
        ruota = st.sidebar.selectbox("Ruota", RUOTE_LOTTO, key="filtri_ruota")

    data_min, data_max = _limiti_storico(gioco)

    preset = st.sidebar.radio("Ampiezza finestra", PRESET_N_ESTRAZIONI, index=1, key="filtri_preset")
    n_estrazioni: Optional[int] = None
    data_da: Optional[str] = None
    data_a: Optional[str] = None

    if preset == "Range di date":
        col1, col2 = st.sidebar.columns(2)
        data_da_val = col1.date_input("Da", value=data_min, min_value=data_min, max_value=data_max, key="filtri_data_da")
        data_a_val = col2.date_input("A", value=data_max, min_value=data_min, max_value=data_max, key="filtri_data_a")
        data_da, data_a = str(data_da_val), str(data_a_val)
    else:
        n_estrazioni = {"Ultime 50": 50, "Ultime 100": 100, "Ultime 500": 500}[preset]

    st.sidebar.caption(f"Storico disponibile: {data_min} → {data_max}")

    with st.sidebar.expander("Somme e distribuzione"):
        somma_range = st.slider("Range somma", 0, 540 if gioco == "lotto" else 550, (0, 540 if gioco == "lotto" else 550), key="filtri_somma")
        ampiezza_bin = st.slider("Ampiezza bin istogramma", 5, 50, 20, step=5, key="filtri_bin")

    with st.sidebar.expander("Ritardo (Tabellone Analitico)"):
        ritardo_range = st.slider("Range ritardo (estrazioni)", 0, 400, (0, 400), key="filtri_ritardo")

    return Filtri(
        gioco=gioco, ruota=ruota, data_da=data_da, data_a=data_a, n_estrazioni=n_estrazioni,
        somma_min=somma_range[0], somma_max=somma_range[1], ampiezza_bin=ampiezza_bin,
        ritardo_min=ritardo_range[0], ritardo_max=ritardo_range[1],
    )
