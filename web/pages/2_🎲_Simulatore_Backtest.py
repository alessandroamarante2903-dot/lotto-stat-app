"""
web/pages/2_🎲_Simulatore_Backtest.py
======================================
Modulo 3: l'utente sceglie una sorte (Estratto..Cinquina) e una
combinazione della lunghezza richiesta su una ruota del Lotto, e vede
il suo andamento storico reale contro il valore atteso teorico
(ipergeometrica esatta, vedi analytics._probabilita_sorte — non
un'approssimazione): frequenza reale vs teorica (Yield), distribuzione
degli intervalli fra le uscite, Max Drawdown (= ritardo massimo
storico: stesso numero, nome diverso per la specifica del Modulo 3).
"""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
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

SORTE_K = {"Estratto": 1, "Ambo": 2, "Terno": 3, "Quaterna": 4, "Cinquina": 5}
sorte = st.selectbox("Sorte target", list(SORTE_K), index=1, key="backtest_sorte")
k = SORTE_K[sorte]
numeri = st.multiselect(
    f"Numeri da simulare (esattamente {k} per '{sorte}')", options=list(range(1, 91)),
    max_selections=k, key="backtest_numeri",
)

if st.button("🔎 Simula", disabled=(f.ruota == "Tutte" or len(numeri) != k)):
    try:
        risultato = api_client.analisi_backtest(numeri, ruota_backtest, sorte, f.data_da)
    except ValueError as exc:
        st.error(str(exc))
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Frequenza reale", risultato["frequenza"])
        c2.metric("Frequenza teorica", risultato["frequenza_teorica"])
        c3.metric("Yield", risultato["yield"], help="Frequenza reale / teorica. >1 = over-performance, <1 = under.")
        c4.metric("Max Drawdown", risultato["max_drawdown"], help="Massimo numero di estrazioni consecutive senza uscite.")

        st.caption(
            f"Combinazione {risultato['numeri']} ({sorte}) su ruota {risultato['ruota']}, "
            f"{risultato['n_estrazioni_analizzate']} estrazioni analizzate. "
            f"Ritardo attuale: {risultato['ritardo_attuale']}."
        )

        fig_yield = go.Figure(go.Bar(
            x=["Reale", "Teorica"], y=[risultato["frequenza"], risultato["frequenza_teorica"]],
            marker_color=["#1f77b4", "#888"],
        ))
        fig_yield.update_layout(title="Frequenza reale vs teorica", height=300)
        st.plotly_chart(fig_yield, use_container_width=True)

        if risultato["date_uscite"]:
            df_date = {"data_estrazione": risultato["date_uscite"]}
            st.plotly_chart(
                px.scatter(df_date, x="data_estrazione", y=[1] * len(risultato["date_uscite"]),
                           title="Uscite storiche della combinazione").update_yaxes(visible=False),
                use_container_width=True,
            )

            if risultato["distribuzione_intervalli"]:
                c5, c6 = st.columns(2)
                c5.metric("Intervallo medio (estrazioni)", risultato["intervallo_medio"])
                c6.metric("Scarto quadratico medio", risultato["intervallo_dev_std"])
                st.plotly_chart(
                    px.histogram(
                        x=risultato["distribuzione_intervalli"], nbins=20,
                        labels={"x": "Estrazioni fra un'uscita e la successiva"},
                        title="Distribuzione degli intervalli fra le uscite",
                    ),
                    use_container_width=True,
                )
            st.dataframe(df_date, use_container_width=True, hide_index=True)
        else:
            st.warning("Questa combinazione non è mai uscita nella finestra storica analizzata.")
