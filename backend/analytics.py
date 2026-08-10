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

import bisect
import functools
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
    posizioni: Optional[list[str]] = None,
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
    colonne_valide = COLONNE_LOTTO if gioco == "lotto" else COLONNE_SUPERENALOTTO
    if posizioni:
        non_valide = set(posizioni) - set(colonne_valide)
        if non_valide:
            raise ValueError(f"Posizioni non valide per {gioco}: {sorted(non_valide)}")
        colonne = tuple(c for c in colonne_valide if c in posizioni)  # preserva l'ordine 1°..k°
    else:
        colonne = colonne_valide
    k = len(colonne)

    if gioco == "lotto":
        righe = righe_finestra_lotto(ruota, data_da, data_a, n_estrazioni)
    else:
        righe = righe_finestra_superenalotto(data_da, data_a, n_estrazioni)

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
            "z_score": None, "skewness": None, "curtosi": None,
            "posizioni_analizzate": list(colonne), "bins": [],
        }

    media_osservata = statistics.fmean(somme)
    dev_std_osservata = statistics.pstdev(somme) if len(somme) > 1 else 0.0
    z_score = (media_osservata - media_teorica) / dev_std_teorica if dev_std_teorica else 0.0

    # Skewness/curtosi (popolazione, non campionarie: qui la "popolazione"
    # è la finestra scelta dall'utente, non un campione di qualcos'altro
    # di più grande — coerente con pstdev sopra, non stdev campionaria).
    if dev_std_osservata > 0 and len(somme) > 1:
        scarti = [s - media_osservata for s in somme]
        skewness = (sum(x ** 3 for x in scarti) / len(somme)) / dev_std_osservata ** 3
        curtosi = (sum(x ** 4 for x in scarti) / len(somme)) / dev_std_osservata ** 4 - 3
    else:
        skewness = curtosi = None

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
        "skewness": round(skewness, 3) if skewness is not None else None,
        "curtosi": round(curtosi, 3) if curtosi is not None else None,
        "posizioni_analizzate": list(colonne),
        "bins": bins,
    }


# ---------------------------------------------------------------------
# Modulo D — Tabellone Analitico Dinamico (matrice 1..90)
# ---------------------------------------------------------------------
# Soglia scelta per "Iper-ritardatario": IRR (ritardo_attuale/ritardo_storico_max)
# >= 0.8 significa che il numero è già all'80% del proprio record storico di
# assenza — più permissiva di IRR>=1 (record eguagliato/superato, categoria
# "oro" qui sotto) così il filtro non resta quasi sempre vuoto su finestre
# tipiche. Valore euristico, non derivato da un test statistico formale.
SOGLIA_IPER_RITARDATARIO_IRR = 0.8
STATI_VALIDI = ("Tutti", "Vergine", "Frequente", "Iper-ritardatario")


@functools.lru_cache(maxsize=64)
def _passo_ritardo(ruota: str, k: int = 5) -> dict[int, Optional[float]]:
    """Media mobile del ritardo sulle ultime k uscite di ciascun numero:
    media degli ultimi k intervalli (in numero di estrazioni) fra
    un'uscita e la successiva, sulla serie storica COMPLETA della ruota
    (mai finestrata: stesso principio di ritardo_attuale/storico_max,
    vedi nota di modulo in testa al file). Un solo passaggio su tutta la
    storia costruisce le posizioni di uscita di tutti i 90 numeri insieme,
    non 90 query separate.

    @lru_cache: misurato in pratica ~3s per ruota='Tutte' (scan di tutte
    le ~77k righe storiche), troppo lento per un modulo pensato come
    "live" (ricalcolo ad ogni variazione di un filtro non correlato —
    vedi specifica UX del Modulo 2). Il risultato dipende SOLO da
    (ruota, k), non dalla finestra temporale scelta dall'utente, quindi
    è candidato naturale a cache: invalidata esplicitamente da
    invalidare_cache_calcoli() dopo ogni refresh dello scraper, non da
    un TTL (i dati cambiano solo quando arriva una nuova estrazione)."""
    storia = righe_finestra_lotto(ruota, None, None, None)
    posizioni: dict[int, list[int]] = {n: [] for n in range(1, 91)}
    for i, riga in enumerate(storia):
        for c in COLONNE_LOTTO:
            posizioni[riga[c]].append(i)

    risultato: dict[int, Optional[float]] = {}
    for numero, pos in posizioni.items():
        if len(pos) < 2:
            risultato[numero] = None
            continue
        gap = [pos[i] - pos[i - 1] - 1 for i in range(1, len(pos))]
        ultimi_k = gap[-k:]
        risultato[numero] = round(statistics.fmean(ultimi_k), 1)
    return risultato


def tabellone_lotto(
    ruota: str = "Tutte",
    data_da: Optional[str] = None,
    data_a: Optional[str] = None,
    n_estrazioni: Optional[int] = None,
    ritardo_min: Optional[int] = None,
    ritardo_max: Optional[int] = None,
    stato: str = "Tutti",
    passo_k: int = 5,
) -> list[dict]:
    if stato not in STATI_VALIDI:
        raise ValueError(f"Stato non valido: {stato!r}. Valori ammessi: {STATI_VALIDI}")

    righe = righe_finestra_lotto(ruota, data_da, data_a, n_estrazioni)
    frequenza_finestra: Counter = Counter()
    for riga in righe:
        for c in COLONNE_LOTTO:
            frequenza_finestra[riga[c]] += 1

    # ruota="Tutte" (sentinella lato web) legge la riga ruota='TUTTE' in
    # cache_lotto_ritardo, materializzata da sp_refresh_lotto_ritardo() a
    # partire da v_lotto_ritardo_attuale_tutte_ruote (vedi
    # db/init-scripts/views/03_procedure_cache_lotto.sql): quella vista da
    # sola costa ~2.1s (EXPLAIN: 5 full table scan in UNION), quindi ORA
    # è materializzata una volta per refresh invece che ricalcolata ad
    # ogni lettura — stesso identico pattern già usato per le altre ruote.
    chiave_ruota_cache = "TUTTE" if ruota == "Tutte" else ruota
    cache_rows = db.query(
        "SELECT numero, ritardo_attuale, ritardo_storico_max FROM cache_lotto_ritardo WHERE ruota = %s",
        (chiave_ruota_cache,),
    )
    if ruota == "Tutte":
        ritardo_storico_max = {}  # sentinella 0 in DB: non definito in modo univoco aggregando 11 ruote
    else:
        ritardo_storico_max = {r["numero"]: int(r["ritardo_storico_max"]) for r in cache_rows}
    ritardo_attuale = {r["numero"]: int(r["ritardo_attuale"]) for r in cache_rows}
    passo = _passo_ritardo(ruota, passo_k)
    n_finestra = len(righe)
    frequenze_ordinate = sorted(frequenza_finestra.values(), reverse=True)
    soglia_frequente = frequenze_ordinate[len(frequenze_ordinate) // 4] if frequenze_ordinate else 0

    risultato = []
    for numero in range(1, 91):
        freq = frequenza_finestra.get(numero, 0)
        rit_att = ritardo_attuale.get(numero)
        rit_max = ritardo_storico_max.get(numero)
        intervallo_medio_atteso = (n_finestra / freq) if freq else None
        irr = (rit_att / rit_max) if (rit_att is not None and rit_max) else None

        if rit_min_max_esclude(rit_att, ritardo_min, ritardo_max):
            continue

        categorie = []
        if freq > 0 and freq >= soglia_frequente and soglia_frequente > 0:
            categorie.append("frequente")
        if freq == 0:
            categorie.append("vergine")
        if rit_max is not None and rit_att is not None and rit_att >= rit_max:
            categorie.append("oro")
        elif rit_att is not None and intervallo_medio_atteso and rit_att >= intervallo_medio_atteso:
            categorie.append("ritardatario")
        if (
            intervallo_medio_atteso is not None and rit_att is not None
            and abs(rit_att - intervallo_medio_atteso) <= max(1, intervallo_medio_atteso * 0.15)
        ):
            categorie.append("isocrono")
        if irr is not None and irr >= SOGLIA_IPER_RITARDATARIO_IRR:
            categorie.append("iper-ritardatario")

        if stato == "Vergine" and freq != 0:
            continue
        if stato == "Frequente" and "frequente" not in categorie:
            continue
        if stato == "Iper-ritardatario" and "iper-ritardatario" not in categorie:
            continue

        risultato.append({
            "numero": numero,
            "frequenza_finestra": freq,
            "ritardo_attuale": rit_att,
            "ritardo_storico_max": rit_max,
            "irr": round(irr, 3) if irr is not None else None,
            "passo_ritardo": passo.get(numero),
            "intervallo_medio_atteso": round(intervallo_medio_atteso, 1) if intervallo_medio_atteso else None,
            "categorie": categorie,
        })
    return risultato


def matrice_isocronia_lotto() -> dict:
    """Per ciascuno dei 90 numeri, il ritardo_attuale su ognuna delle 11
    ruote (da cache_lotto_ritardo, nessuno scan): individua i gruppi dove
    più ruote condividono lo STESSO ritardo esatto per lo stesso numero
    (isocronia inter-ruota, vedi specifica Modulo 2)."""
    righe = db.query("SELECT ruota, numero, ritardo_attuale FROM cache_lotto_ritardo ORDER BY numero, ruota")
    per_numero: dict[int, dict[str, int]] = {}
    for r in righe:
        per_numero.setdefault(r["numero"], {})[r["ruota"]] = int(r["ritardo_attuale"])

    matrice = [
        {"numero": numero, "ritardi": per_numero.get(numero, {})}
        for numero in range(1, 91)
    ]
    gruppi_isocroni = []
    for numero, ritardi_per_ruota in per_numero.items():
        per_valore: dict[int, list[str]] = {}
        for ruota_nome, rit in ritardi_per_ruota.items():
            per_valore.setdefault(rit, []).append(ruota_nome)
        for rit, ruote in per_valore.items():
            if len(ruote) >= 2:
                gruppi_isocroni.append({"numero": numero, "ritardo": rit, "ruote": sorted(ruote)})

    return {"ruote": list(RUOTE_LOTTO), "matrice": matrice, "gruppi_isocroni": gruppi_isocroni}


def rit_min_max_esclude(valore: Optional[int], minimo: Optional[int], massimo: Optional[int]) -> bool:
    if valore is None:
        return False
    if minimo is not None and valore < minimo:
        return True
    if massimo is not None and valore > massimo:
        return True
    return False


# ---------------------------------------------------------------------
# Modulo 3 — Simulatore e Backtesting di una combinazione utente
# ---------------------------------------------------------------------
SORTE_A_K = {"Estratto": 1, "Ambo": 2, "Terno": 3, "Quaterna": 4, "Cinquina": 5}


def _probabilita_sorte(k: int, popolazione: int = 90, estratti: int = 5) -> float:
    """P che un insieme fissato di k numeri sia interamente contenuto in
    un'estrazione di 'estratti' numeri da 'popolazione' (ipergeometrica):
        P = C(popolazione - k, estratti - k) / C(popolazione, estratti)
    Verifica: k=1 -> C(89,4)/C(90,5) = 5/90 = 1/18, la probabilità nota
    dell'Estratto Semplice — formula corretta, non un'approssimazione."""
    return math.comb(popolazione - k, estratti - k) / math.comb(popolazione, estratti)


def backtest_combinazione_lotto(
    numeri: list[int], ruota: str, sorte: str = "Ambo", data_da: Optional[str] = None,
) -> dict:
    if sorte not in SORTE_A_K:
        raise ValueError(f"Sorte non valida: {sorte!r}. Valori ammessi: {list(SORTE_A_K)}")
    k_atteso = SORTE_A_K[sorte]
    if len(numeri) != k_atteso or len(set(numeri)) != k_atteso or any(not (1 <= n <= 90) for n in numeri):
        raise ValueError(f"La sorte '{sorte}' richiede esattamente {k_atteso} numeri distinti tra 1 e 90.")

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

    gap: list[int] = []
    precedente = -1
    for pos in posizioni_match:
        gap.append(pos - precedente - 1)
        precedente = pos
    gap.append(n_totale - 1 - precedente)  # coda aperta fino ad oggi (contribuisce al max drawdown)
    max_drawdown = max(gap) if gap else n_totale

    gap_chiusi = gap[:-1] if posizioni_match else []  # esclude la coda aperta dalla media (non è un intervallo osservato per intero)
    intervallo_medio = statistics.fmean(gap_chiusi) if gap_chiusi else None
    intervallo_dev_std = statistics.pstdev(gap_chiusi) if len(gap_chiusi) > 1 else (0.0 if gap_chiusi else None)

    p_teorica = _probabilita_sorte(k_atteso)
    frequenza_teorica = n_totale * p_teorica
    yield_ = (frequenza / frequenza_teorica) if frequenza_teorica > 0 else None

    return {
        "numeri": sorted(numeri),
        "ruota": ruota,
        "sorte": sorte,
        "n_estrazioni_analizzate": n_totale,
        "frequenza": frequenza,
        "frequenza_teorica": round(frequenza_teorica, 3),
        "yield": round(yield_, 3) if yield_ is not None else None,
        "date_uscite": date_match,
        "ritardo_attuale": ritardo_attuale,
        "ritardo_massimo_storico": max_drawdown,
        "max_drawdown": max_drawdown,
        "intervallo_medio": round(intervallo_medio, 1) if intervallo_medio is not None else None,
        "intervallo_dev_std": round(intervallo_dev_std, 1) if intervallo_dev_std is not None else None,
        "distribuzione_intervalli": gap_chiusi,
    }


# ---------------------------------------------------------------------
# Modulo 4 — Analisi di Frequenza Posizionale (Numero Spia)
# ---------------------------------------------------------------------
CAMPIONE_SPIA_MINIMO_AFFIDABILE = 20


def analisi_spia(
    numero_spia: int, ruota_spia: str, ruota_target: str, orizzonte_h: int = 5,
    data_da: Optional[str] = None,
) -> dict:
    """Per ogni uscita storica di numero_spia sulla ruota_spia, guarda le
    orizzonte_h estrazioni SUCCESSIVE sulla ruota_target e conta quante
    volte vi compare ciascuno dei 90 numeri. Indice di Attrattiva =
    frequenza media post-spia / frequenza naturale attesa in una
    finestra di quella lunghezza (H*5/90, valore atteso di occorrenze di
    un numero fissato in H estrazioni indipendenti da 5/90).

    Occorrenze di spia troppo vicine alla fine dello storico (con meno
    di orizzonte_h estrazioni successive disponibili sulla ruota target)
    vengono escluse dal conteggio, non troncate: altrimenti la coda
    finale peserebbe come se avesse una finestra completa, sbilanciando
    verso il basso la frequenza post-spia proprio sulle occorrenze più
    recenti (le più interessanti per un utente che guarda al "adesso").
    """
    if not (1 <= numero_spia <= 90):
        raise ValueError("numero_spia deve essere tra 1 e 90.")
    if not (1 <= orizzonte_h <= 20):
        raise ValueError("orizzonte_h deve essere tra 1 e 20.")

    condizioni_target = ["ruota = %s"]
    parametri_target: list = [ruota_target]
    if data_da:
        condizioni_target.append("data_estrazione >= %s")
        parametri_target.append(data_da)
    sql_target = f"""
        SELECT data_estrazione, {', '.join(COLONNE_LOTTO)} FROM estrazioni_lotto
        WHERE {' AND '.join(condizioni_target)} ORDER BY data_estrazione
    """
    righe_target = db.query(sql_target, tuple(parametri_target))
    date_target = [str(r["data_estrazione"]) for r in righe_target]

    if ruota_spia == ruota_target:
        righe_spia = righe_target
    else:
        condizioni_spia = ["ruota = %s"]
        parametri_spia: list = [ruota_spia]
        if data_da:
            condizioni_spia.append("data_estrazione >= %s")
            parametri_spia.append(data_da)
        sql_spia = f"""
            SELECT data_estrazione, {', '.join(COLONNE_LOTTO)} FROM estrazioni_lotto
            WHERE {' AND '.join(condizioni_spia)} ORDER BY data_estrazione
        """
        righe_spia = db.query(sql_spia, tuple(parametri_spia))

    occorrenze_totali = 0
    occorrenze_utilizzate = 0
    conteggio: Counter = Counter()
    for riga in righe_spia:
        if numero_spia not in (riga[c] for c in COLONNE_LOTTO):
            continue
        occorrenze_totali += 1
        idx = bisect.bisect_right(date_target, str(riga["data_estrazione"]))
        finestra = righe_target[idx: idx + orizzonte_h]
        if len(finestra) < orizzonte_h:
            continue
        occorrenze_utilizzate += 1
        for r2 in finestra:
            for c in COLONNE_LOTTO:
                conteggio[r2[c]] += 1

    frequenza_naturale_attesa = orizzonte_h * 5 / 90
    ranking = []
    for numero in range(1, 91):
        freq_post = (conteggio.get(numero, 0) / occorrenze_utilizzate) if occorrenze_utilizzate else None
        indice = (freq_post / frequenza_naturale_attesa) if freq_post is not None else None
        ranking.append({
            "numero": numero,
            "frequenza_post_spia": round(freq_post, 4) if freq_post is not None else None,
            "indice_attrattiva": round(indice, 3) if indice is not None else None,
        })
    ranking.sort(key=lambda r: (r["indice_attrattiva"] is None, -(r["indice_attrattiva"] or 0)))

    return {
        "numero_spia": numero_spia,
        "ruota_spia": ruota_spia,
        "ruota_target": ruota_target,
        "orizzonte_h": orizzonte_h,
        "occorrenze_spia_totali": occorrenze_totali,
        "occorrenze_spia_utilizzate": occorrenze_utilizzate,
        "campione_affidabile": occorrenze_utilizzate >= CAMPIONE_SPIA_MINIMO_AFFIDABILE,
        "frequenza_naturale_attesa": round(frequenza_naturale_attesa, 4),
        "ranking": ranking,
    }


def invalidare_cache_calcoli() -> None:
    """Da chiamare dopo ogni scraping/refresh riuscito (vedi main.py,
    /scraper/nuove e /scraper/refresh): i risultati con @lru_cache in
    questo modulo diventano stale non appena arriva una nuova estrazione."""
    _passo_ritardo.cache_clear()
