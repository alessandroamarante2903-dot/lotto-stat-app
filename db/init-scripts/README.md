# Motore di analisi statistica — Lotto & SuperEnalotto

Sei file SQL (+ una versione concatenata) che aggiungono a `lotto_statistics`
un motore completo di viste e stored procedure per Lotto e SuperEnalotto,
mantenendo netta separazione fra i due giochi in ogni oggetto.

## File e ordine di esecuzione

| File | Contenuto |
|---|---|
| `01_dimensioni_helper.sql` | `dim_ruote`, `dim_numeri` (1-90), famiglie classiche Lotto (`v_lotto_numero_famiglie`), `v_ambi_possibili` |
| `02_viste_lotto.sql` | Viste Lotto: basi, frequenze, RA/RS/IC, cadenze/decine/figure/gemelli/vertibili, posizionali |
| `03_procedure_cache_lotto.sql` | Cache + procedure Lotto: Ambi, Terzine, Isocronismi, Ritardo/Indice di Convenienza, Frequenza per ruota, Sincronismi, Cinquine ripetute |
| `04_viste_superenalotto.sql` | Viste SuperEnalotto: basi, frequenze per era, ritardi (2 versioni), Jolly/SuperStar, pari/dispari, decine, somma, consecutività |
| `05_procedure_cache_superenalotto.sql` | Cache + procedure SuperEnalotto: Ambi, Terni (segmentati per `tipo_regolamento`) |
| `06_master_refresh_e_grant.sql` | `sp_refresh_tutte_le_cache()` + GRANT minimi per `statistics_user` |
| `00_tutto_in_uno.sql` | 01→06 concatenati, per un deploy in un solo comando |

Vanno eseguiti **in quest'ordine** (o usa direttamente `00_tutto_in_uno.sql`),
**come utente root**, non come `statistics_user` — vedi la sezione Privilegi.

## Come applicarli al tuo ambiente Podman

Il tuo `db/init-scripts/` viene eseguito automaticamente da MySQL **solo alla
primissima creazione del volume** `db_data`. Dato che il database è già
popolato, questi file NON verranno raccolti automaticamente: vanno lanciati
a mano una tantum, poi salvati comunque dentro `db/init-scripts/` per
documentazione/riproducibilità di un eventuale ambiente pulito futuro.

```bash
# dalla root del progetto, con il container db già in esecuzione
podman exec -i lotto_stat_db mysql -uroot -prootpassword lotto_statistics < db/init-scripts/00_tutto_in_uno.sql
```

(la password `rootpassword` è quella in chiaro nel tuo `docker-compose.yml`
attuale — per un ambiente diverso da dev locale, cambiala).

⚠️ **Importante**: usa sempre il client `mysql` (come sopra), non
`mysql-connector-python`. I file usano `DELIMITER $$ ... $$` per definire le
stored procedure: è una direttiva capita solo dal client `mysql` (e da tool
come MySQL Workbench/phpMyAdmin), non è SQL valido per `cursor.execute()`
lato Python. Se in futuro vuoi automatizzare il deploy da `scraper.py` o da
un modulo di migrazione, esegui i file con `subprocess` che invoca il
client `mysql`, non con `mysql-connector-python`.

## Privilegi

Il tuo `init.sql` concede a `statistics_user`:
`SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER` su `lotto_statistics.*`.

Questo **non basta** per creare viste (`CREATE VIEW`) o stored procedure
(`CREATE ROUTINE`): per questo i file 01-06 vanno eseguiti da root, esattamente
come `init.sql`. Buona notizia: le stored procedure MySQL girano di default
con i privilegi del *definer* (chi le ha create), non del *caller*. Quindi,
una volta create da root, `statistics_user` ha bisogno solo di:

```sql
GRANT EXECUTE ON lotto_statistics.* TO 'statistics_user'@'%';
GRANT EXECUTE ON lotto_statistics.* TO 'statistics_user'@'localhost';
```

(già incluso in `06_master_refresh_e_grant.sql`). Le viste sono già leggibili
grazie al `SELECT` che `statistics_user` ha già a livello di schema.

## Changelog

- **Performance**: aggiunta `cache_lotto_ritardo` + `sp_refresh_lotto_ritardo()`
  (sezione 2quater di `03_procedure_cache_lotto.sql`) per materializzare
  `v_lotto_indice_convenienza`, misurata a ~8,9s per lettura filtrata (era
  la prima query eseguita dalla dashboard). Da cache: ~0,07s. Vedi "Nota
  sulle performance" più sotto per i dettagli della misura.
- **Performance**: aggiunta `cache_lotto_frequenza` + `sp_refresh_lotto_frequenza()`
  (sezione 2quinquies) per materializzare `v_lotto_frequenza_ruota`
  (~0,7s per lettura filtrata, la vista più lenta rimasta dopo il fix
  precedente). Da cache: ~0,08s.
- **Fix**: `v_sen_somma_distribuzione` falliva con errore 1055
  (`sql_mode=only_full_group_by`, default MySQL 8): il `GROUP BY`
  raggruppava su `FLOOR(somma / 20)` ma il `SELECT` esponeva
  `FLOOR(somma/20)*20` — MySQL verifica l'identità sintattica delle
  espressioni, non la dipendenza funzionale. Corretto ripetendo le stesse
  espressioni nel `GROUP BY`.
- **Fix**: `v_sen_superstar_frequenza`/`v_sen_superstar_ritardo` filtravano
  solo `superstar IS NOT NULL`, senza incrociare `tipo_regolamento`. Il
  SuperStar è stato introdotto il 7 maggio 2009, **prima** del cambio
  regolamento (1° luglio 2009): esisteva quindi una manciata di concorsi
  con SuperStar attivo ma ancora in era `LOTTO`, silenziosamente fusi con
  l'era `INDIPENDENTE`. Corretto filtrando sempre anche su
  `tipo_regolamento = 'INDIPENDENTE'` nelle viste di default, con
  `v_sen_superstar_frequenza_per_era` a esporre le altre ere separatamente.
- **Allineamento**: il Jolly ora ha lo stesso trattamento della sestina —
  `v_sen_jolly_frequenza_per_era` e la doppia versione del ritardo
  (`v_sen_jolly_ritardo_storico_completo` / `v_sen_jolly_ritardo_azzerato_2009`,
  al posto della precedente `v_sen_jolly_ritardo` unica). Stesso schema
  applicato anche al SuperStar
  (`v_sen_superstar_ritardo_storico_completo` / `v_sen_superstar_ritardo_azzerato_2009`).

## Decisioni di design da conoscere

- **Ritardo = numero di estrazioni**, mai giorni di calendario. Per il
  "tutte le ruote" del Lotto l'unità è la *data* di estrazione (le ruote
  estraggono lo stesso giorno); per il mono-ruota è la sequenza propria
  di quella ruota.
- **Ritardo Storico Massimo (RS)** = il più grande ritardo **chiuso**
  (terminato da un'estrazione reale) mai registrato, incluso l'eventuale
  gap iniziale prima della primissima uscita in archivio. Non include il
  ritardo aperto corrente (quello è RA — che diventa esso stesso record
  solo quando "chiude", cioè quando il numero viene finalmente estratto).
- **Decine del Lotto**: ho usato la convenzione a gruppi regolari da 10
  (1-10, 11-20, ..., 81-90), non quella "1-9/10-19/...". Se nel tuo progetto
  usi l'altra convenzione, va cambiata solo `v_lotto_numero_famiglie`
  (`decina`), tutto il resto la eredita.
- **Vertibili**: coppie di numeri a due cifre invertite (12↔21), escludendo
  gemelli, numeri terminanti in 0, e ribaltati fuori range 1-90 (es. 89→98
  non è un vertibile valido nel Lotto).
- **Isocronismo** (Lotto): confronto fatto sulla tabella *piccola* dei
  ritardi attuali (990 righe: 11 ruote × 90 numeri), quindi economico anche
  come self-join; solo le coppie con ritardo coincidente vengono verificate
  contro lo storico delle co-uscite.
- **Ambi vs Terzine/Terni**: gli Ambi materializzano **sempre** l'universo
  completo delle combinazioni possibili (4.005 × ruote/ere — poche righe,
  completezza utile). Terzine (Lotto) e Terni (SuperEnalotto) invece
  materializzano **solo le combinazioni effettivamente uscite** (l'universo
  possibile è 117.480 per gioco: troppe righe quasi tutte a frequenza 0 per
  avere senso pratico). `v_sen_terni_mai_usciti_conteggio` dà comunque il
  conteggio dei "mai usciti" per differenza, senza materializzarli.
- **SuperEnalotto — le tre regole non negoziabili** (dalla cronologia del
  progetto) sono rispettate ovunque: frequenze di default filtrate su
  `INDIPENDENTE`; ritardo esposto in due viste esplicite e mai fuse
  (`v_sen_ritardo_storico_completo` vs `v_sen_ritardo_azzerato_2009`);
  Ambi/Terni sempre chiavati anche su `tipo_regolamento`, mai solo su numero.
- **Cache = `DELETE FROM` + `INSERT` dentro una transazione**, non `TRUNCATE`:
  `TRUNCATE` fa commit implicito in MySQL, quindi in caso di errore nella
  fase di `INSERT` il `ROLLBACK` non riuscirebbe a ripristinare i dati
  cancellati. Con `DELETE` l'intero refresh è atomico.

## Uso quotidiano

Dopo ogni importazione di nuove estrazioni (fine di `scraper.py`):

```sql
CALL sp_refresh_tutte_le_cache();
```

oppure singolarmente:

```sql
CALL sp_refresh_lotto_ambi();
CALL sp_refresh_lotto_terzine();
CALL sp_refresh_lotto_isocronismi();
CALL sp_refresh_lotto_ritardo();
CALL sp_refresh_lotto_frequenza();
CALL sp_refresh_sen_ambi();
CALL sp_refresh_sen_terni();
```

Le viste (frequenze, RA, RS, IC, famiglie, posizionali, distribuzioni
SuperEnalotto) sono sempre "live": nessun refresh necessario, riflettono
l'ultima riga inserita in `estrazioni_lotto` / `estrazioni_superenalotto`.

## Query di esempio

```sql
-- I 10 numeri più ritardatari su Napoli, con il loro Indice di Convenienza
SELECT numero, ritardo_attuale, ritardo_storico_max, indice_convenienza
FROM v_lotto_indice_convenienza
WHERE ruota = 'NA'
ORDER BY ritardo_attuale DESC
LIMIT 10;

-- Ambi più ritardatari su Milano (dalla cache)
SELECT numero1, numero2, ritardo_attuale, frequenza
FROM cache_lotto_ambi
WHERE ruota = 'MI'
ORDER BY ritardo_attuale DESC
LIMIT 10;

-- Frequenza SuperEnalotto "ufficiale" (solo era INDIPENDENTE)
SELECT numero, frequenza, frequenza_relativa
FROM v_sen_frequenza_sestina
ORDER BY frequenza DESC
LIMIT 10;

-- Confronto del ritardo di un numero: storico completo vs azzerato 2009
SELECT c.numero, c.ritardo_attuale AS ritardo_completo,
       a.ritardo_attuale AS ritardo_dal_2009
FROM v_sen_ritardo_storico_completo c
JOIN v_sen_ritardo_azzerato_2009 a ON a.numero = c.numero
WHERE c.numero = 47;

-- Terni SuperEnalotto più ritardatari, solo era INDIPENDENTE
SELECT numero1, numero2, numero3, ritardo_attuale, frequenza
FROM cache_sen_terni
WHERE tipo_regolamento = 'INDIPENDENTE'
ORDER BY ritardo_attuale DESC
LIMIT 10;

-- Distribuzione pari/dispari della sestina, era INDIPENDENTE
SELECT conteggio_pari, conteggio_dispari, frequenza
FROM v_sen_pari_dispari_distribuzione
WHERE tipo_regolamento = 'INDIPENDENTE'
ORDER BY frequenza DESC;

-- Ritardo Jolly: confronto storico completo vs azzerato 2009
SELECT c.numero, c.ritardo_attuale AS ritardo_completo,
       a.ritardo_attuale AS ritardo_dal_2009
FROM v_sen_jolly_ritardo_storico_completo c
JOIN v_sen_jolly_ritardo_azzerato_2009 a ON a.numero = c.numero
ORDER BY a.ritardo_attuale DESC
LIMIT 10;

-- SuperStar più ritardatario (default INDIPENDENTE, era corretta)
SELECT numero, ritardo_attuale
FROM v_sen_superstar_ritardo_azzerato_2009
ORDER BY ritardo_attuale DESC
LIMIT 10;
```

## Nota sulle performance

Le viste `v_lotto_ritardo_storico_max`, `v_lotto_estratti_famiglie` e affini
usano funzioni finestra su tutta la vista "flat" (unpivot dello storico
completo, ~380k righe per il solo Lotto dal 1939, prima ancora di contare
il SuperEnalotto). **Misurato in produzione**: `v_lotto_indice_convenienza`
(che combina `v_lotto_ritardo_storico_max` con `v_lotto_ritardo_attuale_ruota`)
impiegava **~8,9 secondi** anche filtrata su una sola ruota con `LIMIT 10`
— `EXPLAIN` mostra una stima di 34+ milioni di righe intermedie, perché il
filtro non si spinge dentro il window function attraverso i livelli di
vista annidati. Era la prima query eseguita dalla dashboard all'apertura.

**Risolto**: `cache_lotto_ritardo` (`03_procedure_cache_lotto.sql`, sezione
2quater) materializza `v_lotto_indice_convenienza` (990 righe: 11 ruote x
90 numeri) con `sp_refresh_lotto_ritardo()`, stesso pattern già usato per
gli Ambi — richiamata da `sp_refresh_tutte_le_cache_lotto()`. Tempo di
lettura dalla cache: ~0,07s (era ~8,9s dalla vista live, ~120x più veloce).
Se in futuro noti la stessa latenza su altre viste con window function non
ancora cachate (es. `v_lotto_estratti_famiglie`), il pattern da seguire è
identico: materializzale in una tabella cache con una stored procedure di
refresh, riusando l'SQL della vista as-is nel corpo della `INSERT`.
