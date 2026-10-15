-- =====================================================================
--  CRIAR SO A TABELA AIHUB_ARQUIVADA
--
--  Para que serve: guarda as notas que a equipe marcou como "nao se aplica /
--  ja tratei" (bonificacao, amostra, CNPJ errado, compra recusada). Sem esta
--  tabela, o botao "Nao se aplica" do painel responde "Recurso nao instalado".
--
--  Rodar conectado como AIHUB (ou como DBA criando no schema AIHUB).
--  E SEGURO rodar mais de uma vez: se a tabela ja existir, nao faz nada e nao
--  da erro (ORA-00955 e ignorado de proposito).
--
--  Esta e a MESMA definicao da PARTE B do INSTALAR_PRODUCAO_COMPLETO.sql,
--  isolada aqui so para quem ja instalou o resto e so precisa desta tabela.
-- =====================================================================
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE AIHUB.AIHUB_ARQUIVADA (
      NRACESSONFE  VARCHAR2(44) PRIMARY KEY,
      MOTIVO       VARCHAR2(40),
      OBS          VARCHAR2(300),
      OPERADOR     VARCHAR2(30),
      DT_ARQUIVO   DATE DEFAULT SYSDATE)';
  DBMS_OUTPUT.PUT_LINE('AIHUB_ARQUIVADA criada.');
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE = -955 THEN            -- ORA-00955: name is already used
      DBMS_OUTPUT.PUT_LINE('AIHUB_ARQUIVADA ja existe - nada a fazer.');
    ELSE
      RAISE;
    END IF;
END;
/

-- Conferencia (so leitura):
SELECT CASE WHEN COUNT(*) > 0 THEN 'OK - AIHUB_ARQUIVADA existe'
            ELSE '*** AINDA NAO existe ***' END AS SITUACAO
FROM   ALL_TABLES
WHERE  OWNER = 'AIHUB' AND TABLE_NAME = 'AIHUB_ARQUIVADA';
