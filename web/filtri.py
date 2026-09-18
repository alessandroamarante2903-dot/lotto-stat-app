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
    filtri.render_active_filters_banner(f)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import streamlit as st

import db
import ui_components as ui

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


def pannello_parametri(
    gioco_default: str = "lotto",
    giochi_disponibili: tuple[str, ...] = ("lotto", "superenalotto"),
) -> Filtri:
    """`giochi_disponibili` limita le opzioni del radio "Gioco Target": i moduli
    Lotto-only (Tabellone Analitico, Simulatore Backtest, Numero Spia — nessuna
    delle rispettive query di backend accetta un parametro 'gioco') passano
    `("lotto",)` per evitare di mostrare un'opzione SuperEnalotto che, se scelta,
    non avrebbe alcun effetto sull'analisi ma verrebbe comunque dichiarata come
    "Gioco: SuperEnalotto" nel banner filtri attivi — bug reale riscontrato in
    pratica (i numeri selezionati finivano nel carrello Lotto nonostante il
    banner dicesse SuperEnalotto)."""
    ui.inject_custom_css()
    st.sidebar.markdown(
        "<div style='display: flex; align-items: center; gap: 8px; margin-bottom: 12px;'>"
        "<span style='font-size: 1.3rem;'>🎛️</span>"
        "<span style='font-size: 1.1rem; font-weight: 700; color: #F8FAFC;'>Filtri di Analisi</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.sidebar.container(border=True):
        if len(giochi_disponibili) == 1:
            gioco = giochi_disponibili[0]
            st.caption(f"🎯 Gioco Target: **{'Lotto' if gioco == 'lotto' else 'SuperEnalotto'}** (modulo dedicato)")
        else:
            gioco = st.radio(
                "Gioco Target", list(giochi_disponibili),
                format_func=lambda g: "Lotto" if g == "lotto" else "SuperEnalotto",
                index=list(giochi_disponibili).index(gioco_default) if gioco_default in giochi_disponibili else 0,
                key="filtri_gioco",
            )

        ruota = "Tutte"
        if gioco == "lotto":
            ruota = st.selectbox("Ruota", RUOTE_LOTTO, key="filtri_ruota")

    data_min, data_max = _limiti_storico(gioco)

    with st.sidebar.container(border=True):
        preset = st.radio("Orizzonte Temporale", PRESET_N_ESTRAZIONI, index=1, key="filtri_preset")
        n_estrazioni: Optional[int] = None
        data_da: Optional[str] = None
        data_a: Optional[str] = None

        if preset == "Range di date":
            col1, col2 = st.columns(2)
            data_da_val = col1.date_input("Da", value=data_min, min_value=data_min, max_value=data_max, key="filtri_data_da")
            data_a_val = col2.date_input("A", value=data_max, min_value=data_min, max_value=data_max, key="filtri_data_a")
            data_da, data_a = str(data_da_val), str(data_a_val)
        else:
            n_estrazioni = {"Ultime 50": 50, "Ultime 100": 100, "Ultime 500": 500}[preset]

        st.caption(f"Archivio: `{data_min}` ➔ `{data_max}`")

    with st.sidebar.expander("Parametri Somme & Distribuzione"):
        max_somma = 540 if gioco == "lotto" else 550
        somma_range = st.slider("Range Somma", 0, max_somma, (0, max_somma), key="filtri_somma")
        ampiezza_bin = st.slider("Ampiezza Bin Istogramma", 5, 50, 20, step=5, key="filtri_bin")

    with st.sidebar.expander("Parametri Tabellone Analitico"):
        ritardo_range = st.slider("Range Ritardo (estrazioni)", 0, 400, (0, 400), key="filtri_ritardo")

    return Filtri(
        gioco=gioco, ruota=ruota, data_da=data_da, data_a=data_a, n_estrazioni=n_estrazioni,
        somma_min=somma_range[0], somma_max=somma_range[1], ampiezza_bin=ampiezza_bin,
        ritardo_min=ritardo_range[0], ritardo_max=ritardo_range[1],
    )


def render_active_filters_banner(f: Filtri) -> None:
    """Renderizza in testa alla pagina una striscia sintetica dei filtri attivi.

    Serve a rendere sempre visibile il contesto dell'analisi anche quando la
    sidebar è chiusa: senza questo promemoria un numero letto a schermo non
    dice su quale gioco, ruota e finestra temporale è stato calcolato.
    """
    gioco_label = "Lotto" if f.gioco == "lotto" else "SuperEnalotto"
    finestra = f"Ultime {f.n_estrazioni} estrazioni" if f.n_estrazioni else f"Dal {f.data_da} al {f.data_a}"
    ruota_info = f" • Ruota: <b>{f.ruota}</b>" if f.gioco == "lotto" else ""

    st.markdown(
        "<div style='background: rgba(30, 41, 59, 0.4); border: 1px solid rgba(255, 255, 255, 0.08); "
        "border-radius: 8px; padding: 8px 14px; margin-bottom: 16px; font-size: 0.85rem; color: #94a3b8; "
        "display: flex; align-items: center; gap: 8px;'>"
        "<span style='color: #f59e0b;'>⚙️ <b>Filtri attivi:</b></span>"
        f"<span>Gioco: <b>{gioco_label}</b>{ruota_info} • Finestra: <b>{finestra}</b></span>"
        "</div>",
        unsafe_allow_html=True,
    )
