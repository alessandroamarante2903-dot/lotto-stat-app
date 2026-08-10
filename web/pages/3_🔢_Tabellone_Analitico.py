"""
web/pages/3_🔢_Tabellone_Analitico.py
======================================
Modulo 2: matrice interattiva dei 90 numeri con categorie derivate
(vedi analytics.tabellone_lotto per la logica esatta):
  - frequente: frequenza nella finestra nel quartile più alto
  - vergine: 0 uscite nella finestra selezionata
  - oro: ritardo attuale ha eguagliato/superato il record storico
  - ritardatario: ritardo attuale oltre l'intervallo medio atteso
  - isocrono: ritardo attuale vicino al proprio intervallo medio atteso
    (±15%) — il numero è "in orario" rispetto al proprio ritmo storico
  - iper-ritardatario: IRR ≥ 0.8 (vicino/oltre il proprio record storico)

Vista primaria = heatmap IRR (Indice di Ritardo Relativo, non ritardo
grezzo: l'IRR è normalizzato sul record storico di ciascun numero,
quindi comparabile su un'unica scala colore fra numeri diversi — il
ritardo grezzo no, vedi specifica Modulo 2). Isocronia inter-ruota in
sezione separata. "Numero spia" non è qui: è il Modulo 4 dedicato.
"""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import api_client
import filtri

st.set_page_config(page_title="Tabellone Analitico — Lotto", page_icon="🔢", layout="wide")
st.title("🔢 Tabellone Analitico Dinamico Interattivo")
st.caption("Disponibile per il Lotto (singola ruota o 'Tutte').")

f = filtri.pannello_parametri(gioco_default="lotto")

col_stato, col_k = st.columns([2, 1])
stato = col_stato.radio(
    "Filtro Stato Numero", ["Tutti", "Vergine", "Frequente", "Iper-ritardatario"],
    horizontal=True, key="tabellone_stato",
)
passo_k = col_k.number_input("K (passo del ritardo)", min_value=2, max_value=20, value=5, key="tabellone_passo_k")

righe = api_client.analisi_tabellone(
    f.ruota, f.data_da, f.data_a, f.n_estrazioni, f.ritardo_min, f.ritardo_max, stato, passo_k,
)
df = pd.DataFrame(righe)

if df.empty:
    st.info("Nessun numero corrisponde ai filtri selezionati: allarga range di ritardo o stato.")
else:
    st.subheader("Heatmap IRR (Indice di Ritardo Relativo)")
    st.caption("IRR = ritardo attuale / ritardo massimo storico. 1.0 = record eguagliato/superato.")

    irr_per_numero = {r["numero"]: r["irr"] for r in righe}
    ritardo_per_numero = {r["numero"]: r["ritardo_attuale"] for r in righe}
    z = [[irr_per_numero.get(riga * 10 + col + 1) for col in range(10)] for riga in range(9)]
    testo = [[str(riga * 10 + col + 1) for col in range(10)] for riga in range(9)]
    hover = [
        [f"Numero {riga*10+col+1}<br>IRR: {irr_per_numero.get(riga*10+col+1)}<br>Ritardo: {ritardo_per_numero.get(riga*10+col+1)}"
         for col in range(10)]
        for riga in range(9)
    ]
    fig = go.Figure(go.Heatmap(
        z=z, text=testo, texttemplate="%{text}", hovertext=hover, hoverinfo="text",
        colorscale="RdYlGn_r", zmin=0, zmax=1, showscale=True,
    ))
    fig.update_layout(yaxis=dict(autorange="reversed", visible=False), xaxis=dict(visible=False), height=420)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("🔗 Matrice di Isocronia inter-ruota (stesso ritardo su più ruote)"):
        iso = api_client.analisi_isocronia()
        gruppi = sorted(iso["gruppi_isocroni"], key=lambda g: len(g["ruote"]), reverse=True)
        if not gruppi:
            st.info("Nessuna coppia di ruote condivide lo stesso ritardo esatto per uno stesso numero, al momento.")
        else:
            st.dataframe(
                pd.DataFrame(gruppi).assign(ruote=lambda d: d["ruote"].apply(", ".join)),
                use_container_width=True, hide_index=True,
            )

    st.subheader("Dettaglio numeri")
    EMOJI = {"oro": "🥇", "vergine": "❌", "ritardatario": "⏳", "frequente": "🔥", "isocrono": "⏱️", "iper-ritardatario": "🚨"}
    df_vista = df.assign(categorie=df["categorie"].apply(lambda cs: " ".join(EMOJI.get(c, c) for c in cs) or "—"))
    st.dataframe(
        df_vista[["numero", "frequenza_finestra", "ritardo_attuale", "ritardo_storico_max", "irr", "passo_ritardo", "categorie"]],
        use_container_width=True, hide_index=True, height=400,
    )

    col_csv, col_json = st.columns(2)
    col_csv.download_button(
        "⬇️ Esporta CSV", df_vista.to_csv(index=False).encode("utf-8"),
        file_name="tabellone_analitico.csv", mime="text/csv", use_container_width=True,
    )
    col_json.download_button(
        "⬇️ Esporta JSON", json.dumps(righe, indent=2, default=str).encode("utf-8"),
        file_name="tabellone_analitico.json", mime="application/json", use_container_width=True,
    )
