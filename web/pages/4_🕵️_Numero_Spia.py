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
import ui_components as ui

st.set_page_config(page_title="Numero Spia — Control Room", page_icon="🕵️", layout="wide")

f = filtri.pannello_parametri(gioco_default="lotto")

ui.render_header(
    title="Analisi Frequenza Posizionale — Numero Spia",
    subtitle=(
        "Analisi empirica condizionata: individua i numeri che mostrano un'anomala attrazione "
        "ad uscire nelle H estrazioni successive alla sortita di uno specifico numero spia."
    ),
    icon="🕵️",
)
filtri.render_active_filters_banner(f)

if f.n_estrazioni is not None:
    st.caption(
        "ℹ️ **Nota di campionamento**: il preset 'Ultime N estrazioni' viene ignorato per questo "
        "modulo per garantire un campione statistico sufficientemente ampio (serve tutto lo "
        "storico). Per circoscrivere il periodo, imposta 'Range di date' nella barra laterale."
    )
RUOTE = [r for r in filtri.RUOTE_LOTTO if r != "Tutte"]

with st.container(border=True):
    c_spia, c_r_spia, c_r_tar, c_h = st.columns([1, 1, 1, 1.2])
    with c_spia:
        numero_spia = st.number_input("Numero spia (1-90)", min_value=1, max_value=90, value=1, key="spia_numero")
    with c_r_spia:
        ruota_spia = st.selectbox("Ruota della spia", RUOTE, key="spia_ruota_spia")
    with c_r_tar:
        ruota_target = st.selectbox("Ruota target da osservare", RUOTE, key="spia_ruota_target")
    with c_h:
        orizzonte_h = st.select_slider("Orizzonte H (concorsi)", options=[3, 5, 9, 12, 18], value=5, key="spia_orizzonte")

col_ball, col_btn = st.columns([1, 3])
with col_ball:
    st.markdown(
        "<div style='display: flex; align-items: center; gap: 10px; margin-top: 4px;'>"
        "<span style='color: #94a3b8; font-size: 0.85rem; font-weight: 600;'>Spia attiva:</span>"
        f"{ui.render_lotto_ball(numero_spia, variant='lotto', size='md')}"
        "</div>",
        unsafe_allow_html=True,
    )
with col_btn:
    btn_analizza = st.button("🔍 Avvia ricerca spia", type="primary")

if btn_analizza:
    try:
        risultato = api_client.analisi_spia(numero_spia, ruota_spia, ruota_target, orizzonte_h, f.data_da)
    except ValueError as exc:
        st.error(f"⚠️ {exc}")
    else:
        st.write("")
        n_occ = risultato["occorrenze_spia_utilizzate"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Occorrenze spia utili", n_occ)
        c2.metric("Attesa naturale", f"{risultato['frequenza_naturale_attesa']:.3f} uscite")
        c3.metric("Orizzonte monitorato", f"{risultato['orizzonte_h']} concorsi successivi")

        if not risultato["campione_affidabile"]:
            st.warning(
                f"⚠️ **Campione ridotto ({n_occ} sortite utili, soglia consigliata ≥ 20)**: "
                "l'Indice di Attrattiva su un campione così limitato è soggetto a elevata "
                "variabilità casuale e va interpretato con cautela."
            )
        else:
            st.success(f"✅ **Campione statistico robusto ({n_occ} occorrenze)**: dati sufficienti per una stima attendibile.")

        df = pd.DataFrame(risultato["ranking"])
        top3 = df.head(3)["numero"].tolist()
        if top3:
            st.markdown("#### 🏆 Podio numeri più attratti:")
            ui.render_balls_row(top3, variant="sen", size="lg")

        st.subheader(f"📊 Top 10 per Indice di Attrattiva (spia {numero_spia} su {ruota_spia} ➔ {ruota_target})")
        top10 = df.head(10)
        fig = px.bar(
            top10, x="numero", y="indice_attrattiva",
            labels={"numero": "Numero target", "indice_attrattiva": "Indice di Attrattiva"},
            color="indice_attrattiva", color_continuous_scale="YlOrRd",
        )
        fig.add_hline(
            y=1.0, line_dash="dash", line_color="#10B981", line_width=2,
            annotation_text="Attesa naturale casuale (1.0x)", annotation_position="top left",
        )
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(ui.apply_plotly_theme(fig, height=360), use_container_width=True)

        with st.expander("📋 Graduatoria completa dei 90 numeri", expanded=True):
            st.dataframe(
                df, use_container_width=True, hide_index=True, height=450,
                column_config={
                    "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                    "frequenza_post_spia": st.column_config.NumberColumn("Frequenza media post-spia", format="%.4f"),
                    "indice_attrattiva": st.column_config.ProgressColumn(
                        "Indice di attrattiva",
                        help="Rapporto rispetto alla frequenza naturale (1.0 = neutrale)",
                        format="%.2fx", min_value=0.0,
                        max_value=max(float(df["indice_attrattiva"].max() or 0.0), 2.0),
                    ),
                },
            )
