-- =====================================================================
--  INVESTIGACAO: arvore de produto, De/Para, tipo de operacao e CFOP
--  Criado em 31/07/2026 - para rodar no banco (HOMOLOGACAO primeiro)
--
--  100% SOMENTE LEITURA. Nao ha INSERT, UPDATE, DELETE, DDL nem COMMIT.
--  Pode rodar em producao sem risco, mas prefira homologacao.
--
--  COMO USAR: rode bloco a bloco e me mande o resultado colado.
--  Se algum bloco der ORA-00942 (table or view does not exist), significa
--  que o usuario nao tem GRANT nessa tabela - me avise, isso ja e resposta.
-- =====================================================================


-- =====================================================================
--  BLOCO 0 - CONFERENCIA (rode este primeiro)
--  Serve para validar se os numeros que eu reportei batem com a realidade.
--  Resultado que EU afirmei ter visto esta no comentario de cada linha.
-- =====================================================================
SELECT
  (SELECT COUNT(*) FROM TEKNISA.PRODUTO)                                AS PRODUTOS,          -- eu vi: 49651
  (SELECT COUNT(*) FROM TEKNISA.PRODUTO WHERE CDARVPROD IS NOT NULL)    AS COM_ARVPROD,       -- eu vi: 49651
  (SELECT COUNT(*) FROM TEKNISA.PRODUTO WHERE CDARVPAI  IS NOT NULL)    AS COM_ARVPAI,        -- eu vi: 0
  (SELECT COUNT(*) FROM TEKNISA.PRODUTO WHERE CDGENERO  IS NOT NULL)    AS COM_GENERO,        -- eu vi: 0
  (SELECT COUNT(*) FROM TEKNISA.PRODFORNXML WHERE IDATIVO='S')          AS DEPARA_ATIVOS      -- eu vi: 82563
FROM DUAL;


-- =====================================================================
--  BLOCO 1 - A ARVORE DE PRODUTO
-- =====================================================================

-- 1.1 Os niveis da arvore e o tamanho do codigo em cada um
--     (eu vi: nivel 1=9, 2=57, 3=464, 4=18302, 5=30819)
SELECT CDNVPRODUTO                          AS NIVEL,
       COUNT(*)                             AS QUANTOS,
       MIN(LENGTH(CDARVPROD))               AS MIN_TAM_COD,
       MAX(LENGTH(CDARVPROD))               AS MAX_TAM_COD,
       COUNT(CDPRODESTO)                    AS TEM_PROD_ESTOQUE,
       COUNT(VRFATOCONV)                    AS TEM_FATOR_CONV
FROM   TEKNISA.PRODUTO
GROUP  BY CDNVPRODUTO
ORDER  BY 1;

-- 1.2 Exemplos de cada nivel (para ver o formato do codigo na pratica)
SELECT NIVEL, CDARVPROD, CDPRODUTO, NOME, UN, CDPRODESTO, VRFATOCONV
FROM (
  SELECT CDNVPRODUTO AS NIVEL, CDARVPROD, CDPRODUTO,
         SUBSTR(NMPRODUTO,1,45) AS NOME, SGUNIDADE AS UN,
         CDPRODESTO, VRFATOCONV,
         ROW_NUMBER() OVER (PARTITION BY CDNVPRODUTO ORDER BY CDPRODUTO) AS RN
  FROM   TEKNISA.PRODUTO)
WHERE  RN <= 5
ORDER  BY NIVEL, CDPRODUTO;

-- 1.3 Dentro do ultimo nivel: os 2 digitos finais separam estoque de compra?
--     (eu vi: termina 00 -> 17708 com fator 1 ; termina !=00 -> 13111 fator medio 20,7)
SELECT CASE WHEN SUBSTR(CDPRODUTO,-2)='00' THEN 'final 00  (estoque?)'
            ELSE 'final !=00 (compra?)' END           AS GRUPO,
       COUNT(*)                                       AS QUANTOS,
       ROUND(AVG(VRFATOCONV),3)                       AS FATOR_MEDIO,
       SUM(CASE WHEN VRFATOCONV=1 THEN 1 ELSE 0 END)  AS COM_FATOR_1,
       COUNT(DISTINCT SGUNIDADE)                      AS UNIDADES_DISTINTAS
FROM   TEKNISA.PRODUTO
WHERE  CDNVPRODUTO = (SELECT MAX(CDNVPRODUTO) FROM TEKNISA.PRODUTO)
GROUP  BY CASE WHEN SUBSTR(CDPRODUTO,-2)='00' THEN 'final 00  (estoque?)'
               ELSE 'final !=00 (compra?)' END;


-- =====================================================================
--  BLOCO 2 - PARA ONDE APONTAM OS DE/PARA  (a pergunta que decide o matcher)
-- =====================================================================

-- 2.1 Nivel do produto vinculado  (eu vi: 100% no nivel 5)
SELECT P.CDNVPRODUTO AS NIVEL_VINCULADO, COUNT(*) AS QT_VINCULOS
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.IDATIVO='S'
GROUP  BY P.CDNVPRODUTO
ORDER  BY 1;

-- 2.2 Estoque (00) x Compra (!=00), cruzado com a unidade
--     (eu vi: 66,3% no 00 [UN,KG,MT] e 33,7% no !=00 [PC,CX,FD,LA])
SELECT CASE WHEN SUBSTR(P.CDPRODUTO,-2)='00' THEN 'ESTOQUE (00)'
            ELSE 'COMPRA (!=00)' END        AS DESTINO,
       P.SGUNIDADE                          AS UNIDADE,
       COUNT(*)                             AS QT,
       ROUND(AVG(P.VRFATOCONV),2)           AS FATOR_MEDIO
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.IDATIVO='S'
GROUP  BY CASE WHEN SUBSTR(P.CDPRODUTO,-2)='00' THEN 'ESTOQUE (00)'
               ELSE 'COMPRA (!=00)' END, P.SGUNIDADE
ORDER  BY QT DESC
FETCH FIRST 20 ROWS ONLY;

-- 2.3 Amostra concreta: descricao do fornecedor -> produto interno escolhido
SELECT * FROM (
  SELECT D.CDFORNECED,
         SUBSTR(D.NMPRODUTOXML,1,38) AS DESCRICAO_NO_XML,
         D.CDPRODUTO,
         P.CDNVPRODUTO               AS NIVEL,
         SUBSTR(P.NMPRODUTO,1,38)    AS PRODUTO_INTERNO,
         P.SGUNIDADE                 AS UN,
         P.VRFATOCONV                AS FATOR
  FROM   TEKNISA.PRODFORNXML D
  JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
  WHERE  D.IDATIVO='S' AND D.NMPRODUTOXML IS NOT NULL
  ORDER  BY D.DTINCLUSAO DESC NULLS LAST)
WHERE ROWNUM <= 30;


-- =====================================================================
--  BLOCO 3 - TIPO DE OPERACAO  (as regras do gestor)
-- =====================================================================

-- 3.1 O CATALOGO oficial dos tipos. Precisamos do nome de cada codigo.
--     No meu teste isto deu ORA-00942 (faltaria GRANT para o AIHUB).
--     Se der erro aqui, rode como um usuario que enxergue a tabela.
SELECT * FROM TEKNISA.TIPOOPERAC ORDER BY 1 FETCH FIRST 100 ROWS ONLY;

-- 3.2 Quais tipos aparecem de fato nas notas
--     (eu vi 30+ tipos, incluindo 08 com 5859 notas e 06 com 863)
SELECT CDTIPOOPER, COUNT(*) AS QT,
       MIN(TO_CHAR(DTEMISSAO,'YYYY-MM')) AS DESDE,
       MAX(TO_CHAR(DTEMISSAO,'YYYY-MM')) AS ATE
FROM   TEKNISA.NOTAFISCAL
GROUP  BY CDTIPOOPER
ORDER  BY QT DESC;

-- 3.3 REGRA "produtor rural -> tipo 08"
--     (eu vi: 2 fornecedores com 'S', e as notas deles em CDTIPOOPER='08')
SELECT NVL(F.IDFORNPRODRURAL,'(nulo)') AS PRODUTOR_RURAL,
       N.CDTIPOOPER,
       COUNT(*) AS QT
FROM   TEKNISA.NOTAFISCAL N
JOIN   TEKNISA.FORNECEDOR F ON F.CDFORNECED = N.CDFORNECED
WHERE  N.DTEMISSAO >= ADD_MONTHS(SYSDATE,-24)
GROUP  BY F.IDFORNPRODRURAL, N.CDTIPOOPER
ORDER  BY PRODUTOR_RURAL, QT DESC;

-- 3.4 REGRA "bonificacao -> tipo 06"
--     (eu vi: praticamente toda natureza com bonificacao/doacao/brinde em '06')
SELECT SUBSTR(UPPER(G.DSNATOPER),1,45) AS NATUREZA_NO_XML,
       G.CDTIPOOPER,
       COUNT(*) AS QT
FROM   TEKNISA.INTG_NOTAFISCAL G
WHERE  UPPER(G.DSNATOPER) LIKE '%BONIF%'
    OR UPPER(G.DSNATOPER) LIKE '%BRINDE%'
    OR UPPER(G.DSNATOPER) LIKE '%DOA%'
GROUP  BY SUBSTR(UPPER(G.DSNATOPER),1,45), G.CDTIPOOPER
ORDER  BY QT DESC
FETCH FIRST 25 ROWS ONLY;

-- 3.5 REGRA "venda 5101/5102/6101/6102 -> tipo 01"
SELECT SUBSTR(UPPER(G.DSNATOPER),1,45) AS NATUREZA_NO_XML,
       G.CDTIPOOPER,
       COUNT(*) AS QT
FROM   TEKNISA.INTG_NOTAFISCAL G
WHERE  G.DSNATOPER IS NOT NULL
GROUP  BY SUBSTR(UPPER(G.DSNATOPER),1,45), G.CDTIPOOPER
ORDER  BY QT DESC
FETCH FIRST 30 ROWS ONLY;


-- =====================================================================
--  BLOCO 4 - A TELA "Importacao/Consulta de NF (Arquivo XML)"
-- =====================================================================

-- 4.1 A coluna "Origem Filial (XML)" do print = DSTAGORIGFILI
--     (eu vi: AF(Relacionar AF) 61,2% · nulo 30,4% · Manual 6,9% · AF (tag xPed) 1,5%)
--     >>> "AF (tag xPed)" e a "tag" que o gestor mencionou <<<
SELECT NVL(DSTAGORIGFILI,'(nulo)') AS ORIGEM_FILIAL_XML,
       COUNT(*) AS QT,
       ROUND(100 * RATIO_TO_REPORT(COUNT(*)) OVER (), 1) AS PCT
FROM   TEKNISA.INTG_NOTAFISCAL
GROUP  BY DSTAGORIGFILI
ORDER  BY QT DESC;

-- 4.2 A coluna "Status" do print (Bloqueado / Pre-Lancada / Utilizada)
SELECT NVL(IDSTATUS,'(nulo)') AS ID_STATUS,
       COUNT(*)               AS QT,
       COUNT(DTUTILIZAXML)    AS TEM_DT_UTILIZA,
       COUNT(NRLANCTONF)      AS TEM_LANCAMENTO
FROM   TEKNISA.INTG_NOTAFISCAL
GROUP  BY IDSTATUS
ORDER  BY QT DESC;

-- 4.3 Origem x Status - o cruzamento que a tela exibe
SELECT NVL(DSTAGORIGFILI,'(nulo)') AS ORIGEM,
       NVL(IDSTATUS,'-')           AS STATUS,
       COUNT(*)                    AS QT
FROM   TEKNISA.INTG_NOTAFISCAL
GROUP  BY DSTAGORIGFILI, IDSTATUS
ORDER  BY QT DESC
FETCH FIRST 25 ROWS ONLY;

-- 4.4 As notas do print (fornecedor ARAU AGRO HORTIFRUTI, CNPJ 49.292.686/0001-20)
SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFILIAL,
       G.DSTAGORIGFILI, G.IDSTATUS, G.CDTIPOOPER,
       G.VRNOTAFISC, TO_CHAR(G.DTEMISSAO,'DD/MM/YYYY') AS EMISSAO,
       SUBSTR(G.DSNATOPER,1,40) AS NATUREZA
FROM   TEKNISA.INTG_NOTAFISCAL G
WHERE  REGEXP_REPLACE(G.CDFORNECED,'[^0-9]','') = '49292686000120'
ORDER  BY G.DTEMISSAO DESC
FETCH FIRST 25 ROWS ONLY;


-- =====================================================================
--  BLOCO 5 - O CFOP E A UNIDADE, DE DENTRO DO XML
--  (o CFOP quase nao existe em coluna; o XML cru tem)
-- =====================================================================

-- 5.1 O CFOP esta preenchido na NOTAFISCAL?
--     (eu vi: so 2483 de 322001 notas de 2025 - menos de 1%)
SELECT COUNT(*) AS TOTAL, COUNT(CDCFOPINFUSU) AS TEM_CFOP
FROM   TEKNISA.NOTAFISCAL
WHERE  DTEMISSAO >= DATE '2025-01-01';

-- 5.2 Onde o XML fica guardado, de verdade.
--     ATENCAO: sao DUAS colunas e o conteudo esta na BLOB, nao na CLOB.
--     Medido: 232.026 linhas -> 231.585 com BLOB e apenas 570 com CLOB.
--     Quem ler DSARQXMLNFE_CLOB acha quase nada.
SELECT COUNT(*) AS LINHAS,
       SUM(CASE WHEN DSARQXMLNFE_CLOB IS NOT NULL
                 AND DBMS_LOB.GETLENGTH(DSARQXMLNFE_CLOB) > 100 THEN 1 ELSE 0 END) AS COM_CLOB,
       SUM(CASE WHEN DSARQXMLNFE IS NOT NULL
                 AND DBMS_LOB.GETLENGTH(DSARQXMLNFE) > 100 THEN 1 ELSE 0 END)      AS COM_BLOB
FROM   TEKNISA.ARQXMLNFE;

-- 5.3 >>> O CRUZAMENTO QUE VALIDA AS REGRAS DO GESTOR <<<
--     Le o CFOP e a natureza de DENTRO do XML (coluna BLOB) e cruza com o
--     tipo de operacao da nota. E a prova direta das regras de CFOP.
--     Comeca com 200 notas; aumente o FETCH se rodar rapido.
SELECT CFOP_XML, NATUREZA_XML, CDTIPOOPER, COUNT(*) AS QT
FROM (
  SELECT REGEXP_SUBSTR(
           UTL_RAW.CAST_TO_VARCHAR2(
             DBMS_LOB.SUBSTR(X.DSARQXMLNFE, 4000,
               GREATEST(1, DBMS_LOB.INSTR(X.DSARQXMLNFE, UTL_RAW.CAST_TO_RAW('<CFOP>'))))),
           '<CFOP>([0-9]+)</CFOP>', 1, 1, NULL, 1)                    AS CFOP_XML,
         SUBSTR(REGEXP_SUBSTR(
           UTL_RAW.CAST_TO_VARCHAR2(DBMS_LOB.SUBSTR(X.DSARQXMLNFE, 4000, 1)),
           '<natOp>([^<]*)</natOp>', 1, 1, NULL, 1), 1, 40)           AS NATUREZA_XML,
         N.CDTIPOOPER
  FROM   TEKNISA.ARQXMLNFE X
  JOIN   TEKNISA.NOTAFISCAL N
         ON N.CDFILIAL = X.CDFILIAL AND N.NRLANCTONF = X.NRLANCTONF
  WHERE  X.DSARQXMLNFE IS NOT NULL
    AND  N.CDTIPOOPER IS NOT NULL
  FETCH FIRST 200 ROWS ONLY)
GROUP BY CFOP_XML, NATUREZA_XML, CDTIPOOPER
ORDER BY QT DESC;

-- 5.4 A UNIDADE COMERCIAL (uCom) que o fornecedor usa no XML.
--     E o dado que permite trocar o palpite de texto do matcher por uma
--     decisao estrutural: uCom do XML  x  SGUNIDADE do produto interno.
--     Se as unidades baterem -> vincula no produto daquela unidade.
SELECT UCOM_XML, COUNT(*) AS QT
FROM (
  SELECT REGEXP_SUBSTR(
           UTL_RAW.CAST_TO_VARCHAR2(
             DBMS_LOB.SUBSTR(X.DSARQXMLNFE, 4000,
               GREATEST(1, DBMS_LOB.INSTR(X.DSARQXMLNFE, UTL_RAW.CAST_TO_RAW('<uCom>'))))),
           '<uCom>([^<]*)</uCom>', 1, 1, NULL, 1) AS UCOM_XML
  FROM   TEKNISA.ARQXMLNFE X
  WHERE  X.DSARQXMLNFE IS NOT NULL
  FETCH FIRST 200 ROWS ONLY)
WHERE  UCOM_XML IS NOT NULL
GROUP  BY UCOM_XML
ORDER  BY QT DESC;

-- 5.5 O par que interessa de verdade: unidade do XML x unidade do produto
--     vinculado. Mostra se o De/Para respeita a unidade de entrega.
--     (rode depois do 5.4, se ele funcionar)
SELECT P.SGUNIDADE                                   AS UNIDADE_INTERNA,
       CASE WHEN SUBSTR(P.CDPRODUTO,-2)='00' THEN 'ESTOQUE' ELSE 'COMPRA' END AS DESTINO,
       ROUND(AVG(P.VRFATOCONV),2)                    AS FATOR_MEDIO,
       COUNT(*)                                      AS QT
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.IDATIVO='S'
GROUP  BY P.SGUNIDADE, CASE WHEN SUBSTR(P.CDPRODUTO,-2)='00' THEN 'ESTOQUE' ELSE 'COMPRA' END
ORDER  BY QT DESC
FETCH FIRST 25 ROWS ONLY;


-- =====================================================================
--  BLOCO 6 - PARAMETROS QUE MUDAM O JOGO
--  Nao sei o nome das tabelas de parametro (nao tenho acesso).
--  6.1 as descobre; depois me mande o resultado que eu monto o SELECT certo.
-- =====================================================================

-- 6.1 Descobrir as tabelas de parametro de documentos fiscais / compras
SELECT OWNER, TABLE_NAME
FROM   ALL_TABLES
WHERE  OWNER='TEKNISA'
  AND (TABLE_NAME LIKE '%PARAM%' OR TABLE_NAME LIKE '%PARM%'
    OR TABLE_NAME LIKE '%TIPOOPER%' OR TABLE_NAME LIKE '%CFOP%'
    OR TABLE_NAME LIKE '%INTGCFOP%' OR TABLE_NAME LIKE '%GRUPOFISC%')
ORDER  BY TABLE_NAME;

-- 6.2 Perguntas que precisam ser respondidas nas TELAS (nao achei no banco):
--     a) "Utiliza Integracao CFOP XML x Tipo de Operacao" esta LIGADO?
--        (Parametros de Doc. Fiscais de Entrada > Gerais > Fechamento de NF)
--     b) A tela "Integracao CFOP XML x Tipo de Operacao" esta POPULADA?
--        Se sim, preciso do conteudo dela - e a fonte de verdade do Teknisa.
--     c) "Bloqueio de lancamento de produtos de estoque em compras" esta ligado?
--        >>> RISCO ALTO: se estiver, todo vinculo que aponta para produto
--        >>> final '00' para de funcionar. Seriam ~2/3 dos nossos De/Para.
--     d) "Utiliza Relacionamento Produto x Produto (XML) por Unidade" esta ligado?
--        Se sim, o De/Para e por filial e a tabela certa pode ser PRODFORNXMLFIL.


-- =====================================================================
--  BLOCO 7 - GRANTS que faltam (rodar como DBA, se aprovado)
--  Sao apenas SELECT. Nenhum da escrita.
-- =====================================================================
-- GRANT SELECT ON TEKNISA.TIPOOPERAC        TO AIHUB;  -- catalogo dos tipos
-- GRANT SELECT ON TEKNISA.INTG_ITEMPRODUTO  TO AIHUB;  -- itens do XML (CFOP por item)

-- Conferir o que o AIHUB enxerga hoje:
SELECT TABLE_NAME, LISTAGG(PRIVILEGE,',') WITHIN GROUP (ORDER BY PRIVILEGE) AS PRIVS
FROM   ALL_TAB_PRIVS
WHERE  GRANTEE='AIHUB' AND TABLE_SCHEMA='TEKNISA'
GROUP  BY TABLE_NAME
ORDER  BY TABLE_NAME;
