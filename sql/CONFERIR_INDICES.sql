-- =====================================================================
--  CONFERIR INDICES  -  somente leitura, roda a qualquer momento
--
--  Diz se os 6 indices que o painel precisa JA existem. Enquanto faltarem,
--  as telas de Notas (fila, inicio, simulacao) ficam LENTAS - varrem tabelas
--  de milhoes de linhas a cada consulta (medido: 22s a 5+ minutos).
--
--  Se algum aparecer como "FALTA", rode sql\CRIAR_INDICES.sql como DBA.
-- =====================================================================
SELECT esperado.NOME_INDICE,
       esperado.TABELA,
       CASE WHEN i.INDEX_NAME IS NULL THEN '*** FALTA - painel lento ***'
            ELSE 'ok, criado' END AS SITUACAO
FROM (
        SELECT 'IX_NF_STATUS_TIPO'  NOME_INDICE, 'NOTAFISCAL'          TABELA FROM DUAL
  UNION ALL SELECT 'IX_NF_ACESSONFE',       'NOTAFISCAL'          FROM DUAL
  UNION ALL SELECT 'IX_INTGNF_FIL_LANC',    'INTG_NOTAFISCAL'     FROM DUAL
  UNION ALL SELECT 'IX_INTGINC_ACESSO',     'INTG_INCONSISTENCIA' FROM DUAL
  UNION ALL SELECT 'IX_HISTXML_ACESSO',     'HISTXMLNFE'          FROM DUAL
  UNION ALL SELECT 'IX_PFXML_FORN',         'PRODFORNXML'         FROM DUAL
     ) esperado
LEFT JOIN ALL_INDEXES i
       ON i.INDEX_NAME = esperado.NOME_INDICE
      AND i.TABLE_OWNER = 'TEKNISA'
ORDER BY esperado.NOME_INDICE;
