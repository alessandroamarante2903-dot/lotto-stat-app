#!/usr/bin/env python3
"""
scraper.py
==========

Gestisce l'intero ciclo di vita del database `lotto_statistics`:

  FASE 1     - crea_tabelle()               -> CREATE TABLE IF NOT EXISTS
                                                (+ migrazione automatica di
                                                tipo_regolamento se mancante)
  FASE 2     - carica_storico_locale()      -> importa db/storici/storico.txt
                                                (solo se estrazioni_lotto è vuota)
  FASE 3     - aggiorna_nuove_estrazioni()  -> recupera l'ultima estrazione
                                                (Lotto + SuperEnalotto) via
                                                web/API e la inserisce se mancante
  FASE 3-ter - aggiorna_superenalotto_storico_lottoced() -> backfill storico
                                                completo del SuperEnalotto da
                                                lottoced.com, anno per anno

Dipendenze (vedi requirements.txt):
    mysql-connector-python
    requests
    beautifulsoup4
    selenium (opzionale: solo se si usa _init_selenium_driver per siti futuri)

CONNESSIONE AL DATABASE
------------------------
Lo script si connette sempre con l'utenza applicativa 'statistics_user'
definita in db/init.sql. Perché la FASE 1 (CREATE TABLE / ALTER TABLE)
funzioni, quella utenza deve avere anche i privilegi CREATE e ALTER oltre
a SELECT/INSERT/UPDATE/DELETE — vedi il GRANT aggiornato in init.sql.

FASE 2: FORMATO DI storico.txt
--------------------------------
Una riga per ruota per estrazione, separata da spazi/tab, es.:
    1939/01/07  BA  58  22  47  49  69
    1939/01/07  FI  27  57  81  43  61
Campi: data (YYYY/MM/DD), sigla ruota a 2 lettere, 5 numeri estratti.
Le sigle riconosciute sono quelle standard dell'archivio ufficiale
Lottomatica: BA, CA, FI, GE, MI, NA, PA, RM, RN, TO, VE (vedi
SIGLE_RUOTE). 'concorso' non è presente nel file e viene salvato come
NULL per tutte le righe storiche.

LE TRE ERE STORICHE DEL SUPERENALOTTO (tipo_regolamento)
-------------------------------------------------------------
Il meccanismo di estrazione del SuperEnalotto è cambiato nel tempo e
questo condiziona le statistiche (vedi discussione completa nella
risposta associata a questa revisione). Ogni riga di
estrazioni_superenalotto viene classificata in un campo ENUM
`tipo_regolamento`, calcolato automaticamente da `_determina_tipo_regolamento()`:

    < 1997-12-03              -> 'SIMULATO'      (dati retroattivi sul Lotto storico)
    1997-12-03 - 2009-06-30   -> 'LOTTO'         (combinazione derivata da 6 ruote del Lotto)
    >= 2009-07-01             -> 'INDIPENDENTE'  (estrazione moderna, urne dedicate)

FASE 3: FONTI DATI LIVE (architettura a provider con fallback)
-------------------------------------------------------------
Per ogni gioco esiste una lista ordinata di "provider", tentati in
ordine finché uno non restituisce un risultato valido:

  SuperEnalotto: SuperenalottoRapidAPI -> SuperenalottoMagayo
                 -> SuperenalottoScrapingHTML
  Lotto:         LottoScrapingHTML

I provider API (RapidAPI, magayo) richiedono una API key via variabile
d'ambiente e si auto-escludono se non configurati (vedi .env.example).
Lo scraping HTML punta a lottoced.com, verificato funzionante via
richieste HTTP dirette (a differenza dei siti ufficiali sisal.it,
lotto-italia.it, superenalotto.it: tutti protetti da bot-detection —
Sisal in particolare risponde con timeout sistematici, verosimilmente
WAF Akamai, sia con Selenium headless sia con richieste dirette da
alcune reti). Nessuna fonte HTML fornisce il numero di concorso per il
SuperEnalotto: viene dedotto con `_deduci_concorso_superenalotto()`
(FASE 3, incrementale) o `_assegna_concorsi_sequenziali_annuali()`
(FASE 3-ter, per anno intero — più affidabile). Il markup di siti terzi
cambia nel tempo: i punti da verificare sono segnalati con `# TODO`.

FASE 3-ter: STORICO SUPERENALOTTO DA LOTTOCED.COM
-------------------------------------------------------------
`aggiorna_superenalotto_storico_lottoced(anno_inizio, anno_fine)` scarica
via HTTP diretto (no Selenium) l'archivio annuale di lottoced.com per
ogni anno nell'intervallo e fa upsert in batch (ON DUPLICATE KEY UPDATE,
IMPORT_BATCH_SIZE righe per batch) con `tipo_regolamento` calcolato
correttamente per ogni riga in base alla sua data.

SELENIUM: DISPONIBILE MA NON PIÙ USATO DI DEFAULT
-------------------------------------------------------------
`_init_selenium_driver()` resta nel codice come utility pronta all'uso
per eventuali future fonti che richiedano JavaScript, ma nessun
percorso corrente la richiama: sia lo storico che il live SuperEnalotto
usano solo `requests`/BeautifulSoup. Se in futuro serve, va richiamata
esplicitamente da una nuova funzione dedicata al sito in questione.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:  # pragma: no cover
    sys.exit("ERRORE: installa la dipendenza con 'pip install mysql-connector-python'")


# =====================================================================
# CONFIGURAZIONE
# =====================================================================

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "db"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "database": os.environ.get("DB_NAME", "lotto_statistics"),
    "user": os.environ.get("DB_USER", "statistics_user"),
    "password": os.environ.get("DB_PASSWORD", "CHANGE_ME_STRONG_PASSWORD"),
}

# Cartella storici: di default si assume che 'backend/' e 'db/' siano
# cartelle sorelle sotto la stessa root di progetto mappata nel
# container (come da setup Podman della chat precedente). Se il tuo
# volume è mappato diversamente, sovrascrivi con la variabile
# d'ambiente STORICO_TXT_PATH.
STORICI_DIR = os.environ.get("STORICI_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "db", "storici"
))
STORICO_TXT_PATH = os.environ.get("STORICO_TXT_PATH", os.path.join(STORICI_DIR, "storico.txt"))

LOTTO_URL = "https://www.lottoced.com/lotto/"
SUPERENALOTTO_URL = "https://www.lottoced.com/superenalotto/"

REQUEST_TIMEOUT = 15          # secondi
HTTP_MAX_RETRIES = 3
DB_CONNECT_MAX_RETRIES = 5
DB_CONNECT_RETRY_DELAY = 3    # secondi
IMPORT_BATCH_SIZE = 5000      # righe per batch durante l'import storico

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "LottoStatsBot/1.0 (+uso interno, contatto: admin@example.com)"
)

# Sigle a 2 lettere dell'archivio ufficiale Lottomatica -> nome ruota
SIGLE_RUOTE = {
    "BA": "Bari", "CA": "Cagliari", "FI": "Firenze", "GE": "Genova",
    "MI": "Milano", "NA": "Napoli", "PA": "Palermo", "RM": "Roma",
    "RN": "Nazionale", "TO": "Torino", "VE": "Venezia",
}
RUOTE_VALIDE = list(SIGLE_RUOTE.values())

# ---------------------------------------------------------------------
# Fonti dati di terze parti (opzionali) per la FASE 3: se le variabili
# d'ambiente corrispondenti non sono impostate, il relativo provider
# viene semplicemente saltato (vedi sezione PROVIDER più sotto).
# ---------------------------------------------------------------------
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_SUPERENALOTTO_HOST = "superenalotto-italy-extraction.p.rapidapi.com"

MAGAYO_API_KEY = os.environ.get("MAGAYO_API_KEY", "")
# TODO: verificare il codice gioco esatto sul pannello magayo
# (Lottery Data API -> Supported Games); questo è il valore più
# plausibile ma non è stato possibile confermarlo senza una API key.
MAGAYO_GAME_CODE_SUPERENALOTTO = os.environ.get("MAGAYO_GAME_CODE", "italy_superenalotto")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("scraper")


# =====================================================================
# ECCEZIONI DEDICATE
# =====================================================================

class ScraperError(Exception):
    """Errore generico dello scraper."""


class ScraperNetworkError(ScraperError):
    """Errore di rete (timeout, connessione, HTTP status)."""


class ScraperParsingError(ScraperError):
    """L'HTML/JSON non corrisponde più alla struttura attesa."""


# =====================================================================
# STRUTTURE DATI
# =====================================================================

@dataclass
class EstrazioneLotto:
    data_estrazione: date
    concorso: Optional[int] = None
    numeri_per_ruota: dict = field(default_factory=dict)  # {"Bari": [1,2,3,4,5], ...}


@dataclass
class EstrazioneSuperenalotto:
    data_estrazione: date
    concorso: int
    numeri: list
    jolly: int
    superstar: Optional[int] = None


# =====================================================================
# UTILITY: SESSIONE HTTP CON RETRY
# =====================================================================

def _build_http_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=HTTP_MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


HTTP_SESSION = _build_http_session()


def _fetch(url: str) -> str:
    """Scarica una pagina gestendo timeout e status HTTP in modo esplicito."""
    try:
        resp = HTTP_SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.Timeout as exc:
        raise ScraperNetworkError(f"Timeout durante il recupero di {url}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise ScraperNetworkError(f"Errore di connessione verso {url}") from exc
    except requests.exceptions.HTTPError as exc:
        raise ScraperNetworkError(f"Risposta HTTP non valida da {url}: {exc}") from exc


# =====================================================================
# CONNESSIONE AL DATABASE (con retry, utile all'avvio del container)
# =====================================================================

def get_db_connection():
    last_error = None
    for tentativo in range(1, DB_CONNECT_MAX_RETRIES + 1):
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            log.info("Connessione al database riuscita (tentativo %d).", tentativo)
            return conn
        except MySQLError as exc:
            last_error = exc
            log.warning(
                "Connessione al DB fallita (tentativo %d/%d): %s",
                tentativo, DB_CONNECT_MAX_RETRIES, exc,
            )
            time.sleep(DB_CONNECT_RETRY_DELAY)
    raise ScraperError(f"Impossibile connettersi al database: {last_error}")


# =====================================================================
# LE TRE ERE STORICHE DEL SUPERENALOTTO
# =====================================================================
# Vedi discussione statistica completa nella risposta associata a
# questa revisione: il meccanismo di estrazione è cambiato due volte
# nella storia del gioco e questo condiziona le statistiche
# (frequenze, ritardi, abbinamenti) se le epoche vengono mescolate
# senza distinzione.

DATA_INIZIO_ERA_LOTTO = date(1997, 12, 3)         # da qui: gioco reale, combinazione derivata dal Lotto
DATA_INIZIO_ERA_INDIPENDENTE = date(2009, 7, 1)   # da qui: urne dedicate, indipendenti dal Lotto

TIPO_REGOLAMENTO_VALORI = ("SIMULATO", "LOTTO", "INDIPENDENTE")


def _determina_tipo_regolamento(data_estrazione: date) -> str:
    """Classifica una data di estrazione SuperEnalotto in una delle tre
    ere regolamentari (vedi costanti sopra)."""
    if data_estrazione < DATA_INIZIO_ERA_LOTTO:
        return "SIMULATO"
    if data_estrazione < DATA_INIZIO_ERA_INDIPENDENTE:
        return "LOTTO"
    return "INDIPENDENTE"


# =====================================================================
# FASE 1: CREAZIONE E CONTROLLO TABELLE
# =====================================================================

_DDL_ESTRAZIONI_LOTTO = """
    CREATE TABLE IF NOT EXISTS estrazioni_lotto (
        id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        data_estrazione DATE        NOT NULL,
        concorso        INT UNSIGNED NULL,
        ruota           VARCHAR(20) NOT NULL,
        Primo           TINYINT UNSIGNED NOT NULL,
        Secondo         TINYINT UNSIGNED NOT NULL,
        Terzo           TINYINT UNSIGNED NOT NULL,
        Quarto          TINYINT UNSIGNED NOT NULL,
        Quinto          TINYINT UNSIGNED NOT NULL,
        creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_estrazione_lotto (data_estrazione, ruota),
        KEY idx_lotto_concorso (concorso),
        KEY idx_lotto_ruota (ruota)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_DDL_ESTRAZIONI_SUPERENALOTTO = """
    CREATE TABLE IF NOT EXISTS estrazioni_superenalotto (
        id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        data_estrazione DATE        NOT NULL,
        concorso        INT UNSIGNED NOT NULL,
        n1              TINYINT UNSIGNED NOT NULL,
        n2              TINYINT UNSIGNED NOT NULL,
        n3              TINYINT UNSIGNED NOT NULL,
        n4              TINYINT UNSIGNED NOT NULL,
        n5              TINYINT UNSIGNED NOT NULL,
        n6              TINYINT UNSIGNED NOT NULL,
        jolly           TINYINT UNSIGNED NOT NULL,
        superstar       TINYINT UNSIGNED NULL,
        tipo_regolamento ENUM('SIMULATO', 'LOTTO', 'INDIPENDENTE') NOT NULL,
        creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uq_estrazione_superenalotto (data_estrazione, concorso),
        KEY idx_superenalotto_tipo_regolamento (tipo_regolamento)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _migra_colonna_tipo_regolamento(conn) -> None:
    """Migrazione idempotente: se estrazioni_superenalotto esiste già
    (da un'installazione precedente a questa revisione) e non ha ancora
    la colonna tipo_regolamento, la aggiunge e la valorizza per tutte le
    righe esistenti in base alla loro data_estrazione, poi la rende
    NOT NULL. Se la tabella è stata appena creata da _DDL_ESTRAZIONI_SUPERENALOTTO
    la colonna esiste già e questa funzione non fa nulla."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'estrazioni_superenalotto'
              AND COLUMN_NAME = 'tipo_regolamento'
            """,
            (DB_CONFIG["database"],),
        )
        (colonna_presente,) = cursor.fetchone()
        if colonna_presente:
            return

        log.info(
            "Migrazione: colonna 'tipo_regolamento' assente su estrazioni_superenalotto, "
            "la aggiungo e la valorizzo per le righe esistenti..."
        )
        # Nullable in un primo momento: una ALTER ADD COLUMN NOT NULL
        # senza DEFAULT fallirebbe su una tabella già popolata.
        cursor.execute(
            "ALTER TABLE estrazioni_superenalotto "
            "ADD COLUMN tipo_regolamento ENUM('SIMULATO', 'LOTTO', 'INDIPENDENTE') NULL"
        )
        cursor.execute(
            """
            UPDATE estrazioni_superenalotto
            SET tipo_regolamento = CASE
                WHEN data_estrazione < %s THEN 'SIMULATO'
                WHEN data_estrazione < %s THEN 'LOTTO'
                ELSE 'INDIPENDENTE'
            END
            WHERE tipo_regolamento IS NULL
            """,
            (DATA_INIZIO_ERA_LOTTO, DATA_INIZIO_ERA_INDIPENDENTE),
        )
        righe_valorizzate = cursor.rowcount
        cursor.execute(
            "ALTER TABLE estrazioni_superenalotto "
            "MODIFY COLUMN tipo_regolamento ENUM('SIMULATO', 'LOTTO', 'INDIPENDENTE') NOT NULL"
        )
        cursor.execute(
            "ALTER TABLE estrazioni_superenalotto "
            "ADD INDEX idx_superenalotto_tipo_regolamento (tipo_regolamento)"
        )
        conn.commit()
        log.info(
            "Migrazione completata: tipo_regolamento aggiunta e valorizzata per %d righe esistenti.",
            righe_valorizzate,
        )
    finally:
        cursor.close()


def crea_tabelle() -> None:
    """FASE 1: crea le tabelle se non esistono già (idempotente) e
    applica la migrazione di tipo_regolamento se necessario. Richiede
    che 'statistics_user' abbia i privilegi CREATE e ALTER su
    lotto_statistics.* (vedi GRANT in init.sql)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        try:
            for nome, ddl in (
                ("estrazioni_lotto", _DDL_ESTRAZIONI_LOTTO),
                ("estrazioni_superenalotto", _DDL_ESTRAZIONI_SUPERENALOTTO),
            ):
                try:
                    cursor.execute(ddl)
                    log.info("Tabella '%s' verificata/creata.", nome)
                except MySQLError as exc:
                    if exc.errno == 1142:  # command denied (privilegio mancante)
                        raise ScraperError(
                            f"L'utente '{DB_CONFIG['user']}' non ha il privilegio "
                            f"CREATE su '{DB_CONFIG['database']}'. Aggiungi CREATE "
                            f"al GRANT in init.sql (vedi le note nel file) e rilancia."
                        ) from exc
                    raise
            conn.commit()
        finally:
            cursor.close()

        try:
            _migra_colonna_tipo_regolamento(conn)
        except MySQLError as exc:
            if exc.errno == 1142:
                raise ScraperError(
                    f"L'utente '{DB_CONFIG['user']}' non ha il privilegio "
                    f"ALTER su '{DB_CONFIG['database']}', necessario per la "
                    f"migrazione di tipo_regolamento. Aggiungi ALTER al GRANT "
                    f"in init.sql e rilancia."
                ) from exc
            raise
    except MySQLError as exc:
        log.error("Errore database durante la creazione delle tabelle: %s", exc)
        conn.rollback()
        raise
    finally:
        conn.close()


# =====================================================================
# FASE 2: CARICAMENTO STORICO DA storico.txt
# =====================================================================

def _itera_righe_storico_lotto(path: str):
    """Generator: legge storico.txt riga per riga e produce tuple
    (data_estrazione, ruota, n1, n2, n3, n4, n5) pronte per l'insert.
    Le righe malformate vengono loggate e saltate senza interrompere
    la lettura del file."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for numero_riga, riga in enumerate(f, start=1):
            riga = riga.strip()
            if not riga:
                continue

            campi = riga.split()  # gestisce sia spazi multipli sia tab
            if len(campi) != 7:
                log.warning(
                    "storico.txt riga %d scartata (attesi 7 campi, trovati %d): %r",
                    numero_riga, len(campi), riga,
                )
                continue

            data_grezza, sigla, *numeri_testo = campi
            ruota = SIGLE_RUOTE.get(sigla.strip().upper())
            if ruota is None:
                log.warning(
                    "storico.txt riga %d scartata (sigla ruota sconosciuta '%s'): %r",
                    numero_riga, sigla, riga,
                )
                continue

            try:
                data_estrazione = datetime.strptime(data_grezza, "%Y/%m/%d").date()
                numeri = [int(n) for n in numeri_testo]
            except ValueError as exc:
                log.warning("storico.txt riga %d scartata (%s): %r", numero_riga, exc, riga)
                continue

            if len(numeri) != 5:
                log.warning(
                    "storico.txt riga %d scartata (attesi 5 numeri, trovati %d): %r",
                    numero_riga, len(numeri), riga,
                )
                continue

            yield (data_estrazione, ruota, *numeri)


def carica_storico_locale() -> None:
    """FASE 2: se la tabella estrazioni_lotto è vuota, importa
    STORICO_TXT_PATH. 'concorso' viene salvato come NULL (il file non
    lo contiene). Usa INSERT IGNORE ed è quindi rilanciabile in
    sicurezza (idempotente rispetto al vincolo UNIQUE su
    data_estrazione+ruota)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM estrazioni_lotto")
        (conteggio,) = cursor.fetchone()
        cursor.close()

        if conteggio > 0:
            log.info(
                "estrazioni_lotto contiene già %d righe: import storico saltato "
                "(la tabella non è vuota).", conteggio,
            )
            return

        if not os.path.isfile(STORICO_TXT_PATH):
            log.warning(
                "File storico non trovato: %s (nessun import eseguito). "
                "Verifica il mapping del volume Podman o imposta STORICO_TXT_PATH.",
                STORICO_TXT_PATH,
            )
            return

        log.info("Tabella vuota: avvio import da %s", STORICO_TXT_PATH)

        query = """
            INSERT IGNORE INTO estrazioni_lotto
                (data_estrazione, concorso, ruota, Primo, Secondo, Terzo, Quarto, Quinto)
            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s)
        """
        insert_cursor = conn.cursor()
        batch = []
        totale_inserite = 0
        totale_lette = 0

        try:
            for riga in _itera_righe_storico_lotto(STORICO_TXT_PATH):
                batch.append(riga)
                totale_lette += 1
                if len(batch) >= IMPORT_BATCH_SIZE:
                    insert_cursor.executemany(query, batch)
                    conn.commit()
                    totale_inserite += insert_cursor.rowcount if insert_cursor.rowcount != -1 else len(batch)
                    log.info("Import storico Lotto: %d righe lette finora...", totale_lette)
                    batch.clear()

            if batch:
                insert_cursor.executemany(query, batch)
                conn.commit()
                totale_inserite += insert_cursor.rowcount if insert_cursor.rowcount != -1 else len(batch)
        finally:
            insert_cursor.close()

        log.info(
            "Import storico Lotto completato: %d righe lette, %d righe inserite "
            "(le differenze sono duplicati scartati da INSERT IGNORE o righe malformate).",
            totale_lette, totale_inserite,
        )
    except MySQLError as exc:
        log.error("Errore database durante l'import storico: %s", exc)
        conn.rollback()
    except OSError as exc:
        log.error("Errore di lettura del file storico: %s", exc)
    finally:
        conn.close()


# =====================================================================
# FASE 3: PARSING HTML DELLE FONTI (lottoced.com)
# =====================================================================

_MESI_ITALIANI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}


def _estrai_prima_data(testo: str) -> Optional[date]:
    """Cerca una data in formato 'gg mese aaaa' o 'gg/mm/aaaa' nel testo."""
    m = re.search(r"(\d{1,2})\s+([A-Za-zàèìòù]+)\s+(\d{4})", testo)
    if m:
        giorno, mese_str, anno = m.groups()
        mese = _MESI_ITALIANI.get(mese_str.lower())
        if mese:
            try:
                return date(int(anno), mese, int(giorno))
            except ValueError:
                pass
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", testo)
    if m:
        giorno, mese, anno = map(int, m.groups())
        try:
            return date(anno, mese, giorno)
        except ValueError:
            pass
    return None


def _parse_data_iso_o_italiana(testo: str) -> date:
    """Prova prima il formato ISO 'YYYY-MM-DD' (tipico delle API), poi
    ricade sul parsing di date in italiano usato per l'HTML."""
    testo = (testo or "").strip()
    try:
        return datetime.strptime(testo, "%Y-%m-%d").date()
    except ValueError:
        pass
    trovata = _estrai_prima_data(testo)
    if trovata is None:
        raise ScraperParsingError(f"Formato data non riconosciuto: '{testo}'")
    return trovata


def _parse_lotto_html(html: str) -> EstrazioneLotto:
    """
    Analizza https://www.lottoced.com/lotto/ (verificato: pagina
    WordPress con una tabella per l'ultima estrazione, una riga per
    ruota, NON protetta da bot-detection). Struttura osservata:

        ## Estrazione del lotto  sabato 04 luglio 2026
        | BARI | 81 | 71 | 24 | 27 | 01 |
        | CAGLIARI | ... |
        ...

    # TODO: se il markup cambia, questa è la funzione da aggiornare
    # (ispezionare il sorgente live della pagina).
    """
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4")
        and "estrazione del lotto" in tag.get_text(" ", strip=True).lower()
    )
    if heading is None:
        raise ScraperParsingError(
            "Titolo 'Estrazione del lotto' non trovato: la struttura della "
            "pagina potrebbe essere cambiata (aggiornare _parse_lotto_html)."
        )

    data_estrazione = _estrai_prima_data(heading.get_text(" ", strip=True))
    if data_estrazione is None:
        raise ScraperParsingError("Data non trovata nel titolo dell'estrazione Lotto.")

    tabella = heading.find_next("table")
    if tabella is None:
        raise ScraperParsingError("Tabella delle ruote non trovata dopo il titolo Lotto.")

    numeri_per_ruota = {}
    for riga in tabella.find_all("tr"):
        celle = [c.get_text(strip=True) for c in riga.find_all(["td", "th"])]
        if not celle:
            continue
        nome_ruota = celle[0].strip().capitalize()
        if nome_ruota not in RUOTE_VALIDE:
            continue
        numeri_testo = celle[1:6]
        if len(numeri_testo) != 5:
            continue
        try:
            numeri_per_ruota[nome_ruota] = [int(n) for n in numeri_testo]
        except ValueError:
            log.warning("Numeri non interpretabili per la ruota %s: %s", nome_ruota, numeri_testo)

    if not numeri_per_ruota:
        raise ScraperParsingError("Nessuna ruota interpretabile nella tabella Lotto.")

    if len(numeri_per_ruota) < len(RUOTE_VALIDE):
        mancanti = set(RUOTE_VALIDE) - set(numeri_per_ruota.keys())
        log.warning("Estrazione Lotto incompleta: mancano le ruote %s (verificare parsing)", mancanti)

    return EstrazioneLotto(
        data_estrazione=data_estrazione,
        concorso=None,  # non fornito da questa fonte
        numeri_per_ruota=numeri_per_ruota,
    )


def _parse_superenalotto_html(html: str) -> EstrazioneSuperenalotto:
    """
    Analizza https://www.lottoced.com/superenalotto/ (verificato).
    Struttura osservata (la pagina elenca più estrazioni passate, la
    più recente per prima):

        ## Estrazione del superenalotto   sabato 04 luglio 2026
        | 02 | 37 | 55 | 62 | 72 | 76 |   <- sestina
        | Jolly     |  |  |  |  | 34 |
        | Superstar |  |  |  |  | 75 |

    # TODO: se il markup cambia, questa è la funzione da aggiornare.
    """
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4")
        and "estrazione del superenalotto" in tag.get_text(" ", strip=True).lower()
    )
    if heading is None:
        raise ScraperParsingError(
            "Titolo 'Estrazione del superenalotto' non trovato: la struttura "
            "della pagina potrebbe essere cambiata (aggiornare _parse_superenalotto_html)."
        )

    data_estrazione = _estrai_prima_data(heading.get_text(" ", strip=True))
    if data_estrazione is None:
        raise ScraperParsingError("Data non trovata nel titolo dell'estrazione SuperEnalotto.")

    tabella = heading.find_next("table")
    if tabella is None:
        raise ScraperParsingError("Tabella della sestina non trovata dopo il titolo SuperEnalotto.")

    righe = tabella.find_all("tr")
    if not righe:
        raise ScraperParsingError("Tabella SuperEnalotto vuota.")

    prima_riga = [c.get_text(strip=True) for c in righe[0].find_all(["td", "th"])]
    numeri = [int(v) for v in prima_riga if v.strip().isdigit()]
    if len(numeri) != 6:
        raise ScraperParsingError(
            f"Attesi 6 numeri nella prima riga della tabella SuperEnalotto, "
            f"trovati {len(numeri)}: {prima_riga}"
        )

    jolly = None
    superstar = None
    for riga in righe[1:]:
        celle = [c.get_text(strip=True) for c in riga.find_all(["td", "th"])]
        if not celle:
            continue
        etichetta = celle[0].strip().lower()
        valori = [v for v in celle[1:] if v.strip().isdigit()]
        valore = int(valori[-1]) if valori else None
        if "jolly" in etichetta:
            jolly = valore
        elif "superstar" in etichetta:
            superstar = valore

    if jolly is None:
        raise ScraperParsingError("Numero Jolly non trovato nella tabella SuperEnalotto.")

    return EstrazioneSuperenalotto(
        data_estrazione=data_estrazione,
        concorso=0,  # non fornito da questa fonte: dedotto a valle
        numeri=numeri,
        jolly=jolly,
        superstar=superstar,
    )


# =====================================================================
# FASE 3: PROVIDER (fonti dati con fallback: API terze parti + scraping)
# =====================================================================

class FonteLotto:
    """Interfaccia base per una fonte dati del Lotto."""
    nome = "fonte-non-implementata"

    def disponibile(self) -> bool:
        return True

    def recupera(self) -> EstrazioneLotto:
        raise NotImplementedError


class FonteSuperenalotto:
    """Interfaccia base per una fonte dati del SuperEnalotto."""
    nome = "fonte-non-implementata"

    def disponibile(self) -> bool:
        return True

    def recupera(self) -> EstrazioneSuperenalotto:
        raise NotImplementedError


class LottoScrapingHTML(FonteLotto):
    """Scraping HTML di lottoced.com. Sempre disponibile, nessuna API
    key richiesta. Vedi i TODO in `_parse_lotto_html`."""
    nome = "scraping-html (lottoced.com)"

    def recupera(self) -> EstrazioneLotto:
        html = _fetch(LOTTO_URL)
        return _parse_lotto_html(html)


class SuperenalottoScrapingHTML(FonteSuperenalotto):
    """Scraping HTML di lottoced.com. Sempre disponibile, nessuna API
    key richiesta. Fa da ultima rete di sicurezza se le fonti API non
    sono configurate o falliscono. Vedi i TODO in
    `_parse_superenalotto_html`."""
    nome = "scraping-html (lottoced.com)"

    def recupera(self) -> EstrazioneSuperenalotto:
        html = _fetch(SUPERENALOTTO_URL)
        return _parse_superenalotto_html(html)


class SuperenalottoRapidAPI(FonteSuperenalotto):
    """
    API di terze parti pubblicata su RapidAPI:
    https://rapidapi.com/keysersoft/api/superenalotto-italy-extraction

    Richiede una sottoscrizione (è disponibile un piano gratuito) e la
    variabile d'ambiente RAPIDAPI_KEY.

    NOTA: lo schema JSON esatto della risposta non è stato verificabile
    in fase di scrittura di questo script (richiede una chiave attiva
    per essere testato). `_estrai_da_json_rapidapi()` prova alcuni nomi
    di campo plausibili; se il parsing fallisce, il JSON grezzo viene
    loggato a livello DEBUG (imposta LOG_LEVEL=DEBUG) per poterlo
    ispezionare e correggere la funzione di parsing di conseguenza.
    """
    nome = "RapidAPI (superenalotto-italy-extraction)"
    ENDPOINT = f"https://{RAPIDAPI_SUPERENALOTTO_HOST}/last-extraction"

    def disponibile(self) -> bool:
        return bool(RAPIDAPI_KEY)

    def recupera(self) -> EstrazioneSuperenalotto:
        headers = {
            "x-rapidapi-host": RAPIDAPI_SUPERENALOTTO_HOST,
            "x-rapidapi-key": RAPIDAPI_KEY,
        }
        try:
            resp = HTTP_SESSION.get(self.ENDPOINT, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            dati = resp.json()
        except requests.exceptions.Timeout as exc:
            raise ScraperNetworkError(f"Timeout RapidAPI: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise ScraperNetworkError(f"Errore RapidAPI: {exc}") from exc
        except ValueError as exc:
            raise ScraperParsingError(f"Risposta RapidAPI non è JSON valido: {exc}") from exc

        log.debug("Risposta grezza RapidAPI: %s", dati)
        return _estrai_da_json_rapidapi(dati)


def _estrai_da_json_rapidapi(dati: dict) -> EstrazioneSuperenalotto:
    """# TODO: sostituire i nomi di campo con quelli reali osservati in
    una risposta autenticata; qui si tenta una lista di alias plausibili."""
    concorso = dati.get("number") or dati.get("extraction") or dati.get("concorso")
    data_str = dati.get("date") or dati.get("data") or dati.get("extraction_date")
    numeri_grezzi = dati.get("numbers") or dati.get("results") or dati.get("numeri")
    jolly = dati.get("jolly")
    superstar = dati.get("superstar") or dati.get("super_star")

    if concorso is None or data_str is None or numeri_grezzi is None:
        raise ScraperParsingError(
            f"Campi attesi mancanti nella risposta RapidAPI (verificare lo schema reale): {dati}"
        )

    if isinstance(numeri_grezzi, str):
        numeri_lista = [int(n) for n in numeri_grezzi.split(",") if n.strip()]
    else:
        numeri_lista = [int(n) for n in numeri_grezzi]

    if jolly is None and len(numeri_lista) >= 7:
        jolly = numeri_lista[6]
        numeri_lista = numeri_lista[:6]
    elif jolly is not None:
        jolly = int(jolly)

    if jolly is None or len(numeri_lista) < 6:
        raise ScraperParsingError(f"Impossibile determinare sestina e Jolly da RapidAPI: {dati}")

    return EstrazioneSuperenalotto(
        data_estrazione=_parse_data_iso_o_italiana(str(data_str)),
        concorso=int(concorso),
        numeri=numeri_lista[:6],
        jolly=jolly,
        superstar=int(superstar) if superstar not in (None, "") else None,
    )


class SuperenalottoMagayo(FonteSuperenalotto):
    """
    Lottery Data API di magayo.com (piano gratuito disponibile):
    https://www.magayo.com/lottery-feeds/lottery-data-api/

    Richiede MAGAYO_API_KEY e, opzionalmente, MAGAYO_GAME_CODE (default
    'italy_superenalotto' — DA VERIFICARE nel pannello magayo).

    LIMITAZIONE NOTA: la risposta di magayo non include il numero di
    concorso, solo data e numeri. Viene dedotto a valle (vedi
    `_deduci_concorso_superenalotto`).
    """
    nome = "magayo Lottery Data API"
    ENDPOINT = "https://www.magayo.com/api/results.php"

    def disponibile(self) -> bool:
        return bool(MAGAYO_API_KEY)

    def recupera(self) -> EstrazioneSuperenalotto:
        params = {"api_key": MAGAYO_API_KEY, "game": MAGAYO_GAME_CODE_SUPERENALOTTO, "format": "json"}
        try:
            resp = HTTP_SESSION.get(self.ENDPOINT, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            dati = resp.json()
        except requests.exceptions.Timeout as exc:
            raise ScraperNetworkError(f"Timeout magayo: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise ScraperNetworkError(f"Errore magayo: {exc}") from exc
        except ValueError as exc:
            raise ScraperParsingError(f"Risposta magayo non è JSON valido: {exc}") from exc

        log.debug("Risposta grezza magayo: %s", dati)

        codice_errore = int(dati.get("error", 0) or 0)
        if codice_errore != 0:
            raise ScraperParsingError(f"magayo ha restituito un errore (codice {codice_errore}): {dati}")

        data_str = dati.get("draw")
        risultati_str = dati.get("results")
        if not data_str or not risultati_str:
            raise ScraperParsingError(f"Risposta magayo incompleta: {dati}")

        numeri_lista = [int(n) for n in risultati_str.split(",")]
        if len(numeri_lista) < 7:
            raise ScraperParsingError(
                f"Attesi almeno 7 numeri (6 + jolly) da magayo, trovati {len(numeri_lista)}: {risultati_str}"
            )

        return EstrazioneSuperenalotto(
            data_estrazione=_parse_data_iso_o_italiana(data_str),
            concorso=0,  # sconosciuto: dedotto a valle
            numeri=numeri_lista[:6],
            jolly=numeri_lista[6],
            superstar=numeri_lista[7] if len(numeri_lista) > 7 else None,
        )


# Ordine di tentativo: le fonti API (se configurate) per prime perché
# più stabili; lo scraping HTML resta come ultima rete di sicurezza,
# sempre disponibile senza alcuna chiave.
PROVIDERS_LOTTO: list = [
    LottoScrapingHTML(),
]

PROVIDERS_SUPERENALOTTO: list = [
    SuperenalottoRapidAPI(),
    SuperenalottoMagayo(),
    SuperenalottoScrapingHTML(),
]


def _recupera_da_prima_fonte_disponibile(providers: list, nome_gioco: str):
    """Prova i provider nell'ordine dato, passando al successivo in
    caso di provider non configurato, errore di rete o di parsing."""
    errori = []
    for provider in providers:
        if not provider.disponibile():
            log.debug("%s: provider '%s' non configurato, salto.", nome_gioco, provider.nome)
            continue
        try:
            log.info("%s: tento la fonte '%s'...", nome_gioco, provider.nome)
            risultato = provider.recupera()
            log.info("%s: dati ottenuti con successo da '%s'.", nome_gioco, provider.nome)
            return risultato
        except ScraperNetworkError as exc:
            log.warning("%s: fonte '%s' non raggiungibile (%s), provo la successiva.", nome_gioco, provider.nome, exc)
            errori.append((provider.nome, str(exc)))
        except ScraperParsingError as exc:
            log.warning("%s: fonte '%s' dati non interpretabili (%s), provo la successiva.", nome_gioco, provider.nome, exc)
            errori.append((provider.nome, str(exc)))
    raise ScraperError(f"{nome_gioco}: nessuna fonte dati disponibile ha funzionato. Dettagli: {errori}")


def _deduci_concorso_superenalotto(conn, data_estrazione: date) -> int:
    """EURISTICA: nessuna fonte HTML disponibile fornisce il numero di
    concorso. Si assume che la numerazione riparta da 1 a ogni nuovo
    anno solare e prosegua senza salti. Se il DB ha dei buchi per
    quell'anno il numero dedotto sarà scorretto: verificarlo a mano in
    quel caso, o configurare RapidAPI (che il concorso lo fornisce)."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(concorso) FROM estrazioni_superenalotto WHERE YEAR(data_estrazione) = %s",
        (data_estrazione.year,),
    )
    (ultimo,) = cursor.fetchone()
    cursor.close()
    return (ultimo or 0) + 1


# =====================================================================
# FASE 3: ACCESSO AL DB (controllo duplicati + insert)
# =====================================================================

def _lotto_gia_presente(conn, estrazione: EstrazioneLotto) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM estrazioni_lotto WHERE data_estrazione = %s",
        (estrazione.data_estrazione,),
    )
    (conteggio,) = cursor.fetchone()
    cursor.close()
    return conteggio >= len(RUOTE_VALIDE)


def _inserisci_lotto(conn, estrazione: EstrazioneLotto) -> int:
    righe = [
        (estrazione.data_estrazione, estrazione.concorso, ruota, *numeri)
        for ruota, numeri in estrazione.numeri_per_ruota.items()
    ]
    if not righe:
        return 0
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT IGNORE INTO estrazioni_lotto
            (data_estrazione, concorso, ruota, Primo, Secondo, Terzo, Quarto, Quinto)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        righe,
    )
    conn.commit()
    inserite = cursor.rowcount if cursor.rowcount != -1 else len(righe)
    cursor.close()
    return inserite


def _superenalotto_gia_presente(conn, estrazione: EstrazioneSuperenalotto) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM estrazioni_superenalotto WHERE concorso = %s AND data_estrazione = %s",
        (estrazione.concorso, estrazione.data_estrazione),
    )
    (conteggio,) = cursor.fetchone()
    cursor.close()
    return conteggio > 0


def _inserisci_superenalotto(conn, estrazione: EstrazioneSuperenalotto) -> int:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT IGNORE INTO estrazioni_superenalotto
            (data_estrazione, concorso, n1, n2, n3, n4, n5, n6, jolly, superstar, tipo_regolamento)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            estrazione.data_estrazione, estrazione.concorso, *estrazione.numeri,
            estrazione.jolly, estrazione.superstar,
            _determina_tipo_regolamento(estrazione.data_estrazione),
        ),
    )
    conn.commit()
    inserite = cursor.rowcount
    cursor.close()
    return inserite


def aggiorna_nuove_estrazioni() -> None:
    """FASE 3: recupera l'ultima estrazione disponibile per entrambi i
    giochi (provando in ordine i provider configurati) e la inserisce
    nel database se non già presente."""
    conn = get_db_connection()
    try:
        # ---- Lotto ----------------------------------------------------
        try:
            estrazione_lotto = _recupera_da_prima_fonte_disponibile(PROVIDERS_LOTTO, "Lotto")
            if _lotto_gia_presente(conn, estrazione_lotto):
                log.info("Lotto: estrazione del %s già presente, nessun inserimento.", estrazione_lotto.data_estrazione)
            else:
                n = _inserisci_lotto(conn, estrazione_lotto)
                log.info("Lotto: inserite %d nuove righe per l'estrazione del %s.", n, estrazione_lotto.data_estrazione)
        except ScraperError as exc:
            log.error("Lotto: impossibile recuperare l'ultima estrazione. %s", exc)
        except MySQLError as exc:
            log.error("Lotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()

        # ---- SuperEnalotto ---------------------------------------------
        try:
            estrazione_sel = _recupera_da_prima_fonte_disponibile(PROVIDERS_SUPERENALOTTO, "SuperEnalotto")

            if estrazione_sel.concorso <= 0:
                estrazione_sel.concorso = _deduci_concorso_superenalotto(conn, estrazione_sel.data_estrazione)
                log.info(
                    "SuperEnalotto: concorso non fornito dalla fonte, dedotto = %s "
                    "(verificare se il DB ha buchi per l'anno).",
                    estrazione_sel.concorso,
                )

            if _superenalotto_gia_presente(conn, estrazione_sel):
                log.info("SuperEnalotto: concorso %s del %s già presente, nessun inserimento.",
                         estrazione_sel.concorso, estrazione_sel.data_estrazione)
            else:
                n = _inserisci_superenalotto(conn, estrazione_sel)
                log.info("SuperEnalotto: inserito concorso %s del %s (%d riga).",
                         estrazione_sel.concorso, estrazione_sel.data_estrazione, n)
        except ScraperError as exc:
            log.error("SuperEnalotto: impossibile recuperare l'ultima estrazione. %s", exc)
        except MySQLError as exc:
            log.error("SuperEnalotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()
    finally:
        conn.close()


# =====================================================================
# UTILITY: SELENIUM (disponibile per usi futuri, non richiamata di
# default da nessun percorso corrente)
# =====================================================================
#
# Sia il backfill storico (FASE 3-ter) sia il live update (FASE 3) del
# SuperEnalotto usano solo `requests`/BeautifulSoup: lottoced.com si è
# rivelato server-renderizzato e raggiungibile via HTTP diretto, quindi
# Selenium non serve più per le fonti attuali. La funzione sotto resta
# disponibile come utility pronta all'uso se in futuro serve fare lo
# scraping di un sito che richiede JavaScript: richiamala da una nuova
# funzione dedicata a quel sito specifico (vedi _attendi_elemento_pagina
# come esempio di attesa con WebDriverWait).
# =====================================================================

SELENIUM_PAGE_LOAD_TIMEOUT = int(os.environ.get("SELENIUM_PAGE_LOAD_TIMEOUT", "30"))
SELENIUM_ELEMENT_WAIT_TIMEOUT = int(os.environ.get("SELENIUM_ELEMENT_WAIT_TIMEOUT", "20"))

# Percorsi opzionali: nei container Linux il binario di Chromium e/o
# chromedriver spesso non sono nel PATH standard di Selenium Manager.
# Default allineati al pacchetto apt 'chromium'/'chromium-driver' su
# Debian/Ubuntu. Sovrascrivi con le variabili d'ambiente CHROME_BIN/
# CHROMEDRIVER_PATH se la tua immagine li installa in percorsi diversi.
CHROME_BIN = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")


def _init_selenium_driver():
    """Crea un driver Chrome headless con le opzioni corrette per un
    container Linux rootless (Podman + SELinux): niente sandbox del
    kernel, niente /dev/shm condiviso, niente GPU. Import lazy: se
    selenium non è installato, solo questa funzione fallisce (con un
    messaggio chiaro) — il resto dello script resta pienamente
    utilizzabile."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.chrome.service import Service as ChromeService
        from selenium.common.exceptions import WebDriverException
    except ImportError as exc:
        raise ScraperError(
            "Il pacchetto 'selenium' non è installato: aggiungilo a "
            "requirements.txt ed esegui 'pip install selenium'. Serve "
            "inoltre chromium + chromedriver nell'immagine del container."
        ) from exc

    opzioni = ChromeOptions()
    opzioni.add_argument("--headless=new")
    opzioni.add_argument("--no-sandbox")
    opzioni.add_argument("--disable-setuid-sandbox")  # fondamentale per Podman rootless/SELinux
    opzioni.add_argument("--disable-dev-shm-usage")
    opzioni.add_argument("--disable-gpu")
    opzioni.add_argument("--window-size=1920,1080")
    opzioni.add_argument(f"--user-agent={USER_AGENT}")
    opzioni.add_argument("--lang=it-IT")
    opzioni.page_load_strategy = "eager"  # non aspetta risorse secondarie (font, immagini, tracker)

    if CHROME_BIN:
        opzioni.binary_location = CHROME_BIN

    try:
        servizio = ChromeService(executable_path=CHROMEDRIVER_PATH) if CHROMEDRIVER_PATH else ChromeService()
        driver = webdriver.Chrome(service=servizio, options=opzioni)
    except WebDriverException as exc:
        raise ScraperError(
            f"Impossibile avviare Chrome/chromedriver headless: {exc}. "
            f"Verifica che chromium e chromedriver siano installati "
            f"nell'immagine e che CHROME_BIN/CHROMEDRIVER_PATH puntino ai "
            f"binari corretti (attuali: CHROME_BIN='{CHROME_BIN}', "
            f"CHROMEDRIVER_PATH='{CHROMEDRIVER_PATH}')."
        ) from exc

    driver.set_page_load_timeout(SELENIUM_PAGE_LOAD_TIMEOUT)
    return driver


def _attendi_elemento_pagina(driver, url: str, selettore_css: str):
    """Naviga all'URL e attende (WebDriverWait) che il selettore CSS
    indicato sia presente nel DOM prima di restituire driver.page_source.
    Esempio di utilizzo per una futura fonte JS-dipendente:
        driver = _init_selenium_driver()
        try:
            html = _attendi_elemento_pagina(driver, url, "a.scheda-estrazione")
        finally:
            driver.quit()
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException

    try:
        driver.get(url)
        WebDriverWait(driver, SELENIUM_ELEMENT_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selettore_css))
        )
    except TimeoutException as exc:
        raise ScraperNetworkError(
            f"Timeout Selenium: elemento '{selettore_css}' non apparso entro "
            f"{SELENIUM_ELEMENT_WAIT_TIMEOUT}s per {url}."
        ) from exc
    except WebDriverException as exc:
        raise ScraperNetworkError(f"Errore Selenium durante il caricamento di {url}: {exc}") from exc

    return driver.page_source


# =====================================================================
# FASE 3-TER: STORICO SUPERENALOTTO DA LOTTOCED.COM (HTTP DIRETTO)
# =====================================================================
#
# URL VERIFICATO: https://www.lottoced.com/superenalotto/estrazioni/?anno=<anno>
# — stesso dominio già usato per lo scraping "nuove estrazioni" in FASE 3,
# quindi già raggiungibile da questo ambiente senza Selenium. Pagina
# server-renderizzata, tabella semplice con una riga per estrazione:
#   - data nell'href in formato ISO: estrazione_super=AAAA-MM-GG
#   - numeri come stringhe punteggiate: "03.16.30.53.55.79."
#   - Jolly e SuperStar nello stesso formato (SuperStar vuoto per i
#     concorsi antecedenti la sua introduzione, coerentemente con
#     l'era 'SIMULATO'/'LOTTO' iniziale)
#
# NOTA: questa pagina non riporta il numero di concorso. Viene dedotto
# numerando in ordine cronologico crescente ENTRO ciascun anno (prima
# estrazione dell'anno = concorso 1) da _assegna_concorsi_sequenziali_annuali.
# Resta un'euristica: se l'elenco di un anno risulta incompleto sul
# sito, i numeri di concorso dedotti saranno scorretti per quell'anno.
# =====================================================================

LOTTOCED_SUPERENALOTTO_ARCHIVIO_URL = "https://www.lottoced.com/superenalotto/estrazioni/"

_HREF_DATA_LOTTOCED_RE = re.compile(r"estrazione_super=(\d{4}-\d{2}-\d{2})")


def _costruisci_url_archivio_annuale_lottoced(anno: int) -> str:
    return f"{LOTTOCED_SUPERENALOTTO_ARCHIVIO_URL}?anno={anno}"


def _parse_archivio_annuale_lottoced_html(html: str) -> list:
    """
    Analizza l'archivio annuale di lottoced.com. Per ogni riga legge la
    data dall'href del link (formato ISO) e cerca nelle celle della
    stessa <tr> i gruppi di cifre punteggiate: il primo gruppo è la
    sestina, il secondo il Jolly, il terzo (se presente) il SuperStar.
    Non fornisce il numero di concorso (dedotto a valle). Ritorna tuple
    grezze (data_estrazione, numeri, jolly, superstar).

    # TODO: se lottoced.com cambia il markup, aggiornare qui.
    """
    soup = BeautifulSoup(html, "html.parser")
    link_data = soup.find_all("a", href=_HREF_DATA_LOTTOCED_RE)

    if not link_data:
        raise ScraperParsingError(
            "Nessuna estrazione trovata nell'archivio lottoced.com: la "
            "struttura potrebbe essere cambiata (aggiornare "
            "_parse_archivio_annuale_lottoced_html), oppure l'anno "
            "richiesto non ha estrazioni disponibili."
        )

    righe = []
    for link in link_data:
        m = _HREF_DATA_LOTTOCED_RE.search(link.get("href", ""))
        if not m:
            continue
        try:
            data_estrazione = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            log.warning("Data non valida nell'href '%s', riga scartata.", link.get("href"))
            continue

        riga_tr = link.find_parent("tr")
        if riga_tr is None:
            log.warning("Riga <tr> non trovata per la data %s, scartata.", data_estrazione)
            continue

        celle = [c.get_text(strip=True) for c in riga_tr.find_all("td")]
        # Isola solo le celle che contengono gruppi di cifre punteggiate
        # (es. "03.16.30.53.55.79."), ignorando celle vuote/spaziatrici.
        gruppi_numerici = [c for c in celle if re.fullmatch(r"(\d{1,2}\.)+", c)]

        if len(gruppi_numerici) < 2:
            log.warning("Dati numerici insufficienti per il %s, riga scartata: %s", data_estrazione, celle)
            continue

        numeri = [int(x) for x in gruppi_numerici[0].split(".") if x]
        if len(numeri) != 6:
            log.warning(
                "Attesi 6 numeri nella sestina del %s, trovati %d, riga scartata: %s",
                data_estrazione, len(numeri), gruppi_numerici[0],
            )
            continue

        jolly = int(gruppi_numerici[1].rstrip("."))
        superstar = int(gruppi_numerici[2].rstrip(".")) if len(gruppi_numerici) > 2 else None

        righe.append((data_estrazione, numeri, jolly, superstar))

    return righe


def _assegna_concorsi_sequenziali_annuali(righe: list, anno: int) -> list:
    """Numera sequenzialmente (1, 2, 3, ...) le estrazioni di un anno in
    ordine cronologico crescente, dato che lottoced.com non riporta il
    concorso, e calcola tipo_regolamento per ciascuna. Segnala nei log
    un conteggio anomalo (indicativamente 100-300 estrazioni/anno a
    seconda dell'epoca) come possibile sintomo di un elenco incompleto."""
    righe_ordinate = sorted(righe, key=lambda r: r[0])
    if righe_ordinate and not (100 <= len(righe_ordinate) <= 300):
        log.warning(
            "Anno %d: trovate %d estrazioni (atteso indicativamente "
            "100-300/anno secondo l'epoca). Verifica che l'elenco sia "
            "completo prima di fidarti dei numeri di concorso dedotti.",
            anno, len(righe_ordinate),
        )
    return [
        EstrazioneSuperenalotto(data_estrazione=data, concorso=i, numeri=numeri, jolly=jolly, superstar=superstar)
        for i, (data, numeri, jolly, superstar) in enumerate(righe_ordinate, start=1)
    ]


def scarica_estrazioni_superenalotto_lottoced_anno(anno: int) -> list:
    """Scarica ed effettua il parsing di TUTTE le estrazioni SuperEnalotto
    di un anno dall'archivio di lottoced.com via HTTP diretto (nessun
    bisogno di Selenium: pagina verificata server-renderizzata)."""
    url = _costruisci_url_archivio_annuale_lottoced(anno)
    log.info("lottoced.com: carico %s ...", url)
    html = _fetch(url)
    righe_grezze = _parse_archivio_annuale_lottoced_html(html)
    estrazioni = _assegna_concorsi_sequenziali_annuali(righe_grezze, anno)
    log.info("lottoced.com: %d estrazioni trovate per l'anno %d.", len(estrazioni), anno)
    return estrazioni


def _upsert_superenalotto_batch(conn, estrazioni: list, batch_size: int = IMPORT_BATCH_SIZE) -> int:
    """Insert/update in batch (ON DUPLICATE KEY UPDATE) sul vincolo
    UNIQUE(data_estrazione, concorso), pensato per volumi grandi come il
    backfill storico completo. tipo_regolamento viene calcolato riga per
    riga da _determina_tipo_regolamento(). Ritorna il numero totale di
    righe elaborate (insert+update aggregati: in batch non si distingue
    in modo granulare l'uno dall'altro, a differenza dell'upsert singolo
    usato in FASE 3 live)."""
    if not estrazioni:
        return 0

    query = """
        INSERT INTO estrazioni_superenalotto
            (data_estrazione, concorso, n1, n2, n3, n4, n5, n6, jolly, superstar, tipo_regolamento)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            n1 = VALUES(n1), n2 = VALUES(n2), n3 = VALUES(n3),
            n4 = VALUES(n4), n5 = VALUES(n5), n6 = VALUES(n6),
            jolly = VALUES(jolly), superstar = VALUES(superstar),
            tipo_regolamento = VALUES(tipo_regolamento)
    """
    cursor = conn.cursor()
    totale_elaborate = 0
    try:
        for i in range(0, len(estrazioni), batch_size):
            chunk = estrazioni[i:i + batch_size]
            righe = [
                (
                    e.data_estrazione, e.concorso, *e.numeri, e.jolly, e.superstar,
                    _determina_tipo_regolamento(e.data_estrazione),
                )
                for e in chunk
            ]
            cursor.executemany(query, righe)
            conn.commit()
            totale_elaborate += len(chunk)
            if len(estrazioni) > batch_size:
                log.info("Upsert SuperEnalotto: %d/%d righe elaborate finora...", totale_elaborate, len(estrazioni))
    finally:
        cursor.close()
    return totale_elaborate


def aggiorna_superenalotto_storico_lottoced(anno_inizio: int = 1997, anno_fine: Optional[int] = None,
                                             pausa_tra_richieste: float = 1.0) -> None:
    """Punto d'ingresso FASE 3-ter: backfill storico completo del
    SuperEnalotto da lottoced.com, anno per anno, con upsert in batch
    (idempotente). anno_fine di default è l'anno corrente.
    pausa_tra_richieste (secondi) distanzia le richieste tra un anno e
    l'altro per non martellare il sito."""
    anno_fine = anno_fine or date.today().year
    conn = get_db_connection()
    try:
        for anno in range(anno_inizio, anno_fine + 1):
            try:
                estrazioni = scarica_estrazioni_superenalotto_lottoced_anno(anno)
            except ScraperError as exc:
                log.error("lottoced.com %d: recupero fallito, salto all'anno successivo. %s", anno, exc)
                continue
            try:
                n = _upsert_superenalotto_batch(conn, estrazioni)
                log.info("lottoced.com %d: %d righe elaborate (insert+update) su %d trovate.", anno, n, len(estrazioni))
            except MySQLError as exc:
                log.error("lottoced.com %d: errore database durante l'upsert: %s", anno, exc)
                conn.rollback()
            if anno < anno_fine:
                time.sleep(pausa_tra_richieste)
    finally:
        conn.close()


# =====================================================================
# ENTRY POINT
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gestisce schema, storico e aggiornamento live di Lotto e SuperEnalotto."
    )
    parser.add_argument("--setup", action="store_true", help="Esegue solo la FASE 1 (crea tabelle).")
    parser.add_argument("--storico", action="store_true", help="Esegue solo la FASE 2 (import storico.txt).")
    parser.add_argument("--nuove", action="store_true", help="Esegue solo la FASE 3 (nuove estrazioni via web/API).")
    parser.add_argument(
        "--superenalotto-storico-lottoced", action="store_true",
        help="Backfill storico completo del SuperEnalotto da lottoced.com via HTTP diretto (FASE 3-ter).",
    )
    parser.add_argument("--dal-anno", type=int, default=1997,
                         help="Anno di inizio per --superenalotto-storico-lottoced (default: 1997).")
    parser.add_argument("--al-anno", type=int,
                         help="Anno di fine per --superenalotto-storico-lottoced (default: anno corrente).")
    args = parser.parse_args()

    if args.superenalotto_storico_lottoced:
        log.info("=== FASE 3-ter: storico SuperEnalotto da lottoced.com ===")
        crea_tabelle()
        aggiorna_superenalotto_storico_lottoced(anno_inizio=args.dal_anno, anno_fine=args.al_anno)
        return

    solo_una_fase = args.setup or args.storico or args.nuove

    # FASE 1: sempre eseguita per prima (idempotente, precondizione per le altre)
    if not solo_una_fase or args.setup:
        log.info("=== FASE 1: creazione/controllo tabelle ===")
        crea_tabelle()
        if args.setup:
            return

    if not solo_una_fase or args.storico:
        log.info("=== FASE 2: import storico locale ===")
        carica_storico_locale()

    if not solo_una_fase or args.nuove:
        log.info("=== FASE 3: aggiornamento nuove estrazioni ===")
        aggiorna_nuove_estrazioni()


if __name__ == "__main__":
    main()