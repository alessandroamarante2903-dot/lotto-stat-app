-- =====================================================================
-- init.sql — Schema per il progetto lotto_statistics
-- =====================================================================

CREATE DATABASE IF NOT EXISTS lotto_statistics
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE lotto_statistics;

-- ---------------------------------------------------------------------
-- Tabella estrazioni del Lotto
-- Una riga per ogni ruota di ogni concorso (11 righe per estrazione)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS estrazioni_lotto (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    data_estrazione DATE        NOT NULL,
    concorso        INT UNSIGNED NOT NULL,
    ruota           VARCHAR(20) NOT NULL,
    n1              TINYINT UNSIGNED NOT NULL,
    n2              TINYINT UNSIGNED NOT NULL,
    n3              TINYINT UNSIGNED NOT NULL,
    n4              TINYINT UNSIGNED NOT NULL,
    n5              TINYINT UNSIGNED NOT NULL,
    creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_estrazione_lotto (data_estrazione, concorso, ruota),
    KEY idx_lotto_concorso (concorso),
    KEY idx_lotto_ruota (ruota)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- Tabella estrazioni del SuperEnalotto
-- Una riga per ogni concorso
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS estrazioni_superenalotto (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    data_estrazione DATE        NOT NULL,
    concorso        INT UNSIGNED NOT NULL,
    n1              TINYINT UNSIGNED NOT NULL,
    n2              TINYINT UNSIGNED NOT NULL,
    n3              TINYINT UNSIGNED NOT NULL,
    n4              TINYINT UNSIGNED NOT NULL,
    n5              TINYINT UNSIGNED NOT NULL,
    n6              TINYINT UNSIGNED NOT NULL,
    jolly           TINYINT UNSIGNED NOT NULL,
    superstar       TINYINT UNSIGNED NULL,   -- NULL per i concorsi antecedenti l'introduzione del SuperStar
    creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_estrazione_superenalotto (data_estrazione, concorso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- Utente applicativo dedicato
-- NOTE DI SICUREZZA:
--  - Sostituisci 'CHANGE_ME_STRONG_PASSWORD' con una password robusta
--    PRIMA di eseguire questo script in qualunque ambiente reale.
--  - Non committare mai la password reale in un repository Git: tienila
--    in un file .env / secret escluso da .gitignore e, se il tuo
--    workflow lo consente, generane il valore qui con envsubst.
--  - L'host '%' consente la connessione da qualsiasi host raggiungibile
--    sulla rete Podman: se il container backend e il container MySQL
--    condividono una user-defined network, puoi restringerlo
--    all'indirizzo/subnet di quella rete per maggiore sicurezza,
--    es. 'statistics_user'@'10.88.0.%'.
-- ---------------------------------------------------------------------
CREATE USER IF NOT EXISTS 'statistics_user'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

GRANT SELECT, INSERT, UPDATE, DELETE
    ON lotto_statistics.*
    TO 'statistics_user'@'%';

FLUSH PRIVILEGES;
