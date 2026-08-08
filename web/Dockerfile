FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Il codice vero e proprio (app.py, backend/, scraper/) arriva a runtime
# via volume (vedi podman-compose.yml): la COPY qui serve solo a far
# funzionare un "podman build" a sé stante, senza dipendere dai volumi.
COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health').read()" || exit 1

# DIAGNOSTICA TEMPORANEA: il container si riavviava da solo, exit code 0,
# nessun errore in log, nessun OOM/AVC/healthcheck a spiegarlo (vedi
# indagine in CLAUDE.md). Il trap qui stampa il segnale ricevuto PRIMA di
# terminare, per capire se è SIGTERM (qualcosa lo chiede "gentilmente",
# intercettabile) o altro. Va rimosso una volta trovata la causa.
CMD ["sh", "-c", "trap 'echo \"[DIAGNOSTICA] Ricevuto SIGTERM\" >&2' TERM; trap 'echo \"[DIAGNOSTICA] Ricevuto SIGINT\" >&2' INT; streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false & pid=$!; wait $pid; echo \"[DIAGNOSTICA] streamlit uscito con codice $?\" >&2"]
