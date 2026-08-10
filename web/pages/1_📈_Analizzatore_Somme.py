"""
web/pages/1_📈_Analizzatore_Somme.py
=====================================
Modulo B della Piattaforma di Controllo: distribuzione reale della
somma dei numeri estratti vs curva normale teorica (calcolata dalla
combinatoria della popolazione {1..90}, non un fit sui dati osservati
— vedi la docstring di analytics.istogramma_somme() nel backend) più
Z-score dello scostamento fra media osservata e media teorica.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import api_client
import filtri

st.set_page_config(page_title="Analizzatore Somme — Lotto & SuperEnalotto", page_icon="📈", layout="wide")
st.title("📈 Analizzatore Avanzato Somme e Distribuzioni")

f = filtri.pannello_parametri(gioco_default="lotto")

POSIZIONI_LOTTO = ["Primo", "Secondo", "Terzo", "Quarto", "Quinto"]
POSIZIONI_SEN = ["n1", "n2", "n3", "n4", "n5", "n6"]
posizioni_disponibili = POSIZIONI_LOTTO if f.gioco == "lotto" else POSIZIONI_SEN
posizioni_scelte = st.multiselect(
    "Filtro posizione (vuoto = tutte)", posizioni_disponibili, key="somme_posizioni",
    help="Somma calcolata solo sulle posizioni selezionate, es. solo 1° e 2° estratto.",
)

dati = api_client.analisi_somme(
    f.gioco, f.ruota, f.data_da, f.data_a, f.n_estrazioni, f.ampiezza_bin,
    tuple(posizioni_scelte) or None,
)

if dati["n_estrazioni_analizzate"] == 0:
    st.info("Nessuna estrazione nella finestra selezionata: allarga il range di date o il numero di estrazioni.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Estrazioni analizzate", dati["n_estrazioni_analizzate"])
    c2.metric("Media osservata", dati["media_osservata"], delta=round(dati["media_osservata"] - dati["media_teorica"], 2))
    c3.metric("Media teorica", dati["media_teorica"])
    c4.metric("Z-score", dati["z_score"], help="(media osservata − media teorica) / dev.std. teorica")

    c5, c6 = st.columns(2)
    c5.metric("Skewness", dati["skewness"], help="0 = simmetrica; >0 coda a destra, <0 coda a sinistra.")
    c6.metric("Curtosi (eccesso)", dati["curtosi"], help="0 = normale; >0 code più pesanti, <0 più piatta.")

    if abs(dati["z_score"]) >= 2:
        st.warning(
            f"Scostamento statisticamente rilevante: |Z| = {abs(dati['z_score'])} ≥ 2 "
            "(oltre ~2 deviazioni standard dalla media teorica)."
        )

    bins = dati["bins"]
    fig = go.Figure()
    fig.add_bar(
        x=[f"{b['da']}-{b['a']}" for b in bins], y=[b["conteggio_reale"] for b in bins],
        name="Distribuzione reale",
    )
    fig.add_scatter(
        x=[f"{b['da']}-{b['a']}" for b in bins], y=[b["conteggio_teorico"] for b in bins],
        name="Curva teorica (normale)", mode="lines+markers", line=dict(color="crimson"),
    )
    fig.update_layout(
        title="Distribuzione reale vs teorica della somma", xaxis_title="Fascia somma",
        yaxis_title="Conteggio", barmode="overlay",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(bins, use_container_width=True, hide_index=True)
