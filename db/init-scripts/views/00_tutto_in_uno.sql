-- =====================================================================
-- 00_tutto_in_uno.sql
-- Progetto: lotto-stat-app
-- Concatenazione dei file 01-06 in un unico script, nell'ordine corretto
-- di dipendenza. Comodo per un deploy in un solo comando:
--   podman exec -i lotto_stat_db mysql -uroot -p lotto_statistics < 00_tutto_in_uno.sql
-- Va eseguito come utente root (vedi note sui privilegi in 06).
-- =====================================================================


-- #####################################################################
-- ## 01_dimensioni_helper.sql
-- #####################################################################
-- =====================================================================
-- 01_dimensioni_helper.sql
-- Progetto: lotto-stat-app
-- Scopo: tabelle dimensionali di supporto usate da tutte le viste e
--        procedure successive (ruote, numeri 1-90, famiglie classiche
--        del Lotto: cadenze, decine, figure, gemelli, vertibili).
--
-- Da eseguire come utente con privilegio CREATE (root o statistics_user,
-- che nell'init.sql del progetto ha già CREATE su lotto_statistics.*).
-- =====================================================================

USE lotto_statistics;

-- ---------------------------------------------------------------------
-- Ruote del Lotto: 10 ruote regionali + Ruota Nazionale (RN)
-- NOTA: la chiave 'ruota' è il NOME ESTESO (es. 'Napoli'), non la sigla
-- a 2 lettere: scraper.py (dizionario SIGLE_RUOTE) traduce la sigla
-- dell'archivio ufficiale nel nome esteso PRIMA di scrivere la colonna
-- 'ruota' in estrazioni_lotto, quindi è quel valore ad essere la chiave
-- reale su cui le viste devono fare JOIN. La sigla resta disponibile
-- nella colonna 'sigla' solo per riferimento/display.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_ruote (
    ruota        VARCHAR(20) PRIMARY KEY,
    sigla        VARCHAR(2)  NOT NULL,
    is_nazionale TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO dim_ruote (ruota, sigla, is_nazionale) VALUES
    ('Bari','BA',0), ('Cagliari','CA',0), ('Firenze','FI',0), ('Genova','GE',0),
    ('Milano','MI',0), ('Napoli','NA',0), ('Palermo','PA',0), ('Roma','RM',0),
    ('Nazionale','RN',1), ('Torino','TO',0), ('Venezia','VE',0);

-- NOTA: se in futuro scraper.py cambia la mappatura SIGLE_RUOTE (nomi
-- diversi/ruote non più attive), aggiorna questa tabella PRIMA di
-- eseguire i file successivi: le viste di frequenza fanno CROSS JOIN su
-- dim_ruote e una ruota mancante qui semplicemente non comparirà nei report.

-- ---------------------------------------------------------------------
-- Numeri 1..90, comuni a Lotto e SuperEnalotto
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_numeri (
    numero TINYINT UNSIGNED PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO dim_numeri (numero)
WITH RECURSIVE seq AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n + 1 FROM seq WHERE n < 90
)
SELECT n FROM seq;

-- ---------------------------------------------------------------------
-- Famiglie classiche del Lotto per singolo numero:
--   cadenza  : ultima cifra (0..9)              -> 10,20,..90 = cadenza 0
--   decina   : gruppo di 10 numeri consecutivi   -> 1-10=decina1 .. 81-90=decina9
--   figura   : numero mod 9 (0 mappato a 9)       -> gruppi di 10 numeri
--   is_gemello       : numeri a cifre ripetute (11,22,...,88)
--   numero_vertibile : il "ribaltato" a due cifre, se esiste ed è nel range 1-90
--
-- NOTA CONVENZIONE DECINE: esistono due convenzioni diffuse nella pratica
-- del Lotto: (a) 1-9/10-19/.../80-90 con la prima decina "corta" di 9
-- numeri, oppure (b) gruppi regolari da 10 come qui implementato
-- (1-10, 11-20, ..., 81-90). Ho scelto (b) perché statisticamente più
-- pulita (9 gruppi di uguale numerosità). Se preferisci la convenzione (a)
-- basta ridefinire questa vista.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_lotto_numero_famiglie AS
SELECT
    n.numero,
    (n.numero % 10)                                        AS cadenza,
    FLOOR((n.numero - 1) / 10) + 1                          AS decina,
    ((n.numero - 1) % 9) + 1                                 AS figura,
    (n.numero IN (11,22,33,44,55,66,77,88))                  AS is_gemello,
    CASE
        WHEN n.numero < 10 THEN NULL                                   -- numeri a una cifra: nessun vertibile
        WHEN (n.numero % 10) = 0 THEN NULL                             -- 10,20,...,90: nessun vertibile classico
        WHEN FLOOR(n.numero / 10) = (n.numero % 10) THEN NULL          -- gemello: coincide con se stesso
        WHEN (n.numero % 10) * 10 + FLOOR(n.numero / 10) > 90 THEN NULL -- il ribaltato uscirebbe fuori range (es. 89->98)
        ELSE (n.numero % 10) * 10 + FLOOR(n.numero / 10)
    END AS numero_vertibile
FROM dim_numeri n;

-- ---------------------------------------------------------------------
-- Tutte le 4.005 combinazioni possibili di ambi (1-90, numero1 < numero2)
-- Usata sia dal motore Lotto sia da quello SuperEnalotto per garantire
-- che le tabelle cache degli ambi contengano SEMPRE tutte le combinazioni
-- possibili (anche quelle mai uscite, che sono ritardatarie "assolute").
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ambi_possibili AS
SELECT a.numero AS numero1, b.numero AS numero2
FROM dim_numeri a
JOIN dim_numeri b ON b.numero > a.numero;


-- #####################################################################
-- ## 02_viste_lotto.sql
-- #####################################################################
-- =====================================================================
-- 02_viste_lotto.sql
-- Progetto: lotto-stat-app
-- Scopo: motore di viste per l'analisi statistica del Lotto.
-- Dipende da: 01_dimensioni_helper.sql
--
-- Convenzione generale sul "ritardo": è sempre contato in NUMERO DI
-- ESTRAZIONI (non giorni di calendario), coerentemente con l'uso
-- tradizionale del Lotto.
-- =====================================================================

USE lotto_statistics;

-- =====================================================================
-- SEZIONE 1 — VISTE DI BASE (unpivot e sequenziamento)
-- =====================================================================

-- Ogni riga di estrazioni_lotto (5 numeri per ruota) diventa 5 righe:
-- una per numero estratto, con la sua posizione (1=Primo .. 5=Quinto).
CREATE OR REPLACE VIEW v_lotto_estratti_flat AS
SELECT id, data_estrazione, concorso, ruota, 1 AS posizione, Primo   AS numero FROM estrazioni_lotto
UNION ALL
SELECT id, data_estrazione, concorso, ruota, 2 AS posizione, Secondo AS numero FROM estrazioni_lotto
UNION ALL
SELECT id, data_estrazione, concorso, ruota, 3 AS posizione, Terzo   AS numero FROM estrazioni_lotto
UNION ALL
SELECT id, data_estrazione, concorso, ruota, 4 AS posizione, Quarto  AS numero FROM estrazioni_lotto
UNION ALL
SELECT id, data_estrazione, concorso, ruota, 5 AS posizione, Quinto  AS numero FROM estrazioni_lotto;

-- Numera progressivamente le estrazioni di ogni ruota in ordine
-- cronologico: è l'unità di misura del ritardo "mono-ruota".
CREATE OR REPLACE VIEW v_lotto_estrazioni_sequenziate AS
SELECT
    id, data_estrazione, concorso, ruota,
    ROW_NUMBER() OVER (PARTITION BY ruota ORDER BY data_estrazione, id) AS seq_ruota
FROM estrazioni_lotto;

-- v_lotto_estratti_flat arricchita con il numero di sequenza per ruota.
CREATE OR REPLACE VIEW v_lotto_estratti_flat_seq AS
SELECT f.id, f.data_estrazione, f.concorso, f.ruota, f.posizione, f.numero, s.seq_ruota
FROM v_lotto_estratti_flat f
JOIN v_lotto_estrazioni_sequenziate s ON s.id = f.id;

-- Numera le DATE di estrazione distinte (usata per il ritardo "tutte le
-- ruote", dove l'unità naturale è la data e non la singola riga-ruota).
CREATE OR REPLACE VIEW v_lotto_date_sequenziate AS
SELECT data_estrazione, ROW_NUMBER() OVER (ORDER BY data_estrazione) AS seq_data
FROM (SELECT DISTINCT data_estrazione FROM estrazioni_lotto) d;

-- v_lotto_estratti_flat con le famiglie classiche allegate (cadenza,
-- decina, figura, gemello, vertibile) — base per le viste di sezione 4.
CREATE OR REPLACE VIEW v_lotto_estratti_famiglie AS
SELECT f.*, nf.cadenza, nf.decina, nf.figura, nf.is_gemello, nf.numero_vertibile
FROM v_lotto_estratti_flat_seq f
JOIN v_lotto_numero_famiglie nf ON nf.numero = f.numero;


-- =====================================================================
-- SEZIONE 2 — FREQUENZE (mono-ruota e tutte-le-ruote)
-- =====================================================================

-- Frequenza assoluta e relativa di ogni numero su ogni singola ruota.
CREATE OR REPLACE VIEW v_lotto_frequenza_ruota AS
SELECT
    r.ruota, n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM dim_ruote r
CROSS JOIN dim_numeri n
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot
    ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, numero, COUNT(*) AS frequenza
    FROM v_lotto_estratti_flat
    GROUP BY ruota, numero
) f ON f.ruota = r.ruota AND f.numero = n.numero;

-- Frequenza "tutte le ruote" ESCLUSA la Nazionale (convenzione classica).
CREATE OR REPLACE VIEW v_lotto_frequenza_tutte_ruote AS
SELECT
    n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_righe,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_righe, 6) AS frequenza_relativa
FROM dim_numeri n
CROSS JOIN (
    SELECT COUNT(*) AS totale_righe
    FROM estrazioni_lotto e JOIN dim_ruote r ON r.ruota = e.ruota AND r.is_nazionale = 0
) tot
LEFT JOIN (
    SELECT flat.numero, COUNT(*) AS frequenza
    FROM v_lotto_estratti_flat flat
    JOIN dim_ruote r ON r.ruota = flat.ruota AND r.is_nazionale = 0
    GROUP BY flat.numero
) f ON f.numero = n.numero;

-- Variante che INCLUDE la Ruota Nazionale.
CREATE OR REPLACE VIEW v_lotto_frequenza_tutte_ruote_con_nazionale AS
SELECT
    n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_righe,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_righe, 6) AS frequenza_relativa
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_righe FROM estrazioni_lotto) tot
LEFT JOIN (
    SELECT numero, COUNT(*) AS frequenza
    FROM v_lotto_estratti_flat
    GROUP BY numero
) f ON f.numero = n.numero;


-- =====================================================================
-- SEZIONE 3 — RITARDI (RA, RS) E INDICE DI CONVENIENZA
-- =====================================================================

-- Ritardo Attuale (RA) per singola ruota: estrazioni consecutive di
-- assenza fino all'ultima estrazione disponibile in tabella.
CREATE OR REPLACE VIEW v_lotto_ritardo_attuale_ruota AS
SELECT
    r.ruota, n.numero,
    tot.totale_estrazioni,
    ultima.ultima_data_estrazione,
    CASE WHEN ultima.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - ultima.ultimo_seq
    END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN dim_numeri n
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot
    ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, numero, MAX(seq_ruota) AS ultimo_seq, MAX(data_estrazione) AS ultima_data_estrazione
    FROM v_lotto_estratti_flat_seq
    GROUP BY ruota, numero
) ultima ON ultima.ruota = r.ruota AND ultima.numero = n.numero;

-- Ritardo Attuale su "tutte le ruote" (esclusa Nazionale), contato in
-- NUMERO DI DATE DI ESTRAZIONE trascorse dall'ultima uscita su una
-- qualsiasi ruota del gruppo.
CREATE OR REPLACE VIEW v_lotto_ritardo_attuale_tutte_ruote AS
SELECT
    n.numero,
    tot.totale_date,
    ultima.ultima_data,
    CASE WHEN ultima.ultimo_seq IS NULL THEN tot.totale_date
         ELSE tot.totale_date - ultima.ultimo_seq
    END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_date FROM v_lotto_date_sequenziate) tot
LEFT JOIN (
    SELECT flat.numero, MAX(ds.seq_data) AS ultimo_seq, MAX(flat.data_estrazione) AS ultima_data
    FROM v_lotto_estratti_flat flat
    JOIN dim_ruote r ON r.ruota = flat.ruota AND r.is_nazionale = 0
    JOIN v_lotto_date_sequenziate ds ON ds.data_estrazione = flat.data_estrazione
    GROUP BY flat.numero
) ultima ON ultima.numero = n.numero;

-- Variante che INCLUDE la Ruota Nazionale.
CREATE OR REPLACE VIEW v_lotto_ritardo_attuale_tutte_ruote_con_nazionale AS
SELECT
    n.numero,
    tot.totale_date,
    ultima.ultima_data,
    CASE WHEN ultima.ultimo_seq IS NULL THEN tot.totale_date
         ELSE tot.totale_date - ultima.ultimo_seq
    END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_date FROM v_lotto_date_sequenziate) tot
LEFT JOIN (
    SELECT flat.numero, MAX(ds.seq_data) AS ultimo_seq, MAX(flat.data_estrazione) AS ultima_data
    FROM v_lotto_estratti_flat flat
    JOIN v_lotto_date_sequenziate ds ON ds.data_estrazione = flat.data_estrazione
    GROUP BY flat.numero
) ultima ON ultima.numero = n.numero;

-- Ritardo Storico Massimo (RS): il più grande ritardo CHIUSO (cioè
-- terminato da un'estrazione reale) mai registrato da un numero su una
-- ruota, includendo anche il possibile gap iniziale prima della sua
-- prima uscita in archivio. Non include il ritardo "aperto" corrente
-- (quello è RA, che può a sua volta diventare record se lo supera).
CREATE OR REPLACE VIEW v_lotto_ritardo_storico_max AS
WITH occorrenze AS (
    SELECT
        ruota, numero, seq_ruota,
        COALESCE(LAG(seq_ruota) OVER (PARTITION BY ruota, numero ORDER BY seq_ruota), 0) AS seq_precedente
    FROM v_lotto_estratti_flat_seq
),
gap AS (
    SELECT ruota, numero, (seq_ruota - seq_precedente - 1) AS ritardo_chiuso
    FROM occorrenze
)
SELECT
    r.ruota, n.numero,
    COALESCE(MAX(g.ritardo_chiuso), 0) AS ritardo_storico_max
FROM dim_ruote r
CROSS JOIN dim_numeri n
LEFT JOIN gap g ON g.ruota = r.ruota AND g.numero = n.numero
GROUP BY r.ruota, n.numero;

-- Indice di Convenienza (IC) = RA / RS.
CREATE OR REPLACE VIEW v_lotto_indice_convenienza AS
SELECT
    ra.ruota, ra.numero,
    ra.ritardo_attuale, rs.ritardo_storico_max,
    CASE WHEN rs.ritardo_storico_max = 0 THEN NULL
         ELSE ROUND(ra.ritardo_attuale / rs.ritardo_storico_max, 4)
    END AS indice_convenienza
FROM v_lotto_ritardo_attuale_ruota ra
JOIN v_lotto_ritardo_storico_max rs ON rs.ruota = ra.ruota AND rs.numero = ra.numero;


-- =====================================================================
-- SEZIONE 4 — FAMIGLIE CLASSICHE: CADENZE, DECINE, FIGURE, GEMELLI, VERTIBILI
-- (frequenza e ritardo "di famiglia": quante volte / da quanto non esce
--  ALMENO UN numero appartenente al gruppo, sulla singola ruota)
-- =====================================================================

-- --- Cadenze -----------------------------------------------------------
CREATE OR REPLACE VIEW v_lotto_cadenza_frequenza AS
SELECT r.ruota, c.cadenza, COALESCE(cnt.frequenza, 0) AS frequenza
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT cadenza FROM v_lotto_numero_famiglie) c
LEFT JOIN (
    SELECT ruota, cadenza, COUNT(*) AS frequenza
    FROM v_lotto_estratti_famiglie GROUP BY ruota, cadenza
) cnt ON cnt.ruota = r.ruota AND cnt.cadenza = c.cadenza;

CREATE OR REPLACE VIEW v_lotto_cadenza_ritardo AS
SELECT
    r.ruota, c.cadenza, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT cadenza FROM v_lotto_numero_famiglie) c
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, cadenza, MAX(seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_famiglie GROUP BY ruota, cadenza
) u ON u.ruota = r.ruota AND u.cadenza = c.cadenza;

-- --- Decine --------------------------------------------------------------
CREATE OR REPLACE VIEW v_lotto_decina_frequenza AS
SELECT r.ruota, d.decina, COALESCE(cnt.frequenza, 0) AS frequenza
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT decina FROM v_lotto_numero_famiglie) d
LEFT JOIN (
    SELECT ruota, decina, COUNT(*) AS frequenza
    FROM v_lotto_estratti_famiglie GROUP BY ruota, decina
) cnt ON cnt.ruota = r.ruota AND cnt.decina = d.decina;

CREATE OR REPLACE VIEW v_lotto_decina_ritardo AS
SELECT
    r.ruota, d.decina, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT decina FROM v_lotto_numero_famiglie) d
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, decina, MAX(seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_famiglie GROUP BY ruota, decina
) u ON u.ruota = r.ruota AND u.decina = d.decina;

-- --- Figure --------------------------------------------------------------
CREATE OR REPLACE VIEW v_lotto_figura_frequenza AS
SELECT r.ruota, fg.figura, COALESCE(cnt.frequenza, 0) AS frequenza
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT figura FROM v_lotto_numero_famiglie) fg
LEFT JOIN (
    SELECT ruota, figura, COUNT(*) AS frequenza
    FROM v_lotto_estratti_famiglie GROUP BY ruota, figura
) cnt ON cnt.ruota = r.ruota AND cnt.figura = fg.figura;

CREATE OR REPLACE VIEW v_lotto_figura_ritardo AS
SELECT
    r.ruota, fg.figura, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT figura FROM v_lotto_numero_famiglie) fg
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, figura, MAX(seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_famiglie GROUP BY ruota, figura
) u ON u.ruota = r.ruota AND u.figura = fg.figura;

-- --- Gemelli (11,22,...,88) ------------------------------------------------
-- Dettaglio per singolo numero gemello (frequenza puntuale, riusa v_lotto_frequenza_ruota).
CREATE OR REPLACE VIEW v_lotto_gemelli_dettaglio AS
SELECT * FROM v_lotto_frequenza_ruota WHERE numero IN (11,22,33,44,55,66,77,88);

-- Riepilogo di famiglia: frequenza/ritardo di "almeno un gemello" sulla ruota.
CREATE OR REPLACE VIEW v_lotto_gemelli_frequenza AS
SELECT r.ruota, COALESCE(cnt.frequenza, 0) AS frequenza
FROM dim_ruote r
LEFT JOIN (
    SELECT ruota, COUNT(*) AS frequenza
    FROM v_lotto_estratti_famiglie WHERE is_gemello = 1 GROUP BY ruota
) cnt ON cnt.ruota = r.ruota;

CREATE OR REPLACE VIEW v_lotto_gemelli_ritardo AS
SELECT
    r.ruota, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ruota, MAX(seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_famiglie WHERE is_gemello = 1 GROUP BY ruota
) u ON u.ruota = r.ruota;

-- --- Vertibili (es. 12-21, 13-31, ...) -------------------------------------
-- Riepilogo di famiglia per singola coppia vertibile: frequenza/ritardo di
-- "almeno uno dei due membri della coppia" sulla ruota. La coppia è
-- identificata dal numero più basso (LEAST).
CREATE OR REPLACE VIEW v_lotto_vertibili_frequenza AS
SELECT
    r.ruota,
    LEAST(nf.numero, nf.numero_vertibile) AS numero_base,
    GREATEST(nf.numero, nf.numero_vertibile) AS numero_vertibile,
    COALESCE(cnt.frequenza, 0) AS frequenza
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT numero, numero_vertibile FROM v_lotto_numero_famiglie WHERE numero_vertibile IS NOT NULL) nf
LEFT JOIN (
    SELECT ef.ruota,
           LEAST(ef.numero, ef.numero_vertibile) AS numero_base,
           COUNT(*) AS frequenza
    FROM v_lotto_estratti_famiglie ef
    WHERE ef.numero_vertibile IS NOT NULL
    GROUP BY ef.ruota, LEAST(ef.numero, ef.numero_vertibile)
) cnt ON cnt.ruota = r.ruota AND cnt.numero_base = LEAST(nf.numero, nf.numero_vertibile)
WHERE nf.numero < nf.numero_vertibile;  -- una sola riga per coppia (non duplicata al contrario)

CREATE OR REPLACE VIEW v_lotto_vertibili_ritardo AS
SELECT
    r.ruota,
    LEAST(nf.numero, nf.numero_vertibile) AS numero_base,
    GREATEST(nf.numero, nf.numero_vertibile) AS numero_vertibile,
    tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN (SELECT DISTINCT numero, numero_vertibile FROM v_lotto_numero_famiglie WHERE numero_vertibile IS NOT NULL) nf
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT ef.ruota,
           LEAST(ef.numero, ef.numero_vertibile) AS numero_base,
           MAX(ef.seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_famiglie ef
    WHERE ef.numero_vertibile IS NOT NULL
    GROUP BY ef.ruota, LEAST(ef.numero, ef.numero_vertibile)
) u ON u.ruota = r.ruota AND u.numero_base = LEAST(nf.numero, nf.numero_vertibile)
WHERE nf.numero < nf.numero_vertibile;


-- =====================================================================
-- SEZIONE 5 — STATISTICHE POSIZIONALI (1° .. 5° estratto)
-- =====================================================================

CREATE OR REPLACE VIEW v_lotto_frequenza_posizionale AS
SELECT r.ruota, p.posizione, n.numero, COALESCE(f.frequenza, 0) AS frequenza
FROM dim_ruote r
CROSS JOIN (SELECT 1 AS posizione UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) p
CROSS JOIN dim_numeri n
LEFT JOIN (
    SELECT ruota, posizione, numero, COUNT(*) AS frequenza
    FROM v_lotto_estratti_flat GROUP BY ruota, posizione, numero
) f ON f.ruota = r.ruota AND f.posizione = p.posizione AND f.numero = n.numero;

-- Ritardo "Estratto Determinato": ritardo di un numero legato alla sua
-- posizione esatta su una ruota (es. il 47 come 3° estratto a Napoli).
CREATE OR REPLACE VIEW v_lotto_ritardo_posizionale AS
SELECT
    r.ruota, p.posizione, n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_ruote r
CROSS JOIN (SELECT 1 AS posizione UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) p
CROSS JOIN dim_numeri n
JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot ON tot.ruota = r.ruota
LEFT JOIN (
    SELECT f.ruota, f.posizione, f.numero, MAX(s.seq_ruota) AS ultimo_seq
    FROM v_lotto_estratti_flat f
    JOIN v_lotto_estrazioni_sequenziate s ON s.id = f.id
    GROUP BY f.ruota, f.posizione, f.numero
) u ON u.ruota = r.ruota AND u.posizione = p.posizione AND u.numero = n.numero;

-- Distribuzione pari/dispari per posizione (utile per capire se una
-- posizione favorisce sistematicamente pari o dispari).
CREATE OR REPLACE VIEW v_lotto_pari_dispari_posizionale AS
SELECT ruota, posizione,
       SUM(numero % 2 = 0) AS conteggio_pari,
       SUM(numero % 2 = 1) AS conteggio_dispari,
       COUNT(*) AS totale
FROM v_lotto_estratti_flat
GROUP BY ruota, posizione;


-- =====================================================================
-- SEZIONE 6 — AMBI (vista di supporto; la vera aggregazione pesante è
-- nella tabella cache di 03_procedure_cache_lotto.sql)
-- =====================================================================

-- Tutte le 10 coppie (Ambi) presenti in ciascuna estrazione, generate
-- per self-join sulla vista flat (join su stesso id, numero2 > numero1):
-- è l'equivalente dichiarativo di "tutte le combinazioni C(5,2)".
CREATE OR REPLACE VIEW v_lotto_ambi_per_estrazione AS
SELECT fa.id, fa.data_estrazione, fa.concorso, fa.ruota,
       fa.numero AS numero1, fb.numero AS numero2
FROM v_lotto_estratti_flat fa
JOIN v_lotto_estratti_flat fb ON fb.id = fa.id AND fb.numero > fa.numero;


-- #####################################################################
-- ## 03_procedure_cache_lotto.sql
-- #####################################################################
-- =====================================================================
-- 03_procedure_cache_lotto.sql
-- Progetto: lotto-stat-app
-- Scopo: tabelle cache + stored procedure per le elaborazioni
--        combinatorie pesanti del Lotto (Ambi, Isocronismi).
-- Dipende da: 01_dimensioni_helper.sql, 02_viste_lotto.sql
--
-- IMPORTANTE SUI PRIVILEGI: la creazione di VIEW/PROCEDURE richiede i
-- privilegi CREATE VIEW / CREATE ROUTINE, non presenti nell'attuale
-- GRANT di statistics_user (vedi init.sql: SELECT, INSERT, UPDATE,
-- DELETE, CREATE, SHOW VIEW, ALTER). Esegui questo file e i precedenti
-- come utente root, come già fatto per lo schema in init.sql. Per la
-- concessione dei privilegi minimi necessari a statistics_user per
-- l'uso quotidiano (SOLO EXECUTE sulle procedure), vedi
-- 06_master_refresh_e_grant.sql.
-- =====================================================================

USE lotto_statistics;

-- =====================================================================
-- SEZIONE 1 — AMBI (4.005 combinazioni x 11 ruote = 44.055 righe cache)
-- =====================================================================

CREATE TABLE IF NOT EXISTS cache_lotto_ambi (
    ruota                VARCHAR(20)      NOT NULL,
    numero1              TINYINT UNSIGNED NOT NULL,
    numero2              TINYINT UNSIGNED NOT NULL,
    frequenza            INT UNSIGNED     NOT NULL DEFAULT 0,
    ultima_data_uscita   DATE             NULL,
    ultimo_seq_uscita    INT UNSIGNED     NULL,
    ritardo_attuale      INT UNSIGNED     NOT NULL DEFAULT 0,
    ritardo_storico_max  INT UNSIGNED     NOT NULL DEFAULT 0,
    aggiornato_il        TIMESTAMP        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ruota, numero1, numero2),
    KEY idx_lotto_ambi_ritardo (ruota, ritardo_attuale DESC),
    KEY idx_lotto_ambi_frequenza (ruota, frequenza DESC),
    CONSTRAINT chk_lotto_ambi_ordine CHECK (numero1 < numero2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- NOTA: il CHECK richiede MySQL >= 8.0.16 per essere effettivamente
-- applicato (su versioni precedenti viene analizzato ma ignorato).
-- L'immagine docker.io/library/mysql:8.0 usata dal progetto lo soddisfa.

DELIMITER $$

CREATE PROCEDURE sp_refresh_lotto_ambi()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;

    -- DELETE (non TRUNCATE) per restare dentro la transazione: TRUNCATE
    -- fa commit implicito e vanificherebbe il rollback in caso di errore
    -- nella successiva INSERT.
    DELETE FROM cache_lotto_ambi;

    INSERT INTO cache_lotto_ambi
        (ruota, numero1, numero2, frequenza, ultima_data_uscita, ultimo_seq_uscita,
         ritardo_attuale, ritardo_storico_max)
    SELECT
        r.ruota, p.numero1, p.numero2,
        COALESCE(occ.frequenza, 0),
        occ.ultima_data_uscita,
        occ.ultimo_seq_uscita,
        (tot.totale_estrazioni - COALESCE(occ.ultimo_seq_uscita, 0)) AS ritardo_attuale,
        COALESCE(rs.ritardo_storico_max, tot.totale_estrazioni) AS ritardo_storico_max
    FROM dim_ruote r
    CROSS JOIN v_ambi_possibili p
    JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot
        ON tot.ruota = r.ruota
    LEFT JOIN (
        SELECT ape.ruota, ape.numero1, ape.numero2,
               COUNT(*) AS frequenza,
               MAX(ape.data_estrazione) AS ultima_data_uscita,
               MAX(seq.seq_ruota) AS ultimo_seq_uscita
        FROM v_lotto_ambi_per_estrazione ape
        JOIN v_lotto_estrazioni_sequenziate seq ON seq.id = ape.id
        GROUP BY ape.ruota, ape.numero1, ape.numero2
    ) occ ON occ.ruota = r.ruota AND occ.numero1 = p.numero1 AND occ.numero2 = p.numero2
    LEFT JOIN (
        -- ritardo storico massimo per ambo: massimo gap chiuso tra due uscite
        -- consecutive della stessa coppia (stesso principio di v_lotto_ritardo_storico_max)
        SELECT ruota, numero1, numero2, MAX(gap) AS ritardo_storico_max
        FROM (
            SELECT
                ape.ruota, ape.numero1, ape.numero2, seq.seq_ruota,
                seq.seq_ruota - COALESCE(LAG(seq.seq_ruota) OVER (
                    PARTITION BY ape.ruota, ape.numero1, ape.numero2 ORDER BY seq.seq_ruota
                ), 0) - 1 AS gap
            FROM v_lotto_ambi_per_estrazione ape
            JOIN v_lotto_estrazioni_sequenziate seq ON seq.id = ape.id
        ) gaps
        GROUP BY ruota, numero1, numero2
    ) rs ON rs.ruota = r.ruota AND rs.numero1 = p.numero1 AND rs.numero2 = p.numero2;

    COMMIT;
END$$

DELIMITER ;

-- Uso: CALL sp_refresh_lotto_ambi();
-- Da rilanciare dopo ogni import di nuove estrazioni (es. a fine scraper.py).

-- Viste di comodo sopra la cache (nessun ricalcolo, sola lettura veloce).
CREATE OR REPLACE VIEW v_lotto_ambi_top_frequenti AS
SELECT * FROM cache_lotto_ambi ORDER BY ruota, frequenza DESC;

CREATE OR REPLACE VIEW v_lotto_ambi_ritardatari AS
SELECT * FROM cache_lotto_ambi ORDER BY ruota, ritardo_attuale DESC;

-- Sincronismo: ambo uscito almeno una volta ma "che continua a non
-- uscire insieme" (ritardo_attuale > 0). E' una semplice lettura
-- filtrata della cache, non serve una tabella dedicata.
CREATE OR REPLACE VIEW v_lotto_sincronismi AS
SELECT ruota, numero1, numero2, frequenza, ultima_data_uscita, ritardo_attuale, ritardo_storico_max
FROM cache_lotto_ambi
WHERE frequenza > 0
ORDER BY ritardo_attuale DESC;


-- =====================================================================
-- SEZIONE 2 — ISOCRONISMI
-- "Numeri usciti contemporaneamente nella stessa estrazione su ruote
-- diverse, che condividono OGGI lo stesso ritardo attuale."
--
-- Strategia: il confronto di uguaglianza sul ritardo attuale avviene
-- sulla tabella dei ritardi (piccola: 11 ruote x 90 numeri = 990 righe),
-- quindi il self-join è economico. Le co-uscite storiche NON vengono
-- verificate con una subquery correlata per ogni coppia candidata (con
-- centinaia/migliaia di candidati equivaleva a ripetere un self-join su
-- 383k righe "flat" altrettante volte: minuti anziché secondi, perché
-- v_lotto_estratti_flat è un UNION ALL su colonne fisiche e non è
-- indicizzabile). Si materializza invece UNA VOLTA SOLA, in tabelle
-- temporanee indicizzate, prima (a) lo storico flat e poi (b) l'intero
-- universo delle co-uscite fra ruote diverse: il confronto finale con i
-- candidati diventa un semplice lookup su chiave primaria.
-- =====================================================================

CREATE TABLE IF NOT EXISTS cache_lotto_isocronismi (
    ruota_a             VARCHAR(20)      NOT NULL,
    numero_a            TINYINT UNSIGNED NOT NULL,
    ruota_b             VARCHAR(20)      NOT NULL,
    numero_b            TINYINT UNSIGNED NOT NULL,
    ritardo_condiviso   INT UNSIGNED     NOT NULL,
    ultima_coincidenza  DATE             NOT NULL,
    aggiornato_il       TIMESTAMP        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ruota_a, numero_a, ruota_b, numero_b)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DROP PROCEDURE IF EXISTS sp_refresh_lotto_isocronismi;

DELIMITER $$

CREATE PROCEDURE sp_refresh_lotto_isocronismi()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    -- (a) storico flat materializzato e indicizzato su data_estrazione:
    -- rende il self-join successivo un index lookup invece di una
    -- scansione completa della vista UNION ALL.
    DROP TEMPORARY TABLE IF EXISTS tmp_lotto_flat;
    CREATE TEMPORARY TABLE tmp_lotto_flat (
        data_estrazione DATE             NOT NULL,
        ruota           VARCHAR(20)      NOT NULL,
        numero          TINYINT UNSIGNED NOT NULL,
        KEY idx_data (data_estrazione)
    ) ENGINE=InnoDB;

    INSERT INTO tmp_lotto_flat (data_estrazione, ruota, numero)
    SELECT data_estrazione, ruota, numero FROM v_lotto_estratti_flat;

    -- (b) universo delle co-uscite (stessa data, ruote diverse), chiavato
    -- esattamente come i candidati verranno interrogati in seguito.
    DROP TEMPORARY TABLE IF EXISTS tmp_lotto_coocorrenze;
    CREATE TEMPORARY TABLE tmp_lotto_coocorrenze (
        ruota_a            VARCHAR(20)      NOT NULL,
        numero_a           TINYINT UNSIGNED NOT NULL,
        ruota_b            VARCHAR(20)      NOT NULL,
        numero_b           TINYINT UNSIGNED NOT NULL,
        ultima_coincidenza DATE             NOT NULL,
        PRIMARY KEY (ruota_a, numero_a, ruota_b, numero_b)
    ) ENGINE=InnoDB;

    INSERT INTO tmp_lotto_coocorrenze (ruota_a, numero_a, ruota_b, numero_b, ultima_coincidenza)
    SELECT fa.ruota, fa.numero, fb.ruota, fb.numero, MAX(fa.data_estrazione)
    FROM tmp_lotto_flat fa
    JOIN tmp_lotto_flat fb
        ON fb.data_estrazione = fa.data_estrazione
       AND fb.ruota > fa.ruota
    GROUP BY fa.ruota, fa.numero, fb.ruota, fb.numero;

    START TRANSACTION;
    DELETE FROM cache_lotto_isocronismi;

    INSERT INTO cache_lotto_isocronismi
        (ruota_a, numero_a, ruota_b, numero_b, ritardo_condiviso, ultima_coincidenza)
    SELECT
        a.ruota, a.numero, b.ruota, b.numero, a.ritardo_attuale, co.ultima_coincidenza
    FROM v_lotto_ritardo_attuale_ruota a
    JOIN v_lotto_ritardo_attuale_ruota b
        ON a.ritardo_attuale = b.ritardo_attuale
       AND a.ritardo_attuale > 0
       AND a.ruota < b.ruota
    JOIN tmp_lotto_coocorrenze co
        ON co.ruota_a = a.ruota AND co.numero_a = a.numero
       AND co.ruota_b = b.ruota AND co.numero_b = b.numero;

    COMMIT;

    DROP TEMPORARY TABLE IF EXISTS tmp_lotto_flat;
    DROP TEMPORARY TABLE IF EXISTS tmp_lotto_coocorrenze;
END$$

DELIMITER ;

-- Uso: CALL sp_refresh_lotto_isocronismi();


-- =====================================================================
-- SEZIONE 2bis — TERZINE
-- C(90,3) = 117.480 combinazioni possibili per ruota: troppe per
-- pre-calcolare l'intero universo (a differenza degli Ambi). Cache solo
-- le terzine EFFETTIVAMENTE uscite almeno una volta: è lì che vivono
-- frequenze e ritardi (una terzina mai uscita non è "ritardataria" in
-- senso classico, è semplicemente inedita).
-- =====================================================================

-- Le 10 terzine (C(5,3)) di ogni estrazione, per doppio self-join sulla
-- vista flat (stesso principio delle coppie, con un livello in più).
CREATE OR REPLACE VIEW v_lotto_terzine_per_estrazione AS
SELECT fa.id, fa.data_estrazione, fa.concorso, fa.ruota,
       fa.numero AS numero1, fb.numero AS numero2, fc.numero AS numero3
FROM v_lotto_estratti_flat fa
JOIN v_lotto_estratti_flat fb ON fb.id = fa.id AND fb.numero > fa.numero
JOIN v_lotto_estratti_flat fc ON fc.id = fa.id AND fc.numero > fb.numero;

CREATE TABLE IF NOT EXISTS cache_lotto_terzine (
    ruota                VARCHAR(20)      NOT NULL,
    numero1              TINYINT UNSIGNED NOT NULL,
    numero2              TINYINT UNSIGNED NOT NULL,
    numero3              TINYINT UNSIGNED NOT NULL,
    frequenza            INT UNSIGNED     NOT NULL DEFAULT 0,
    ultima_data_uscita   DATE             NULL,
    ultimo_seq_uscita    INT UNSIGNED     NULL,
    ritardo_attuale      INT UNSIGNED     NOT NULL DEFAULT 0,
    ritardo_storico_max  INT UNSIGNED     NOT NULL DEFAULT 0,
    aggiornato_il        TIMESTAMP        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ruota, numero1, numero2, numero3),
    KEY idx_lotto_terzine_ritardo (ruota, ritardo_attuale DESC),
    KEY idx_lotto_terzine_frequenza (ruota, frequenza DESC),
    CONSTRAINT chk_lotto_terzine_ordine CHECK (numero1 < numero2 AND numero2 < numero3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER $$

CREATE PROCEDURE sp_refresh_lotto_terzine()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    DELETE FROM cache_lotto_terzine;

    INSERT INTO cache_lotto_terzine
        (ruota, numero1, numero2, numero3, frequenza, ultima_data_uscita, ultimo_seq_uscita,
         ritardo_attuale, ritardo_storico_max)
    SELECT
        base.ruota, base.numero1, base.numero2, base.numero3,
        base.frequenza, base.ultima_data_uscita, base.ultimo_seq_uscita,
        (tot.totale_estrazioni - base.ultimo_seq_uscita) AS ritardo_attuale,
        COALESCE(rs.ritardo_storico_max, tot.totale_estrazioni) AS ritardo_storico_max
    FROM (
        SELECT tpe.ruota, tpe.numero1, tpe.numero2, tpe.numero3,
               COUNT(*) AS frequenza,
               MAX(tpe.data_estrazione) AS ultima_data_uscita,
               MAX(seq.seq_ruota) AS ultimo_seq_uscita
        FROM v_lotto_terzine_per_estrazione tpe
        JOIN v_lotto_estrazioni_sequenziate seq ON seq.id = tpe.id
        GROUP BY tpe.ruota, tpe.numero1, tpe.numero2, tpe.numero3
    ) base
    JOIN (SELECT ruota, COUNT(*) AS totale_estrazioni FROM estrazioni_lotto GROUP BY ruota) tot
        ON tot.ruota = base.ruota
    LEFT JOIN (
        SELECT ruota, numero1, numero2, numero3, MAX(gap) AS ritardo_storico_max
        FROM (
            SELECT tpe.ruota, tpe.numero1, tpe.numero2, tpe.numero3, seq.seq_ruota,
                seq.seq_ruota - COALESCE(LAG(seq.seq_ruota) OVER (
                    PARTITION BY tpe.ruota, tpe.numero1, tpe.numero2, tpe.numero3 ORDER BY seq.seq_ruota
                ), 0) - 1 AS gap
            FROM v_lotto_terzine_per_estrazione tpe
            JOIN v_lotto_estrazioni_sequenziate seq ON seq.id = tpe.id
        ) gaps
        GROUP BY ruota, numero1, numero2, numero3
    ) rs ON rs.ruota = base.ruota AND rs.numero1 = base.numero1
        AND rs.numero2 = base.numero2 AND rs.numero3 = base.numero3;

    COMMIT;
END$$

DELIMITER ;

-- Uso: CALL sp_refresh_lotto_terzine();

CREATE OR REPLACE VIEW v_lotto_terzine_top_frequenti AS
SELECT * FROM cache_lotto_terzine ORDER BY ruota, frequenza DESC;

-- Sincronismo di terzina: uscita insieme almeno una volta, non più ripetuta.
CREATE OR REPLACE VIEW v_lotto_terzine_sincronismi AS
SELECT ruota, numero1, numero2, numero3, frequenza, ultima_data_uscita, ritardo_attuale, ritardo_storico_max
FROM cache_lotto_terzine
WHERE frequenza > 0
ORDER BY ritardo_attuale DESC;


-- =====================================================================
-- SEZIONE 2ter — CINQUINE (la sestina... anzi qui è "cinquina": l'intera
-- estrazione di 5 numeri di una ruota). Non serve una cache combinatoria
-- (C(90,5) sarebbe astronomico e privo di senso pratico): l'unica domanda
-- statisticamente sensata è "questa esatta cinquina si è già ripetuta
-- identica, sulla stessa ruota, in passato?" — cosa rarissima ma
-- verificabile con una semplice GROUP BY, quindi resta una vista leggera.
-- =====================================================================

CREATE OR REPLACE VIEW v_lotto_cinquine_ripetute AS
SELECT
    ruota, Primo, Secondo, Terzo, Quarto, Quinto,
    COUNT(*) AS numero_ripetizioni,
    MIN(data_estrazione) AS prima_uscita,
    MAX(data_estrazione) AS ultima_uscita
FROM estrazioni_lotto
GROUP BY ruota, Primo, Secondo, Terzo, Quarto, Quinto
HAVING COUNT(*) > 1;
-- NOTA: qui Primo..Quinto sono usati come chiave di raggruppamento "as-is"
-- (ordine di estrazione), che è la definizione più comune di "cinquina
-- ripetuta". Se preferisci ignorare l'ordine di estrazione e confrontare
-- solo l'insieme dei 5 numeri, raggruppa invece su
-- v_lotto_estratti_flat con FIND_IN_SET/GROUP_CONCAT ordinato.


-- =====================================================================
-- SEZIONE 3 — PROCEDURA UNICA PER IL LOTTO
-- =====================================================================
DELIMITER $$

CREATE PROCEDURE sp_refresh_tutte_le_cache_lotto()
BEGIN
    CALL sp_refresh_lotto_ambi();
    CALL sp_refresh_lotto_terzine();
    CALL sp_refresh_lotto_isocronismi();
END$$

DELIMITER ;


-- #####################################################################
-- ## 04_viste_superenalotto.sql
-- #####################################################################
-- =====================================================================
-- 04_viste_superenalotto.sql
-- Progetto: lotto-stat-app
-- Scopo: motore di viste per l'analisi statistica del SuperEnalotto.
-- Dipende da: 01_dimensioni_helper.sql
--
-- REGOLE DI INTEGRITA' DATI (non negoziabili, vedi memoria di progetto):
--  1. Le frequenze "ufficiali" filtrano SEMPRE su tipo_regolamento =
--     'INDIPENDENTE'. Le altre ere sono esposte solo in viste separate,
--     mai fuse silenziosamente nell'aggregato storico.
--  2. Il ritardo è esposto in DUE versioni esplicite e mai confuse:
--     storico completo (attraversa il 2009, "per curiosità") e azzerato
--     al 1° luglio 2009 (nuovo giorno zero, quello statisticamente onesto).
--  3. Gli abbinamenti (ambi/terni, vedi file 05) sono sempre segmentati
--     per tipo_regolamento.
-- =====================================================================

USE lotto_statistics;

-- =====================================================================
-- SEZIONE 1 — VISTE DI BASE (unpivot e sequenziamento)
-- =====================================================================

-- Sestina unpivotata: 6 righe per estrazione (posizione 1..6). Il campo
-- tipo_regolamento NON viene filtrato qui: ogni vista a valle decide
-- esplicitamente quale/i era/e includere.
CREATE OR REPLACE VIEW v_sen_sestina_flat AS
SELECT id, data_estrazione, concorso, tipo_regolamento, 1 AS posizione, n1 AS numero FROM estrazioni_superenalotto
UNION ALL SELECT id, data_estrazione, concorso, tipo_regolamento, 2, n2 FROM estrazioni_superenalotto
UNION ALL SELECT id, data_estrazione, concorso, tipo_regolamento, 3, n3 FROM estrazioni_superenalotto
UNION ALL SELECT id, data_estrazione, concorso, tipo_regolamento, 4, n4 FROM estrazioni_superenalotto
UNION ALL SELECT id, data_estrazione, concorso, tipo_regolamento, 5, n5 FROM estrazioni_superenalotto
UNION ALL SELECT id, data_estrazione, concorso, tipo_regolamento, 6, n6 FROM estrazioni_superenalotto;

-- Sequenziamento GLOBALE (attraversa tutte le ere: usato SOLO dalla
-- vista "ritardo storico completo", mai per frequenze/abbinamenti) e
-- sequenziamento PER ERA (usato ovunque altrove, incluse le cache di
-- ambi/terni nel file 05, per non mescolare macchine diverse).
CREATE OR REPLACE VIEW v_sen_estrazioni_sequenziate AS
SELECT id, data_estrazione, concorso, tipo_regolamento,
    ROW_NUMBER() OVER (ORDER BY data_estrazione, id) AS seq_globale,
    ROW_NUMBER() OVER (PARTITION BY tipo_regolamento ORDER BY data_estrazione, id) AS seq_regolamento
FROM estrazioni_superenalotto;

-- Sequenziamento "moderno": solo estrazioni dal 1 luglio 2009 in poi,
-- nuovo giorno zero per il ritardo statisticamente onesto.
CREATE OR REPLACE VIEW v_sen_estrazioni_sequenziate_moderne AS
SELECT id, data_estrazione, concorso, tipo_regolamento,
    ROW_NUMBER() OVER (ORDER BY data_estrazione, id) AS seq_moderna
FROM estrazioni_superenalotto
WHERE data_estrazione >= '2009-07-01';

-- Sequenziamento limitato alle estrazioni con SuperStar valorizzato
-- (colonna nullable: introdotta il 7 maggio 2009, quindi PRIMA del cambio
-- regolamento del 1 luglio 2009 — esiste una manciata di concorsi con
-- SuperStar valorizzato ma ancora in era 'LOTTO'. Le due viste seguenti
-- tengono questo caso separato, con lo stesso principio "mai fondere le
-- ere" già applicato alla sestina: seq_superstar_globale attraversa tutto,
-- seq_superstar_moderna riparte da zero al 1 luglio 2009).
CREATE OR REPLACE VIEW v_sen_estrazioni_sequenziate_superstar AS
SELECT id, data_estrazione, concorso, tipo_regolamento,
    ROW_NUMBER() OVER (ORDER BY data_estrazione, id) AS seq_superstar_globale
FROM estrazioni_superenalotto
WHERE superstar IS NOT NULL;

CREATE OR REPLACE VIEW v_sen_estrazioni_sequenziate_superstar_moderne AS
SELECT id, data_estrazione, concorso,
    ROW_NUMBER() OVER (ORDER BY data_estrazione, id) AS seq_superstar_moderna
FROM estrazioni_superenalotto
WHERE superstar IS NOT NULL AND data_estrazione >= '2009-07-01';


-- =====================================================================
-- SEZIONE 2 — FREQUENZA E RITARDO DELLA SESTINA (numeri 1-90)
-- =====================================================================

-- Frequenza "ufficiale" (default INDIPENDENTE, era corrente).
CREATE OR REPLACE VIEW v_sen_frequenza_sestina AS
SELECT
    n.numero, COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto WHERE tipo_regolamento = 'INDIPENDENTE') tot
LEFT JOIN (
    SELECT numero, COUNT(*) AS frequenza
    FROM v_sen_sestina_flat WHERE tipo_regolamento = 'INDIPENDENTE'
    GROUP BY numero
) f ON f.numero = n.numero;

-- Frequenza per TUTTE le ere, esposte fianco a fianco senza fonderle:
-- una riga per (era, numero). Utile per confronto/curiosità, mai come
-- default applicativo.
CREATE OR REPLACE VIEW v_sen_frequenza_sestina_per_era AS
SELECT
    tot.tipo_regolamento, n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM (SELECT DISTINCT tipo_regolamento FROM estrazioni_superenalotto) e
CROSS JOIN dim_numeri n
JOIN (SELECT tipo_regolamento, COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto GROUP BY tipo_regolamento) tot
    ON tot.tipo_regolamento = e.tipo_regolamento
LEFT JOIN (
    SELECT tipo_regolamento, numero, COUNT(*) AS frequenza
    FROM v_sen_sestina_flat GROUP BY tipo_regolamento, numero
) f ON f.tipo_regolamento = e.tipo_regolamento AND f.numero = n.numero;

-- Ritardo STORICO COMPLETO: attraversa il confine 2009, esposto solo
-- "per curiosità" — non usarlo come ritardo di riferimento operativo.
CREATE OR REPLACE VIEW v_sen_ritardo_storico_completo AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto) tot
LEFT JOIN (
    SELECT f.numero, MAX(s.seq_globale) AS ultimo_seq
    FROM v_sen_sestina_flat f
    JOIN v_sen_estrazioni_sequenziate s ON s.id = f.id
    GROUP BY f.numero
) u ON u.numero = n.numero;

-- Ritardo AZZERATO al 1 luglio 2009: quello statisticamente onesto,
-- da usare come default nell'applicazione.
CREATE OR REPLACE VIEW v_sen_ritardo_azzerato_2009 AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM v_sen_estrazioni_sequenziate_moderne) tot
LEFT JOIN (
    SELECT f.numero, MAX(s.seq_moderna) AS ultimo_seq
    FROM v_sen_sestina_flat f
    JOIN v_sen_estrazioni_sequenziate_moderne s ON s.id = f.id
    GROUP BY f.numero
) u ON u.numero = n.numero;


-- =====================================================================
-- SEZIONE 3 — JOLLY E SUPERSTAR
-- =====================================================================

-- Frequenza Jolly "ufficiale" (default INDIPENDENTE, stesso principio della sestina).
CREATE OR REPLACE VIEW v_sen_jolly_frequenza AS
SELECT
    n.numero, COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto WHERE tipo_regolamento = 'INDIPENDENTE') tot
LEFT JOIN (
    SELECT jolly AS numero, COUNT(*) AS frequenza
    FROM estrazioni_superenalotto WHERE tipo_regolamento = 'INDIPENDENTE'
    GROUP BY jolly
) f ON f.numero = n.numero;

-- Frequenza Jolly per TUTTE le ere, esposte fianco a fianco (mai fuse).
CREATE OR REPLACE VIEW v_sen_jolly_frequenza_per_era AS
SELECT
    tot.tipo_regolamento, n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM (SELECT DISTINCT tipo_regolamento FROM estrazioni_superenalotto) e
CROSS JOIN dim_numeri n
JOIN (SELECT tipo_regolamento, COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto GROUP BY tipo_regolamento) tot
    ON tot.tipo_regolamento = e.tipo_regolamento
LEFT JOIN (
    SELECT tipo_regolamento, jolly AS numero, COUNT(*) AS frequenza
    FROM estrazioni_superenalotto GROUP BY tipo_regolamento, jolly
) f ON f.tipo_regolamento = e.tipo_regolamento AND f.numero = n.numero;

-- Ritardo Jolly STORICO COMPLETO (attraversa il 2009, "per curiosità").
CREATE OR REPLACE VIEW v_sen_jolly_ritardo_storico_completo AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto) tot
LEFT JOIN (
    SELECT e.jolly AS numero, MAX(s.seq_globale) AS ultimo_seq
    FROM estrazioni_superenalotto e
    JOIN v_sen_estrazioni_sequenziate s ON s.id = e.id
    GROUP BY e.jolly
) u ON u.numero = n.numero;

-- Ritardo Jolly AZZERATO al 1 luglio 2009 (operativo, default applicativo).
CREATE OR REPLACE VIEW v_sen_jolly_ritardo_azzerato_2009 AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM v_sen_estrazioni_sequenziate_moderne) tot
LEFT JOIN (
    SELECT e.jolly AS numero, MAX(s.seq_moderna) AS ultimo_seq
    FROM estrazioni_superenalotto e
    JOIN v_sen_estrazioni_sequenziate_moderne s ON s.id = e.id
    GROUP BY e.jolly
) u ON u.numero = n.numero;

-- --- SuperStar -----------------------------------------------------------
-- FIX: filtrare SOLO "superstar IS NOT NULL" non basta — mischierebbe la
-- manciata di concorsi 'LOTTO' fra il 7 maggio e il 30 giugno 2009 (già
-- con SuperStar attivo, ma ancora prima del cambio regolamento) con l'era
-- 'INDIPENDENTE'. Il default filtra quindi SEMPRE anche su tipo_regolamento.

-- Frequenza SuperStar "ufficiale" (default INDIPENDENTE).
CREATE OR REPLACE VIEW v_sen_superstar_frequenza AS
SELECT
    n.numero, COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM dim_numeri n
CROSS JOIN (
    SELECT COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto
    WHERE superstar IS NOT NULL AND tipo_regolamento = 'INDIPENDENTE'
) tot
LEFT JOIN (
    SELECT superstar AS numero, COUNT(*) AS frequenza
    FROM estrazioni_superenalotto
    WHERE superstar IS NOT NULL AND tipo_regolamento = 'INDIPENDENTE'
    GROUP BY superstar
) f ON f.numero = n.numero;

-- Frequenza SuperStar per TUTTE le ere in cui è stato estratto (di fatto
-- quasi solo INDIPENDENTE, più la manciata di concorsi 'LOTTO' di cui
-- sopra — esposti qui separatamente, mai fusi con l'era corrente).
CREATE OR REPLACE VIEW v_sen_superstar_frequenza_per_era AS
SELECT
    tot.tipo_regolamento, n.numero,
    COALESCE(f.frequenza, 0) AS frequenza,
    tot.totale_estrazioni,
    ROUND(COALESCE(f.frequenza, 0) / tot.totale_estrazioni, 6) AS frequenza_relativa
FROM (SELECT DISTINCT tipo_regolamento FROM estrazioni_superenalotto WHERE superstar IS NOT NULL) e
CROSS JOIN dim_numeri n
JOIN (
    SELECT tipo_regolamento, COUNT(*) AS totale_estrazioni
    FROM estrazioni_superenalotto WHERE superstar IS NOT NULL
    GROUP BY tipo_regolamento
) tot ON tot.tipo_regolamento = e.tipo_regolamento
LEFT JOIN (
    SELECT tipo_regolamento, superstar AS numero, COUNT(*) AS frequenza
    FROM estrazioni_superenalotto WHERE superstar IS NOT NULL
    GROUP BY tipo_regolamento, superstar
) f ON f.tipo_regolamento = e.tipo_regolamento AND f.numero = n.numero;

-- Ritardo SuperStar STORICO COMPLETO (attraversa il 2009, "per curiosità").
CREATE OR REPLACE VIEW v_sen_superstar_ritardo_storico_completo AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM v_sen_estrazioni_sequenziate_superstar) tot
LEFT JOIN (
    SELECT e.superstar AS numero, MAX(s.seq_superstar_globale) AS ultimo_seq
    FROM estrazioni_superenalotto e
    JOIN v_sen_estrazioni_sequenziate_superstar s ON s.id = e.id
    GROUP BY e.superstar
) u ON u.numero = n.numero;

-- Ritardo SuperStar AZZERATO al 1 luglio 2009 (operativo, default applicativo).
CREATE OR REPLACE VIEW v_sen_superstar_ritardo_azzerato_2009 AS
SELECT
    n.numero, tot.totale_estrazioni,
    CASE WHEN u.ultimo_seq IS NULL THEN tot.totale_estrazioni
         ELSE tot.totale_estrazioni - u.ultimo_seq END AS ritardo_attuale
FROM dim_numeri n
CROSS JOIN (SELECT COUNT(*) AS totale_estrazioni FROM v_sen_estrazioni_sequenziate_superstar_moderne) tot
LEFT JOIN (
    SELECT e.superstar AS numero, MAX(s.seq_superstar_moderna) AS ultimo_seq
    FROM estrazioni_superenalotto e
    JOIN v_sen_estrazioni_sequenziate_superstar_moderne s ON s.id = e.id
    GROUP BY e.superstar
) u ON u.numero = n.numero;


-- =====================================================================
-- SEZIONE 4 — PARI/DISPARI, DECINE/CADENZE, SOMMA, CONSECUTIVITA'
-- (tutte segmentate per tipo_regolamento: mai fuse fra ere)
-- =====================================================================

-- --- Pari/Dispari ----------------------------------------------------
CREATE OR REPLACE VIEW v_sen_pari_dispari AS
SELECT
    id, data_estrazione, concorso, tipo_regolamento,
    (n1 % 2 = 0) + (n2 % 2 = 0) + (n3 % 2 = 0) + (n4 % 2 = 0) + (n5 % 2 = 0) + (n6 % 2 = 0) AS conteggio_pari,
    6 - ((n1 % 2 = 0) + (n2 % 2 = 0) + (n3 % 2 = 0) + (n4 % 2 = 0) + (n5 % 2 = 0) + (n6 % 2 = 0)) AS conteggio_dispari
FROM estrazioni_superenalotto;

CREATE OR REPLACE VIEW v_sen_pari_dispari_distribuzione AS
SELECT tipo_regolamento, conteggio_pari, conteggio_dispari, COUNT(*) AS frequenza
FROM v_sen_pari_dispari
GROUP BY tipo_regolamento, conteggio_pari, conteggio_dispari;

-- --- Decine e cadenze all'interno della sestina -----------------------
CREATE OR REPLACE VIEW v_sen_numero_decina AS
SELECT numero, FLOOR((numero - 1) / 10) + 1 AS decina, (numero % 10) AS cadenza
FROM dim_numeri;

-- Quanti numeri della stessa decina/cadenza cadono in una singola estrazione.
CREATE OR REPLACE VIEW v_sen_decine_per_estrazione AS
SELECT f.id, f.data_estrazione, f.tipo_regolamento, d.decina, COUNT(*) AS quanti_numeri
FROM v_sen_sestina_flat f
JOIN v_sen_numero_decina d ON d.numero = f.numero
GROUP BY f.id, f.data_estrazione, f.tipo_regolamento, d.decina;

CREATE OR REPLACE VIEW v_sen_decine_distribuzione AS
SELECT tipo_regolamento, quanti_numeri AS numeri_nella_stessa_decina, COUNT(*) AS frequenza_estrazioni
FROM v_sen_decine_per_estrazione
WHERE quanti_numeri >= 2
GROUP BY tipo_regolamento, quanti_numeri;

CREATE OR REPLACE VIEW v_sen_cadenze_per_estrazione AS
SELECT f.id, f.data_estrazione, f.tipo_regolamento, d.cadenza, COUNT(*) AS quanti_numeri
FROM v_sen_sestina_flat f
JOIN v_sen_numero_decina d ON d.numero = f.numero
GROUP BY f.id, f.data_estrazione, f.tipo_regolamento, d.cadenza;

CREATE OR REPLACE VIEW v_sen_cadenze_distribuzione AS
SELECT tipo_regolamento, quanti_numeri AS numeri_nella_stessa_cadenza, COUNT(*) AS frequenza_estrazioni
FROM v_sen_cadenze_per_estrazione
WHERE quanti_numeri >= 2
GROUP BY tipo_regolamento, quanti_numeri;

-- --- Somma della sestina e curva gaussiana ----------------------------
CREATE OR REPLACE VIEW v_sen_somma_sestina AS
SELECT id, data_estrazione, concorso, tipo_regolamento, (n1 + n2 + n3 + n4 + n5 + n6) AS somma
FROM estrazioni_superenalotto;

-- Istogramma a bande da 20 (il range statisticamente valido 200-340 cade
-- così in 7 bande centrali, comodo per disegnare la curva a campana).
CREATE OR REPLACE VIEW v_sen_somma_distribuzione AS
SELECT
    tipo_regolamento,
    FLOOR(somma / 20) * 20 AS fascia_somma_da,
    FLOOR(somma / 20) * 20 + 19 AS fascia_somma_a,
    COUNT(*) AS frequenza
FROM v_sen_somma_sestina
GROUP BY tipo_regolamento, FLOOR(somma / 20);

CREATE OR REPLACE VIEW v_sen_somma_statistiche AS
SELECT
    tipo_regolamento,
    COUNT(*) AS totale_estrazioni,
    ROUND(AVG(somma), 2) AS somma_media,
    MIN(somma) AS somma_minima,
    MAX(somma) AS somma_massima,
    ROUND(STDDEV_POP(somma), 2) AS deviazione_standard
FROM v_sen_somma_sestina
GROUP BY tipo_regolamento;

-- --- Consecutivita' (almeno una coppia di numeri contigui in sestina) --
CREATE OR REPLACE VIEW v_sen_consecutivi AS
SELECT id, data_estrazione, tipo_regolamento,
       MAX(consecutivo) AS ha_consecutivi,
       SUM(consecutivo) AS numero_coppie_consecutive
FROM (
    SELECT f.id, f.data_estrazione, f.tipo_regolamento,
           (f.numero - LAG(f.numero) OVER (PARTITION BY f.id ORDER BY f.numero) = 1) AS consecutivo
    FROM v_sen_sestina_flat f
) x
GROUP BY id, data_estrazione, tipo_regolamento;
-- NOTA: l'ordinamento per il confronto di contiguita' avviene per VALORE
-- del numero (ORDER BY f.numero), non per posizione di estrazione: è la
-- definizione corretta di "numeri consecutivi" (es. 14 e 15), indipendente
-- da come n1..n6 sono fisicamente ordinati in tabella.

CREATE OR REPLACE VIEW v_sen_consecutivi_distribuzione AS
SELECT
    tipo_regolamento,
    SUM(ha_consecutivi = 1) AS estrazioni_con_consecutivi,
    COUNT(*) AS totale_estrazioni,
    ROUND(SUM(ha_consecutivi = 1) / COUNT(*), 4) AS percentuale
FROM v_sen_consecutivi
GROUP BY tipo_regolamento;


-- =====================================================================
-- SEZIONE 5 — VISTE DI SUPPORTO PER AMBI/TERNI (la vera aggregazione
-- pesante è nella tabella cache di 05_procedure_cache_superenalotto.sql)
-- =====================================================================

-- Le 15 coppie (C(6,2)) di ogni estrazione.
CREATE OR REPLACE VIEW v_sen_ambi_per_estrazione AS
SELECT fa.id, fa.data_estrazione, fa.concorso, fa.tipo_regolamento,
       fa.numero AS numero1, fb.numero AS numero2
FROM v_sen_sestina_flat fa
JOIN v_sen_sestina_flat fb ON fb.id = fa.id AND fb.numero > fa.numero;

-- Le 20 terzine (C(6,3)) di ogni estrazione.
CREATE OR REPLACE VIEW v_sen_terni_per_estrazione AS
SELECT fa.id, fa.data_estrazione, fa.concorso, fa.tipo_regolamento,
       fa.numero AS numero1, fb.numero AS numero2, fc.numero AS numero3
FROM v_sen_sestina_flat fa
JOIN v_sen_sestina_flat fb ON fb.id = fa.id AND fb.numero > fa.numero
JOIN v_sen_sestina_flat fc ON fc.id = fa.id AND fc.numero > fb.numero;


-- #####################################################################
-- ## 05_procedure_cache_superenalotto.sql
-- #####################################################################
-- =====================================================================
-- 05_procedure_cache_superenalotto.sql
-- Progetto: lotto-stat-app
-- Scopo: tabelle cache + stored procedure per le elaborazioni
--        combinatorie pesanti del SuperEnalotto (Ambi, Terni).
-- Dipende da: 01_dimensioni_helper.sql, 04_viste_superenalotto.sql
--
-- REGOLA NON NEGOZIABILE: entrambe le tabelle cache sono chiavate anche
-- su tipo_regolamento, non solo su numero/i — altrimenti il problema
-- della fusione fra ere si sposta dalle viste alla cache, ma non si
-- risolve (vedi memoria di progetto).
-- =====================================================================

USE lotto_statistics;

-- =====================================================================
-- SEZIONE 1 — AMBI (4.005 combinazioni x fino a 3 ere = max 12.015 righe)
-- Qui l'universo completo (anche le coppie mai uscite in una data era)
-- viene materializzato: sono poche righe e la completezza è utile per
-- individuare "il grande mai-uscito" di una specifica era.
-- =====================================================================

CREATE TABLE IF NOT EXISTS cache_sen_ambi (
    tipo_regolamento     ENUM('SIMULATO','LOTTO','INDIPENDENTE') NOT NULL,
    numero1              TINYINT UNSIGNED NOT NULL,
    numero2              TINYINT UNSIGNED NOT NULL,
    frequenza            INT UNSIGNED     NOT NULL DEFAULT 0,
    ultima_data_uscita   DATE             NULL,
    ultimo_seq_uscita    INT UNSIGNED     NULL,
    ritardo_attuale      INT UNSIGNED     NOT NULL DEFAULT 0,
    ritardo_storico_max  INT UNSIGNED     NOT NULL DEFAULT 0,
    aggiornato_il        TIMESTAMP        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (tipo_regolamento, numero1, numero2),
    KEY idx_sen_ambi_ritardo (tipo_regolamento, ritardo_attuale DESC),
    KEY idx_sen_ambi_frequenza (tipo_regolamento, frequenza DESC),
    CONSTRAINT chk_sen_ambi_ordine CHECK (numero1 < numero2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER $$

CREATE PROCEDURE sp_refresh_sen_ambi()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    DELETE FROM cache_sen_ambi;

    INSERT INTO cache_sen_ambi
        (tipo_regolamento, numero1, numero2, frequenza, ultima_data_uscita, ultimo_seq_uscita,
         ritardo_attuale, ritardo_storico_max)
    SELECT
        era.tipo_regolamento, p.numero1, p.numero2,
        COALESCE(occ.frequenza, 0),
        occ.ultima_data_uscita,
        occ.ultimo_seq_uscita,
        (tot.totale_estrazioni - COALESCE(occ.ultimo_seq_uscita, 0)) AS ritardo_attuale,
        COALESCE(rs.ritardo_storico_max, tot.totale_estrazioni) AS ritardo_storico_max
    FROM (SELECT DISTINCT tipo_regolamento FROM estrazioni_superenalotto) era
    CROSS JOIN v_ambi_possibili p
    JOIN (SELECT tipo_regolamento, COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto GROUP BY tipo_regolamento) tot
        ON tot.tipo_regolamento = era.tipo_regolamento
    LEFT JOIN (
        SELECT ape.tipo_regolamento, ape.numero1, ape.numero2,
               COUNT(*) AS frequenza,
               MAX(ape.data_estrazione) AS ultima_data_uscita,
               MAX(seq.seq_regolamento) AS ultimo_seq_uscita
        FROM v_sen_ambi_per_estrazione ape
        JOIN v_sen_estrazioni_sequenziate seq ON seq.id = ape.id
        GROUP BY ape.tipo_regolamento, ape.numero1, ape.numero2
    ) occ ON occ.tipo_regolamento = era.tipo_regolamento AND occ.numero1 = p.numero1 AND occ.numero2 = p.numero2
    LEFT JOIN (
        -- ritardo storico massimo per ambo, calcolato SEMPRE dentro la
        -- singola era (seq_regolamento, mai seq_globale): coerente con
        -- il divieto di attraversare il 2009 senza accorgersene.
        SELECT tipo_regolamento, numero1, numero2, MAX(gap) AS ritardo_storico_max
        FROM (
            SELECT ape.tipo_regolamento, ape.numero1, ape.numero2, seq.seq_regolamento,
                seq.seq_regolamento - COALESCE(LAG(seq.seq_regolamento) OVER (
                    PARTITION BY ape.tipo_regolamento, ape.numero1, ape.numero2 ORDER BY seq.seq_regolamento
                ), 0) - 1 AS gap
            FROM v_sen_ambi_per_estrazione ape
            JOIN v_sen_estrazioni_sequenziate seq ON seq.id = ape.id
        ) gaps
        GROUP BY tipo_regolamento, numero1, numero2
    ) rs ON rs.tipo_regolamento = era.tipo_regolamento AND rs.numero1 = p.numero1 AND rs.numero2 = p.numero2;

    COMMIT;
END$$

DELIMITER ;

-- Uso: CALL sp_refresh_sen_ambi();

-- Viste di comodo — ATTENZIONE: filtrare sempre per tipo_regolamento
-- nell'applicazione (WHERE tipo_regolamento = 'INDIPENDENTE' per il
-- comportamento di default).
CREATE OR REPLACE VIEW v_sen_ambi_top_frequenti AS
SELECT * FROM cache_sen_ambi ORDER BY tipo_regolamento, frequenza DESC;

CREATE OR REPLACE VIEW v_sen_ambi_ritardatari AS
SELECT * FROM cache_sen_ambi ORDER BY tipo_regolamento, ritardo_attuale DESC;


-- =====================================================================
-- SEZIONE 2 — TERNI
-- C(90,3) = 117.480 combinazioni possibili: troppe per materializzare
-- l'intero universo per ogni era (fino a 352.440 righe di cui la
-- stragrande maggioranza mai uscita). Cache solo i terni EFFETTIVAMENTE
-- usciti almeno una volta in quella specifica era, come per le terzine
-- del Lotto — stesso ragionamento, stessa scelta.
-- =====================================================================

CREATE TABLE IF NOT EXISTS cache_sen_terni (
    tipo_regolamento     ENUM('SIMULATO','LOTTO','INDIPENDENTE') NOT NULL,
    numero1              TINYINT UNSIGNED NOT NULL,
    numero2              TINYINT UNSIGNED NOT NULL,
    numero3              TINYINT UNSIGNED NOT NULL,
    frequenza            INT UNSIGNED     NOT NULL DEFAULT 0,
    ultima_data_uscita   DATE             NULL,
    ultimo_seq_uscita    INT UNSIGNED     NULL,
    ritardo_attuale      INT UNSIGNED     NOT NULL DEFAULT 0,
    ritardo_storico_max  INT UNSIGNED     NOT NULL DEFAULT 0,
    aggiornato_il        TIMESTAMP        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (tipo_regolamento, numero1, numero2, numero3),
    KEY idx_sen_terni_ritardo (tipo_regolamento, ritardo_attuale DESC),
    KEY idx_sen_terni_frequenza (tipo_regolamento, frequenza DESC),
    CONSTRAINT chk_sen_terni_ordine CHECK (numero1 < numero2 AND numero2 < numero3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER $$

CREATE PROCEDURE sp_refresh_sen_terni()
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
    DELETE FROM cache_sen_terni;

    INSERT INTO cache_sen_terni
        (tipo_regolamento, numero1, numero2, numero3, frequenza, ultima_data_uscita, ultimo_seq_uscita,
         ritardo_attuale, ritardo_storico_max)
    SELECT
        base.tipo_regolamento, base.numero1, base.numero2, base.numero3,
        base.frequenza, base.ultima_data_uscita, base.ultimo_seq_uscita,
        (tot.totale_estrazioni - base.ultimo_seq_uscita) AS ritardo_attuale,
        COALESCE(rs.ritardo_storico_max, tot.totale_estrazioni) AS ritardo_storico_max
    FROM (
        SELECT tpe.tipo_regolamento, tpe.numero1, tpe.numero2, tpe.numero3,
               COUNT(*) AS frequenza,
               MAX(tpe.data_estrazione) AS ultima_data_uscita,
               MAX(seq.seq_regolamento) AS ultimo_seq_uscita
        FROM v_sen_terni_per_estrazione tpe
        JOIN v_sen_estrazioni_sequenziate seq ON seq.id = tpe.id
        GROUP BY tpe.tipo_regolamento, tpe.numero1, tpe.numero2, tpe.numero3
    ) base
    JOIN (SELECT tipo_regolamento, COUNT(*) AS totale_estrazioni FROM estrazioni_superenalotto GROUP BY tipo_regolamento) tot
        ON tot.tipo_regolamento = base.tipo_regolamento
    LEFT JOIN (
        SELECT tipo_regolamento, numero1, numero2, numero3, MAX(gap) AS ritardo_storico_max
        FROM (
            SELECT tpe.tipo_regolamento, tpe.numero1, tpe.numero2, tpe.numero3, seq.seq_regolamento,
                seq.seq_regolamento - COALESCE(LAG(seq.seq_regolamento) OVER (
                    PARTITION BY tpe.tipo_regolamento, tpe.numero1, tpe.numero2, tpe.numero3 ORDER BY seq.seq_regolamento
                ), 0) - 1 AS gap
            FROM v_sen_terni_per_estrazione tpe
            JOIN v_sen_estrazioni_sequenziate seq ON seq.id = tpe.id
        ) gaps
        GROUP BY tipo_regolamento, numero1, numero2, numero3
    ) rs ON rs.tipo_regolamento = base.tipo_regolamento AND rs.numero1 = base.numero1
        AND rs.numero2 = base.numero2 AND rs.numero3 = base.numero3;

    COMMIT;
END$$

DELIMITER ;

-- Uso: CALL sp_refresh_sen_terni();

CREATE OR REPLACE VIEW v_sen_terni_top_frequenti AS
SELECT * FROM cache_sen_terni ORDER BY tipo_regolamento, frequenza DESC;

CREATE OR REPLACE VIEW v_sen_terni_ritardatari AS
SELECT * FROM cache_sen_terni ORDER BY tipo_regolamento, ritardo_attuale DESC;

-- Quante terzine NON sono mai uscite in una data era (derivato per
-- combinatoria, senza dover materializzare le 117.480 righe possibili):
CREATE OR REPLACE VIEW v_sen_terni_mai_usciti_conteggio AS
SELECT
    e.tipo_regolamento,
    117480 - COUNT(c.numero1) AS terzine_mai_uscite,
    117480 AS terzine_possibili_totali
FROM (SELECT DISTINCT tipo_regolamento FROM estrazioni_superenalotto) e
LEFT JOIN cache_sen_terni c ON c.tipo_regolamento = e.tipo_regolamento
GROUP BY e.tipo_regolamento;


-- =====================================================================
-- SEZIONE 3 — PROCEDURA UNICA PER IL SUPERENALOTTO
-- =====================================================================
DELIMITER $$

CREATE PROCEDURE sp_refresh_tutte_le_cache_superenalotto()
BEGIN
    CALL sp_refresh_sen_ambi();
    CALL sp_refresh_sen_terni();
END$$

DELIMITER ;


-- #####################################################################
-- ## 06_master_refresh_e_grant.sql
-- #####################################################################
-- =====================================================================
-- 06_master_refresh_e_grant.sql
-- Progetto: lotto-stat-app
-- Scopo: procedura master unica + privilegi minimi da concedere a
--        statistics_user per l'uso quotidiano da FastAPI.
-- Dipende da: tutti i file precedenti (01..05)
-- =====================================================================

USE lotto_statistics;

DELIMITER $$

CREATE PROCEDURE sp_refresh_tutte_le_cache()
BEGIN
    CALL sp_refresh_tutte_le_cache_lotto();
    CALL sp_refresh_tutte_le_cache_superenalotto();
END$$

DELIMITER ;

-- Uso tipico: rilanciarla a fine importazione di nuove estrazioni
-- (es. in coda a scraper.py, dopo il commit dei nuovi INSERT), oppure
-- via scheduler (vedi esempio in fondo al file).


-- =====================================================================
-- PRIVILEGI — perché servono e cosa concedere
-- =====================================================================
-- L'init.sql del progetto concede a statistics_user:
--   SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER
-- su lotto_statistics.*. Questo NON basta per:
--   - creare le viste/procedure di questo motore (serve CREATE VIEW e
--     CREATE ROUTINE): per questo i file 01-06 vanno eseguiti come root,
--     esattamente come già avviene per init.sql;
--   - richiamare le procedure a runtime dal backend (serve EXECUTE).
--
-- Buona notizia: le stored procedure MySQL girano di default con i
-- privilegi del DEFINER (SQL SECURITY DEFINER, l'impostazione standard),
-- non del chiamante. Se le crei come root, statistics_user ha bisogno
-- SOLO del privilegio EXECUTE per poterle chiamare — non serve dargli
-- CREATE VIEW, CREATE ROUTINE né tantomeno DROP.
--
-- Le viste, allo stesso modo, sono già leggibili da statistics_user
-- grazie al SELECT concesso a livello di schema (lotto_statistics.*):
-- non serve alcun GRANT aggiuntivo per la sola lettura.
-- =====================================================================

GRANT EXECUTE ON lotto_statistics.* TO 'statistics_user'@'%';
GRANT EXECUTE ON lotto_statistics.* TO 'statistics_user'@'localhost';

FLUSH PRIVILEGES;

-- ---------------------------------------------------------------------
-- OPZIONALE — solo se in futuro vuoi che sia statistics_user (e non un
-- amministratore) a poter (ri)creare/modificare viste e procedure, ad
-- esempio da uno script di migrazione lanciato dal backend:
-- ---------------------------------------------------------------------
-- GRANT CREATE VIEW, CREATE ROUTINE, DROP ON lotto_statistics.* TO 'statistics_user'@'%';
-- GRANT CREATE VIEW, CREATE ROUTINE, DROP ON lotto_statistics.* TO 'statistics_user'@'localhost';
-- FLUSH PRIVILEGES;


-- =====================================================================
-- OPZIONALE — scheduling automatico del refresh notturno
-- =====================================================================
-- L'Event Scheduler di MySQL è disattivato di default. Per attivarlo in
-- questo ambiente Podman devi impostarlo nel container (non sopravvive
-- a un riavvio se non lo persisti in un file di configurazione montato,
-- es. my.cnf con "event_scheduler = ON", oppure eseguendo la SET GLOBAL
-- ad ogni avvio da uno script di init):
--
-- SET GLOBAL event_scheduler = ON;
--
-- CREATE EVENT IF NOT EXISTS ev_refresh_cache_statistiche
-- ON SCHEDULE EVERY 1 DAY
-- STARTS (TIMESTAMP(CURRENT_DATE) + INTERVAL 1 DAY + INTERVAL 3 HOUR)
-- DO
--     CALL sp_refresh_tutte_le_cache();
--
-- In alternativa, più semplice e più in linea con un'app FastAPI che
-- già orchestra lo scraper: richiama sp_refresh_tutte_le_cache() da
-- Python (mysql-connector-python, già in requirements.txt) subito dopo
-- ogni import riuscito di nuove estrazioni, invece di affidarti
-- all'Event Scheduler del DB.

