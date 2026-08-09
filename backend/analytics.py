"""
backend/analytics.py
=====================
Motore di analisi PARAMETRICA per la nuova Piattaforma di Controllo
(web/pages/*.py + endpoint FastAPI in main.py).

Differenza rispetto alle viste/cache in db/init-scripts/views/: quelle
sono calcolate SEMPRE sull'intero storico (design fisso, refresh via
stored procedure — vedi CLAUDE.md, "Limite transazionale"). Qui invece
la finestra di analisi (ruota, range di date, numero di estrazioni
recenti) è scelta dall'utente a runtime, quindi le query sono ad-hoc
su estrazioni_lotto / estrazioni_superenalotto, non su cache
precalcolate. Fanno eccezione ritardo_attuale/ritardo_storico_max, che
restano letti dalla cache esistente: il "ritardo" è per definizione un
concetto ancorato allo storico intero, non ha senso finestrarlo (vedi
CLAUDE.md, "il ritardo è sempre conteggiato in numero di estrazioni").

Nessuna dipendenza da numpy/pandas qui (solo 'statistics'/'math' della
stdlib): backend/requirements.txt non le include e non serve
aggiungerle solo per media/deviazione standard/pdf normale.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Optional

import db

RUOTE_LOTTO = (
    "Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli",
    "Palermo", "Roma", "Torino", "Venezia", "Nazionale",
)

COLONNE_LOTTO = ("Primo", "Secondo", "Terzo", "Quarto", "Quinto")
COLONNE_SUPERENALOTTO = ("n1", "n2", "n3", "n4", "n5", "n6")


# ---------------------------------------------------------------------
# Finestre parametriche (ruota/range-date/N-estrazioni) sulle tabelle grezze
# ---------------------------------------------------------------------
def righe_finestra_lotto(
    ruota: str = "Tutte",
    data_da: Optional[str] = None,
    data_a: Optional[str] = None,
    n_estrazioni: Optional[int] = None,
) -> list[dict]:
    condizioni, parametri = [], []
    if ruota and ruota != "Tutte":
        condizioni.append("ruota = %s")
        parametri.append(ruota)
    if data_da:
        condizioni.append("data_estrazione >= %s")
        parametri.append(data_da)
    if data_a:
        condizioni.append("data_estrazione <= %s")
        parametri.append(data_a)
    where = f"WHERE {' AND '.join(condizioni)}" if condizioni else ""
    colonne = f"data_estrazione, ruota, {', '.join(COLONNE_LOTTO)}"
    if n_estrazioni:
        sql = f"""
            SELECT * FROM (
                SELECT {colonne} FROM estrazioni_lotto {where}
                ORDER BY data_estrazione DESC LIMIT %s
            ) AS finestra ORDER BY data_estrazione
        """
        parametri.append(int(n_estrazioni))
    else:
        sql = f"SELECT {colonne} FROM estrazioni_lotto {where} ORDER BY data_estrazione"
    return db.query(sql, tuple(parametri))


def righe_finestra_superenalotto(
    data_da: Optional[str] = None,
    data_a: Optional[str] = None,
    n_estrazioni: Optional[int] = None,
) -> list[dict]:
    condizioni, parametri = [], []
    if data_da:
        condizioni.append("data_estrazione >= %s")
        parametri.append(data_da)
    if data_a:
        condizioni.append("data_estrazione <= %s")
        parametri.append(data_a)
    where = f"WHERE {' AND '.join(condizioni)}" if condizioni else ""
    colonne = f"data_estrazione, concorso, {', '.join(COLONNE_SUPERENALOTTO)}"
    if n_estrazioni:
        sql = f"""
            SELECT * FROM (
                SELECT {colonne} FROM estrazioni_superenalotto {where}
                ORDER BY data_estrazione DESC LIMIT %s
            ) AS finestra ORDER BY data_estrazione
        """
        parametri.append(int(n_estrazioni))
    else:
        sql = f"SELECT {colonne} FROM estrazioni_superenalotto {where} ORDER BY data_estrazione"
    return db.query(sql, tuple(parametri))


# ---------------------------------------------------------------------
# Modulo B — Analizzatore Somme e Distribuzioni (reale vs teorica + z-score)
# ---------------------------------------------------------------------
def _pdf_normale(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 0.0
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))


def istogramma_somme(
    gioco: str,
    ruota: str = "Tutte",
    data_da: Optional[str] = None,
    data_a: Optional[str] = None,
    n_estrazioni: Optional[int] = None,
    ampiezza_bin: int = 20,
) -> dict:
    """Distribuzione reale della somma dei numeri estratti vs curva
    normale teorica.

    La teorica NON è un semplice fit gaussiano sui dati osservati, ma
    calcolata dalla combinatoria della popolazione {1..90}: media e
    varianza di una somma di k estrazioni SENZA reinserimento da una
    popolazione finita di N elementi si ottengono con la correzione
    per popolazione finita:
        media_teorica = k * media_popolazione
        varianza_teorica = k * varianza_popolazione * (N - k) / (N - 1)
    con media_popolazione=45.5, varianza_popolazione = Var({1..90}).
    L'approssimazione a una normale è per Teorema del Limite Centrale
    (esatta sarebbe la pmf combinatoria della somma, calcolabile per
    programmazione dinamica ma non necessaria per uno z-score utile in
    pratica: k=5 o 6 è già una buona approssimazione).
    """
    if gioco == "lotto":
        righe = righe_finestra_lotto(ruota, data_da, data_a, n_estrazioni)
        colonne, k = COLONNE_LOTTO, 5
    else:
        righe = righe_finestra_superenalotto(data_da, data_a, n_estrazioni)
        colonne, k = COLONNE_SUPERENALOTTO, 6

    somme = [sum(riga[c] for c in colonne) for riga in righe]
    N = 90
    media_popolazione = (1 + N) / 2
    varianza_popolazione = sum((i - media_popolazione) ** 2 for i in range(1, N + 1)) / N
    media_teorica = k * media_popolazione
    dev_std_teorica = math.sqrt(k * varianza_popolazione * (N - k) / (N - 1))

    if not somme:
        return {
            "n_estrazioni_analizzate": 0, "media_osservata": None, "dev_std_osservata": None,
            "media_teorica": media_teorica, "dev_std_teorica": dev_std_teorica,
            "z_score": None, "bins": [],
        }

    media_osservata = statistics.fmean(somme)
    dev_std_osservata = statistics.pstdev(somme) if len(somme) > 1 else 0.0
    z_score = (media_osservata - media_teorica) / dev_std_teorica if dev_std_teorica else 0.0

    bin_min = (min(somme) // ampiezza_bin) * ampiezza_bin
    bin_max = (max(somme) // ampiezza_bin + 1) * ampiezza_bin
    bordi = list(range(int(bin_min), int(bin_max) + ampiezza_bin, ampiezza_bin))
    conteggi_reali = [0] * (len(bordi) - 1)
    for s in somme:
        idx = min((s - bordi[0]) // ampiezza_bin, len(conteggi_reali) - 1)
        conteggi_reali[int(idx)] += 1

    bins = []
    for i in range(len(bordi) - 1):
        centro = (bordi[i] + bordi[i + 1]) / 2
        conteggio_teorico = _pdf_normale(centro, media_teorica, dev_std_teorica) * ampiezza_bin * len(somme)
        bins.append({
            "da": bordi[i], "a": bordi[i + 1],
            "conteggio_reale": conteggi_reali[i],
            "conteggio_teorico": round(conteggio_teorico, 2),
        })

    return {
        "n_estrazioni_analizzate": len(somme),
        "media_osservata": round(media_osservata, 2),
        "dev_std_osservata": round(dev_std_osservata, 2),
        "media_teorica": round(media_teorica, 2),
        "dev_std_teorica": round(dev_std_teorica, 2),
        "z_score": round(z_score, 3),
        "bins": bins,
    }


# ---------------------------------------------------------------------
# Modulo D — Tabellone Analitico Dinamico (matrice 1..90)
# ---------------------------------------------------------------------
def tabellone_lotto(
    ruota: str = "Tutte",
    data_da: Optional[str] = None,
    data_a: Optional[str] = None,
    n_estrazioni: Optional[int] = None,
    ritardo_min: Optional[int] = None,
    ritardo_max: Optional[int] = None,
) -> list[dict]:
    righe = righe_finestra_lotto(ruota, data_da, data_a, n_estrazioni)
    frequenza_finestra: Counter = Counter()
    for riga in righe:
        for c in COLONNE_LOTTO:
            frequenza_finestra[riga[c]] += 1

    if ruota == "Tutte":
        cache = db.query(
            "SELECT numero, ritardo_attuale FROM v_lotto_ritardo_attuale_tutte_ruote"
        )
        ritardo_storico_max = {}  # non definito in modo univoco aggregando le 11 ruote
    else:
        cache_rows = db.query(
            "SELECT numero, ritardo_attuale, ritardo_storico_max FROM cache_lotto_ritardo WHERE ruota = %s",
            (ruota,),
        )
        cache = cache_rows
        ritardo_storico_max = {r["numero"]: int(r["ritardo_storico_max"]) for r in cache_rows}

    # int() esplicito: v_lotto_ritardo_attuale_tutte_ruote (ramo "Tutte") lo
    # ritorna come decimal.Decimal (sottrazione fra aggregati nella vista),
    # a differenza di cache_lotto_ritardo (colonna INT UNSIGNED) — senza
    # normalizzare qui, il confronto con un float più sotto (intervallo
    # medio atteso) esplode con TypeError solo nel ramo "Tutte" (bug reale,
    # riprodotto con streamlit.testing.v1.AppTest su pages/3, non teorico).
    ritardo_attuale = {r["numero"]: int(r["ritardo_attuale"]) for r in cache}
    n_finestra = len(righe)
    frequenze_ordinate = sorted(frequenza_finestra.values(), reverse=True)
    soglia_frequente = frequenze_ordinate[len(frequenze_ordinate) // 4] if frequenze_ordinate else 0

    risultato = []
    for numero in range(1, 91):
        freq = frequenza_finestra.get(numero, 0)
        rit_att = ritardo_attuale.get(numero)
        rit_max = ritardo_storico_max.get(numero)
        intervallo_medio_atteso = (n_finestra / freq) if freq else None

        if rit_min_max_esclude(rit_att, ritardo_min, ritardo_max):
            continue

        categorie = []
        if freq > 0 and freq >= soglia_frequente and soglia_frequente > 0:
            categorie.append("frequente")
        if freq == 0:
            categorie.append("scomparso")
        if rit_max is not None and rit_att is not None and rit_att >= rit_max:
            categorie.append("oro")
        elif rit_att is not None and intervallo_medio_atteso and rit_att >= intervallo_medio_atteso:
            categorie.append("ritardatario")
        if (
            intervallo_medio_atteso is not None and rit_att is not None
            and abs(rit_att - intervallo_medio_atteso) <= max(1, intervallo_medio_atteso * 0.15)
        ):
            categorie.append("isocrono")

        risultato.append({
            "numero": numero,
            "frequenza_finestra": freq,
            "ritardo_attuale": rit_att,
            "ritardo_storico_max": rit_max,
            "intervallo_medio_atteso": round(intervallo_medio_atteso, 1) if intervallo_medio_atteso else None,
            "categorie": categorie,
        })
    return risultato


def rit_min_max_esclude(valore: Optional[int], minimo: Optional[int], massimo: Optional[int]) -> bool:
    if valore is None:
        return False
    if minimo is not None and valore < minimo:
        return True
    if massimo is not None and valore > massimo:
        return True
    return False


# ---------------------------------------------------------------------
# Modulo C — Simulatore e Backtesting di una combinazione utente
# ---------------------------------------------------------------------
def backtest_combinazione_lotto(numeri: list[int], ruota: str, data_da: Optional[str] = None) -> dict:
    if not (1 <= len(numeri) <= 5) or any(not (1 <= n <= 90) for n in numeri):
        raise ValueError("La combinazione deve avere da 1 a 5 numeri distinti tra 1 e 90.")

    condizioni = ["ruota = %s"]
    parametri: list = [ruota]
    if data_da:
        condizioni.append("data_estrazione >= %s")
        parametri.append(data_da)
    sql = f"""
        SELECT data_estrazione, Primo, Secondo, Terzo, Quarto, Quinto
        FROM estrazioni_lotto WHERE {' AND '.join(condizioni)}
        ORDER BY data_estrazione
    """
    tutte = db.query(sql, tuple(parametri))

    insieme_richiesto = set(numeri)
    posizioni_match: list[int] = []
    date_match: list[str] = []
    for i, riga in enumerate(tutte):
        estratti = {riga[c] for c in COLONNE_LOTTO}
        if insieme_richiesto <= estratti:
            posizioni_match.append(i)
            date_match.append(str(riga["data_estrazione"]))

    n_totale = len(tutte)
    frequenza = len(posizioni_match)
    ritardo_attuale = (n_totale - 1 - posizioni_match[-1]) if posizioni_match else n_totale

    ritardo_massimo_storico = 0
    precedente = -1
    for pos in posizioni_match:
        ritardo_massimo_storico = max(ritardo_massimo_storico, pos - precedente - 1)
        precedente = pos
    ritardo_massimo_storico = max(ritardo_massimo_storico, n_totale - 1 - precedente)

    return {
        "numeri": sorted(numeri),
        "ruota": ruota,
        "n_estrazioni_analizzate": n_totale,
        "frequenza": frequenza,
        "date_uscite": date_match,
        "ritardo_attuale": ritardo_attuale,
        "ritardo_massimo_storico": ritardo_massimo_storico,
    }
