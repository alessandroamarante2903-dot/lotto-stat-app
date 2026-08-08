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

### Bug reali trovati testando lo stack containerizzato (non teorici — riprodotti e risolti)

- **`:Z` vs `:z` su path montati in più container → `Permission denied`**: SELinux (Fedora, enforcing di default) con `:Z` assegna una label MCS **privata** al path host: se lo stesso host path (`./backend`, `./scraper`, `./db/storici`) è montato in PIÙ container (`lotto_stat_web` e `lotto_stat_backend` montano entrambi gli stessi tre), l'ultimo container che parte si prende l'accesso esclusivo e l'altro riceve `Permission denied` anche con permessi POSIX corretti (verificato con `ls -laZ`: categoria `s0:cX,cY` esclusiva). Fix: `:z` minuscola (label condivisa) per qualunque path montato in più di un container; `:Z` resta corretto solo per path esclusivi di un singolo container (es. `./web`, `./nginx/nginx.conf`, `./grafana/provisioning`).
- **`uvicorn --reload` in reload-storm su `backend/.venv`**: montare l'intero `./backend` (che include il venv locale usato per sviluppare/testare fuori da Podman) dentro il container fa sì che WatchFiles cammini ricorsivamente su quel venv (decine di migliaia di file) ad ogni avvio, con un log da >200KB per un solo evento e successivo crash del reloader (`PermissionError` durante lo spawn del worker). Fix a due livelli: volumi anonimi che mascherano `/app/backend/.venv` (e i `__pycache__`) dentro sia `web` sia `backend`, più `--reload-exclude '.venv/*'` in `backend/Dockerfile` come difesa in profondità. Streamlit (`web`) non ha lo stesso problema: il suo watcher segue solo i moduli realmente importati, non cammina l'intero albero di `/app`.
- **`CALL sp_refresh_tutte_le_cache()` impiega ~4 minuti su storico reale**: misurato in pratica (Lotto dal 1939 + SuperEnalotto, ~77k + ~4,3k righe). Il timeout del pannello "Gestione Scraper" in `web/app.py` era 180s: falliva per timeout anche a refresh riuscito. Alzato a `TIMEOUT_PIPELINE_SECONDI = 600`.
- **502 Bad Gateway dopo il riavvio di un solo container** (`Host is unreachable` nei log di `lotto_stat_proxy`): un blocco `upstream { server web:8501; }` in nginx risolve il nome DNS **una sola volta**, all'avvio/reload di nginx. Se `web` (o `backend`/`grafana`) viene ricreato da `podman-compose` senza che anche `proxy` riparta, il container ottiene un nuovo IP sulla rete Podman ma nginx continua a puntare al vecchio. Fix in `nginx/nginx.conf`: niente più blocchi `upstream{}` statici, sostituiti da `resolver 10.89.0.1 valid=10s;` (aardvark-dns della rete Podman — verificare con `podman exec lotto_stat_proxy cat /etc/resolv.conf` se la subnet cambia) + `set $upstream_xxx host:porta;` prima di ogni `proxy_pass http://$upstream_xxx;`, che forza una nuova risoluzione DNS ad ogni richiesta entro il TTL. **Attenzione all'ordine**: nel blocco `/api/` il `set` va scritto PRIMA di `rewrite ^/api/(.*)$ /$1 break;`, non dopo — nell'ordine inverso (riscontrato in pratica) la variabile arriva vuota a `proxy_pass`, che fallisce con `500` e log `invalid URL prefix in "http://"`.
- **`v_sen_somma_distribuzione` rompe con `sql_mode=only_full_group_by`**: la vista (`db/init-scripts/views/04_viste_superenalotto.sql`) faceva `GROUP BY tipo_regolamento, FLOOR(somma / 20)` ma il `SELECT` esponeva `FLOOR(somma/20)*20` e `FLOOR(somma/20)*20+19` — MySQL 8 (default `only_full_group_by`) verifica l'**identità sintattica** delle espressioni, non la dipendenza funzionale: `FLOOR(x)*20` non è considerato "lo stesso" di `FLOOR(x)`, quindi errore `1055`. Causava un traceback visibile nella tab "Statistiche → SuperEnalotto" di `web/app.py` (uno degli "errori" segnalati). Fix: `GROUP BY` deve ripetere le stesse espressioni del `SELECT` parola per parola. Corretto in `04_viste_superenalotto.sql` e nel concatenato `00_tutto_in_uno.sql`, e riapplicato a caldo sul DB con `CREATE OR REPLACE VIEW` (non serve un redeploy completo del motore per un fix di una singola vista).
- **Transazione MySQL mai committata, aperta per ore, che blocca `CREATE OR REPLACE VIEW`/DDL e degrada le prestazioni**: `web/db.py` usa un'UNICA connessione `mysql.connector` condivisa per tutta la vita del processo Streamlit (`st.cache_resource`). `mysql-connector-python` ha **`autocommit=False` di default** (a differenza di altri driver): ogni `SELECT` in `query_df()` apriva quindi una transazione che restava agganciata alla connessione finché non arrivava un commit esplicito — che per un percorso di sola lettura non c'era mai. Riscontrato in pratica: dopo qualche minuto di uso della dashboard, `information_schema.innodb_trx` mostrava una transazione viva da centinaia di secondi con `SHARED_READ` lock su decine di viste/tabelle, che bloccava indefinitamente qualunque DDL da un'altra sessione (`SHOW PROCESSLIST` → `Waiting for table metadata lock`) ed è una concausa plausibile della lentezza generale percepita (history list InnoDB che cresce senza mai essere ripulita). Fix: `mysql.connector.connect(autocommit=True, ...)` sia in `web/db.py` sia — per difesa in profondità, anche se il pool sembrava già al sicuro grazie al reset-on-return — in `backend/db.py` (`MySQLConnectionPool(..., autocommit=True)`). **Verificare dopo ogni modifica a `web/db.py`**: un `podman-compose up -d --build web` da solo NON basta a far ripartire il processo se il container non viene anche *ricreato* (comportamento riscontrato più volte in questa sessione: l'immagine si aggiorna ma il container già in esecuzione continua con il vecchio processo Python, che ha già importato il modulo `db` in memoria) — serve `--force-recreate web` per garantire che il fix sia davvero attivo.

### Riavvii periodici di `lotto_stat_web`: causa VERA isolata e risolta (la diagnosi "pressione memoria host" era sbagliata)

Il container si riavviava da solo (exit 134 SIGABRT, poi 139 SIGSEGV dopo il fix di `use_pure`), sempre durante la navigazione utente reale in browser — indizio decisivo che ha permesso di isolare la causa vera, diversa da quanto ipotizzato in un primo momento (vedi sotto).

**Riproduzione deterministica** con `streamlit.testing.v1.AppTest` (simula rerun reali dentro il container, senza bisogno di un browser):
```python
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("/app/app.py")
at.run(timeout=30)
at.selectbox(key="ruota_statistiche").set_value("Bari").run(timeout=30)  # <- crash qui, 100% delle volte
```
Crash al **secondo rerun**, sempre, non intermittente. Bisezionato togliendo pezzi da `app.py` fino a un caso minimo: **`st.dataframe(df, ...)` da solo, con un DataFrame statico hardcoded (nessun DB, nessun `Decimal`, nessun plotly) crasha comunque al secondo rerun.** `plotly` da solo, sugli stessi rerun, non crasha mai. Questo ha escluso completamente sia il DB layer sia i tipi `Decimal` di MySQL (entrambe ipotesi intermedie rivelatesi sbagliate) e isolato il problema nel percorso di serializzazione Arrow di `st.dataframe()` (pandas → pyarrow → Arrow IPC).

**Causa reale**: `web/requirements.txt` non pinnava `numpy`/`pyarrow` (dipendenze transitive di streamlit/pandas). Alla build, pip risolveva **numpy 2.4.6 + pyarrow 25.0.0** — molto più recenti di quanto `streamlit==1.38.0`/`pandas==2.2.2` (rilasciate a metà 2024) siano mai state testate contro. **ABI mismatch di numpy 2.x** nel codice C++ di conversione pyarrow↔numpy, che segfaulta silenziosamente al secondo utilizzo dello stesso percorso di serializzazione all'interno dello stesso processo.

**Fix**: pinnati esplicitamente in `web/requirements.txt`:
```
numpy==1.26.4
pyarrow==17.0.0
```
(versioni coeve a streamlit 1.38.0/pandas 2.2.2, prima della transizione ABI di numpy 2.0). Verificato dopo il fix: stesso identico test di riproduzione, **0 crash su 80 rerun consecutivi** (5 giri × 16 interazioni ciascuno, tutte le 11 ruote, entrambi i giochi, calcolatore integrale/ridotto/Lotto). Prima del fix falliva già al secondo.

`use_pure=True` su `mysql.connector` (web/backend/scraper) e `mem_limit` su `web` in `podman-compose.yml` restano comunque applicati: non erano la causa di questo bug specifico, ma sono difese ragionevoli indipendenti (rispettivamente: fragilità nota dell'estensione C di mysql-connector sotto multithreading/pressione, e contenimento generico della memoria di un container). Il trap diagnostico sui segnali in `web/Dockerfile` (`sh -c 'trap ... TERM; streamlit run ... & wait $pid; ...'`) è stato lasciato attivo: utile per diagnosticare rapidamente un futuro crash nativo (segnale + exit code nei log) senza dover ripetere da capo l'isolamento con AppTest.

**Lezione**: build senza pin su dipendenze transitive pesanti (numpy, pyarrow) sono un rischio reale di regressione binaria silenziosa — un rebuild dell'immagine in un giorno diverso può risolvere versioni diverse e reintrodurre lo stesso bug se i pin vengono rimossi.
