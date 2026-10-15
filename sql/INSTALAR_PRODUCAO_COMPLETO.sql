-- ======================================================================
--  INSTALACAO COMPLETA EM PRODUCAO
--  Sistema: Gestao de Recebimento de NF-e   (versao v12b)
--
--  ORDEM DE EXECUCAO:
--     PARTE A  -> rodar como DBA / dono do schema TEKNISA   (permissoes)
--     PARTE B  -> rodar conectado como AIHUB                (tabelas)
--     PARTE C  -> rodar como DBA                            (indices)
--     PARTE D  -> opcional, como AIHUB                      (IA - Gemini)
--     PARTE E  -> conferencia final                         (so leitura)
--
--  PODE RODAR MAIS DE UMA VEZ: o que ja existe e ignorado, sem erro.
--  NADA AQUI ALTERA DADO DO TEKNISA. Só cria objetos no schema AIHUB,
--  concede leitura e cria indices.
-- ======================================================================


-- ======================================================================
--  PARTE A - PERMISSOES            >>> rodar como DBA <<<
-- ======================================================================

-- A1. o usuario do sistema precisa poder criar os objetos dele
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO AIHUB;

-- A2. LEITURA nas tabelas do Teknisa que o painel consulta
GRANT SELECT ON TEKNISA.NOTAFISCAL          TO AIHUB;
GRANT SELECT ON TEKNISA.NOTADESTINADA       TO AIHUB;
GRANT SELECT ON TEKNISA.ITEMPRODUTO         TO AIHUB;
GRANT SELECT ON TEKNISA.AUTOFORNE           TO AIHUB;
GRANT SELECT ON TEKNISA.ITEMAUTF            TO AIHUB;
GRANT SELECT ON TEKNISA.PRODUTO             TO AIHUB;
GRANT SELECT ON TEKNISA.FORNECEDOR          TO AIHUB;
GRANT SELECT ON TEKNISA.INTG_NOTAFISCAL     TO AIHUB;
GRANT SELECT ON TEKNISA.INTG_INCONSISTENCIA TO AIHUB;
GRANT SELECT ON TEKNISA.INTG_ITEMPRODUTO    TO AIHUB;  -- itens de nota ainda nao lancada
GRANT SELECT ON TEKNISA.HISTXMLNFE          TO AIHUB;  -- quem bloqueou o XML e quando
GRANT SELECT ON TEKNISA.ARQXMLNFE           TO AIHUB;
GRANT SELECT ON TEKNISA.RELACXMLAF          TO AIHUB;

-- A3. o De/Para de produto e a UNICA tabela do Teknisa em que o sistema
--     escreve (o vinculo "produto do fornecedor -> produto interno")
GRANT SELECT, INSERT, UPDATE ON TEKNISA.PRODFORNXML TO AIHUB;

-- A4. as rotinas NATIVAS do Teknisa que o robo chama
--     (sao as MESMAS que a tela de importacao usa - nada foi reescrito)
GRANT EXECUTE ON TEKNISA.NF_BAIXA_AF               TO AIHUB;
GRANT EXECUTE ON TEKNISA.VERIFICA_INCONS_NF        TO AIHUB;
GRANT EXECUTE ON TEKNISA.CONFIRMACAO_AUTOMATICA_NF TO AIHUB;
GRANT EXECUTE ON TEKNISA.GERA_NOTAFISCAL_CORPO_NFE TO AIHUB;   -- so p/ importacao automatica


-- ======================================================================
--  PARTE B - TABELAS DO SISTEMA    >>> rodar conectado como AIHUB <<<
--  (o bloco ignora "ja existe", entao pode repetir)
-- ======================================================================
DECLARE
  PROCEDURE cria(p_sql VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      -- ORA-00955 nome ja usado | ORA-01408 coluna ja indexada
      IF SQLCODE IN (-955, -1408) THEN NULL; ELSE RAISE; END IF;
  END;
BEGIN

  -- B1. LOG do robo: tudo que ele fez, com motivo
  cria('CREATE TABLE AIHUB.AIHUB_PROC_LOG (
          ID           NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          NUMERO_NF    VARCHAR2(20),
          CHAVE_NF     VARCHAR2(60),
          CDFORNECED   VARCHAR2(21),
          FILIAL_AF    VARCHAR2(4),
          STATUS       VARCHAR2(30),
          DETALHE      VARCHAR2(4000),
          SCORE_MATCH  NUMBER(5,4),
          DT_PROC      DATE DEFAULT SYSDATE)');
  cria('CREATE INDEX AIHUB.IX_PROCLOG_CHAVE ON AIHUB.AIHUB_PROC_LOG (CHAVE_NF)');
  cria('CREATE INDEX AIHUB.IX_PROCLOG_DT    ON AIHUB.AIHUB_PROC_LOG (DT_PROC)');

  -- B2. FILA DE REVISAO: itens que o robo nao reconheceu com certeza
  cria('CREATE TABLE AIHUB.AIHUB_PENDENCIA (
          ID            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          CHAVE_NF      VARCHAR2(44),
          CDFILIAL      VARCHAR2(4),
          NRLANCTONF    VARCHAR2(20),
          NRSEQITPROD   VARCHAR2(10),
          CDFORNECED    VARCHAR2(21),
          NM_FORNECED   VARCHAR2(150),
          CPROD_XML     VARCHAR2(60),
          DESC_XML      VARCHAR2(300),
          CD_SUGERIDO   VARCHAR2(20),
          NM_SUGERIDO   VARCHAR2(150),
          SCORE         NUMBER(5,2),
          STATUS        VARCHAR2(12) DEFAULT ''ABERTO'',
          DT_CRIACAO    DATE DEFAULT SYSDATE,
          DT_RESOLUCAO  DATE,
          CD_ESCOLHIDO  VARCHAR2(20),
          CD_OPERADOR   VARCHAR2(30))');
  cria('CREATE INDEX AIHUB.IX_PEND_STATUS ON AIHUB.AIHUB_PENDENCIA (STATUS)');
  cria('CREATE INDEX AIHUB.IX_PEND_NOTA   ON AIHUB.AIHUB_PENDENCIA (CDFILIAL, NRLANCTONF)');

  -- B3. CONFIGURACAO: e o que os botoes da tela ligam/desligam
  cria('CREATE TABLE AIHUB.AIHUB_CONFIG (
          CHAVE        VARCHAR2(40) PRIMARY KEY,
          VALOR        VARCHAR2(200),
          DT_ALTERACAO DATE DEFAULT SYSDATE)');

  -- B4. CONFERENCIA: vinculos ja auditados por uma pessoa
  cria('CREATE TABLE AIHUB.AIHUB_CONFERIDO (
          CDFORNECED   VARCHAR2(21),
          CDPRODUTOXML VARCHAR2(60),
          OK           VARCHAR2(1) DEFAULT ''S'',
          DT_CONF      DATE DEFAULT SYSDATE,
          OPERADOR     VARCHAR2(30),
          CONSTRAINT PK_AIHUB_CONFERIDO PRIMARY KEY (CDFORNECED, CDPRODUTOXML))');

  -- B5. ARQUIVADAS: o botao "Nao se aplica / ja tratei"
  --     (nota que nunca vira lancamento: bonificacao, amostra, CNPJ errado)
  cria('CREATE TABLE AIHUB.AIHUB_ARQUIVADA (
          NRACESSONFE  VARCHAR2(44) PRIMARY KEY,
          MOTIVO       VARCHAR2(40),
          OBS          VARCHAR2(300),
          OPERADOR     VARCHAR2(30),
          DT_ARQUIVO   DATE DEFAULT SYSDATE)');

END;
/

-- B6. VALORES INICIAIS - tudo comeca DESLIGADO, de proposito.
--     Ligar so pela tela, em fases (ver PRODUCAO_CHECKLIST.md).
MERGE INTO AIHUB.AIHUB_CONFIG c USING (
  SELECT 'ROBO_LIGADO'   CHAVE, '0'  VALOR FROM dual UNION ALL
  SELECT 'MODO_AUTO'          , '0'        FROM dual UNION ALL
  SELECT 'MODO_IMPORTA'       , '0'        FROM dual UNION ALL
  SELECT 'INTERVALO_MIN'      , '10'       FROM dual UNION ALL
  SELECT 'USA_GEMINI'         , '0'        FROM dual
) s ON (c.CHAVE = s.CHAVE)
WHEN NOT MATCHED THEN INSERT (CHAVE, VALOR) VALUES (s.CHAVE, s.VALOR);

COMMIT;


-- ======================================================================
--  PARTE C - INDICES DE DESEMPENHO      >>> rodar como DBA <<<
--
--  POR QUE: o painel filtra a NOTAFISCAL (centenas de milhares de linhas)
--  por colunas sem indice. Medido em homologacao SEM eles:
--     indicadores da tela inicial ... 118 a 273 segundos
--     simulacao de 5 notas ......... 420 segundos
--  Com eles, as mesmas consultas respondem em segundos.
--
--  SEM 'ONLINE', DE PROPOSITO. O banco do cliente e Oracle 19c STANDARD
--  EDITION 2, e ali a criacao ONLINE nao existe: devolve ORA-00439 "feature
--  not enabled". Pior, o tratamento abaixo so engole "indice ja existe"
--  (-955) e "colunas ja indexadas" (-1408): o ORA-00439 subiria e abortaria
--  a instalacao INTEIRA. Consequencia de tirar o ONLINE: a criacao TRAVA a
--  tabela para escrita enquanto roda - fazer em janela de baixo movimento.
--
--  O -1408 e o que torna esta parte segura de repetir: se ja houver um indice
--  equivalente com outro nome (o Teknisa tem varios), o banco recusa e a
--  procedure ignora, em vez de criar um duplicado ocupando espaco a toa.
-- ======================================================================
DECLARE
  PROCEDURE idx(p_sql VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE IN (-955, -1408) THEN NULL; ELSE RAISE; END IF;
  END;
BEGIN
  idx('CREATE INDEX TEKNISA.IX_NF_STATUS_TIPO
         ON TEKNISA.NOTAFISCAL (CDTIPOOPER, IDSTATUS, DTEMISSAO)');
  -- o mais importante: sem ele, "esta nota ja entrou?" varre a NOTAFISCAL
  -- inteira. Medido em homologacao: 31 SEGUNDOS por execucao, e ela roda
  -- dentro de um NOT EXISTS correlacionado (uma vez por linha).
  idx('CREATE INDEX TEKNISA.IX_NF_ACESSONFE
         ON TEKNISA.NOTAFISCAL (NRACESSONFE)');
  idx('CREATE INDEX TEKNISA.IX_INTGNF_FIL_LANC
         ON TEKNISA.INTG_NOTAFISCAL (CDFILIAL, NRLANCTONF)');
  idx('CREATE INDEX TEKNISA.IX_INTGINC_ACESSO
         ON TEKNISA.INTG_INCONSISTENCIA (NRACESSONFE, IDATIVO)');
  idx('CREATE INDEX TEKNISA.IX_HISTXML_ACESSO
         ON TEKNISA.HISTXMLNFE (NRACESSONFE, NRSEQHISTOR)');
  idx('CREATE INDEX TEKNISA.IX_PFXML_FORN
         ON TEKNISA.PRODFORNXML (CDFORNECED, CDPRODUTOXML, IDATIVO)');
END;
/

-- atualiza as estatisticas para o Oracle passar a usar os indices novos
BEGIN
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','NOTAFISCAL',          cascade=>TRUE, degree=>4, estimate_percent=>10);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','INTG_NOTAFISCAL',     cascade=>TRUE, degree=>4);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','INTG_INCONSISTENCIA', cascade=>TRUE, degree=>4);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','HISTXMLNFE',          cascade=>TRUE, degree=>4);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','PRODFORNXML',         cascade=>TRUE, degree=>4);
END;
/


-- ======================================================================
--  PARTE D - ASSISTENTE DE IA (OPCIONAL)    >>> como AIHUB <<<
--  So rode se for usar a segunda opiniao da IA na fila do robo.
--  ATENCAO: no plano GRATUITO o Google PODE usar os dados enviados.
--  Para producao, use plano pago. Sem esta tabela o sistema funciona
--  igual, apenas sem o parecer da IA.
-- ======================================================================
DECLARE
  PROCEDURE cria(p_sql VARCHAR2) IS
  BEGIN EXECUTE IMMEDIATE p_sql;
  EXCEPTION WHEN OTHERS THEN
    IF SQLCODE IN (-955, -1408) THEN NULL; ELSE RAISE; END IF;
  END;
BEGIN
  cria('CREATE TABLE AIHUB.AIHUB_GEMINI (
          PEND_ID      NUMBER PRIMARY KEY,
          CD_SUGERIDO  VARCHAR2(20),
          NM_SUGERIDO  VARCHAR2(150),
          MOTIVO       VARCHAR2(400),
          DT           DATE DEFAULT SYSDATE)');
END;
/
COMMIT;


-- ======================================================================
--  PARTE E - CONFERENCIA FINAL (so leitura)
--  Rode como AIHUB. Tudo tem que dar "OK".
-- ======================================================================
SELECT 'TABELAS DO SISTEMA' AS VERIFICACAO,
       CASE WHEN COUNT(*) >= 5 THEN 'OK (' || COUNT(*) || ' de 5)'
            ELSE 'FALTA -> ' || (5 - COUNT(*)) || ' tabela(s)' END AS RESULTADO
FROM   ALL_TABLES
WHERE  OWNER='AIHUB'
  AND  TABLE_NAME IN ('AIHUB_PROC_LOG','AIHUB_PENDENCIA','AIHUB_CONFIG',
                      'AIHUB_CONFERIDO','AIHUB_ARQUIVADA')
UNION ALL
SELECT 'CONFIGURACOES',
       CASE WHEN COUNT(*) >= 4 THEN 'OK' ELSE 'FALTA rodar a PARTE B6' END
FROM   AIHUB.AIHUB_CONFIG
UNION ALL
SELECT 'LEITURA NO TEKNISA',
       CASE WHEN COUNT(*) >= 12 THEN 'OK (' || COUNT(*) || ' tabelas)'
            ELSE 'FALTAM PERMISSOES -> rodar PARTE A' END
FROM   ALL_TAB_PRIVS
WHERE  GRANTEE='AIHUB' AND TABLE_SCHEMA='TEKNISA' AND PRIVILEGE='SELECT'
UNION ALL
SELECT 'ESCRITA NO DE/PARA',
       CASE WHEN COUNT(*) >= 2 THEN 'OK' ELSE 'FALTA -> GRANT em PRODFORNXML' END
FROM   ALL_TAB_PRIVS
WHERE  GRANTEE='AIHUB' AND TABLE_SCHEMA='TEKNISA'
  AND  TABLE_NAME='PRODFORNXML' AND PRIVILEGE IN ('INSERT','UPDATE')
UNION ALL
SELECT 'ROTINAS NATIVAS',
       CASE WHEN COUNT(*) >= 3 THEN 'OK' ELSE 'FALTAM GRANTs de EXECUTE' END
FROM   ALL_TAB_PRIVS
WHERE  GRANTEE='AIHUB' AND PRIVILEGE='EXECUTE'
  AND  TABLE_NAME IN ('NF_BAIXA_AF','VERIFICA_INCONS_NF','CONFIRMACAO_AUTOMATICA_NF')
UNION ALL
SELECT 'INDICES DE DESEMPENHO',
       CASE WHEN COUNT(*) >= 5 THEN 'OK' ELSE 'FALTAM ' || (6-COUNT(*)) ||
            ' -> painel vai ficar LENTO' END
FROM   ALL_INDEXES
WHERE  OWNER='TEKNISA'
  AND  INDEX_NAME IN ('IX_NF_STATUS_TIPO','IX_NF_ACESSONFE','IX_INTGNF_FIL_LANC',
                      'IX_INTGINC_ACESSO','IX_HISTXML_ACESSO','IX_PFXML_FORN')
UNION ALL
SELECT 'IA (opcional)',
       CASE WHEN COUNT(*) > 0 THEN 'instalada' ELSE 'nao instalada (tudo bem)' END
FROM   ALL_TABLES WHERE OWNER='AIHUB' AND TABLE_NAME='AIHUB_GEMINI';


-- ======================================================================
--  PARA DESFAZER (se precisar)
-- ======================================================================
-- DROP INDEX TEKNISA.IX_NF_STATUS_TIPO;
-- DROP INDEX TEKNISA.IX_NF_ACESSONFE;
-- DROP INDEX TEKNISA.IX_INTGNF_FIL_LANC;
-- DROP INDEX TEKNISA.IX_INTGINC_ACESSO;
-- DROP INDEX TEKNISA.IX_HISTXML_ACESSO;
-- DROP INDEX TEKNISA.IX_PFXML_FORN;
--
-- DROP TABLE AIHUB.AIHUB_ARQUIVADA;
-- DROP TABLE AIHUB.AIHUB_CONFERIDO;
-- DROP TABLE AIHUB.AIHUB_PENDENCIA;
-- DROP TABLE AIHUB.AIHUB_PROC_LOG;
-- DROP TABLE AIHUB.AIHUB_CONFIG;
-- DROP TABLE AIHUB.AIHUB_GEMINI;
-- (nada disso afeta dados do Teknisa)
