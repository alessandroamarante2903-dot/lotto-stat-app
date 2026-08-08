"""
web/app.py
==========

Frontend Streamlit di lotto-stat-app (container lotto_stat_web):
  - Tab "Statistiche": ritardatari, frequenze e ambi da Lotto e SuperEnalotto.
  - Tab "Calcolatore & Sistemi": preventivo costi ADM, Sistemi Integrali e Ridotti.
  - Tab "Gestione Scraper": stato archivio + trigger on-demand della pipeline
    di scraping (scraper/update_pipeline.py), eseguita come sottoprocesso
    Python nello stesso container (nessun bisogno di un container scraper
    separato: backend/ e scraper/ sono montati anche qui, vedi podman-compose.yml).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

import calcolo_costi as costi
import db

st.set_page_config(page_title="Lotto & SuperEnalotto — Statistiche", page_icon="🎱", layout="wide")

UPDATE_PIPELINE_PATH = Path(__file__).resolve().parent / "scraper" / "update_pipeline.py"
TIMEOUT_PIPELINE_SECONDI = 180

st.title("🎱 Lotto & SuperEnalotto — Statistiche")

tab_statistiche, tab_calcolatore, tab_scraper = st.tabs(
    ["📊 Statistiche", "🎯 Calcolatore & Sistemi", "⚙️ Gestione Scraper"]
)

# =====================================================================
# TAB 1 — STATISTICHE
# =====================================================================
with tab_statistiche:
    gioco = st.radio("Gioco", ["Lotto", "SuperEnalotto"], horizontal=True, key="gioco_statistiche")

    if gioco == "Lotto":
        col_sel, _ = st.columns([1, 3])
        with col_sel:
            ruota = st.selectbox("Ruota", list(costi.RUOTE_LOTTO), key="ruota_statistiche")
            top_n = st.slider("Quanti numeri mostrare", 5, 90, 10, key="topn_lotto")

        st.subheader(f"Ritardatari — ruota {ruota}")
        df_ritardo = db.query_df(
            """
            SELECT numero, ritardo_attuale, ritardo_storico_max, indice_convenienza
            FROM v_lotto_indice_convenienza
            WHERE ruota = %s
            ORDER BY ritardo_attuale DESC
            LIMIT %s
            """,
            (ruota, top_n),
        )
        if df_ritardo.empty:
            st.info("Nessun dato disponibile: verifica che lo storico sia stato importato (Tab 'Gestione Scraper').")
        else:
            st.plotly_chart(
                px.bar(
                    df_ritardo, x="numero", y="ritardo_attuale",
                    hover_data=["ritardo_storico_max", "indice_convenienza"],
                    labels={"numero": "Numero", "ritardo_attuale": "Ritardo (estrazioni)"},
                ),
                use_container_width=True,
            )
            st.dataframe(df_ritardo, use_container_width=True, hide_index=True)

        st.subheader(f"Frequenze — ruota {ruota}")
        df_freq = db.query_df(
            """
            SELECT numero, frequenza, frequenza_relativa
            FROM v_lotto_frequenza_ruota
            WHERE ruota = %s
            ORDER BY frequenza DESC
            LIMIT %s
            """,
            (ruota, top_n),
        )
        if not df_freq.empty:
            st.plotly_chart(
                px.bar(df_freq, x="numero", y="frequenza", labels={"numero": "Numero", "frequenza": "Frequenza"}),
                use_container_width=True,
            )

        st.subheader(f"Ambi più ritardatari — ruota {ruota} (dalla cache)")
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
            st.info("Cache Ambi non ancora popolata: vai al Tab 'Gestione Scraper' e lancia il refresh cache.")
        else:
            st.dataframe(df_ambi, use_container_width=True, hide_index=True)

    else:  # SuperEnalotto
        top_n = st.slider("Quanti numeri mostrare", 5, 90, 10, key="topn_sen")

        col_a, col_b = st.columns(2)
        with col_a:
            st.subheader("Frequenza sestina (era INDIPENDENTE)")
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
                st.plotly_chart(px.bar(df_freq_sen, x="numero", y="frequenza"), use_container_width=True)

        with col_b:
            st.subheader("Ritardo sestina (azzerato al 2009)")
            df_rit_sen = db.query_df(
                """
                SELECT numero, ritardo_attuale
                FROM v_sen_ritardo_azzerato_2009
                ORDER BY ritardo_attuale DESC
                LIMIT %s
                """,
                (top_n,),
            )
            if not df_rit_sen.empty:
                st.plotly_chart(px.bar(df_rit_sen, x="numero", y="ritardo_attuale"), use_container_width=True)

        st.subheader("Curva a campana — somma della sestina (era INDIPENDENTE)")
        df_somma = db.query_df(
            """
            SELECT fascia_somma_da, frequenza
            FROM v_sen_somma_distribuzione
            WHERE tipo_regolamento = 'INDIPENDENTE'
            ORDER BY fascia_somma_da
            """
        )
        if not df_somma.empty:
            st.plotly_chart(
                px.line(
                    df_somma, x="fascia_somma_da", y="frequenza", markers=True,
                    labels={"fascia_somma_da": "Somma sestina (fascia da 20)", "frequenza": "Frequenza"},
                ),
                use_container_width=True,
            )

        st.subheader("Distribuzione pari/dispari (era INDIPENDENTE)")
        df_pd = db.query_df(
            """
            SELECT conteggio_pari, conteggio_dispari, frequenza
            FROM v_sen_pari_dispari_distribuzione
            WHERE tipo_regolamento = 'INDIPENDENTE'
            ORDER BY frequenza DESC
            """
        )
        if not df_pd.empty:
            st.dataframe(df_pd, use_container_width=True, hide_index=True)


# =====================================================================
# TAB 2 — CALCOLATORE & SISTEMI
# =====================================================================
with tab_calcolatore:
    gioco_calc = st.radio("Gioco", ["SuperEnalotto", "Lotto"], horizontal=True, key="gioco_calcolatore")

    if gioco_calc == "SuperEnalotto":
        st.caption(
            f"Quota ufficiale ADM: {costi.QUOTA_UNITARIA_SUPERENALOTTO:.2f} €/colonna "
            "(1,00 € puntata + 0,25 € quota Stato)."
        )
        numeri_sen = st.multiselect("Numeri selezionati (min. 6)", options=list(range(1, 91)), key="numeri_sen")
        tipo_sistema = st.radio("Tipo di sistema", ["Integrale", "Ridotto (euristico)"], horizontal=True, key="tipo_sistema_sen")

        if tipo_sistema == "Ridotto (euristico)":
            garanzia_label = st.selectbox(
                "Garanzia minima", ["Ambo (2)", "Terno (3)", "Quaterna (4)", "Cinquina (5)"], key="garanzia_sen",
            )
            garanzia = {"Ambo (2)": 2, "Terno (3)": 3, "Quaterna (4)": 4, "Cinquina (5)": 5}[garanzia_label]
            st.caption(
                "⚠️ Riduzione euristica (covering design greedy), NON le tabelle di riduzione "
                "ufficiali Sisal (proprietarie e non pubblicate in formato machine-readable): "
                "garantisce comunque, per costruzione, che ogni combinazione dei numeri scelti "
                "con la garanzia indicata sia coperta da almeno una colonna giocata."
            )

        if st.button("Calcola", key="calcola_sen"):
            try:
                if tipo_sistema == "Integrale":
                    risultato = costi.sistema_integrale_superenalotto(numeri_sen)
                else:
                    risultato = costi.sistema_ridotto_superenalotto(numeri_sen, garanzia=garanzia)
            except costi.CalcoloCostiError as exc:
                st.error(str(exc))
            else:
                c1, c2 = st.columns(2)
                c1.metric("Colonne da giocare", risultato["numero_colonne"])
                c2.metric("Costo totale", f"{risultato['costo_totale_euro']:.2f} €")
                if risultato["tipo"] == "ridotto":
                    st.caption(
                        f"Il sistema integrale equivalente su questi {len(risultato['numeri'])} numeri "
                        f"richiederebbe {risultato['colonne_sistema_integrale_equivalente']} colonne "
                        f"({risultato['colonne_sistema_integrale_equivalente'] * costi.QUOTA_UNITARIA_SUPERENALOTTO:.2f} €)."
                    )
                    if risultato["numero_colonne"] <= 200:
                        st.dataframe(
                            [{"colonna": i + 1, "numeri": " - ".join(f"{n:02d}" for n in col)}
                             for i, col in enumerate(risultato["colonne"])],
                            use_container_width=True, hide_index=True,
                        )
                    else:
                        st.info(f"{risultato['numero_colonne']} colonne generate: elenco non mostrato (troppo lungo).")

    else:  # Lotto
        st.caption(
            f"Puntata minima ADM: {costi.QUOTA_MINIMA_LOTTO:.2f} €/colonna/ruota, "
            f"in multipli di {costi.INCREMENTO_PUNTATA_LOTTO:.2f} €."
        )
        numeri_lotto = st.multiselect("Numeri selezionati", options=list(range(1, 91)), key="numeri_lotto")
        sorte_label = st.selectbox(
            "Sorte", ["Estratto", "Ambo", "Terno", "Quaterna", "Cinquina"], key="sorte_lotto",
        )
        tutte_le_ruote = st.checkbox("Tutte le ruote", key="tutte_ruote_lotto")
        if tutte_le_ruote:
            ruote_scelte = list(costi.RUOTE_LOTTO)
            st.multiselect("Ruote", options=list(costi.RUOTE_LOTTO), default=ruote_scelte, disabled=True, key="ruote_lotto_disabled")
        else:
            ruote_scelte = st.multiselect("Ruote", options=list(costi.RUOTE_LOTTO), key="ruote_lotto")
        puntata_unitaria = st.number_input(
            "Puntata unitaria (€)", min_value=costi.QUOTA_MINIMA_LOTTO, step=costi.INCREMENTO_PUNTATA_LOTTO,
            value=costi.QUOTA_MINIMA_LOTTO, key="puntata_lotto",
        )

        if st.button("Calcola", key="calcola_lotto"):
            try:
                risultato = costi.costo_lotto(numeri_lotto, sorte_label, ruote_scelte, puntata_unitaria)
            except costi.CalcoloCostiError as exc:
                st.error(str(exc))
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Colonne per ruota", risultato["colonne_per_ruota"])
                c2.metric("Ruote selezionate", risultato["numero_ruote"])
                c3.metric("Costo totale", f"{risultato['costo_totale_euro']:.2f} €")
                st.caption(
                    f"Sorte '{risultato['sorte']}' ({risultato['numeri_richiesti']} numeri richiesti) "
                    f"su {len(risultato['numeri'])} numeri scelti, {risultato['numero_ruote']} ruota/e."
                )


# =====================================================================
# TAB 3 — GESTIONE SCRAPER
# =====================================================================
def _esegui_pipeline(argomenti: list[str]) -> tuple[int, str]:
    comando = [sys.executable, str(UPDATE_PIPELINE_PATH), *argomenti]
    processo = subprocess.run(comando, capture_output=True, text=True, timeout=TIMEOUT_PIPELINE_SECONDI)
    output = (processo.stdout or "") + (processo.stderr or "")
    return processo.returncode, output


with tab_scraper:
    st.subheader("Stato archivio")
    df_stato = db.query_df(
        """
        SELECT
            (SELECT MAX(data_estrazione) FROM estrazioni_lotto) AS ultima_estrazione_lotto,
            (SELECT COUNT(*) FROM estrazioni_lotto) AS righe_lotto,
            (SELECT MAX(data_estrazione) FROM estrazioni_superenalotto) AS ultima_estrazione_superenalotto,
            (SELECT COUNT(*) FROM estrazioni_superenalotto) AS righe_superenalotto
        """
    )
    if not df_stato.empty:
        st.dataframe(df_stato, use_container_width=True, hide_index=True)

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
            (st.success if codice == 0 else st.error)(f"Terminato con codice {codice}.")
            st.code(output or "(nessun output)")
