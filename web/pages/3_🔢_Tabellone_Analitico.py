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
import carrello
import filtri
import ui_components as ui

st.set_page_config(page_title="Tabellone Analitico — Control Room", page_icon="🔢", layout="wide")

f = filtri.pannello_parametri(gioco_default="lotto")

ui.render_header(
    title="Tabellone Analitico Dinamico Interattivo",
    subtitle=(
        "Matrice termica 1-90 con Indice di Ritardo Relativo (IRR), categorie cicliche "
        "e matrice di isocronia inter-ruota."
    ),
    icon="🔢",
)
filtri.render_active_filters_banner(f)
carrello.render_carrello_status()

st.markdown(
    """
    <div style='display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px;'>
        <span class='status-chip status-chip-amber'>🥇 <b>Oro:</b> record storico eguagliato/superato</span>
        <span class='status-chip status-chip-red'>🚨 <b>Iper-ritardatario:</b> IRR ≥ 0.80</span>
        <span class='status-chip status-chip-amber'>⏳ <b>Ritardatario:</b> oltre intervallo atteso</span>
        <span class='status-chip status-chip-green'>🔥 <b>Frequente:</b> quartile alto estrazioni</span>
        <span class='status-chip status-chip-blue'>⏱️ <b>Isocrono:</b> in orario (±15% ritmo naturale)</span>
        <span class='status-chip status-chip-blue'>❌ <b>Vergine:</b> 0 uscite nel periodo</span>
    </div>
    """,
    unsafe_allow_html=True,
)

ruota_tabellone = f.ruota if f.ruota != "Tutte" else "Napoli"
if f.ruota == "Tutte":
    st.info(
        "ℹ️ L'IRR (Indice di Ritardo Relativo) richiede una ruota specifica: imposta una singola "
        "ruota nella barra laterale (impostata provvisoriamente: **Napoli**)."
    )

with st.container(border=True):
    col_stato, col_k = st.columns([3, 1])
    with col_stato:
        stato = st.radio(
            "Filtra per categoria speciale", ["Tutti", "Vergine", "Frequente", "Iper-ritardatario"],
            horizontal=True, key="tabellone_stato",
        )
    with col_k:
        passo_k = st.number_input("K (passo del ritardo)", min_value=2, max_value=20, value=5, key="tabellone_passo_k")

righe = api_client.analisi_tabellone(
    ruota_tabellone, f.data_da, f.data_a, f.n_estrazioni, f.ritardo_min, f.ritardo_max, stato, passo_k,
)
df = pd.DataFrame(righe)

if df.empty:
    st.info("Nessun numero corrisponde ai filtri selezionati: prova ad ampliare il range o seleziona 'Tutti'.")
else:
    st.subheader("🔥 Heatmap IRR (Indice di Ritardo Relativo)")
    st.caption(
        "Scala normalizzata: **0.0** = appena uscito (verde) | **0.8+** = allerta iper-ritardatario "
        "(arancio) | **1.0** = record battuto (rosso)."
    )

    irr_per_numero = {r["numero"]: r["irr"] for r in righe}
    ritardo_per_numero = {r["numero"]: r["ritardo_attuale"] for r in righe}
    z = [[irr_per_numero.get(riga * 10 + col + 1) for col in range(10)] for riga in range(9)]
    testo = [[f"{riga * 10 + col + 1:02d}" for col in range(10)] for riga in range(9)]
    hover = [
        [
            f"<b>Numero {riga*10+col+1:02d}</b><br>"
            f"IRR: {irr_per_numero.get(riga*10+col+1, 'N/D')}<br>"
            f"Ritardo attuale: {ritardo_per_numero.get(riga*10+col+1, 'N/D')} estr."
            for col in range(10)
        ]
        for riga in range(9)
    ]

    custom_colorscale = [
        [0.0, "#059669"],
        [0.4, "#3B82F6"],
        [0.7, "#F59E0B"],
        [0.85, "#DC2626"],
        [1.0, "#7F1D1D"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z, text=testo, texttemplate="<b>%{text}</b>", hovertext=hover, hoverinfo="text",
        colorscale=custom_colorscale, zmin=0, zmax=1, showscale=True,
        colorbar=dict(title="IRR", thickness=14),
    ))
    fig.update_layout(
        yaxis=dict(autorange="reversed", visible=False), xaxis=dict(visible=False),
        margin=dict(l=10, r=10, t=20, b=10),
    )
    st.plotly_chart(ui.apply_plotly_theme(fig, height=400), use_container_width=True)

    with st.expander("🔗 Matrice di isocronia inter-ruota (numeri con ritardo identico su più ruote)"):
        iso = api_client.analisi_isocronia()
        gruppi = sorted(iso["gruppi_isocroni"], key=lambda g: len(g["ruote"]), reverse=True)
        if not gruppi:
            st.info("Nessuna sincronia esatta riscontrata tra coppie di ruote per lo stesso numero al momento.")
        else:
            df_iso = pd.DataFrame(gruppi).assign(ruote=lambda d: d["ruote"].apply(", ".join))
            st.dataframe(
                df_iso, use_container_width=True, hide_index=True,
                column_config={
                    "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                    "ritardo": st.column_config.NumberColumn("Ritardo coincidente", format="%d estr."),
                    "ruote": st.column_config.TextColumn("Ruote sincrone"),
                },
            )

    st.subheader("📋 Prospetto analitico completo 1-90")
    EMOJI = {
        "oro": "🥇 Oro", "vergine": "❌ Vergine", "ritardatario": "⏳ Ritardo",
        "frequente": "🔥 Freq", "isocrono": "⏱️ Isocrono", "iper-ritardatario": "🚨 Iper-Rit",
    }
    df_vista = df.assign(categorie=df["categorie"].apply(lambda cs: " • ".join(EMOJI.get(c, c) for c in cs) or "—"))
    max_freq = int(df["frequenza_finestra"].max() or 0)

    st.dataframe(
        df_vista[["numero", "frequenza_finestra", "ritardo_attuale", "ritardo_storico_max", "irr", "passo_ritardo", "categorie"]],
        use_container_width=True, hide_index=True, height=420,
        column_config={
            "numero": st.column_config.NumberColumn("Numero", format="%02d"),
            "frequenza_finestra": st.column_config.ProgressColumn(
                "Frequenza finestra", format="%d", min_value=0, max_value=max(max_freq, 1),
            ),
            "ritardo_attuale": st.column_config.NumberColumn("Ritardo attuale", format="%d"),
            "ritardo_storico_max": st.column_config.NumberColumn("Max storico", format="%d"),
            "irr": st.column_config.NumberColumn("IRR", format="%.3f"),
            "passo_ritardo": st.column_config.NumberColumn("Passo ritardo", format="%.1f"),
            "categorie": st.column_config.TextColumn("Stati & categorie"),
        },
    )

    st.write("")
    st.subheader("🛒 Aggiungi al carrello")
    st.caption("Aggiunge i numeri Lotto della categoria (o della selezione manuale) al carrello condiviso, da usare nel Calcolatore & Sistemi.")
    categorie_utili = ["oro", "iper-ritardatario", "ritardatario", "frequente"]
    cols_cat = st.columns(len(categorie_utili))
    for col, cat in zip(cols_cat, categorie_utili):
        sottoinsieme = df[df["categorie"].apply(lambda cs, c=cat: c in cs)]["numero"].tolist()
        if col.button(f"➕ {EMOJI.get(cat, cat)} ({len(sottoinsieme)})", key=f"add_cat_{cat}", disabled=not sottoinsieme):
            n = carrello.aggiungi_numeri(sottoinsieme, gioco="lotto")
            st.toast(f"Aggiunti {n} numeri al carrello Lotto.")

    col_sel, col_add = st.columns([3, 1])
    with col_sel:
        selezione_manuale = st.multiselect(
            "Oppure seleziona manualmente numeri dal prospetto", options=df["numero"].tolist(),
            key="tabellone_selezione_manuale",
        )
    with col_add:
        st.write("")
        st.write("")
        if st.button("➕ Aggiungi selezionati", key="add_selezione_manuale", disabled=not selezione_manuale):
            n = carrello.aggiungi_numeri(selezione_manuale, gioco="lotto")
            st.toast(f"Aggiunti {n} numeri al carrello Lotto.")

    col_csv, col_json = st.columns(2)
    with col_csv:
        st.download_button(
            "⬇️ Esporta CSV", df_vista.to_csv(index=False).encode("utf-8"),
            file_name=f"tabellone_analitico_{f.ruota}.csv", mime="text/csv", use_container_width=True,
        )
    with col_json:
        st.download_button(
            "⬇️ Esporta JSON", json.dumps(righe, indent=2, default=str).encode("utf-8"),
            file_name=f"tabellone_analitico_{f.ruota}.json", mime="application/json", use_container_width=True,
        )
