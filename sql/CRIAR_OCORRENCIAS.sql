-- ===========================================================================
--  FILA DE OCORRENCIAS - nada se perde
--
--  POR QUE ESTA TABELA EXISTE
--  --------------------------
--  Ate aqui, quando o Teknisa recusava uma importacao, o motivo aparecia num
--  modal. A pessoa lia, fechava, e acabou: nao ficava registro, ninguem sabia
--  quantas notas estavam paradas por preco, nem se alguem tinha ido atras.
--
--  Caso real (NF 9667827, 07\08\2026): a recusa trouxe SETE apontamentos de
--  uma vez - 051 (vencimento), dois 055 (preco difere da AF) e quatro 056
--  (produto nao e oriundo da AF). Tres areas diferentes envolvidas, e tudo
--  isso morria quando a janela fechasse.
--
--  Agora cada apontamento vira UMA LINHA aqui, com QUEM RESOLVE, e so sai da
--  fila quando alguem disser que resolveu.
--
--  RODAR COMO AIHUB. Idempotente: pode rodar de novo sem quebrar.
-- ===========================================================================

DECLARE
  PROCEDURE cria(p_sql VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    -- -955 = nome ja usado. E o unico erro que engolimos: significa "ja
    -- existe", nao "falhou". Qualquer outro sobe, para a instalacao nao
    -- terminar dizendo que deu certo quando nao deu.
    WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF;
  END;
BEGIN
  cria('CREATE TABLE AIHUB.AIHUB_OCORRENCIA (
          ID            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          CHAVE_NF      VARCHAR2(44)  NOT NULL,
          NUMERO_NF     VARCHAR2(20),
          CDFORNECED    VARCHAR2(21),
          NM_FORNECED   VARCHAR2(150),
          CDFILIAL      VARCHAR2(10),
          CODIGO        VARCHAR2(10)  NOT NULL,
          QUEM          VARCHAR2(10)  NOT NULL,
          TITULO        VARCHAR2(150),
          ACAO          VARCHAR2(400),
          DETALHE       VARCHAR2(400),
          STATUS        VARCHAR2(12)  DEFAULT ''ABERTO'' NOT NULL,
          DT_ABERTURA   DATE          DEFAULT SYSDATE,
          DT_RESOLUCAO  DATE,
          CD_OPERADOR   VARCHAR2(30),
          OBS           VARCHAR2(400))');

  -- UMA ocorrencia ABERTA por (nota, codigo, detalhe). Sem isto, cada clique
  -- em "Importar" duplicaria a fila - e a NF 9667827 sozinha geraria 7 linhas
  -- novas por tentativa.
  --
  -- O indice inclui DETALHE porque o mesmo codigo aparece varias vezes na
  -- mesma nota com textos diferentes: os quatro 056 dela eram produtos
  -- distintos, e cada um e um problema separado.
  cria('CREATE UNIQUE INDEX AIHUB.IX_OCOR_ABERTA ON AIHUB.AIHUB_OCORRENCIA (
          CASE WHEN STATUS = ''ABERTO''
               THEN CHAVE_NF || ''|'' || CODIGO || ''|'' ||
                    SUBSTR(NVL(DETALHE,''-''),1,120)
          END)');

  -- as duas telas: por responsavel, e a nota inteira
  cria('CREATE INDEX AIHUB.IX_OCOR_QUEM   ON AIHUB.AIHUB_OCORRENCIA (STATUS, QUEM)');
  cria('CREATE INDEX AIHUB.IX_OCOR_CHAVE  ON AIHUB.AIHUB_OCORRENCIA (CHAVE_NF)');
END;
/

-- Conferencia
SELECT COUNT(*) AS OCORRENCIAS FROM AIHUB.AIHUB_OCORRENCIA;
SELECT INDEX_NAME, STATUS FROM ALL_INDEXES
 WHERE TABLE_OWNER = 'AIHUB' AND TABLE_NAME = 'AIHUB_OCORRENCIA';
