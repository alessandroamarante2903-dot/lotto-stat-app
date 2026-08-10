"""
web/pages/4_🕵️_Numero_Spia.py
==============================
Modulo 4: dato un numero spia su una ruota, analizza quali numeri
tendono a uscire nelle H estrazioni successive su una ruota target
(anche la stessa) più spesso di quanto ci si aspetterebbe per caso
(vedi analytics.analisi_spia per l'algoritmo esatto — conteggio
condizionato con bisect sulla timeline della ruota target, non una
semplice correlazione).

L'input è solo il numero spia: il "target" non è un numero fissato ma
tutti e 90 i numeri, ordinati per Indice di Attrattiva — è la forma più
utile del risultato (vedi specifica Modulo 4), non un singolo scalare.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import api_client
import filtri

st.set_page_config(page_title="Numero Spia — Lotto", page_icon="🕵️", layout="wide")
st.title("🕵️ Analisi di Frequenza Posizionale — Numero Spia")

f = filtri.pannello_parametri(gioco_default="lotto")
if f.n_estrazioni is not None:
    st.caption(
        "⚠️ Il preset 'Ultime N estrazioni' del Pannello Parametri non si applica a questo modulo "
        "(serve tutto lo storico per raccogliere abbastanza occorrenze dello spia): per limitare il "
        "periodo analizzato usa 'Range di date' nella sidebar."
    )
RUOTE = [r for r in filtri.RUOTE_LOTTO if r != "Tutte"]

col1, col2, col3, col4 = st.columns(4)
numero_spia = col1.number_input("Numero Spia", min_value=1, max_value=90, value=1, key="spia_numero")
ruota_spia = col2.selectbox("Ruota Spia", RUOTE, key="spia_ruota_spia")
ruota_target = col3.selectbox("Ruota Target", RUOTE, key="spia_ruota_target")
orizzonte_h = col4.select_slider("Orizzonte H", options=[3, 5, 9, 12, 18], value=5, key="spia_orizzonte")

if st.button("🔍 Analizza"):
    try:
        risultato = api_client.analisi_spia(numero_spia, ruota_spia, ruota_target, orizzonte_h, f.data_da)
    except ValueError as exc:
        st.error(str(exc))
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Occorrenze spia (campione)", risultato["occorrenze_spia_utilizzate"])
        c2.metric("Frequenza naturale attesa", risultato["frequenza_naturale_attesa"])
        c3.metric("Orizzonte H", risultato["orizzonte_h"])

        if not risultato["campione_affidabile"]:
            st.warning(
                f"Campione di sole {risultato['occorrenze_spia_utilizzate']} occorrenze utilizzabili "
                "(soglia minima consigliata: 20): l'Indice di Attrattiva su un campione così piccolo "
                "è statisticamente poco affidabile, va letto con cautela."
            )

        df = pd.DataFrame(risultato["ranking"])
        top10 = df.head(10)
        st.plotly_chart(
            px.bar(
                top10, x="numero", y="indice_attrattiva",
                title=f"Top 10 numeri per Indice di Attrattiva (spia {numero_spia} su {ruota_spia} → {ruota_target}, H={orizzonte_h})",
                labels={"numero": "Numero target", "indice_attrattiva": "Indice di Attrattiva"},
            ).add_hline(y=1.0, line_dash="dash", annotation_text="attesa naturale (1.0)"),
            use_container_width=True,
        )

        st.subheader("Ranking completo")
        st.dataframe(df, use_container_width=True, hide_index=True, height=500)
