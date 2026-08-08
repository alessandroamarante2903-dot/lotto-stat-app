FROM python:3.11-slim

# Evita che Python generi file .pyc e forza l'output in tempo reale nei log di Podman
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Impostiamo le variabili d'ambiente che Claude userà per trovare Chromium e il driver
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Installazione di Chromium, WebDriver e librerie di sistema necessarie per far girare il browser "headless"
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# WORKDIR annidato (non /app) apposta: a runtime podman-compose.yml
# monta ./backend su /app/backend e ./scraper su /app/scraper, sibling
# fra loro sotto /app — stesso layout del container lotto_stat_web.
# scraper/update_pipeline.py risolve backend/scraper.py con un percorso
# relativo ("../backend/scraper.py" rispetto a sé stesso): deve trovare
# davvero un /app/backend accanto a /app/scraper, non un /app piatto.
WORKDIR /app/backend

# Copia e installa le dipendenze Python (incluso selenium e bs4)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice: utile per un "podman build"/"podman run" a sé
# stante, senza volumi; a runtime con podman-compose viene comunque
# sovrascritto dal bind mount ./backend:/app/backend.
COPY . .

# --reload-exclude in difesa: il bind mount di ./backend porta dentro
# anche backend/.venv (venv locale usata per lo sviluppo/test fuori dal
# container, mai il vero .venv del container, che ha le sue dipendenze
# già installate da pip sopra). Senza esclusione, WatchFiles cammina
# ricorsivamente su migliaia di file di quel venv ad ogni avvio e va in
# reload-storm (visto in pratica: un solo evento con ~15.000 path
# elencati, seguito da un crash della fase di reload). L'esclusione qui
# è difesa in profondità: la vera correzione è NON montare .venv dentro
# il container (vedi l'anonymous volume che lo maschera in podman-compose.yml).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", \
     "--reload-exclude", ".venv/*", "--reload-exclude", "__pycache__/*"]