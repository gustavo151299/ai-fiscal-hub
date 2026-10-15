-- =====================================================================
--  ACESSO DE CONSULTA AO BANCO DE PRODUCAO  -  rodar como DBA
--
--  OBJETIVO: permitir analisar em PRODUCAO como as notas foram tratadas
--  de verdade (homologacao esta congelada em 23/06/2026 e e uma copia).
--
--  IMPORTANTE: este usuario e SO LEITURA. Ele NAO pode gravar, alterar,
--  apagar nem executar procedure. E de proposito: em producao a analise
--  nao pode ter risco de mexer em nota fiscal.
--
--  NAO usar o usuario AIHUB de homologacao aqui: aquele tem GRANT de
--  escrita em PRODFORNXML e permissao de executar as procedures de
--  lancamento. Em producao isso nao pode existir para analise.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1) Cria o usuario de consulta
--    Troque a senha por uma que voce escolher.
-- ---------------------------------------------------------------------
CREATE USER AIHUB_LEITURA IDENTIFIED BY "TROQUE_ESTA_SENHA";

GRANT CREATE SESSION TO AIHUB_LEITURA;     -- so conectar, nada alem disso

-- ---------------------------------------------------------------------
-- 2) SELECT nas tabelas que o sistema analisa (somente estas)
-- ---------------------------------------------------------------------
GRANT SELECT ON TEKNISA.NOTAFISCAL          TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.ITEMPRODUTO         TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.INTG_NOTAFISCAL     TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.INTG_ITEMPRODUTO    TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.INTG_INCONSISTENCIA TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.NOTADESTINADA       TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.HISTXMLNFE          TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.AUTOFORNE           TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.ITEMAUTF            TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.PRODUTO             TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.PRODFORNXML         TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.FORNECEDOR          TO AIHUB_LEITURA;
GRANT SELECT ON TEKNISA.CFOPXMLTPOPER       TO AIHUB_LEITURA;

-- ---------------------------------------------------------------------
-- 3) CONFERIR que ficou so leitura (o resultado deve ser SO 'SELECT')
-- ---------------------------------------------------------------------
SELECT DISTINCT PRIVILEGE
FROM   DBA_TAB_PRIVS
WHERE  GRANTEE = 'AIHUB_LEITURA';
-- esperado: uma unica linha, SELECT

-- ---------------------------------------------------------------------
-- PARA DESFAZER (quando a analise terminar)
-- ---------------------------------------------------------------------
-- DROP USER AIHUB_LEITURA CASCADE;
