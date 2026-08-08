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
