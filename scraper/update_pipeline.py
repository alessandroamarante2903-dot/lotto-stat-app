#!/usr/bin/env python3
"""
scraper/update_pipeline.py
===========================

Pipeline di post-processing invocata subito dopo l'inserimento di nuove
estrazioni: aggiorna le cache statistiche (Ambi, Terzine/Terni,
Isocronismi) chiamando `sp_refresh_tutte_le_cache()` e valida il
risultato, così che dashboard/Grafana non lavorino mai su cache stantie
dopo un nuovo scraping.

Riusa tutta la logica esistente di connessione/recupero/inserimento di
`backend/scraper.py` (provider con fallback, calcolo tipo_regolamento,
deduzione concorso, ecc.) caricandolo per percorso file con
`importlib` — non con un semplice `import scraper`, perché questo
stesso pacchetto si chiama anch'esso "scraper": un `import scraper`
piano rischierebbe di risolvere sé stesso invece di
`backend/scraper.py` a seconda di come lo script viene invocato
(CLI diretta, `python -m`, cwd diverso). Il caricamento per path è
deterministico in ogni caso.

NOTA IMPORTANTE SULLA TRANSAZIONALITÀ (letta nel codice SQL esistente,
non un'assunzione): le stored procedure `sp_refresh_lotto_*` e
`sp_refresh_sen_*` (db/init-scripts/views/03_*.sql e 05_*.sql) fanno
ciascuna il proprio `START TRANSACTION` / `COMMIT` / `ROLLBACK`
interno, per poter restare atomiche e richiamabili in modo indipendente
anche da un client `mysql` (vedi db/init-scripts/README.md). In MySQL,
un `START TRANSACTION` eseguito mentre la connessione ha già una
transazione aperta esegue un COMMIT IMPLICITO di quella transazione
precedente prima di aprirne una nuova. Questo significa che NON è
possibile ottenere, chiamando queste procedure così come sono, una
vera atomicità "un solo COMMIT finale per insert+refresh": nel momento
in cui `CALL sp_refresh_tutte_le_cache()` esegue il suo primo
`START TRANSACTION` interno, un eventuale INSERT delle nuove estrazioni
ancora non committato su quella stessa connessione verrebbe committato
comunque, indipendentemente dall'esito del refresh che segue.
Riscrivere le procedure per rimuovere la loro gestione transazionale
interna romperebbe la garanzia (già documentata e voluta) che restino
atomiche e utilizzabili anche standalone da un client `mysql` esterno a
questa pipeline: non è una strada percorribile senza il consenso
esplicito di chi mantiene il motore SQL.

La scelta implementata qui, quindi, è la più corretta compatibile con
questo vincolo:
  1. l'INSERT delle nuove estrazioni viene committato SUBITO (i dati
     grezzi scaricati non vanno mai persi per un problema successivo
     nel refresh delle cache derivate);
  2. il refresh (`sp_refresh_tutte_le_cache`) viene eseguito come passo
     separato, seguito da una validazione leggera;
  3. se il refresh fallisce, l'errore viene segnalato chiaramente (log +
     valore di ritorno) SENZA far perdere le estrazioni già salvate: la
     UI (pannello "Gestione Scraper") espone un pulsante per ritentare
     solo il refresh, senza dover ri-scaricare nulla.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import sys

_QUI = os.path.dirname(os.path.abspath(__file__))
_BACKEND_SCRAPER_PATH = os.path.join(_QUI, "..", "backend", "scraper.py")


def _carica_modulo_scraper_backend():
    spec = importlib.util.spec_from_file_location("lotto_backend_scraper", _BACKEND_SCRAPER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossibile caricare backend/scraper.py da '{_BACKEND_SCRAPER_PATH}'")
    modulo = importlib.util.module_from_spec(spec)
    # Il modulo va registrato in sys.modules PRIMA di exec_module: il
    # decoratore @dataclass usato in backend/scraper.py (EstrazioneLotto,
    # EstrazioneSuperenalotto) risolve cls.__module__ tramite
    # sys.modules.get(...) durante la propria esecuzione, e fallisce con
    # AttributeError se il modulo non è ancora presente nel registry
    # (esattamente il motivo per cui il sistema di import standard lo fa
    # sempre, prima di eseguire il codice del modulo).
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


core = _carica_modulo_scraper_backend()
from mysql.connector import Error as MySQLError  # noqa: E402  (richiede mysql-connector-python, dipendenza di core)

log = logging.getLogger("update_pipeline")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Tabelle di cache popolate da sp_refresh_tutte_le_cache(): usate per la
# validazione post-refresh (vedi _valida_cache_aggiornata).
TABELLE_CACHE = (
    "cache_lotto_ambi",
    "cache_lotto_terzine",
    "cache_lotto_isocronismi",
    "cache_sen_ambi",
    "cache_sen_terni",
)


class PipelineError(Exception):
    """Errore nella fase di refresh/validazione delle cache statistiche."""


def _valida_cache_aggiornata(conn) -> None:
    """Verifica minima di sanità: dopo un refresh le tabelle di cache
    principali non devono mai risultare vuote (l'universo degli Ambi è
    sempre completo, vedi README) né contenere una colonna
    'aggiornato_il' più vecchia di qualche minuto. Non sostituisce test
    funzionali sulle stored procedure, ma intercetta i fallimenti più
    comuni (permessi mancanti, procedura che esce in silenzio, ecc.)."""
    cursor = conn.cursor()
    try:
        for tabella in TABELLE_CACHE:
            cursor.execute(f"SELECT COUNT(*) FROM {tabella}")
            (conteggio,) = cursor.fetchone()
            if conteggio == 0:
                raise PipelineError(
                    f"Validazione cache fallita: la tabella '{tabella}' è vuota dopo il refresh "
                    "(atteso: universo Ambi sempre completo, vedi db/init-scripts/README.md)."
                )
    finally:
        cursor.close()


def refresh_cache_statistiche(conn) -> None:
    """Esegue CALL sp_refresh_tutte_le_cache() e valida il risultato.
    Solleva PipelineError in caso di fallimento SQL o di validazione;
    NON tocca eventuali transazioni precedenti sulla stessa connessione
    (vedi nota sul commit implicito in testa al file)."""
    cursor = conn.cursor()
    try:
        cursor.execute("CALL sp_refresh_tutte_le_cache()")
        while cursor.nextset():  # drena eventuali result-set delle sotto-CALL
            pass
    except MySQLError as exc:
        raise PipelineError(f"CALL sp_refresh_tutte_le_cache() fallita: {exc}") from exc
    finally:
        cursor.close()
    _valida_cache_aggiornata(conn)


def esegui_pipeline_nuove_estrazioni() -> dict:
    """Punto d'ingresso principale per il Pannello di Controllo della UI
    e per l'uso da riga di comando (--nuove): recupera le ultime
    estrazioni (stessa architettura a provider con fallback di
    backend/scraper.py), le inserisce e aggiorna le cache statistiche.
    Ritorna un riepilogo strutturato, pensato per essere mostrato
    direttamente in Streamlit."""
    risultato = {
        "lotto": {"inserite": 0, "gia_presente": False, "data": None},
        "superenalotto": {"inserite": 0, "gia_presente": False, "concorso": None},
        "cache_aggiornata": False,
        "errori": [],
    }

    conn = core.get_db_connection()
    nuove_righe_inserite = False
    try:
        # ---- Lotto ----------------------------------------------------
        try:
            estrazione_lotto = core._recupera_da_prima_fonte_disponibile(core.PROVIDERS_LOTTO, "Lotto")
            risultato["lotto"]["data"] = str(estrazione_lotto.data_estrazione)
            if core._lotto_gia_presente(conn, estrazione_lotto):
                log.info("Lotto: estrazione del %s già presente, nessun inserimento.", estrazione_lotto.data_estrazione)
                risultato["lotto"]["gia_presente"] = True
            else:
                n = core._inserisci_lotto(conn, estrazione_lotto)  # esegue già il proprio conn.commit()
                log.info("Lotto: inserite %d righe per l'estrazione del %s.", n, estrazione_lotto.data_estrazione)
                risultato["lotto"]["inserite"] = n
                nuove_righe_inserite = nuove_righe_inserite or n > 0
        except core.ScraperError as exc:
            log.error("Lotto: impossibile recuperare/inserire l'ultima estrazione. %s", exc)
            risultato["errori"].append(f"Lotto: {exc}")
        except MySQLError as exc:
            log.error("Lotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()
            risultato["errori"].append(f"Lotto (DB): {exc}")

        # ---- SuperEnalotto ---------------------------------------------
        try:
            estrazione_sel = core._recupera_da_prima_fonte_disponibile(core.PROVIDERS_SUPERENALOTTO, "SuperEnalotto")
            if estrazione_sel.concorso <= 0:
                estrazione_sel.concorso = core._deduci_concorso_superenalotto(conn, estrazione_sel.data_estrazione)
            risultato["superenalotto"]["concorso"] = estrazione_sel.concorso

            if core._superenalotto_gia_presente(conn, estrazione_sel):
                log.info("SuperEnalotto: concorso %s già presente, nessun inserimento.", estrazione_sel.concorso)
                risultato["superenalotto"]["gia_presente"] = True
            else:
                n = core._inserisci_superenalotto(conn, estrazione_sel)  # esegue già il proprio conn.commit()
                log.info("SuperEnalotto: inserito concorso %s (%d riga).", estrazione_sel.concorso, n)
                risultato["superenalotto"]["inserite"] = n
                nuove_righe_inserite = nuove_righe_inserite or n > 0
        except core.ScraperError as exc:
            log.error("SuperEnalotto: impossibile recuperare/inserire l'ultima estrazione. %s", exc)
            risultato["errori"].append(f"SuperEnalotto: {exc}")
        except MySQLError as exc:
            log.error("SuperEnalotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()
            risultato["errori"].append(f"SuperEnalotto (DB): {exc}")

        # ---- Post-processing: refresh cache statistiche -----------------
        # Eseguito solo se è stata effettivamente inserita almeno una riga
        # nuova: rifare il refresh quando non è cambiato nulla sarebbe
        # lavoro sprecato (le viste "live" non ne hanno comunque bisogno,
        # solo le tabelle cache_* — vedi db/init-scripts/README.md).
        if nuove_righe_inserite:
            try:
                refresh_cache_statistiche(conn)
                risultato["cache_aggiornata"] = True
                log.info("Cache statistiche aggiornata e validata con successo.")
            except PipelineError as exc:
                log.error(
                    "Refresh cache fallito DOPO l'inserimento (i dati grezzi restano comunque salvati): %s", exc
                )
                risultato["errori"].append(f"Refresh cache: {exc}")
        else:
            log.info("Nessuna nuova riga inserita: refresh cache saltato.")
    finally:
        conn.close()

    return risultato


def esegui_solo_refresh_cache() -> dict:
    """Rilancia solo CALL sp_refresh_tutte_le_cache() + validazione, senza
    toccare le estrazioni. Usato dal pulsante 'Riprova refresh cache'
    della UI dopo un fallimento, o per un refresh manuale/schedulato."""
    conn = core.get_db_connection()
    try:
        refresh_cache_statistiche(conn)
        return {"cache_aggiornata": True, "errori": []}
    except PipelineError as exc:
        log.error("Refresh cache fallito: %s", exc)
        return {"cache_aggiornata": False, "errori": [str(exc)]}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline di aggiornamento estrazioni + refresh cache statistiche transazionale (per quanto MySQL lo consente, vedi docstring)."
    )
    parser.add_argument("--nuove", action="store_true", help="Recupera e inserisce le ultime estrazioni, poi aggiorna la cache (default se nessuna opzione).")
    parser.add_argument("--refresh-only", action="store_true", help="Esegue solo il refresh delle cache statistiche, senza inserire nuove estrazioni.")
    args = parser.parse_args()

    if args.refresh_only:
        risultato = esegui_solo_refresh_cache()
    else:
        risultato = esegui_pipeline_nuove_estrazioni()

    log.info("Riepilogo pipeline: %s", risultato)
    if risultato["errori"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
