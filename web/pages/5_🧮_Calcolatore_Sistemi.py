"""
web/pages/5_🧮_Calcolatore_Sistemi.py
======================================
Calcolatore & Sistemi: preventivo costi ADM, schedina con palline
grafiche, Sistemi Integrali e Ridotti (SuperEnalotto) o combinazioni
per sorte su ruota (Lotto). Puro calcolo combinatorio (web/calcolo_costi.py),
nessuna dipendenza dal DB.

Ex tab "Calcolatore & Sistemi" di web/app.py, promosso a pagina dedicata
per essere raggiungibile via carrello.render_carrello_status()/page_link
dalle pagine di analisi (1-4): l'utente evidenzia numeri interessanti
in un modulo di analisi, li aggiunge al carrello condiviso (web/carrello.py)
e li carica qui con un click, invece di ridigitarli a mano.

Niente filtri.pannello_parametri() qui: i filtri di data/somma/ritardo
non hanno significato per un calcolo di colonne/costo puro.
"""

from __future__ import annotations

import streamlit as st

import calcolo_costi as costi
import carrello
import ui_components as ui

st.set_page_config(page_title="Calcolatore & Sistemi — Control Room", page_icon="🧮", layout="wide")

ui.render_header(
    title="Calcolatore & Sistemi",
    subtitle=(
        "Costruisci un Sistema Integrale o Ridotto (SuperEnalotto) o una giocata per sorte su "
        "ruota (Lotto): preventivo ufficiale ADM, colonne sviluppate, palline della schedina."
    ),
    icon="🧮",
)
carrello.render_carrello_status(with_link=False)

gioco_calc = st.radio(
    "Seleziona gioco per il preventivo", ["SuperEnalotto", "Lotto"],
    horizontal=True, key="gioco_calcolatore",
)

if gioco_calc == "SuperEnalotto":
    st.caption(
        f"Quota ufficiale ADM: **{costi.QUOTA_UNITARIA_SUPERENALOTTO:.2f} €**/colonna "
        "(1,00 € puntata + 0,25 € quota erariale dello Stato)."
    )

    carrello_sen = carrello.get_carrello("superenalotto")
    if st.button("📥 Carica dal carrello", key="carica_carrello_sen", disabled=not carrello_sen):
        st.session_state["numeri_sen"] = carrello_sen

    with st.container(border=True):
        numeri_sen = st.multiselect(
            "Numeri selezionati (min. 6)", options=list(range(1, 91)), key="numeri_sen",
        )
        if numeri_sen:
            ui.render_balls_row(
                numeri_sen, variant="sen",
                title=f"Schedina selezionata ({len(numeri_sen)} numeri):",
            )

        col_tipo, col_gar = st.columns(2)
        with col_tipo:
            tipo_sistema = st.radio(
                "Tipo di sistema", ["Integrale", "Ridotto (euristico)"],
                horizontal=True, key="tipo_sistema_sen",
            )

        garanzia = 2
        if tipo_sistema == "Ridotto (euristico)":
            with col_gar:
                garanzia_label = st.selectbox(
                    "Garanzia minima",
                    ["Ambo (2)", "Terno (3)", "Quaterna (4)", "Cinquina (5)"],
                    key="garanzia_sen",
                )
                garanzia = {"Ambo (2)": 2, "Terno (3)": 3, "Quaterna (4)": 4, "Cinquina (5)": 5}[garanzia_label]
            st.caption(
                "⚠️ Riduzione euristica (covering design greedy), NON le tabelle di riduzione "
                "ufficiali Sisal (proprietarie e non pubblicate in formato machine-readable): "
                "garantisce comunque, per costruzione, che ogni combinazione dei numeri scelti "
                "con la garanzia indicata sia coperta da almeno una colonna giocata."
            )

    if st.button("🚀 Calcola colonne e costo", key="calcola_sen", type="primary"):
        try:
            if tipo_sistema == "Integrale":
                risultato = costi.sistema_integrale_superenalotto(numeri_sen)
            else:
                risultato = costi.sistema_ridotto_superenalotto(numeri_sen, garanzia=garanzia)
        except costi.CalcoloCostiError as exc:
            st.error(f"⚠️ {exc}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Colonne da giocare", risultato["numero_colonne"])
            c2.metric("Costo totale ADM", f"{risultato['costo_totale_euro']:.2f} €")
            if risultato["tipo"] == "ridotto":
                colonne_integrale = risultato["colonne_sistema_integrale_equivalente"]
                costo_integrale = colonne_integrale * costi.QUOTA_UNITARIA_SUPERENALOTTO
                risparmio_euro = costo_integrale - risultato["costo_totale_euro"]
                risparmio_pct = (risparmio_euro / costo_integrale * 100) if costo_integrale else 0.0
                c3.metric("Risparmio", f"{risparmio_euro:.2f} €", delta=f"-{risparmio_pct:.1f}%")

                st.markdown(
                    f"<div class='savings-badge'>🎉 Risparmi il <b>{risparmio_pct:.1f}%</b> rispetto al "
                    f"sistema integrale equivalente su questi {len(risultato['numeri'])} numeri "
                    f"({colonne_integrale} colonne, {costo_integrale:.2f} €), mantenendo la garanzia richiesta.</div>",
                    unsafe_allow_html=True,
                )

                st.write("")
                if risultato["numero_colonne"] <= 200:
                    with st.expander(f"📋 Le {risultato['numero_colonne']} colonne sviluppate", expanded=True):
                        st.dataframe(
                            [{"Colonna": f"#{i + 1:03d}", "Numeri": "  —  ".join(f"{n:02d}" for n in col)}
                             for i, col in enumerate(risultato["colonne"])],
                            use_container_width=True, hide_index=True,
                        )
                else:
                    st.info(f"{risultato['numero_colonne']} colonne generate: elenco non mostrato (troppo lungo).")

else:  # Lotto
    st.caption(
        f"Puntata minima ADM: **{costi.QUOTA_MINIMA_LOTTO:.2f} €**/colonna/ruota, "
        f"in multipli di **{costi.INCREMENTO_PUNTATA_LOTTO:.2f} €**."
    )

    carrello_lotto = carrello.get_carrello("lotto")
    if st.button("📥 Carica dal carrello", key="carica_carrello_lotto", disabled=not carrello_lotto):
        st.session_state["numeri_lotto"] = carrello_lotto

    with st.container(border=True):
        numeri_lotto = st.multiselect(
            "Numeri selezionati", options=list(range(1, 91)), key="numeri_lotto",
        )
        if numeri_lotto:
            ui.render_balls_row(
                numeri_lotto, variant="lotto",
                title=f"Schedina selezionata ({len(numeri_lotto)} numeri):",
            )

        col_sorte, col_ruote, col_puntata = st.columns(3)
        with col_sorte:
            sorte_label = st.selectbox(
                "Sorte", ["Estratto", "Ambo", "Terno", "Quaterna", "Cinquina"], key="sorte_lotto",
            )
        with col_ruote:
            tutte_le_ruote = st.checkbox("Tutte le ruote", key="tutte_ruote_lotto")
            if tutte_le_ruote:
                ruote_scelte = list(costi.RUOTE_LOTTO)
                st.caption(f"Selezionate tutte le {len(ruote_scelte)} ruote.")
            else:
                ruote_scelte = st.multiselect("Ruote", options=list(costi.RUOTE_LOTTO), key="ruote_lotto")
        with col_puntata:
            puntata_unitaria = st.number_input(
                "Puntata unitaria (€)", min_value=costi.QUOTA_MINIMA_LOTTO,
                step=costi.INCREMENTO_PUNTATA_LOTTO, value=costi.QUOTA_MINIMA_LOTTO,
                key="puntata_lotto",
            )

    if st.button("🚀 Calcola colonne e costo", key="calcola_lotto", type="primary"):
        try:
            risultato = costi.costo_lotto(numeri_lotto, sorte_label, ruote_scelte, puntata_unitaria)
        except costi.CalcoloCostiError as exc:
            st.error(f"⚠️ {exc}")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Colonne per ruota", risultato["colonne_per_ruota"])
            c2.metric("Ruote selezionate", risultato["numero_ruote"])
            c3.metric("Costo totale", f"{risultato['costo_totale_euro']:.2f} €")
            st.caption(
                f"Sorte '{risultato['sorte']}' ({risultato['numeri_richiesti']} numeri richiesti) "
                f"su {len(risultato['numeri'])} numeri scelti, {risultato['numero_ruote']} ruota/e."
            )
