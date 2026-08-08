"""
backend/main.py
================
API FastAPI di lotto-stat-app (container lotto_stat_backend, servita
con "uvicorn main:app --reload" — vedi Dockerfile). Espone in HTTP le
stesse viste MySQL già usate dalla dashboard Streamlit (web/app.py) più
due endpoint per pilotare la pipeline di scraping
(scraper/update_pipeline.py) da un consumer esterno diverso dalla UI
interattiva (script, automazioni, un frontend diverso in futuro).

Non sostituisce Streamlit: è un livello API separato letto/scritto in
parallelo, non serve pagine HTML.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

import db

app = FastAPI(title="lotto-stat-app API", version="1.0.0")

RUOTE_LOTTO = (
    "Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli",
    "Palermo", "Roma", "Torino", "Venezia", "Nazionale",
)

# ---------------------------------------------------------------------
# Caricamento di scraper/update_pipeline.py per percorso file, non con
# un "import" semplice (stesso motivo per cui update_pipeline.py stesso
# carica backend/scraper.py per path: nomi di pacchetto/modulo ambigui
# a seconda di come il processo viene avviato). Il modulo va registrato
# in sys.modules PRIMA di exec_module: update_pipeline.py, a sua volta,
# carica backend/scraper.py che usa @dataclass, il cui decoratore
# fallisce con AttributeError se il modulo non è ancora nel registry
# (bug reale già riscontrato e corretto in scraper/update_pipeline.py).
# ---------------------------------------------------------------------
_PIPELINE_PATH = Path(__file__).resolve().parent / ".." / "scraper" / "update_pipeline.py"


def _carica_modulo_per_path(nome: str, percorso: Path):
    spec = importlib.util.spec_from_file_location(nome, percorso)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossibile caricare '{percorso}'")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


pipeline = _carica_modulo_per_path("lotto_update_pipeline", _PIPELINE_PATH)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/stato")
def stato() -> dict:
    righe = db.query(
        """
        SELECT
            (SELECT MAX(data_estrazione) FROM estrazioni_lotto) AS ultima_estrazione_lotto,
            (SELECT COUNT(*) FROM estrazioni_lotto) AS righe_lotto,
            (SELECT MAX(data_estrazione) FROM estrazioni_superenalotto) AS ultima_estrazione_superenalotto,
            (SELECT COUNT(*) FROM estrazioni_superenalotto) AS righe_superenalotto
        """
    )
    return righe[0] if righe else {}


def _valida_ruota(ruota: str) -> None:
    if ruota not in RUOTE_LOTTO:
        raise HTTPException(status_code=400, detail=f"Ruota non valida. Valori ammessi: {', '.join(RUOTE_LOTTO)}")


@app.get("/lotto/ritardatari")
def lotto_ritardatari(
    ruota: str = Query(..., description="Nome esteso della ruota, es. 'Napoli'"),
    limite: int = Query(10, ge=1, le=90),
) -> list[dict]:
    _valida_ruota(ruota)
    return db.query(
        """
        SELECT numero, ritardo_attuale, ritardo_storico_max, indice_convenienza
        FROM cache_lotto_ritardo
        WHERE ruota = %s
        ORDER BY ritardo_attuale DESC
        LIMIT %s
        """,
        (ruota, limite),
    )


@app.get("/lotto/frequenze")
def lotto_frequenze(
    ruota: str = Query(..., description="Nome esteso della ruota, es. 'Napoli'"),
    limite: int = Query(10, ge=1, le=90),
) -> list[dict]:
    _valida_ruota(ruota)
    return db.query(
        """
        SELECT numero, frequenza, frequenza_relativa
        FROM cache_lotto_frequenza
        WHERE ruota = %s
        ORDER BY frequenza DESC
        LIMIT %s
        """,
        (ruota, limite),
    )


@app.get("/lotto/ambi")
def lotto_ambi(
    ruota: str = Query(..., description="Nome esteso della ruota, es. 'Napoli'"),
    limite: int = Query(10, ge=1, le=200),
) -> list[dict]:
    _valida_ruota(ruota)
    return db.query(
        """
        SELECT numero1, numero2, ritardo_attuale, frequenza
        FROM cache_lotto_ambi
        WHERE ruota = %s
        ORDER BY ritardo_attuale DESC
        LIMIT %s
        """,
        (ruota, limite),
    )


@app.get("/superenalotto/frequenze")
def sen_frequenze(limite: int = Query(10, ge=1, le=90)) -> list[dict]:
    return db.query(
        "SELECT numero, frequenza, frequenza_relativa FROM v_sen_frequenza_sestina ORDER BY frequenza DESC LIMIT %s",
        (limite,),
    )


@app.get("/superenalotto/ritardi")
def sen_ritardi(limite: int = Query(10, ge=1, le=90)) -> list[dict]:
    return db.query(
        "SELECT numero, ritardo_attuale FROM v_sen_ritardo_azzerato_2009 ORDER BY ritardo_attuale DESC LIMIT %s",
        (limite,),
    )


@app.post("/scraper/nuove")
def scraper_nuove() -> dict:
    """Recupera e inserisce le ultime estrazioni, poi aggiorna la cache
    statistiche. Stessa logica invocata dal pannello di controllo di
    web/app.py (lì via subprocess) — qui in-process, dato che main.py
    gira già come processo Python dedicato sotto uvicorn."""
    return pipeline.esegui_pipeline_nuove_estrazioni()


@app.post("/scraper/refresh")
def scraper_refresh() -> dict:
    return pipeline.esegui_solo_refresh_cache()
