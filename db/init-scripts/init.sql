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
-- NOTA: 'concorso' è nullable perché l'archivio storico ufficiale
-- (storico.txt, 1939-oggi) riporta solo data/ruota/numeri, non il
-- numero di concorso. Per questo il vincolo di unicità si basa su
-- (data_estrazione, ruota) e non include più 'concorso'.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS estrazioni_lotto (
    id              INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    data_estrazione DATE        NOT NULL,
    concorso        INT UNSIGNED NULL,
    ruota           VARCHAR(20) NOT NULL,
    Primo           TINYINT UNSIGNED NOT NULL,
    Secondo         TINYINT UNSIGNED NOT NULL,
    Terzo           TINYINT UNSIGNED NOT NULL,
    Quarto          TINYINT UNSIGNED NOT NULL,
    Quinto          TINYINT UNSIGNED NOT NULL,
    creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_estrazione_lotto (data_estrazione, ruota),
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

-- 1. CREAZIONE DEGLI UTENTI (se non esistono già)

CREATE USER IF NOT EXISTS 'statistics_user'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
CREATE USER IF NOT EXISTS 'statistics_user'@'localhost' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- 2. ASSEGNAZIONE PRIVILEGI PER L'ACCESSO ESTERNO (Script Python nel container backend)
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW
    ON lotto_statistics.*
    TO 'statistics_user'@'%';

-- 3. ASSEGNAZIONE PRIVILEGI PER L'ACCESSO LOCALE (I tuoi test con podman-compose exec)
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW 
    ON lotto_statistics.* 
    TO 'statistics_user'@'localhost';

-- 4. APPLICAZIONE DEI PRIVILEGI
FLUSH PRIVILEGES;
