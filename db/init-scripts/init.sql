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
-- NOTA: 'tipo_regolamento' classifica ogni riga in una delle tre ere
-- regolamentari del gioco (vedi discussione statistica nella chat):
--   SIMULATO      -> < 1997-12-03 (dati retroattivi sul Lotto storico)
--   LOTTO         -> 1997-12-03..2009-06-30 (combinazione derivata dal Lotto)
--   INDIPENDENTE  -> >= 2009-07-01 (estrazione moderna, urne dedicate)
-- Calcolato automaticamente da _determina_tipo_regolamento() in scraper.py,
-- mai inserito manualmente.
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
    tipo_regolamento ENUM('SIMULATO', 'LOTTO', 'INDIPENDENTE') NOT NULL,
    creato_il       TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_estrazione_superenalotto (data_estrazione, concorso),
    KEY idx_superenalotto_tipo_regolamento (tipo_regolamento)
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
--  - CREATE e ALTER sono stati aggiunti ai privilegi (oltre a SELECT/
--    INSERT/UPDATE/DELETE) perché scraper.py esegue da sé, all'avvio,
--    "CREATE TABLE IF NOT EXISTS" (FASE 1) ed eventuali "ALTER TABLE"
--    di migrazione (es. aggiunta della colonna tipo_regolamento su
--    installazioni preesistenti) con questa stessa utenza.
--    Se preferisci NON dare CREATE/ALTER all'utente applicativo,
--    rimuovili da qui e gestisci tu manualmente lo schema come utente
--    admin: in tal caso disabilita/salta la FASE 1 dello script (vedi
--    crea_tabelle() in scraper.py).
-- ---------------------------------------------------------------------

CREATE USER IF NOT EXISTS 'statistics_user'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
CREATE USER IF NOT EXISTS 'statistics_user'@'localhost' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- 2. ASSEGNAZIONE PRIVILEGI PER L'ACCESSO ESTERNO (Script Python nel container backend)
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER
    ON lotto_statistics.*
    TO 'statistics_user'@'%';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER
    ON estrazioni_superenalotto.*
    TO 'statistics_user'@'%';

-- 3. ASSEGNAZIONE PRIVILEGI PER L'ACCESSO LOCALE (I tuoi test con podman-compose exec)
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER
    ON lotto_statistics.* 
    TO 'statistics_user'@'localhost';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, SHOW VIEW, ALTER
    ON estrazioni_superenalotto.*
    TO 'statistics_user'@'localhost';

-- 4. APPLICAZIONE DEI PRIVILEGI
FLUSH PRIVILEGES;
