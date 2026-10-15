-- =====================================================================
-- AI FISCAL HUB - Consultas de investigacao do De/Para (somente leitura)
-- Objetivo: entender de onde vem cada dado, achar mapeamentos errados e
-- ambiguidades ANTES de o sistema rodar, para nao descartar o que estava
-- certo nem repetir o que estava errado. Nada aqui altera o banco.
-- =====================================================================


-- ---------------------------------------------------------------------
-- BLOCO A - Origem do De/Para (quem cadastrou e quando)
-- ---------------------------------------------------------------------

-- A1. Tamanho e origem geral
SELECT COUNT(*) AS TOTAL,
       SUM(CASE WHEN IDATIVO='S' THEN 1 ELSE 0 END) AS ATIVOS,
       COUNT(DISTINCT CDFORNECED) AS FORNECEDORES
FROM   TEKNISA.PRODFORNXML;

-- A2. Quem e quando cadastrou (concentracao por operador/mes)
SELECT CDOPERINCLUSAO,
       TO_CHAR(DTINCLUSAO,'YYYY-MM') AS MES,
       COUNT(*) AS QT
FROM   TEKNISA.PRODFORNXML
GROUP  BY CDOPERINCLUSAO, TO_CHAR(DTINCLUSAO,'YYYY-MM')
ORDER  BY QT DESC
FETCH  FIRST 30 ROWS ONLY;


-- ---------------------------------------------------------------------
-- BLOCO B - Achar lixo e ambiguidades de forma sistematica
-- ---------------------------------------------------------------------

-- B1. Mapeamentos provavelmente ERRADOS (sem quase nada em comum)
SELECT D.CDFORNECED, D.NMPRODUTOXML, P.NMPRODUTO,
       UTL_MATCH.JARO_WINKLER_SIMILARITY(UPPER(D.NMPRODUTOXML),UPPER(P.NMPRODUTO)) AS SCORE,
       D.CDOPERINCLUSAO, TO_CHAR(D.DTINCLUSAO,'DD/MM/YYYY') AS DT
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.IDATIVO='S' AND D.NMPRODUTOXML IS NOT NULL
  AND  UTL_MATCH.JARO_WINKLER_SIMILARITY(UPPER(D.NMPRODUTOXML),UPPER(P.NMPRODUTO)) < 40
ORDER  BY SCORE;

-- B2. Conflitos (mesmo item do fornecedor -> produtos internos diferentes)
SELECT CDFORNECED, CDPRODUTOXML, COUNT(DISTINCT CDPRODUTO) AS DESTINOS
FROM   TEKNISA.PRODFORNXML
WHERE  IDATIVO='S'
GROUP  BY CDFORNECED, CDPRODUTOXML
HAVING COUNT(DISTINCT CDPRODUTO) > 1;


-- ---------------------------------------------------------------------
-- BLOCO C - Confirmar que o robo le/grava na tabela certa
-- ---------------------------------------------------------------------

-- C1. Global x por filial (qual o ERP usa de verdade)
SELECT (SELECT COUNT(*) FROM TEKNISA.PRODFORNXML    WHERE IDATIVO='S') AS GLOBAL,
       (SELECT COUNT(*) FROM TEKNISA.PRODFORNXMLFIL WHERE IDATIVO='S') AS POR_FILIAL
FROM DUAL;


-- ---------------------------------------------------------------------
-- BLOCO D - Exportar a base inteira para testar o matcher em escala
-- (rode e exporte o resultado como CSV: pares_depara.csv)
-- ---------------------------------------------------------------------

-- D1. Todos os pares ativos (descricao do fornecedor x produto interno)
SELECT D.CDFORNECED, D.CDPRODUTOXML, D.NMPRODUTOXML,
       D.CDPRODUTO, P.NMPRODUTO
FROM   TEKNISA.PRODFORNXML D
JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = D.CDPRODUTO
WHERE  D.IDATIVO='S' AND D.NMPRODUTOXML IS NOT NULL;
