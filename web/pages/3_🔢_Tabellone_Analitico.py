"""
web/pages/3_🔢_Tabellone_Analitico.py
======================================
Modulo D: matrice interattiva dei 90 numeri con categorie derivate
(vedi analytics.tabellone_lotto per la logica esatta):
  - frequente: frequenza nella finestra nel quartile più alto
  - scomparso: 0 uscite nella finestra selezionata
  - oro: ritardo attuale ha eguagliato/superato il record storico
  - ritardatario: ritardo attuale oltre l'intervallo medio atteso
  - isocrono: ritardo attuale vicino al proprio intervallo medio atteso
    (±15%) — il numero è "in orario" rispetto al proprio ritmo storico

Nota: "numero spia" (folklore del gioco: un numero che tende a
precedere l'uscita di un altro) non è incluso — richiederebbe un
algoritmo di correlazione a parte, fuori dallo scope di questo starter.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import api_client
import filtri

st.set_page_config(page_title="Tabellone Analitico — Lotto", page_icon="🔢", layout="wide")
st.title("🔢 Tabellone Analitico Dinamico Interattivo")
st.caption("Disponibile per il Lotto (singola ruota o 'Tutte').")

f = filtri.pannello_parametri(gioco_default="lotto")

righe = api_client.analisi_tabellone(f.ruota, f.data_da, f.data_a, f.n_estrazioni, f.ritardo_min, f.ritardo_max)
df = pd.DataFrame(righe)

if df.empty:
    st.info("Nessun numero nel range di ritardo selezionato: allarga il filtro nel Pannello Parametri.")
else:
    CATEGORIE = ["frequente", "ritardatario", "isocrono", "oro", "scomparso"]
    scelte = st.multiselect("Evidenzia categorie", CATEGORIE, default=CATEGORIE, key="tabellone_categorie")

    # Niente pandas.Styler qui apposta: dipende da Jinja2 a runtime (mai
    # pinnato/verificato in web/requirements.txt) e questo progetto ha già
    # avuto un crash serio da una dipendenza transitiva non pinnata (vedi
    # CLAUDE.md, numpy/pyarrow ABI mismatch) — un semplice emoji per
    # categoria evidenzia comunque a colpo d'occhio, senza quel rischio.
    EMOJI = {"oro": "🥇", "scomparso": "❌", "ritardatario": "⏳", "frequente": "🔥", "isocrono": "⏱️"}
    df_vista = df[df["categorie"].apply(lambda cs: any(c in scelte for c in cs) or not cs)]
    df_vista = df_vista.assign(
        categorie=df_vista["categorie"].apply(lambda cs: " ".join(EMOJI.get(c, c) for c in cs) or "—")
    )

    st.dataframe(df_vista, use_container_width=True, hide_index=True, height=600)

    col_csv, col_json = st.columns(2)
    col_csv.download_button(
        "⬇️ Esporta CSV", df_vista.to_csv(index=False).encode("utf-8"),
        file_name="tabellone_analitico.csv", mime="text/csv", use_container_width=True,
    )
    col_json.download_button(
        "⬇️ Esporta JSON", json.dumps(righe, indent=2, default=str).encode("utf-8"),
        file_name="tabellone_analitico.json", mime="application/json", use_container_width=True,
    )
