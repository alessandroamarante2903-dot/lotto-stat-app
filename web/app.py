"""
web/app.py
==========

Frontend Streamlit di lotto-stat-app (container lotto_stat_web):
  - Tab "Statistiche Live": ritardatari, frequenze e ambi da Lotto e
    SuperEnalotto, con grafici e tabelle a barre di avanzamento.
  - Tab "Gestione & Scraper": stato archivio + trigger on-demand della pipeline
    di scraping (scraper/update_pipeline.py), eseguita come sottoprocesso
    Python nello stesso container (nessun bisogno di un container scraper
    separato: backend/ e scraper/ sono montati anche qui, vedi podman-compose.yml).

Il Calcolatore & Sistemi è una pagina dedicata (web/pages/5_🧮_Calcolatore_Sistemi.py),
non più un tab qui: deve essere raggiungibile via page_link dalle pagine di
analisi (carrello numeri condiviso, vedi web/carrello.py).

Tutta la presentazione (CSS, testata, palline, tema dei grafici, navigazione
verso i moduli parametrici) sta in web/ui_components.py, condivisa con le
pagine sotto web/pages/.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

import api_client
import calcolo_costi as costi
import carrello
import db
import ui_components as ui

# Deve restare la PRIMA chiamata Streamlit del file.
st.set_page_config(
    page_title="Lotto & SuperEnalotto — Control Room",
    page_icon="🎱",
    layout="wide",
    initial_sidebar_state="expanded",
)

UPDATE_PIPELINE_PATH = Path(__file__).resolve().parent / "scraper" / "update_pipeline.py"
# Misurato in pratica su storico reale (Lotto dal 1939 + SuperEnalotto):
# CALL sp_refresh_tutte_le_cache() da solo impiega ~4 minuti (237s), prima
# ancora di considerare lo scraping stesso. 180s andava in timeout anche a
# refresh riuscito. Margine ampio perché la durata cresce con lo storico.
TIMEOUT_PIPELINE_SECONDI = 600

# Unica query di stato archivio, usata sia per i badge di testata sia per il
# riepilogo del tab "Gestione & Scraper" (db.query_df è cachata: una sola
# andata al DB per rerun).
SQL_STATO_ARCHIVIO = """
    SELECT
        (SELECT MAX(data_estrazione) FROM estrazioni_lotto) AS ultima_estrazione_lotto,
        (SELECT COUNT(*) FROM estrazioni_lotto) AS righe_lotto,
        (SELECT MAX(data_estrazione) FROM estrazioni_superenalotto) AS ultima_estrazione_superenalotto,
        (SELECT COUNT(*) FROM estrazioni_superenalotto) AS righe_superenalotto
"""

ui.inject_custom_css()

df_stato = db.query_df(SQL_STATO_ARCHIVIO)
for _colonna_data in ("ultima_estrazione_lotto", "ultima_estrazione_superenalotto"):
    if _colonna_data in df_stato.columns:
        df_stato[_colonna_data] = df_stato[_colonna_data].map(lambda v: str(v) if v is not None else None)

badges_testata: list[tuple[str, str]] = []
if not df_stato.empty:
    riga_stato = df_stato.iloc[0]
    if riga_stato["ultima_estrazione_lotto"] is not None:
        badges_testata.append((f"Lotto: {riga_stato['ultima_estrazione_lotto']}", "amber"))
    if riga_stato["ultima_estrazione_superenalotto"] is not None:
        badges_testata.append((f"SuperEnalotto: {riga_stato['ultima_estrazione_superenalotto']}", "green"))
    badges_testata.append(("DB: connesso", "blue"))

ui.render_header(
    title="Lotto & SuperEnalotto — Control Room Statistica",
    subtitle=(
        "Dashboard statistica, combinatoria di gioco, sistemi ridotti e "
        "monitoraggio dell'archivio in tempo reale."
    ),
    icon="🎱",
    badges=badges_testata,
)

ui.render_quick_nav()
st.write("")
carrello.render_carrello_status()

tab_statistiche, tab_scraper = st.tabs(
    ["📊 Statistiche Live", "⚙️ Gestione & Scraper"]
)

# =====================================================================
# TAB 1 — STATISTICHE LIVE
# =====================================================================
with tab_statistiche:
    col_gioco, _ = st.columns([1, 2])
    with col_gioco:
        gioco = st.radio(
            "Seleziona gioco", ["Lotto", "SuperEnalotto"],
            horizontal=True, key="gioco_statistiche",
        )

    if gioco == "Lotto":
        with st.container(border=True):
            col_sel, col_top = st.columns([1, 2])
            with col_sel:
                ruota = st.selectbox("Ruota di estrazione", list(costi.RUOTE_LOTTO), key="ruota_statistiche")
            with col_top:
                top_n = st.slider("Quanti numeri mostrare", 5, 90, 10, key="topn_lotto")

        col_rit, col_frq = st.columns(2)

        with col_rit:
            st.subheader(f"⏳ Top {top_n} ritardatari — {ruota}")
            df_ritardo = db.query_df(
                """
                SELECT numero, ritardo_attuale, ritardo_storico_max, indice_convenienza
                FROM cache_lotto_ritardo
                WHERE ruota = %s
                ORDER BY ritardo_attuale DESC
                LIMIT %s
                """,
                (ruota, top_n),
            )
            if df_ritardo.empty:
                st.info(
                    "Nessun dato disponibile: verifica che lo storico sia stato importato e che la cache "
                    "ritardi sia stata popolata (Tab 'Gestione & Scraper' → refresh cache)."
                )
            else:
                fig_rit = px.bar(
                    df_ritardo, x="numero", y="ritardo_attuale",
                    hover_data=["ritardo_storico_max", "indice_convenienza"],
                    labels={"numero": "Numero", "ritardo_attuale": "Ritardo (estrazioni)"},
                    color="ritardo_attuale", color_continuous_scale="YlOrRd",
                )
                fig_rit.update_coloraxes(showscale=False)
                st.plotly_chart(ui.apply_plotly_theme(fig_rit, height=320), use_container_width=True)

                max_rit = int(df_ritardo["ritardo_attuale"].max() or 0)
                st.dataframe(
                    df_ritardo,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                        "ritardo_attuale": st.column_config.ProgressColumn(
                            "Ritardo attuale",
                            help="Estrazioni consecutive di assenza (mai giorni di calendario)",
                            format="%d estr.",
                            min_value=0,
                            max_value=max(max_rit, 1),
                        ),
                        "ritardo_storico_max": st.column_config.NumberColumn("Max storico", format="%d estr."),
                        "indice_convenienza": st.column_config.NumberColumn("Indice conv.", format="%.2f"),
                    },
                )

        with col_frq:
            st.subheader(f"🔥 Top {top_n} più frequenti — {ruota}")
            df_freq = db.query_df(
                """
                SELECT numero, frequenza, frequenza_relativa
                FROM cache_lotto_frequenza
                WHERE ruota = %s
                ORDER BY frequenza DESC
                LIMIT %s
                """,
                (ruota, top_n),
            )
            if df_freq.empty:
                st.info("Cache frequenze non ancora popolata: lancia il refresh dal tab 'Gestione & Scraper'.")
            else:
                fig_frq = px.bar(
                    df_freq, x="numero", y="frequenza",
                    labels={"numero": "Numero", "frequenza": "Uscite totali"},
                    color="frequenza", color_continuous_scale="Teal",
                )
                fig_frq.update_coloraxes(showscale=False)
                st.plotly_chart(ui.apply_plotly_theme(fig_frq, height=320), use_container_width=True)

                max_frq = int(df_freq["frequenza"].max() or 0)
                st.dataframe(
                    df_freq,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                        "frequenza": st.column_config.ProgressColumn(
                            "Frequenza assoluta",
                            format="%d uscite",
                            min_value=0,
                            max_value=max(max_frq, 1),
                        ),
                        "frequenza_relativa": st.column_config.NumberColumn("Freq. relativa", format="%.4f"),
                    },
                )

        st.write("")
        st.subheader(f"👥 Ambi più ritardatari — {ruota} (dalla cache)")
        df_ambi = db.query_df(
            """
            SELECT numero1, numero2, ritardo_attuale, frequenza
            FROM cache_lotto_ambi
            WHERE ruota = %s
            ORDER BY ritardo_attuale DESC
            LIMIT %s
            """,
            (ruota, top_n),
        )
        if df_ambi.empty:
            st.info("Cache Ambi non ancora popolata: lancia il refresh dal tab 'Gestione & Scraper'.")
        else:
            st.dataframe(
                df_ambi,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "numero1": st.column_config.NumberColumn("Primo numero", format="%02d"),
                    "numero2": st.column_config.NumberColumn("Secondo numero", format="%02d"),
                    "ritardo_attuale": st.column_config.NumberColumn("Ritardo attuale", format="%d estr."),
                    "frequenza": st.column_config.NumberColumn("Frequenza storica", format="%d uscite"),
                },
            )

    else:  # SuperEnalotto
        with st.container(border=True):
            top_n = st.slider("Quanti numeri mostrare", 5, 90, 10, key="topn_sen")

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader(f"🔥 Top {top_n} frequenza sestina (era INDIPENDENTE)")
            df_freq_sen = db.query_df(
                """
                SELECT numero, frequenza, frequenza_relativa
                FROM v_sen_frequenza_sestina
                ORDER BY frequenza DESC
                LIMIT %s
                """,
                (top_n,),
            )
            if df_freq_sen.empty:
                st.info("Nessun dato disponibile.")
            else:
                fig_sen_f = px.bar(
                    df_freq_sen, x="numero", y="frequenza",
                    labels={"numero": "Numero", "frequenza": "Uscite totali"},
                    color="frequenza", color_continuous_scale="Emrld",
                )
                fig_sen_f.update_coloraxes(showscale=False)
                st.plotly_chart(ui.apply_plotly_theme(fig_sen_f, height=320), use_container_width=True)

                max_sen_f = int(df_freq_sen["frequenza"].max() or 0)
                st.dataframe(
                    df_freq_sen,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                        "frequenza": st.column_config.ProgressColumn(
                            "Frequenza sestina",
                            format="%d uscite",
                            min_value=0,
                            max_value=max(max_sen_f, 1),
                        ),
                        "frequenza_relativa": st.column_config.NumberColumn("Freq. relativa", format="%.4f"),
                    },
                )

        with col_b:
            st.subheader(f"⏳ Top {top_n} ritardi sestina (azzerato al 2009)")
            df_rit_sen = db.query_df(
                """
                SELECT numero, ritardo_attuale
                FROM v_sen_ritardo_azzerato_2009
                ORDER BY ritardo_attuale DESC
                LIMIT %s
                """,
                (top_n,),
            )
            if df_rit_sen.empty:
                st.info("Nessun dato disponibile.")
            else:
                fig_sen_r = px.bar(
                    df_rit_sen, x="numero", y="ritardo_attuale",
                    labels={"numero": "Numero", "ritardo_attuale": "Ritardo (concorsi)"},
                    color="ritardo_attuale", color_continuous_scale="YlOrRd",
                )
                fig_sen_r.update_coloraxes(showscale=False)
                st.plotly_chart(ui.apply_plotly_theme(fig_sen_r, height=320), use_container_width=True)

                max_sen_r = int(df_rit_sen["ritardo_attuale"].max() or 0)
                st.dataframe(
                    df_rit_sen,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "numero": st.column_config.NumberColumn("Numero", format="%02d"),
                        "ritardo_attuale": st.column_config.ProgressColumn(
                            "Ritardo (concorsi)",
                            format="%d",
                            min_value=0,
                            max_value=max(max_sen_r, 1),
                        ),
                    },
                )

        st.write("")
        col_som, col_pd = st.columns(2)

        with col_som:
            st.subheader("🔔 Curva a campana — somma della sestina (era INDIPENDENTE)")
            df_somma = db.query_df(
                """
                SELECT fascia_somma_da, frequenza
                FROM v_sen_somma_distribuzione
                WHERE tipo_regolamento = 'INDIPENDENTE'
                ORDER BY fascia_somma_da
                """
            )
            if df_somma.empty:
                st.info("Nessun dato disponibile.")
            else:
                fig_som = px.area(
                    df_somma, x="fascia_somma_da", y="frequenza", markers=True,
                    labels={"fascia_somma_da": "Somma sestina (fascia da 20)", "frequenza": "Frequenza"},
                )
                fig_som.update_traces(line_color="#10B981", fillcolor="rgba(16, 185, 129, 0.2)")
                st.plotly_chart(ui.apply_plotly_theme(fig_som, height=320), use_container_width=True)

        with col_pd:
            st.subheader("⚖️ Distribuzione pari/dispari (era INDIPENDENTE)")
            df_pd = db.query_df(
                """
                SELECT conteggio_pari, conteggio_dispari, frequenza
                FROM v_sen_pari_dispari_distribuzione
                WHERE tipo_regolamento = 'INDIPENDENTE'
                ORDER BY frequenza DESC
                """
            )
            if df_pd.empty:
                st.info("Nessun dato disponibile.")
            else:
                st.dataframe(
                    df_pd,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "conteggio_pari": st.column_config.NumberColumn("Pari"),
                        "conteggio_dispari": st.column_config.NumberColumn("Dispari"),
                        "frequenza": st.column_config.ProgressColumn(
                            "Occorrenze",
                            format="%d",
                            min_value=0,
                            max_value=max(int(df_pd["frequenza"].max() or 0), 1),
                        ),
                    },
                )


# =====================================================================
# TAB 3 — GESTIONE & SCRAPER
# =====================================================================
def _esegui_pipeline(argomenti: list[str]) -> tuple[int, str]:
    comando = [sys.executable, str(UPDATE_PIPELINE_PATH), *argomenti]
    processo = subprocess.run(comando, capture_output=True, text=True, timeout=TIMEOUT_PIPELINE_SECONDI)
    output = (processo.stdout or "") + (processo.stderr or "")
    return processo.returncode, output


with tab_scraper:
    st.subheader("Stato archivio")
    if not df_stato.empty:
        st.dataframe(
            df_stato,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ultima_estrazione_lotto": st.column_config.TextColumn("Ultima estrazione Lotto"),
                "righe_lotto": st.column_config.NumberColumn("Righe Lotto", format="%d"),
                "ultima_estrazione_superenalotto": st.column_config.TextColumn("Ultima estrazione SuperEnalotto"),
                "righe_superenalotto": st.column_config.NumberColumn("Righe SuperEnalotto", format="%d"),
            },
        )

    st.divider()
    st.subheader("Pannello di controllo")
    st.caption(
        "Recupera l'ultima estrazione disponibile (Lotto + SuperEnalotto) e aggiorna le cache "
        "statistiche. I dati grezzi vengono salvati anche se il refresh delle cache fallisce: "
        "in quel caso usa 'Aggiorna solo cache statistiche' per ritentare senza ri-scaricare nulla "
        "(vedi i commenti in scraper/update_pipeline.py per il motivo tecnico)."
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Recupera nuove estrazioni", use_container_width=True, key="btn_nuove"):
            with st.spinner("Scraping in corso..."):
                try:
                    codice, output = _esegui_pipeline(["--nuove"])
                except subprocess.TimeoutExpired:
                    codice, output = 1, f"Timeout: superati {TIMEOUT_PIPELINE_SECONDI}s."
            db.svuota_cache_query()
            if codice == 0 and not api_client.invalidare_cache_backend():
                st.warning(
                    "Dati aggiornati, ma non sono riuscito ad avvisare il backend "
                    "(lotto_stat_backend non raggiungibile): la Piattaforma di Controllo "
                    "parametrica potrebbe mostrare dati non aggiornati fino al prossimo "
                    "refresh via API o riavvio del backend."
                )
            (st.success if codice == 0 else st.error)(f"Terminato con codice {codice}.")
            st.code(output or "(nessun output)")

    with col2:
        if st.button("♻️ Aggiorna solo cache statistiche", use_container_width=True, key="btn_refresh"):
            with st.spinner("Refresh cache in corso..."):
                try:
                    codice, output = _esegui_pipeline(["--refresh-only"])
                except subprocess.TimeoutExpired:
                    codice, output = 1, f"Timeout: superati {TIMEOUT_PIPELINE_SECONDI}s."
            db.svuota_cache_query()
            if codice == 0 and not api_client.invalidare_cache_backend():
                st.warning(
                    "Dati aggiornati, ma non sono riuscito ad avvisare il backend "
                    "(lotto_stat_backend non raggiungibile): la Piattaforma di Controllo "
                    "parametrica potrebbe mostrare dati non aggiornati fino al prossimo "
                    "refresh via API o riavvio del backend."
                )
            (st.success if codice == 0 else st.error)(f"Terminato con codice {codice}.")
            st.code(output or "(nessun output)")
