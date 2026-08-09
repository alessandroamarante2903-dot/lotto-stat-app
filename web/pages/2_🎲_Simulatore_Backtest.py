"""
web/pages/2_🎲_Simulatore_Backtest.py
======================================
Modulo C: l'utente inserisce una combinazione (1-5 numeri per una
ruota del Lotto) e vede il suo andamento storico reale — frequenza di
uscita, date, ritardo attuale e ritardo massimo mai raggiunto.
Combinazione libera, non necessariamente un ambo/terno già in cache:
la query cerca contenimento dell'insieme richiesto in ogni estrazione
storica (vedi analytics.backtest_combinazione_lotto).
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

import api_client
import filtri

st.set_page_config(page_title="Simulatore & Backtest — Lotto", page_icon="🎲", layout="wide")
st.title("🎲 Simulatore e Backtesting Combinazioni")
st.caption("Al momento disponibile solo per il Lotto (una ruota alla volta).")

f = filtri.pannello_parametri(gioco_default="lotto")
ruota_backtest = f.ruota if f.ruota != "Tutte" else "Napoli"
if f.ruota == "Tutte":
    st.info("Il backtest richiede una ruota specifica: seleziona una ruota nel Pannello Parametri (non 'Tutte').")

numeri = st.multiselect("Numeri da simulare (1-5)", options=list(range(1, 91)), max_selections=5, key="backtest_numeri")

if st.button("🔎 Simula", disabled=(f.ruota == "Tutte" or not numeri)):
    try:
        risultato = api_client.analisi_backtest(numeri, ruota_backtest, f.data_da)
    except ValueError as exc:
        st.error(str(exc))
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Frequenza storica", risultato["frequenza"])
        c2.metric("Ritardo attuale", risultato["ritardo_attuale"])
        c3.metric("Ritardo massimo storico", risultato["ritardo_massimo_storico"])

        st.caption(
            f"Combinazione {risultato['numeri']} su ruota {risultato['ruota']}, "
            f"{risultato['n_estrazioni_analizzate']} estrazioni analizzate."
        )

        if risultato["date_uscite"]:
            df_date = {"data_estrazione": risultato["date_uscite"]}
            st.plotly_chart(
                px.scatter(df_date, x="data_estrazione", y=[1] * len(risultato["date_uscite"]),
                           title="Uscite storiche della combinazione").update_yaxes(visible=False),
                use_container_width=True,
            )
            st.dataframe(df_date, use_container_width=True, hide_index=True)
        else:
            st.warning("Questa combinazione non è mai uscita nella finestra storica analizzata.")
