#!/usr/bin/env python3
"""
scraper.py
==========

Gestisce il popolamento del database `lotto_statistics`:

  1. carica_storico_csv()        -> importa file CSV storici da db/storici/
  2. aggiorna_nuove_estrazioni() -> recupera via web l'ultima estrazione
                                     disponibile di Lotto e SuperEnalotto
                                     e la inserisce se mancante.

Dipendenze (vedi requirements.txt):
    mysql-connector-python
    requests
    beautifulsoup4

NOTA IMPORTANTE SULLE FONTI WEB
--------------------------------
I siti ufficiali (lotto-italia.it, superenalotto.it) sono protetti da
bot-detection e possono richiedere tecniche più sofisticate di un
semplice `requests.get`. Questo script punta invece a due fonti che
espongono il risultato come testo/tabelle HTML semplici:

    LOTTO_URL          -> https://www.estrazionedellotto.it/ultime-estrazioni-lotto
    SUPERENALOTTO_URL  -> https://www.superenalotto.it/

Qualunque sia la fonte scelta, il markup di siti di terze parti cambia
nel tempo senza preavviso. Per questo tutta la logica di parsing è
isolata nelle funzioni `_parse_lotto_html()` e `_parse_superenalotto_html()`:
se lo scraping smette di funzionare, il problema (e la correzione) si
troveranno quasi certamente lì. I punti da verificare/adattare
ispezionando il sorgente della pagina live sono segnalati con `# TODO`.
"""

from __future__ import annotations

import argparse
import csv
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
    "host": os.environ.get("DB_HOST", "mysql"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "database": os.environ.get("DB_NAME", "lotto_statistics"),
    "user": os.environ.get("DB_USER", "statistics_user"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

STORICI_DIR = os.environ.get("STORICI_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "db", "storici"
))

LOTTO_URL = "https://www.estrazionedellotto.it/ultime-estrazioni-lotto"
SUPERENALOTTO_URL = "https://www.superenalotto.it/"

REQUEST_TIMEOUT = 15          # secondi
HTTP_MAX_RETRIES = 3
DB_CONNECT_MAX_RETRIES = 5
DB_CONNECT_RETRY_DELAY = 3    # secondi

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "LottoStatsBot/1.0 (+uso interno, contatto: admin@example.com)"
)

RUOTE_VALIDE = [
    "Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli",
    "Palermo", "Roma", "Torino", "Venezia", "Nazionale",
]

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
    """L'HTML non corrisponde più alla struttura attesa."""


# =====================================================================
# STRUTTURE DATI
# =====================================================================

@dataclass
class EstrazioneLotto:
    data_estrazione: date
    concorso: int
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
# A) CARICAMENTO STORICO DA CSV
# =====================================================================

def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","  # fallback ragionevole


def _parse_int_or_none(value: str) -> Optional[int]:
    value = (value or "").strip()
    if value == "":
        return None
    return int(value)


def _leggi_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = _sniff_delimiter(sample)
        reader = csv.DictReader(f, delimiter=delimiter)
        return [row for row in reader]


def _importa_file_lotto(conn, path: str) -> int:
    righe = _leggi_csv(path)
    da_inserire = []
    for i, row in enumerate(righe, start=2):  # riga 1 = header
        try:
            ruota = row["ruota"].strip().capitalize()
            if ruota not in RUOTE_VALIDE:
                raise ValueError(f"ruota sconosciuta: '{ruota}'")
            da_inserire.append((
                row["data_estrazione"].strip(),
                int(row["concorso"]),
                ruota,
                int(row["n1"]), int(row["n2"]), int(row["n3"]),
                int(row["n4"]), int(row["n5"]),
            ))
        except (KeyError, ValueError) as exc:
            log.error("Riga %d di %s scartata (%s): %s", i, path, exc, row)

    if not da_inserire:
        return 0

    query = """
        INSERT IGNORE INTO estrazioni_lotto
            (data_estrazione, concorso, ruota, n1, n2, n3, n4, n5)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    cursor = conn.cursor()
    cursor.executemany(query, da_inserire)
    conn.commit()
    inserite = cursor.rowcount if cursor.rowcount != -1 else len(da_inserire)
    cursor.close()
    return inserite


def _importa_file_superenalotto(conn, path: str) -> int:
    righe = _leggi_csv(path)
    da_inserire = []
    for i, row in enumerate(righe, start=2):
        try:
            da_inserire.append((
                row["data_estrazione"].strip(),
                int(row["concorso"]),
                int(row["n1"]), int(row["n2"]), int(row["n3"]),
                int(row["n4"]), int(row["n5"]), int(row["n6"]),
                int(row["jolly"]),
                _parse_int_or_none(row.get("superstar", "")),
            ))
        except (KeyError, ValueError) as exc:
            log.error("Riga %d di %s scartata (%s): %s", i, path, exc, row)

    if not da_inserire:
        return 0

    query = """
        INSERT IGNORE INTO estrazioni_superenalotto
            (data_estrazione, concorso, n1, n2, n3, n4, n5, n6, jolly, superstar)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cursor = conn.cursor()
    cursor.executemany(query, da_inserire)
    conn.commit()
    inserite = cursor.rowcount if cursor.rowcount != -1 else len(da_inserire)
    cursor.close()
    return inserite


def carica_storico_csv() -> None:
    """
    Cerca in STORICI_DIR i file CSV storici e li importa nel database.
    Convenzione sui nomi file (case-insensitive):
        *lotto*.csv            -> tabella estrazioni_lotto
        *superenalotto*.csv    -> tabella estrazioni_superenalotto
    (i file superenalotto vengono esclusi dal pattern "lotto" grazie
    all'ordine dei controlli qui sotto)
    """
    if not os.path.isdir(STORICI_DIR):
        log.warning("Cartella storici non trovata: %s (nessun import eseguito)", STORICI_DIR)
        return

    tutti_i_csv = glob.glob(os.path.join(STORICI_DIR, "*.csv"))
    if not tutti_i_csv:
        log.info("Nessun file CSV trovato in %s", STORICI_DIR)
        return

    conn = get_db_connection()
    try:
        for path in sorted(tutti_i_csv):
            nome = os.path.basename(path).lower()
            try:
                if "superenalotto" in nome:
                    n = _importa_file_superenalotto(conn, path)
                    log.info("SuperEnalotto: importate %d righe da %s", n, path)
                elif "lotto" in nome:
                    n = _importa_file_lotto(conn, path)
                    log.info("Lotto: importate %d righe da %s", n, path)
                else:
                    log.warning("File ignorato (nome non riconosciuto): %s", path)
            except (OSError, UnicodeDecodeError) as exc:
                log.error("Impossibile leggere il file %s: %s", path, exc)
            except MySQLError as exc:
                log.error("Errore database durante l'import di %s: %s", path, exc)
                conn.rollback()
    finally:
        conn.close()


# =====================================================================
# B) SCRAPING NUOVE ESTRAZIONI
# =====================================================================

def _parse_lotto_html(html: str) -> EstrazioneLotto:
    """
    Analizza la pagina delle ultime estrazioni del Lotto.

    # TODO: verificare questi selettori ispezionando il sorgente HTML
    # live della pagina (tasto destro -> Visualizza sorgente pagina, o
    # DevTools). La struttura ipotizzata qui è:
    #   - un elemento di testo tipo "Estrazione n. 107" con la data
    #     associata nelle vicinanze;
    #   - una tabella con una riga per ruota e colonne 1°..5°.
    """
    soup = BeautifulSoup(html, "html.parser")

    testo_pagina = soup.get_text(" ", strip=True)
    match_concorso = re.search(r"Estrazione\s+n\.?\s*(\d+)", testo_pagina, re.IGNORECASE)
    match_data = re.search(r"(\d{1,2})[/\s](\w+|\d{1,2})[/\s](\d{4})", testo_pagina)

    if not match_concorso:
        raise ScraperParsingError(
            "Numero di concorso non trovato: la struttura della pagina "
            "Lotto potrebbe essere cambiata (aggiornare _parse_lotto_html)."
        )
    concorso = int(match_concorso.group(1))

    data_estrazione = _estrai_prima_data(testo_pagina)
    if data_estrazione is None:
        raise ScraperParsingError(
            "Data dell'estrazione non trovata nella pagina Lotto."
        )

    # TODO: adattare il selettore della tabella al markup reale
    tabella = soup.find("table")
    if tabella is None:
        raise ScraperParsingError(
            "Tabella delle ruote non trovata: verificare il selettore in "
            "_parse_lotto_html (il sito potrebbe usare div invece di table)."
        )

    numeri_per_ruota = {}
    for riga in tabella.find_all("tr"):
        celle = [c.get_text(strip=True) for c in riga.find_all(["td", "th"])]
        if not celle:
            continue
        nome_ruota = celle[0].strip().capitalize()
        if nome_ruota not in RUOTE_VALIDE:
            continue  # riga di intestazione o non pertinente
        numeri_testo = celle[1:6]
        if len(numeri_testo) != 5:
            continue
        try:
            numeri_per_ruota[nome_ruota] = [int(n) for n in numeri_testo]
        except ValueError:
            log.warning("Numeri non interpretabili per la ruota %s: %s", nome_ruota, numeri_testo)

    if len(numeri_per_ruota) < len(RUOTE_VALIDE):
        mancanti = set(RUOTE_VALIDE) - set(numeri_per_ruota.keys())
        log.warning(
            "Estrazione Lotto incompleta: mancano le ruote %s (verificare parsing)",
            mancanti,
        )

    return EstrazioneLotto(
        data_estrazione=data_estrazione,
        concorso=concorso,
        numeri_per_ruota=numeri_per_ruota,
    )


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


def _parse_superenalotto_html(html: str) -> EstrazioneSuperenalotto:
    """
    Analizza la home page di SuperEnalotto.it che riporta in un testo
    del tipo:
        "La combinazione vincente del concorso numero 106 del
         SuperEnalotto di venerdì 3 luglio 2026 è: 22, 26, 30, 40, 68, 86."

    # TODO: verificare il testo/selettore live: il sito potrebbe
    # cambiare formattazione. Se possibile è preferibile individuare
    # un elemento HTML dedicato (es. una lista di span con i numeri)
    # invece di fare regex sul testo libero della pagina.
    """
    soup = BeautifulSoup(html, "html.parser")
    testo_pagina = soup.get_text(" ", strip=True)

    match = re.search(
        r"concorso numero\s+(\d+).*?è:\s*([\d,\s]+?)\.", testo_pagina, re.IGNORECASE
    )
    if not match:
        raise ScraperParsingError(
            "Combinazione vincente non trovata nella pagina SuperEnalotto: "
            "la struttura potrebbe essere cambiata (aggiornare "
            "_parse_superenalotto_html)."
        )

    concorso = int(match.group(1))
    numeri_grezzi = [n.strip() for n in match.group(2).split(",") if n.strip()]
    numeri = [int(n) for n in numeri_grezzi]

    if len(numeri) < 7:
        raise ScraperParsingError(
            f"Attesi almeno 7 numeri (6 + jolly), trovati {len(numeri)}: {numeri}"
        )

    data_estrazione = _estrai_prima_data(testo_pagina)
    if data_estrazione is None:
        raise ScraperParsingError("Data dell'estrazione SuperEnalotto non trovata.")

    # Il SuperStar, quando presente, va cercato separatamente:
    # TODO: individuare il selettore/pattern corretto per il SuperStar
    # sulla pagina live; per ora viene lasciato a None se non trovato.
    superstar = None
    match_superstar = re.search(r"superstar[^\d]{0,15}(\d{1,2})", testo_pagina, re.IGNORECASE)
    if match_superstar:
        superstar = int(match_superstar.group(1))

    return EstrazioneSuperenalotto(
        data_estrazione=data_estrazione,
        concorso=concorso,
        numeri=numeri[:6],
        jolly=numeri[6],
        superstar=superstar,
    )


def _lotto_gia_presente(conn, estrazione: EstrazioneLotto) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM estrazioni_lotto WHERE concorso = %s AND data_estrazione = %s",
        (estrazione.concorso, estrazione.data_estrazione),
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
            (data_estrazione, concorso, ruota, n1, n2, n3, n4, n5)
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
            (data_estrazione, concorso, n1, n2, n3, n4, n5, n6, jolly, superstar)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            estrazione.data_estrazione, estrazione.concorso,
            *estrazione.numeri, estrazione.jolly, estrazione.superstar,
        ),
    )
    conn.commit()
    inserite = cursor.rowcount
    cursor.close()
    return inserite


def aggiorna_nuove_estrazioni() -> None:
    """Recupera l'ultima estrazione disponibile online per entrambi i
    giochi e la inserisce nel database se non già presente."""
    conn = get_db_connection()
    try:
        # ---- Lotto ----------------------------------------------------
        try:
            html = _fetch(LOTTO_URL)
            estrazione_lotto = _parse_lotto_html(html)
            if _lotto_gia_presente(conn, estrazione_lotto):
                log.info(
                    "Lotto: concorso %s del %s già presente, nessun inserimento.",
                    estrazione_lotto.concorso, estrazione_lotto.data_estrazione,
                )
            else:
                n = _inserisci_lotto(conn, estrazione_lotto)
                log.info(
                    "Lotto: inserite %d nuove righe per il concorso %s del %s.",
                    n, estrazione_lotto.concorso, estrazione_lotto.data_estrazione,
                )
        except ScraperNetworkError as exc:
            log.error("Lotto: errore di rete, riprovare più tardi. Dettagli: %s", exc)
        except ScraperParsingError as exc:
            log.error("Lotto: errore di parsing HTML. Dettagli: %s", exc)
        except MySQLError as exc:
            log.error("Lotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()

        # ---- SuperEnalotto ---------------------------------------------
        try:
            html = _fetch(SUPERENALOTTO_URL)
            estrazione_sel = _parse_superenalotto_html(html)
            if _superenalotto_gia_presente(conn, estrazione_sel):
                log.info(
                    "SuperEnalotto: concorso %s del %s già presente, nessun inserimento.",
                    estrazione_sel.concorso, estrazione_sel.data_estrazione,
                )
            else:
                n = _inserisci_superenalotto(conn, estrazione_sel)
                log.info(
                    "SuperEnalotto: inserito concorso %s del %s (%d riga).",
                    estrazione_sel.concorso, estrazione_sel.data_estrazione, n,
                )
        except ScraperNetworkError as exc:
            log.error("SuperEnalotto: errore di rete, riprovare più tardi. Dettagli: %s", exc)
        except ScraperParsingError as exc:
            log.error("SuperEnalotto: errore di parsing HTML. Dettagli: %s", exc)
        except MySQLError as exc:
            log.error("SuperEnalotto: errore database durante l'inserimento: %s", exc)
            conn.rollback()
    finally:
        conn.close()


# =====================================================================
# ENTRY POINT
# =====================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carica lo storico e/o aggiorna le nuove estrazioni di Lotto e SuperEnalotto."
    )
    parser.add_argument("--storico", action="store_true", help="Importa solo i CSV storici.")
    parser.add_argument("--nuove", action="store_true", help="Scarica solo le nuove estrazioni via web.")
    args = parser.parse_args()

    esegui_storico = args.storico or not (args.storico or args.nuove)
    esegui_nuove = args.nuove or not (args.storico or args.nuove)

    if esegui_storico:
        log.info("=== Avvio import storico CSV ===")
        carica_storico_csv()

    if esegui_nuove:
        log.info("=== Avvio aggiornamento nuove estrazioni ===")
        aggiorna_nuove_estrazioni()


if __name__ == "__main__":
    main()
