"""
web/pages/1_📈_Analizzatore_Somme.py
=====================================
Modulo 1: distribuzione osservata vs teorica della somma dei numeri
estratti (curva normale calcolata dalla combinatoria della popolazione
{1..90}, non un fit sui dati osservati — vedi la docstring di
analytics.istogramma_somme() nel backend) più Z-score dello scostamento
fra media osservata e media teorica.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import api_client
import carrello
import filtri
import ui_components as ui

st.set_page_config(page_title="Analizzatore Somme — Control Room", page_icon="📈", layout="wide")

f = filtri.pannello_parametri(gioco_default="lotto")

ui.render_header(
    title="Analizzatore Avanzato Somme & Distribuzioni",
    subtitle=(
        "Confronto empirico tra la somma dei numeri estratti e la curva gaussiana teorica "
        "della popolazione {1..90}, con Z-score di significatività."
    ),
    icon="📈",
)
filtri.render_active_filters_banner(f)
carrello.render_carrello_status()

POSIZIONI_LOTTO = ["Primo", "Secondo", "Terzo", "Quarto", "Quinto"]
POSIZIONI_SEN = ["n1", "n2", "n3", "n4", "n5", "n6"]
posizioni_disponibili = POSIZIONI_LOTTO if f.gioco == "lotto" else POSIZIONI_SEN

with st.container(border=True):
    col_pos, col_info = st.columns([2, 1])
    with col_pos:
        posizioni_scelte = st.multiselect(
            "Filtro posizionale estratti (vuoto = tutte le posizioni)",
            posizioni_disponibili,
            key="somme_posizioni",
            help="Permette di calcolare la somma solo su specifiche posizioni, es. solo 1° e 2° estratto.",
        )
    with col_info:
        st.caption(
            "ℹ️ **Teorema del limite centrale**: la media e la varianza teoriche considerano la "
            "correzione per popolazione finita (FPC) di estrazioni senza reinserimento."
        )

dati = api_client.analisi_somme(
    f.gioco, f.ruota, f.data_da, f.data_a, f.n_estrazioni, f.ampiezza_bin,
    tuple(posizioni_scelte) or None,
)

if dati["n_estrazioni_analizzate"] == 0:
    st.info("Nessuna estrazione nella finestra selezionata: allarga il range temporale nella barra laterale.")
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Estrazioni campionate", dati["n_estrazioni_analizzate"])
    c2.metric(
        "Media osservata", dati["media_osservata"],
        delta=round(dati["media_osservata"] - dati["media_teorica"], 2),
    )
    c3.metric("Media teorica", dati["media_teorica"])

    z = dati["z_score"] or 0.0
    c4.metric("Z-score scostamento", f"{z:+.3f}", help="(media osservata − media teorica) / dev.std. teorica")

    col_shape1, col_shape2, col_stat = st.columns([1, 1, 2])
    with col_shape1:
        c_skew = dati.get("skewness")
        st.metric("Asimmetria (skewness)", f"{c_skew:+.3f}" if c_skew is not None else "N/D")
    with col_shape2:
        c_kurt = dati.get("curtosi")
        st.metric("Curtosi d'eccesso", f"{c_kurt:+.3f}" if c_kurt is not None else "N/D")
    with col_stat:
        abs_z = abs(z)
        if abs_z >= 2.0:
            st.error(
                f"🚨 **Scostamento significativo (|Z| = {abs_z:.2f} ≥ 2.0)**: la media osservata devia di oltre "
                "2 deviazioni standard dal baricentro teorico (probabilità dell'evento fortuito < 5%)."
            )
        elif abs_z >= 1.0:
            st.warning(
                f"⚠️ **Scostamento moderato (|Z| = {abs_z:.2f})**: lieve sbilanciamento rispetto all'attesa."
            )
        else:
            st.success(
                f"✅ **Distribuzione fisiologica (|Z| = {abs_z:.2f} < 1.0)**: le estrazioni campionate sono "
                "in linea con la teoria probabilistica."
            )

    bins = dati["bins"]
    fig = go.Figure()
    fig.add_bar(
        x=[f"{b['da']}-{b['a']}" for b in bins], y=[b["conteggio_reale"] for b in bins],
        name="Distribuzione reale (istogramma)", marker_color="#F59E0B", opacity=0.85,
    )
    fig.add_scatter(
        x=[f"{b['da']}-{b['a']}" for b in bins], y=[b["conteggio_teorico"] for b in bins],
        name="Campana teorica gaussiana", mode="lines+markers",
        line=dict(color="#10B981", width=3), marker=dict(size=6, color="#10B981"),
    )
    fig.update_layout(
        title="Distribuzione empirica della somma vs curva normale teorica",
        xaxis_title="Fascia di somma", yaxis_title="Frequenza assoluta", barmode="overlay",
    )
    st.plotly_chart(ui.apply_plotly_theme(fig, height=380), use_container_width=True)

    with st.expander("📋 Tabella dettagliata per fasce di somma", expanded=True):
        st.dataframe(
            bins,
            use_container_width=True,
            hide_index=True,
            column_config={
                "da": st.column_config.NumberColumn("Da", format="%d"),
                "a": st.column_config.NumberColumn("A", format="%d"),
                "conteggio_reale": st.column_config.ProgressColumn(
                    "Uscite reali", format="%d", min_value=0,
                    max_value=max((b["conteggio_reale"] for b in bins), default=10) or 1,
                ),
                "conteggio_teorico": st.column_config.NumberColumn("Attesa teorica", format="%.2f"),
            },
        )
