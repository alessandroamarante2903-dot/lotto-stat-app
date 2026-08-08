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
