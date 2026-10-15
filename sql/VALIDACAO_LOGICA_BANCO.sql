-- =====================================================================
-- AI FISCAL HUB - VALIDACAO DA LOGICA NO BANCO (somente leitura)
-- Rode no SQL Developer. NAO escreve nada, NAO chama procedure que altera.
-- Valida: (1) leitura, (2) cobertura do De/Para, (3) o "cerebro" de
-- matching, usando a funcao de similaridade nativa do Oracle (UTL_MATCH).
-- Para testar outro fornecedor, troque o codigo nas queries.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1) LEITURA: o robo consegue enxergar as AFs do fornecedor, os itens e
--    saber quais ja tem De/Para (TEM) e quais nao (SEM)?
-- ---------------------------------------------------------------------
SELECT A.CDFILLANC AS FILIAL,
       A.NRAUTOFORN AS AF,
       I.CDPRODUTO,
       P.NMPRODUTO,
       CASE WHEN D.CDPRODUTO IS NULL THEN 'SEM' ELSE 'TEM' END AS DEPARA
FROM   TEKNISA.AUTOFORNE A
JOIN   TEKNISA.ITEMAUTF  I
       ON I.CDFILDEST = A.CDFILDEST
      AND I.CDFILLANC = A.CDFILLANC
      AND I.NRAUTOFORN = A.NRAUTOFORN
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = I.CDPRODUTO
LEFT   JOIN TEKNISA.PRODFORNXML D
       ON D.CDPRODUTO = I.CDPRODUTO
      AND D.CDFORNECED = A.CDFORNECED
      AND D.IDATIVO = 'S'
WHERE  A.CDFORNECED = 'J00000000000000'
FETCH  FIRST 50 ROWS ONLY;


-- ---------------------------------------------------------------------
-- 2) COBERTURA: no total de ligacoes De/Para cadastradas para este
--    fornecedor, quantas existem? (mede o tamanho da "memoria" do robo)
-- ---------------------------------------------------------------------
SELECT COUNT(*) AS TOTAL_DEPARA_FORNECEDOR
FROM   TEKNISA.PRODFORNXML
WHERE  CDFORNECED = 'J00000000000000'
   AND IDATIVO = 'S';


-- ---------------------------------------------------------------------
-- 3) BACK-TEST DO CEREBRO DE MATCHING (a validacao mais importante):
--    Nas ligacoes De/Para JA cadastradas (que estao corretas), a
--    similaridade entre a descricao que vem no XML do fornecedor
--    (NMPRODUTOXML) e o nome do produto interno (NMPRODUTO) bateria o
--    limiar de decisao? Usa a funcao nativa do Oracle.
--    Se a maioria cair em ">=95", o robo acertaria sozinho a maioria.
-- ---------------------------------------------------------------------
SELECT FAIXA_SIMILARIDADE, COUNT(*) AS QT_LIGACOES
FROM (
  SELECT CASE
           WHEN SC >= 95 THEN '1) >=95  (robo aprova sozinho)'
           WHEN SC >= 85 THEN '2) 85-94 (quase - ajuste de limiar)'
           WHEN SC >= 70 THEN '3) 70-84 (vai para revisao humana)'
           ELSE               '4) <70   (humano decide)'
         END AS FAIXA_SIMILARIDADE
  FROM (
    SELECT UTL_MATCH.JARO_WINKLER_SIMILARITY(
             UPPER(D.NMPRODUTOXML), UPPER(P.NMPRODUTO)) AS SC
    FROM   TEKNISA.PRODFORNXML D
    JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
    WHERE  D.IDATIVO = 'S'
      AND  D.NMPRODUTOXML IS NOT NULL
  )
)
GROUP BY FAIXA_SIMILARIDADE
ORDER BY FAIXA_SIMILARIDADE;


-- ---------------------------------------------------------------------
-- 4) AMOSTRA do back-test: ver, lado a lado, a descricao do fornecedor,
--    o produto interno e a nota de similaridade. Da para "ver com os
--    olhos" se o casamento faz sentido.
-- ---------------------------------------------------------------------
SELECT D.NMPRODUTOXML AS DESC_DO_FORNECEDOR,
       P.NMPRODUTO    AS PRODUTO_INTERNO,
       UTL_MATCH.JARO_WINKLER_SIMILARITY(
         UPPER(D.NMPRODUTOXML), UPPER(P.NMPRODUTO)) AS SCORE
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.CDFORNECED = 'J00000000000000'
   AND D.IDATIVO = 'S'
   AND D.NMPRODUTOXML IS NOT NULL
ORDER  BY SCORE DESC
FETCH  FIRST 20 ROWS ONLY;


-- ---------------------------------------------------------------------
-- 5) PROCEDURES: confirmar que as 3 rotinas de aprovacao existem e estao
--    validas (a chamada real delas depende de uma nota nova - Opcao A).
-- ---------------------------------------------------------------------
SELECT OBJECT_NAME, STATUS
FROM   ALL_OBJECTS
WHERE  OWNER = 'TEKNISA'
   AND OBJECT_NAME IN ('NF_BAIXA_AF','VERIFICA_INCONS_NF','CONFIRMACAO_AUTOMATICA_NF')
   AND OBJECT_TYPE = 'PROCEDURE';
