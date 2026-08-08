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
