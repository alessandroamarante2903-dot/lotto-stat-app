==# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Lingua

Tutte le conversazioni in questo repository avvengono in italiano (vedi `.clauderc`).

## Panoramica del progetto

App di statistiche per Lotto e SuperEnalotto italiani. Uno scraper Python popola un database MySQL con lo storico e le nuove estrazioni; un motore SQL di viste e stored procedure calcola frequenze, ritardi, ambi/terzine e altre statistiche derivate; un frontend Streamlit espone dashboard, calcolatore schedine/sistemi e pannello di controllo per lo scraping; un'API FastAPI espone le stesse statistiche in HTTP per consumer esterni; Grafana offre analytics avanzate sulle stesse viste MySQL; Nginx fa da reverse proxy unico. Ambiente containerizzato con Podman rootless su Fedora.

**`podman-compose.yml`** (root) è l'orchestrazione attuale, a 5 container: `lotto_stat_db`, `lotto_stat_web`, `lotto_stat_backend`, `lotto_stat_grafana`, `lotto_stat_proxy`. Il vecchio `docker-compose.yml` (solo `db` + `backend`, quest'ultimo con un comando `uvicorn main:app` a lungo non funzionante perché `backend/main.py` non esisteva) resta nel repo ma è superato: `backend/main.py` ora esiste davvero (vedi sotto) e `lotto_stat_backend` lo serve con hot-reload; lo scraping resta invocabile anche da `lotto_stat_web` (pannello di controllo), che monta `backend/` e `scraper/` allo stesso modo.

## Comandi comuni

Avvio dello stack completo (Podman rootless):
```bash
cp .env.example .env   # poi valorizza MYSQL_ROOT_PASSWORD, DB_PASSWORD, GRAFANA_ADMIN_PASSWORD
podman-compose up -d
```
Accesso: Streamlit su `http://localhost/`, API FastAPI su `http://localhost/api/` (o direttamente `http://localhost:8000/`, docs interattivi su `/docs`), Grafana su `http://localhost/grafana/` (tutti dietro `lotto_stat_proxy`, porta 80).

Esecuzione manuale dello scraper "grezzo" (dentro il container o in locale con le stesse variabili d'ambiente):
```bash
python backend/scraper.py                                   # tutte le fasi: crea tabelle, storico, nuove estrazioni
python backend/scraper.py --setup                            # solo FASE 1: crea/verifica le tabelle
python backend/scraper.py --storico                          # solo FASE 2: importa db/storici/storico.txt
python backend/scraper.py --nuove                            # solo FASE 3: recupera l'ultima estrazione via web/API
python backend/scraper.py --superenalotto-storico-lottoced --dal-anno 1997 --al-anno 2009  # FASE 3-ter: backfill storico SuperEnalotto
```

Pipeline "con post-processing" (inserimento + refresh cache statistiche + validazione — usata dal pannello Streamlit, vedi sotto):
```bash
python scraper/update_pipeline.py --nuove          # recupera+inserisce ultime estrazioni, poi refresh cache
python scraper/update_pipeline.py --refresh-only   # solo refresh cache (retry senza ri-scaricare)
```

Deploy del motore di viste/procedure SQL (una tantum, va fatto a mano perché il volume `db_data` è già inizializzato — vedi `db/init-scripts/README.md`):
```bash
podman exec -i lotto_stat_db mysql -uroot -p"$MYSQL_ROOT_PASSWORD" lotto_statistics < db/init-scripts/00_tutto_in_uno.sql
```

Refresh manuale delle cache statistiche via client `mysql` (equivalente SQL di `update_pipeline.py --refresh-only`):
```sql
CALL sp_refresh_tutte_le_cache();
```

Non esistono al momento suite di test, linter o build automatizzati in questo repo (eccetto i test manuali ad-hoc di `web/calcolo_costi.py`, senza framework, lanciati come script Python).

## Architettura

### Componenti

- **`backend/scraper.py`**: script Python unico che gestisce l'intero ciclo di vita del database. È strutturato in fasi sequenziali (vedi docstring in testa al file per il dettaglio completo):
  - FASE 1 `crea_tabelle()`: `CREATE TABLE IF NOT EXISTS` + migrazione automatica idempotente (es. aggiunta di `tipo_regolamento` su installazioni preesistenti).
  - FASE 2 `carica_storico_locale()`: importa `db/storici/storico.txt` solo se `estrazioni_lotto` è vuota.
  - FASE 3 `aggiorna_nuove_estrazioni()`: recupera l'ultima estrazione via un'architettura a provider con fallback (vedi sotto) e la inserisce se mancante.
  - FASE 3-ter `aggiorna_superenalotto_storico_lottoced()`: backfill storico completo del SuperEnalotto da lottoced.com, anno per anno, con upsert in batch.
  - Si connette sempre come `statistics_user` (mai come root), configurabile via variabili d'ambiente (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`).
  - Eccezioni dedicate: `ScraperNetworkError`, `ScraperParsingError` (sottoclassi di `ScraperError`), gestite in modo esplicito ad ogni livello.
- **`backend/main.py`** (container `lotto_stat_backend`, servito con `uvicorn main:app --reload`): API FastAPI sulle stesse viste MySQL della dashboard Streamlit (`/lotto/ritardatari`, `/lotto/frequenze`, `/lotto/ambi`, `/superenalotto/frequenze`, `/superenalotto/ritardi`, `/stato`, `/health`) più `POST /scraper/nuove` e `POST /scraper/refresh` che richiamano `scraper/update_pipeline.py` in-process (caricato per path con lo stesso pattern `importlib` + registrazione in `sys.modules` prima di `exec_module`, per lo stesso motivo dataclass). `backend/db.py` usa un vero connection pool (`mysql.connector.pooling`), non una singola connessione condivisa: uvicorn esegue gli endpoint sync in un threadpool, quindi richieste concorrenti reali sono possibili (a differenza di `web/db.py`, dove Streamlit serve di fatto una sessione alla volta). **Layout container non banale**: `backend/Dockerfile` ha `WORKDIR /app/backend` (non `/app`) apposta, perché a runtime `podman-compose.yml` monta `./backend` su `/app/backend` e `./scraper` su `/app/scraper`, sibling sotto `/app` — esattamente il layout che il percorso relativo `"../backend/scraper.py"` dentro `update_pipeline.py` si aspetta di trovare.

- **`db/init-scripts/init.sql`**: schema base (`estrazioni_lotto`, `estrazioni_superenalotto`) e creazione di `statistics_user`. Eseguito automaticamente da MySQL solo alla primissima creazione del volume `db_data`.

- **`db/init-scripts/views/`**: motore SQL di statistiche, sei file numerati (01→06) + `00_tutto_in_uno.sql` che li concatena. Vanno eseguiti in ordine come utente **root** (non `statistics_user`), perché serve `CREATE VIEW`/`CREATE ROUTINE`. Dopo la creazione, le stored procedure girano con i privilegi del *definer*, quindi a runtime basta `EXECUTE` per `statistics_user`. Dettagli, query di esempio e changelog completi in `db/init-scripts/README.md` — consultarlo prima di modificare le viste, specialmente le decisioni di design già prese (vedi sotto).

- **`db/storici/storico.txt`**: archivio storico ufficiale del Lotto (1939-oggi), una riga per ruota per estrazione, separata da spazi/tab: `AAAA/MM/GG  SIGLA  n1 n2 n3 n4 n5`. Le sigle valide sono le 11 ruote ufficiali Lottomatica (vedi `SIGLE_RUOTE` in `scraper.py`).

- **`scraper/update_pipeline.py`**: wrapper attorno a `backend/scraper.py`, caricato per **percorso file** con `importlib` (non `import scraper` semplice: il pacchetto stesso si chiama `scraper/`, e caricare per path evita ambiguità a seconda di come lo script viene invocato). **Attenzione se lo si modifica**: il modulo caricato va registrato in `sys.modules[spec.name]` PRIMA di `spec.loader.exec_module(...)` — il `@dataclass` usato in `backend/scraper.py` risolve `cls.__module__` tramite `sys.modules.get(...)` durante la propria esecuzione e va in `AttributeError` se il modulo non è ancora nel registry (bug reale riscontrato e corretto in fase di sviluppo, non solo teorico). Espone `esegui_pipeline_nuove_estrazioni()` e `esegui_solo_refresh_cache()`, richiamate dal Pannello di Controllo di `web/app.py` via `subprocess`.

- **`web/`** (container `lotto_stat_web`, Streamlit): `app.py` (3 tab: Statistiche, Calcolatore & Sistemi, Gestione Scraper), `db.py` (connessione MySQL condivisa con `st.cache_resource` + query cachate con `st.cache_data`, TTL 60s), `calcolo_costi.py` (combinatoria pura, nessuna dipendenza DB — vedi sotto). Monta anche `backend/` e `scraper/` per poter invocare lo scraping on-demand via `subprocess`. `.streamlit/config.toml` ha `runOnSave = true`: Streamlit non è un'app ASGI (nessun `uvicorn --reload` applicabile), ma con il codice montato a volume e questa opzione si auto-riavvia comunque ad ogni modifica di un file, senza il prompt manuale di conferma.

- **`nginx/nginx.conf`** (container `lotto_stat_proxy`): unico punto d'ingresso (porta 80, predisposto per 443). `/` → Streamlit (con `Upgrade`/`Connection` per il WebSocket); `/api/` → FastAPI (`lotto_stat_backend`, con rewrite che toglie il prefisso `/api/`); `/grafana/` → Grafana (che genera già i propri link con quel prefisso grazie a `GF_SERVER_SERVE_FROM_SUB_PATH=true`, impostato in `podman-compose.yml`).

- **`grafana/provisioning/datasources/datasource.yml`** (container `lotto_stat_grafana`): datasource MySQL provisionato automaticamente al primo avvio, credenziali lette dalle variabili d'ambiente del container (sintassi `$VARIABILE` nativa di Grafana ≥ 8). MySQL è un datasource core: nessun plugin da installare.

### Modello dati chiave

- **Lotto**: una riga per ruota per estrazione. La **ruota è la variabile chiave** — un numero estratto a Bari ha una storia statistica indipendente da quello estratto a Milano o sulla Nazionale. `concorso` è nullable perché lo storico ufficiale non lo riporta (solo data+ruota); il vincolo di unicità è su `(data_estrazione, ruota)`.

- **SuperEnalotto — tre ere regolamentari** (`tipo_regolamento`, calcolato automaticamente da `_determina_tipo_regolamento()`, mai inserito a mano): il meccanismo di estrazione è cambiato nel tempo e questo condiziona pesantemente le statistiche se le epoche vengono mescolate senza distinzione.
  - `SIMULATO` (< 3 dic 1997): estrazioni retroattive, regole SuperEnalotto applicate ai vecchi dati del Lotto.
  - `LOTTO` (3 dic 1997 – 30 giu 2009): gioco reale ma combinazione derivata dalle prime estrazioni di 6 ruote del Lotto; Jolly = 2° estratto di Bari; SuperStar (dal 2006) = ruota Nazionale.
  - `INDIPENDENTE` (dal 1° lug 2009): urne dedicate, estrazione totalmente separata per sestina+Jolly e per SuperStar.
  - Le viste di frequenza filtrano di default su `INDIPENDENTE`; il ritardo è sempre esposto in due viste esplicite mai fuse (`*_storico_completo` vs `*_azzerato_2009`); Ambi/Terni sono sempre chiavati anche su `tipo_regolamento`.

### Architettura a provider con fallback (FASE 3)

Per ogni gioco esiste una lista ordinata di provider (`PROVIDERS_LOTTO`, `PROVIDERS_SUPERENALOTTO`), tentati in ordine finché uno non restituisce un risultato valido: i provider API (RapidAPI, magayo — richiedono API key via variabile d'ambiente) vengono tentati per primi perché più stabili, lo scraping HTML di lottoced.com resta come ultima rete di sicurezza, sempre disponibile. I siti ufficiali (sisal.it, lotto-italia.it, superenalotto.it) sono protetti da bot-detection/WAF e non sono usati come fonte. Selenium (`_init_selenium_driver`) è disponibile nel codice ma non richiamato da nessun percorso corrente: lottoced.com è server-renderizzato e raggiungibile via `requests`/BeautifulSoup diretti. I punti di parsing HTML fragili (dipendenti dal markup di terzi) sono segnalati con `# TODO` nel codice.

### Decisioni di design da conoscere (motore SQL)

Riassunto delle scelte più importanti — dettagli completi in `db/init-scripts/README.md`:
- Il **ritardo** è sempre conteggiato in numero di estrazioni, mai giorni di calendario.
- **Cache = `DELETE FROM` + `INSERT` dentro una transazione**, mai `TRUNCATE` (che fa commit implicito e comprometterebbe il rollback in caso di errore).
- Ambi materializzano sempre l'universo completo delle combinazioni possibili; Terzine/Terni solo le combinazioni effettivamente uscite (l'universo possibile sarebbe troppo grande).
- Decine del Lotto a gruppi regolari da 10 (1-10, 11-20, ...): se serve cambiare convenzione, va modificata solo `v_lotto_numero_famiglie`.

### Limite transazionale scoperto: insert + refresh cache NON sono atomici in un singolo commit

Le stored procedure `sp_refresh_lotto_*` / `sp_refresh_sen_*` (`db/init-scripts/views/03_*.sql`, `05_*.sql`) fanno ciascuna il proprio `START TRANSACTION`/`COMMIT`/`ROLLBACK` interno, per restare atomiche e richiamabili in modo indipendente anche da un client `mysql` esterno (design intenzionale, vedi `db/init-scripts/README.md`). In MySQL uno `START TRANSACTION` mentre la connessione ha già una transazione aperta esegue un **commit implicito** di quella precedente. Di conseguenza `scraper/update_pipeline.py` **non** prova a mettere insert+refresh in un'unica transazione con un solo commit finale (sarebbe fasullo): l'insert delle nuove estrazioni viene committato subito (i dati grezzi non vanno mai persi per un bug nel refresh), il refresh (`CALL sp_refresh_tutte_le_cache()`) è un passo separato con validazione propria, e in caso di fallimento resta disponibile un retry mirato (`--refresh-only` da CLI, pulsante dedicato in `web/app.py`) senza dover ri-scaricare nulla. Se in futuro serve vera atomicità end-to-end, l'unica strada è rimuovere la gestione transazionale interna dalle stored procedure e spostarla nel chiamante — cosa che romperebbe la loro atomicità/richiamabilità standalone attuale, quindi da concordare esplicitamente prima di farlo.

### Calcolatore Schedine/Sistemi (`web/calcolo_costi.py`)

Puro calcolo combinatorio, nessuna dipendenza dal DB (testabile in isolamento). SuperEnalotto: quota ADM 1,25 €/colonna (1,00 puntata + 0,25 Stato); Sistema Integrale = tutte le combinazioni C(N,6); Sistema Ridotto = **algoritmo di covering design greedy** (non le tabelle di riduzione ufficiali Sisal, che sono proprietarie e non pubblicate in formato machine-readable) — garantisce comunque per costruzione che ogni combinazione della garanzia scelta (ambo/terno/quaterna/cinquina) sia coperta da almeno una colonna, ma non è detto sia il numero minimo assoluto di colonne (il covering design minimo è NP-hard). Lotto: puntata minima 1,00 €/colonna/ruota in multipli di 0,50 €; colonne = C(N, k) dove k dipende dalla sorte (Estratto=1 … Cinquina=5); il costo si moltiplica per il numero di ruote selezionate.
