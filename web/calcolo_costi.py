"""
web/calcolo_costi.py
=====================

Combinatoria e calcolo costi per il Playground/Calcolatore Schedine
(Tab "Calcolatore & Sistemi" di app.py). Nessuna dipendenza dal
database: puro calcolo, testabile in isolamento.

SuperEnalotto
-------------
Quota ufficiale ADM per colonna: 1,00 € di puntata + 0,25 € di quota
Stato/raccolta = 1,25 €/colonna (QUOTA_UNITARIA_SUPERENALOTTO). Un
Sistema Integrale su N numeri gioca TUTTE le combinazioni possibili di
6 su N: colonne = C(N,6).

Il Sistema Ridotto qui implementato NON è una delle tabelle di
riduzione ufficiali Sisal (sono schemi proprietari pubblicati come
prodotto commerciale a parte, non disponibili in un formato
machine-readable pubblico): è un algoritmo di "covering design" greedy,
matematicamente corretto per la garanzia dichiarata (copre sempre TUTTE
le combinazioni di 'garanzia' numeri scelti dall'utente con almeno una
colonna giocata) ma non garantito ottimale al 100% nel numero minimo di
colonne, perché il covering design minimo è un problema NP-hard. Va
presentato in UI per quello che è: una riduzione euristica, non lo
schema Sisal.

Lotto
-----
Puntata minima ufficiale ADM: 1,00 €/colonna/ruota, in multipli di
0,50 €. Sorte richiesta -> quanti numeri devono uscire (Estratto=1,
Ambo=2, Terno=3, Quaterna=4, Cinquina=5). Se si scelgono più numeri di
quelli richiesti dalla sorte si gioca "a sistema": colonne = C(N,k).
Il costo si moltiplica per il numero di ruote selezionate, perché ogni
ruota è una giocata indipendente.
"""

from __future__ import annotations

import itertools
import math

QUOTA_UNITARIA_SUPERENALOTTO = 1.25
NUMERI_PER_COLONNA_SUPERENALOTTO = 6
MAX_NUMERI_SISTEMA_INTEGRALE = 20   # oltre, C(N,6) esplode a costi non plausibili
MAX_NUMERI_SISTEMA_RIDOTTO = 16     # limite di reattività dell'algoritmo greedy (vedi sotto)

QUOTA_MINIMA_LOTTO = 1.00
INCREMENTO_PUNTATA_LOTTO = 0.50

SORTI_LOTTO = {
    "estratto": 1,
    "ambo": 2,
    "terno": 3,
    "quaterna": 4,
    "cinquina": 5,
}

RUOTE_LOTTO = (
    "Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli",
    "Palermo", "Roma", "Torino", "Venezia", "Nazionale",
)


class CalcoloCostiError(ValueError):
    """Errore di validazione input per il calcolatore schedine/sistemi."""


def combinazioni(n: int, k: int) -> int:
    if k < 0 or n < 0 or k > n:
        return 0
    return math.comb(n, k)


# =====================================================================
# SUPERENALOTTO
# =====================================================================

def sistema_integrale_superenalotto(numeri: list[int]) -> dict:
    numeri = sorted(set(numeri))
    n = len(numeri)
    if n < NUMERI_PER_COLONNA_SUPERENALOTTO:
        raise CalcoloCostiError(f"Servono almeno {NUMERI_PER_COLONNA_SUPERENALOTTO} numeri distinti (selezionati: {n}).")
    if n > MAX_NUMERI_SISTEMA_INTEGRALE:
        raise CalcoloCostiError(f"Massimo {MAX_NUMERI_SISTEMA_INTEGRALE} numeri per il sistema integrale (selezionati: {n}).")
    if any(not (1 <= x <= 90) for x in numeri):
        raise CalcoloCostiError("I numeri del SuperEnalotto devono essere compresi tra 1 e 90.")

    colonne = combinazioni(n, NUMERI_PER_COLONNA_SUPERENALOTTO)
    return {
        "tipo": "integrale",
        "numeri": numeri,
        "numero_colonne": colonne,
        "quota_unitaria_euro": QUOTA_UNITARIA_SUPERENALOTTO,
        "costo_totale_euro": round(colonne * QUOTA_UNITARIA_SUPERENALOTTO, 2),
    }


def sistema_ridotto_superenalotto(numeri: list[int], garanzia: int = 2) -> dict:
    """Greedy set-cover: sceglie il minor numero di colonne da 6 (tra
    quelle possibili sugli N numeri scelti) tali che ogni sotto-insieme
    di 'garanzia' numeri sia contenuto in almeno una colonna giocata."""
    numeri = sorted(set(numeri))
    n = len(numeri)
    if n < NUMERI_PER_COLONNA_SUPERENALOTTO:
        raise CalcoloCostiError(f"Servono almeno {NUMERI_PER_COLONNA_SUPERENALOTTO} numeri distinti (selezionati: {n}).")
    if n > MAX_NUMERI_SISTEMA_RIDOTTO:
        raise CalcoloCostiError(f"Massimo {MAX_NUMERI_SISTEMA_RIDOTTO} numeri per il sistema ridotto (selezionati: {n}).")
    if garanzia not in (2, 3, 4, 5):
        raise CalcoloCostiError("La garanzia deve essere: 2 (ambo), 3 (terno), 4 (quaterna) o 5 (cinquina).")
    if any(not (1 <= x <= 90) for x in numeri):
        raise CalcoloCostiError("I numeri del SuperEnalotto devono essere compresi tra 1 e 90.")

    da_coprire = set(itertools.combinations(numeri, garanzia))
    tutte_le_colonne = list(itertools.combinations(numeri, NUMERI_PER_COLONNA_SUPERENALOTTO))
    sottoinsiemi_per_colonna = {
        col: set(itertools.combinations(col, garanzia)) for col in tutte_le_colonne
    }

    colonne_scelte: list[tuple[int, ...]] = []
    coperti: set[tuple[int, ...]] = set()
    candidati = list(tutte_le_colonne)

    while coperti != da_coprire:
        migliore = max(candidati, key=lambda col: len(sottoinsiemi_per_colonna[col] - coperti))
        nuovi = sottoinsiemi_per_colonna[migliore] - coperti
        if not nuovi:
            break  # difensivo: con n >= 6 non dovrebbe mai accadere prima di aver coperto tutto
        colonne_scelte.append(migliore)
        coperti |= nuovi
        candidati.remove(migliore)

    numero_colonne = len(colonne_scelte)
    return {
        "tipo": "ridotto",
        "numeri": numeri,
        "garanzia": garanzia,
        "colonne": colonne_scelte,
        "numero_colonne": numero_colonne,
        "colonne_sistema_integrale_equivalente": combinazioni(n, NUMERI_PER_COLONNA_SUPERENALOTTO),
        "quota_unitaria_euro": QUOTA_UNITARIA_SUPERENALOTTO,
        "costo_totale_euro": round(numero_colonne * QUOTA_UNITARIA_SUPERENALOTTO, 2),
    }


# =====================================================================
# LOTTO
# =====================================================================

def costo_lotto(numeri: list[int], sorte: str, ruote: list[str], puntata_unitaria: float = QUOTA_MINIMA_LOTTO) -> dict:
    sorte = sorte.lower().strip()
    if sorte not in SORTI_LOTTO:
        raise CalcoloCostiError(f"Sorte sconosciuta '{sorte}'. Valori validi: {', '.join(SORTI_LOTTO)}.")
    k = SORTI_LOTTO[sorte]

    numeri = sorted(set(numeri))
    n = len(numeri)
    if n < k:
        raise CalcoloCostiError(f"La sorte '{sorte}' richiede almeno {k} numeri (selezionati: {n}).")
    if any(not (1 <= x <= 90) for x in numeri):
        raise CalcoloCostiError("I numeri del Lotto devono essere compresi tra 1 e 90.")

    ruote_scelte = list(dict.fromkeys(ruote))  # dedup mantenendo l'ordine
    if not ruote_scelte:
        raise CalcoloCostiError("Seleziona almeno una ruota (o 'Tutte').")
    non_valide = [r for r in ruote_scelte if r not in RUOTE_LOTTO]
    if non_valide:
        raise CalcoloCostiError(f"Ruote non valide: {', '.join(non_valide)}.")

    passi_da_incremento = round((puntata_unitaria - QUOTA_MINIMA_LOTTO) / INCREMENTO_PUNTATA_LOTTO, 6)
    if puntata_unitaria < QUOTA_MINIMA_LOTTO or abs(passi_da_incremento - round(passi_da_incremento)) > 1e-9:
        raise CalcoloCostiError(
            f"La puntata unitaria minima è {QUOTA_MINIMA_LOTTO:.2f}€, in multipli di {INCREMENTO_PUNTATA_LOTTO:.2f}€."
        )

    colonne_per_ruota = combinazioni(n, k)
    numero_ruote = len(ruote_scelte)
    costo_totale = round(colonne_per_ruota * numero_ruote * puntata_unitaria, 2)

    return {
        "numeri": numeri,
        "sorte": sorte,
        "numeri_richiesti": k,
        "ruote": ruote_scelte,
        "numero_ruote": numero_ruote,
        "colonne_per_ruota": colonne_per_ruota,
        "puntata_unitaria_euro": puntata_unitaria,
        "costo_totale_euro": costo_totale,
    }
