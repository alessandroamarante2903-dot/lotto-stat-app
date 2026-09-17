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
import carrello
import filtri
import ui_components as ui

st.set_page_config(page_title="Simulatore & Backtest — Control Room", page_icon="🎲", layout="wide")

f = filtri.pannello_parametri(gioco_default="lotto")

ui.render_header(
    title="Simulatore & Backtesting Combinazioni",
    subtitle=(
        "Verifica l'efficacia storica reale di qualunque combinazione del Lotto contro il "
        "modello ipergeometrico teorico esatto."
    ),
    icon="🎲",
)
filtri.render_active_filters_banner(f)
carrello.render_carrello_status()

ruota_backtest = f.ruota if f.ruota != "Tutte" else "Napoli"
if f.ruota == "Tutte":
    st.info(
        "ℹ️ Il backtest richiede una ruota specifica: imposta una singola ruota nella barra "
        "laterale (impostata provvisoriamente: **Napoli**)."
    )

SORTE_K = {"Estratto": 1, "Ambo": 2, "Terno": 3, "Quaterna": 4, "Cinquina": 5}

with st.container(border=True):
    col_sorte, col_num = st.columns([1, 2])
    with col_sorte:
        sorte = st.selectbox("Sorte target", list(SORTE_K), index=1, key="backtest_sorte")
        k = SORTE_K[sorte]
    with col_num:
        numeri = st.multiselect(
            f"Numeri da simulare (esattamente {k} per '{sorte}')",
            options=list(range(1, 91)),
            max_selections=k, key="backtest_numeri",
        )
    if numeri:
        ui.render_balls_row(numeri, variant="lotto", title=f"Combinazione in analisi ({len(numeri)}/{k} numeri scelti):")

if st.button("🔎 Esegui backtest storico", disabled=(f.ruota == "Tutte" or len(numeri) != k), type="primary"):
    try:
        risultato = api_client.analisi_backtest(numeri, ruota_backtest, sorte, f.data_da)
    except ValueError as exc:
        st.session_state["backtest_risultato"] = None
        st.error(f"⚠️ {exc}")
    else:
        st.session_state["backtest_risultato"] = risultato
        st.session_state["backtest_numeri_usati"] = numeri
        st.session_state["backtest_sorte_usata"] = sorte

risultato = st.session_state.get("backtest_risultato")
if risultato is not None:
    numeri_usati = st.session_state["backtest_numeri_usati"]
    sorte_usata = st.session_state["backtest_sorte_usata"]
    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Frequenza reale", risultato["frequenza"])
    c2.metric("Frequenza teorica", risultato["frequenza_teorica"])

    y_val = risultato["yield"]
    delta_str = f"{(y_val - 1.0) * 100:+.1f}%" if y_val is not None else None
    c3.metric("Yield (reale/teorico)", f"{y_val:.3f}" if y_val is not None else "N/D", delta=delta_str,
              help="Frequenza reale / teorica. >1 = over-performance, <1 = under.")
    c4.metric("Max drawdown", f"{risultato['max_drawdown']} estr.",
              help="Massimo numero di estrazioni consecutive senza uscite.")

    col_badge, col_dett = st.columns([1, 2])
    with col_badge:
        if y_val is not None:
            if y_val >= 1.15:
                st.success(f"🔥 **Over-performance storica ({y_val:.2f}x)**: la combinazione ha premiato più del previsto.")
            elif y_val <= 0.85:
                st.warning(f"❄️ **Under-performance storica ({y_val:.2f}x)**: la combinazione è uscita meno della sua probabilità naturale.")
            else:
                st.info(f"⚖️ **Equilibrio fisiologico ({y_val:.2f}x)**: comportamento allineato alle probabilità del gioco.")
    with col_dett:
        st.caption(
            f"Combinazione {risultato['numeri']} ({sorte_usata}) su ruota {risultato['ruota']}, "
            f"{risultato['n_estrazioni_analizzate']} estrazioni analizzate. "
            f"Ritardo attuale: {risultato['ritardo_attuale']}."
        )

    if st.button("➕ Aggiungi questa combinazione al carrello Lotto", key="backtest_add_carrello"):
        n = carrello.aggiungi_numeri(numeri_usati, gioco="lotto")
        st.toast(f"Aggiunti {n} numeri al carrello Lotto.")

    col_g1, col_g2 = st.columns(2)
    with col_g1:
        fig_yield = go.Figure(go.Bar(
            x=["Frequenza reale", "Attesa teorica"],
            y=[risultato["frequenza"], risultato["frequenza_teorica"]],
            marker_color=["#F59E0B", "#64748B"],
            text=[str(risultato["frequenza"]), f"{risultato['frequenza_teorica']:.1f}"],
            textposition="auto",
        ))
        fig_yield.update_layout(title="Confronto frequenza reale vs teorica")
        st.plotly_chart(ui.apply_plotly_theme(fig_yield, height=330), use_container_width=True)

    if risultato["date_uscite"]:
        df_date = {"data_estrazione": risultato["date_uscite"]}

        if risultato["distribuzione_intervalli"]:
            with col_g2:
                c5, c6 = st.columns(2)
                c5.metric("Intervallo medio", f"{risultato['intervallo_medio']} estr.")
                c6.metric("Deviazione standard", f"{risultato['intervallo_dev_std']}")
                fig_hist = px.histogram(
                    x=risultato["distribuzione_intervalli"], nbins=18,
                    labels={"x": "Estrazioni fra un'uscita e la successiva"},
                    title="Distribuzione degli intervalli fra le uscite",
                    color_discrete_sequence=["#10B981"],
                )
                st.plotly_chart(ui.apply_plotly_theme(fig_hist, height=330), use_container_width=True)

        with st.expander(f"📅 Elenco cronologico delle {len(risultato['date_uscite'])} uscite storiche"):
            st.plotly_chart(
                px.scatter(df_date, x="data_estrazione", y=[1] * len(risultato["date_uscite"]),
                           title="Uscite storiche della combinazione").update_yaxes(visible=False),
                use_container_width=True,
            )
            st.dataframe(df_date, use_container_width=True, hide_index=True)
    else:
        st.warning("⚠️ Questa combinazione non è mai uscita nella finestra storica analizzata.")
