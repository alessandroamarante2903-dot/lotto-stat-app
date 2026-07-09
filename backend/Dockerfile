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

WORKDIR /app

# Copia e installa le dipendenze Python (incluso selenium e bs4)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice (compreso il nuovo scraper modificato da Claude)
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]