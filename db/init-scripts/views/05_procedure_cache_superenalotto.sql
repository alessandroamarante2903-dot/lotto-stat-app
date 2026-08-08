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
