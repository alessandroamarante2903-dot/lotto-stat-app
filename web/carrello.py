"""
web/carrello.py
================
Carrello numeri condiviso fra le pagine Streamlit: permette di raccogliere
numeri "interessanti" evidenziati dai moduli di analisi (Tabellone,
Numero Spia, Simulatore Backtest) e di riversarli nel Calcolatore &
Sistemi, senza dover ridigitare a mano la selezione.

Stesso pattern di web/filtri.py: funzioni pure + funzioni di rendering,
chiavi st.session_state stabili. Nessuna dipendenza da calcolo_costi.py:
il carrello è un contenitore "dumb", la validazione (6-20 numeri per il
sistema integrale SuperEnalotto, 6-16 per il ridotto, ecc.) resta
esclusivamente in calcolo_costi.py al momento del calcolo.

Due liste indipendenti (Lotto/SuperEnalotto) perché calcolo_costi.py
tratta i due giochi con vincoli diversi (con/senza ruota, range di N
diverso): un carrello unico "misto" non avrebbe un significato univoco.

Uso in una pagina:
    import carrello
    carrello.aggiungi_numeri([7, 23, 45], gioco="lotto")
    carrello.render_carrello_status()
"""

from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

_CHIAVI = {"lotto": "carrello_lotto_numeri", "superenalotto": "carrello_sen_numeri"}
_GIOCHI = tuple(_CHIAVI)


def _valida_gioco(gioco: str) -> str:
    if gioco not in _GIOCHI:
        raise ValueError(f"gioco deve essere uno di {_GIOCHI}, ricevuto '{gioco}'.")
    return gioco


def _ensure_state() -> None:
    for chiave in _CHIAVI.values():
        st.session_state.setdefault(chiave, [])


def aggiungi_numeri(numeri: Iterable[int], gioco: str) -> int:
    """Unisce numeri al carrello del gioco indicato (dedup). Ritorna quanti erano nuovi."""
    gioco = _valida_gioco(gioco)
    _ensure_state()
    chiave = _CHIAVI[gioco]
    esistenti = set(st.session_state[chiave])
    nuovi = {int(n) for n in numeri if 1 <= int(n) <= 90} - esistenti
    st.session_state[chiave] = sorted(esistenti | nuovi)
    return len(nuovi)


def rimuovi_numero(numero: int, gioco: str) -> None:
    gioco = _valida_gioco(gioco)
    _ensure_state()
    chiave = _CHIAVI[gioco]
    st.session_state[chiave] = [n for n in st.session_state[chiave] if n != numero]


def svuota_carrello(gioco: Optional[str] = None) -> None:
    _ensure_state()
    for g in (_GIOCHI if gioco is None else (_valida_gioco(gioco),)):
        st.session_state[_CHIAVI[g]] = []


def get_carrello(gioco: str) -> list[int]:
    """Ritorna una COPIA del carrello: mutare il risultato non altera lo stato interno."""
    gioco = _valida_gioco(gioco)
    _ensure_state()
    return list(st.session_state[_CHIAVI[gioco]])


def render_carrello_status(with_link: bool = True) -> None:
    """Banner riusabile con il conteggio numeri per gioco, pulsante di svuotamento
    ed eventuale link diretto al Calcolatore & Sistemi (pagina 5)."""
    _ensure_state()
    n_lotto = len(st.session_state[_CHIAVI["lotto"]])
    n_sen = len(st.session_state[_CHIAVI["superenalotto"]])
    with st.container(border=True):
        col_stato, col_svuota, col_link = st.columns([2, 1, 1])
        col_stato.markdown(
            f"<span class='status-chip status-chip-blue'>🛒 Lotto: <b>{n_lotto}</b></span>&nbsp;"
            f"<span class='status-chip status-chip-green'>🛒 SuperEnalotto: <b>{n_sen}</b></span>",
            unsafe_allow_html=True,
        )
        if col_svuota.button("Svuota carrello", key="carrello_svuota_btn", disabled=(n_lotto == 0 and n_sen == 0)):
            svuota_carrello()
            st.rerun()
        if with_link:
            col_link.page_link("pages/5_🧮_Calcolatore_Sistemi.py", label="Vai al Calcolatore →", icon="🧮")
