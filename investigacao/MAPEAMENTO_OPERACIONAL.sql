-- =====================================================================
-- AI FISCAL HUB - Mapeamento operacional (somente leitura)
-- Mapeia cada PONTO DE DECISAO do robo e valida o risco de a nota ir para
-- o lugar errado, mesmo estando correta. Nada aqui altera o banco.
-- Rode cada bloco e envie o retorno. Quanto mais evidencia, mais seguro.
-- =====================================================================


-- ---------------------------------------------------------------------
-- DECISAO 1 - Resolver o fornecedor (CNPJ do XML -> CDFORNECED)
-- RISCO: um mesmo CNPJ apontar para mais de um fornecedor -> escolhe o errado.
-- ESPERADO: zero linhas (cada CNPJ = um fornecedor).
-- ---------------------------------------------------------------------
SELECT DOC, COUNT(*) AS QT_FORNECEDORES
FROM (
  SELECT CDFORNECED, REGEXP_REPLACE(NRINSJURFORN,'[^0-9]','') AS DOC
  FROM   TEKNISA.FORNECEDOR
  WHERE  IDATIVO='S' AND NRINSJURFORN IS NOT NULL
)
GROUP BY DOC
HAVING COUNT(*) > 1
ORDER BY QT_FORNECEDORES DESC;


-- ---------------------------------------------------------------------
-- DECISAO 2 - Localizar a AF (numero da AF -> AF unica)
-- RISCO: o mesmo numero de AF existir para varios fornecedores/filiais ->
--        vincular na AF errada se so o numero for usado.
-- (Por isso o robo casa por fornecedor + filial; aqui medimos o tamanho do risco.)
-- ---------------------------------------------------------------------
SELECT NRAUTOFORN,
       COUNT(*)                   AS QT_AFS,
       COUNT(DISTINCT CDFORNECED) AS QT_FORNECEDORES,
       COUNT(DISTINCT CDFILLANC)  AS QT_FILIAIS
FROM   TEKNISA.AUTOFORNE
GROUP  BY NRAUTOFORN
HAVING COUNT(*) > 1
ORDER  BY QT_AFS DESC
FETCH  FIRST 40 ROWS ONLY;


-- ---------------------------------------------------------------------
-- DECISAO 3 - De/Para do item (item do XML -> produto interno)
-- RISCO: um mesmo item do fornecedor apontar para produtos internos diferentes.
-- ESPERADO: zero linhas (ja confirmado, aqui reconfirma na base inteira).
-- ---------------------------------------------------------------------
SELECT CDFORNECED, CDPRODUTOXML, COUNT(DISTINCT CDPRODUTO) AS DESTINOS
FROM   TEKNISA.PRODFORNXML
WHERE  IDATIVO='S'
GROUP  BY CDFORNECED, CDPRODUTOXML
HAVING COUNT(DISTINCT CDPRODUTO) > 1;


-- ---------------------------------------------------------------------
-- DECISAO 4 - Formato do vinculo XML x AF (RELACXMLAF)
-- RISCO: o NRAUTOFORN aqui tem 20 digitos (vs 10 na AUTOFORNE). Precisamos
--        entender o formato para nao vincular na AF errada.
-- ---------------------------------------------------------------------
SELECT NRACESSONFE,
       NRAUTOFORN,
       LENGTH(TRIM(NRAUTOFORN)) AS TAM
FROM   TEKNISA.RELACXMLAF
WHERE  IDATIVO='S'
FETCH  FIRST 25 ROWS ONLY;


-- ---------------------------------------------------------------------
-- DECISAO 5 - Tipo de operacao (CDTIPOOPER usado na aprovacao)
-- RISCO: usar o tipo de operacao errado ao vincular/aprovar a NF.
-- Confirma o valor real do "01 - NF Compra Mod 55".
-- ---------------------------------------------------------------------
SELECT * FROM TEKNISA.TIPOOPERAC WHERE ROWNUM <= 50;


-- ---------------------------------------------------------------------
-- DECISAO 6 - Estado da nota (so processar pendente)
-- RISCO: reprocessar nota ja aprovada.
-- Mede quantas estao pendentes x aprovadas (dimensiona a fila do robo).
-- ---------------------------------------------------------------------
SELECT CASE WHEN DTAPROVACAO IS NULL THEN 'PENDENTE' ELSE 'APROVADA' END AS ESTADO,
       COUNT(*) AS QT
FROM   TEKNISA.NOTAFISCAL
WHERE  CDFORNECED IS NOT NULL
GROUP  BY CASE WHEN DTAPROVACAO IS NULL THEN 'PENDENTE' ELSE 'APROVADA' END;


-- ---------------------------------------------------------------------
-- DECISAO 7 - Cobertura do De/Para por fornecedor
-- RISCO: fornecedor com pouco De/Para -> muita intervencao humana.
-- Mostra os fornecedores com mais itens mapeados (a "memoria" concentrada).
-- ---------------------------------------------------------------------
SELECT CDFORNECED, COUNT(*) AS ITENS_MAPEADOS
FROM   TEKNISA.PRODFORNXML
WHERE  IDATIVO='S'
GROUP  BY CDFORNECED
ORDER  BY ITENS_MAPEADOS DESC
FETCH  FIRST 30 ROWS ONLY;
