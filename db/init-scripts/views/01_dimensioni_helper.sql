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
