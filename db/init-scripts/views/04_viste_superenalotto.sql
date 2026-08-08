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
-- GROUP BY deve ripetere le stesse espressioni del SELECT parola per
-- parola: con sql_mode=only_full_group_by (default MySQL 8) raggruppare
-- solo su FLOOR(somma/20) non basta a rendere valide le colonne SELECT
-- "FLOOR(somma/20)*20" e "FLOOR(somma/20)*20+19", perché MySQL verifica
-- l'identità sintattica dell'espressione, non la dipendenza funzionale
-- (errore reale riscontrato: 1055 "Expression #2 of SELECT list is not
-- in GROUP BY clause").
GROUP BY tipo_regolamento, FLOOR(somma / 20) * 20, FLOOR(somma / 20) * 20 + 19;

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
