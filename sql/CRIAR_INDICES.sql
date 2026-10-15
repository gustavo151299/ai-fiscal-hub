-- =====================================================================
--  INDICES DE APOIO  -  rodar como DBA (dono do schema TEKNISA)
--
--  LEVANTADO CONTRA HOMOLOGACAO EM 06/08/2026, com plano de execucao real.
--  Nao e estimativa: cada numero abaixo foi cronometrado no banco.
--
--  SEM 'ONLINE'. O banco e Oracle 19c STANDARD EDITION 2, e ali a criacao
--  ONLINE nao existe: devolve ORA-00439 "feature not enabled". Consequencia
--  pratica: a criacao TRAVA a tabela para escrita enquanto roda. Fazer em
--  janela de baixo movimento. E o motivo de este arquivo nao trazer ONLINE.
--
--  JA EXISTEM em homologacao (nao recriar, o CREATE falharia):
--     IX_NF_STATUS_TIPO      NOTAFISCAL (CDTIPOOPER, IDSTATUS, DTEMISSAO)
--     IX_INTGNF_FIL_LANC     INTG_NOTAFISCAL (CDFILIAL, NRLANCTONF)
--
--  DESCARTADOS depois de conferir o que ja havia:
--     HISTXMLNFE  -> ja tem HISTXMLNFE_02 (NRACESSONFE, NRSEQHISTOR, DTHISTORICO),
--                    que cobre a mesma busca. Criar outro so gastaria espaco.
--     AUTOFORNE   -> para a BUSCA DE AF do motor, que filtra tambem por
--                    CDFILDEST, nao falta: resolve em 637 ms com CMP00068_FK e
--                    AUTOFORNE_11.
--                    CUIDADO: isso NAO vale para a lista de CANDIDATOS DE
--                    PRODUTO, que filtra SO por CDFORNECED e nao tem indice
--                    que sirva. Medido em 20/08/2026: 137 SEGUNDOS, com
--                    TABLE ACCESS FULL sobre 818 mil linhas. Ver o item 7.
-- =====================================================================

-- ======================================================================
--  JA RODOU EM HOMOLOGACAO (06/08/2026) E O RESULTADO FOI MEDIDO:
--     consulta por chave da NF-e ... 31.000 ms  ->  153 ms
--     plano ........................ TABLE ACCESS FULL (custo 93.646)
--                                    -> INDEX RANGE SCAN (custo 3)
--     tela de indicadores .......... estourava o tempo -> 2,3 s
--
--  SE VOCE RODAR DE NOVO e vier ORA-00955 "name is already used by an
--  existing object", esta TUDO CERTO: quer dizer que o indice ja existe.
--  Nao e falha. Este arquivo e CREATE puro, entao ele nao engole esse erro
--  (o INSTALAR_PRODUCAO_COMPLETO.sql engole, por isso pode repetir a vontade).
--
--  FALTA RODAR EM PRODUCAO.
-- ======================================================================

-- ---------------------------------------------------------------------
-- 1) NOTAFISCAL por CHAVE DA NF-E   <<< E ESTE QUE IMPORTA >>>
--
--  Medido: "SELECT COUNT(*) FROM NOTAFISCAL WHERE NRACESSONFE = :c"
--     hoje ......... 31 SEGUNDOS, TABLE ACCESS FULL, custo 93.646
--     sao 1.972.160 linhas varridas para achar uma.
--
--  E a pergunta mais repetida do sistema ("esta nota ja entrou?"), e ela
--  aparece dentro de um NOT EXISTS CORRELACIONADO em _sql_ainda_parada():
--  roda uma vez POR LINHA da INTG_INCONSISTENCIA. E por isso que a tela de
--  indicadores, a de notas paradas e a de produto divergente demoram minutos
--  ou estouram o tempo.
--
--  Depois deste indice a mesma consulta vira acesso por indice unico.
--  Espaco estimado: ~110 MB (1,97 mi de linhas x chave de 44 caracteres).
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_NF_ACESSONFE
    ON TEKNISA.NOTAFISCAL (NRACESSONFE);

-- ---------------------------------------------------------------------
-- 2) PRODFORNXML por FORNECEDOR + ITEM DO XML
--
--  O De/Para: o robo faz uma consulta destas por ITEM de cada nota, e o
--  painel refaz a mesma a cada gravacao de vinculo.
--  Medido: 153 ms hoje. Nao e um desastre como o de cima, mas a tabela tem
--  so 82 mil linhas e o indice e barato (~4 MB).
--  Por que os indices atuais nao servem: a PK comeca por CDPRODUTO (que a
--  consulta nao informa) e EST00567_FK tem so CDFORNECED, entao o banco le
--  todas as linhas daquele fornecedor para filtrar o item.
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_PFXML_FORN
    ON TEKNISA.PRODFORNXML (CDFORNECED, CDPRODUTOXML, IDATIVO);

-- ---------------------------------------------------------------------
-- 3) INTG_INCONSISTENCIA por CHAVE + ATIVO   (OPCIONAL, ganho pequeno)
--
--  Ja existe FIS00492_FK (NRACESSONFE). Este so acrescenta o IDATIVO, para
--  o banco nao precisar ler a linha. Com 12 mil linhas a diferenca e
--  pequena; incluido por completude. Se o DBA preferir, pode pular.
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_INTGINC_ACESSO
    ON TEKNISA.INTG_INCONSISTENCIA (NRACESSONFE, IDATIVO);

-- ---------------------------------------------------------------------
-- 4) INTG_NOTAFISCAL por DATA DE EMISSAO   (a fila do robo)
--
--  A fila de importacao (buscar_para_importar) precisa da nota MAIS NOVA
--  primeiro: sem ordenar, o Oracle devolvia quaisquer linhas, e na pratica as
--  mais antigas - medido em 11/08/2026, as 25 notas da rodada eram de 2018 e
--  2019, e acima de 30 dias 100% das AFs ja foram consumidas. A rodada inteira
--  parava em AF_VENCIDA (achado 88).
--
--  Ordenar sem indice custa caro. Medido em homologacao, pedindo 10 notas:
--     sem janela de data ....... 36.543 ms
--     janela de 365 dias ....... 32.016 ms
--     janela de 180 dias ....... 855 a 2.045 ms   <- o que esta no codigo hoje
--  As tres devolvem AS MESMAS notas. A diferenca e so o plano: com mais linhas
--  estimadas o Oracle troca o nested loop pelo HASH JOIN ANTI.
--
--  Depender dessa virada e fragil. Com este indice a ordenacao sai do proprio
--  indice, o banco para nas primeiras linhas e o tempo deixa de depender do
--  tamanho da janela. Tabela de ~520 mil linhas; indice pequeno (~12 MB).
--  Hoje o plano usa INDEX SKIP SCAN em INTG_NOTAFISCAL_01, porque ali a
--  DTEMISSAO e a TERCEIRA coluna (CDFILIAL, CDFORNECED, DTEMISSAO, ...).
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_INTGNF_EMISSAO
    ON TEKNISA.INTG_NOTAFISCAL (DTEMISSAO, CDTIPOOPER);

-- ---------------------------------------------------------------------
-- 5) NOTAFISCAL por FORNECEDOR   (a busca por AF)
--
--  Medido em 11/08/2026, buscar as 40 notas mais recentes de UM fornecedor:
--     com o ORDER BY .......... 268.765 ms   (4 minutos e meio)
--     sem o ORDER BY ...........  61.268 ms
--  O fornecedor do teste tem 175 notas. Sao 4 minutos para achar 175 linhas
--  em 1,97 milhao, porque NAO EXISTE indice por CDFORNECED nesta tabela: os
--  unicos sao a PK, IX_NF_ACESSONFE (NRACESSONFE) e IX_NF_STATUS_TIPO
--  (CDTIPOOPER, IDSTATUS, DTEMISSAO), e nenhum comeca por fornecedor.
--
--  E a tela "Buscar por numero" quando se procura uma AF: ela lista as notas
--  daquele fornecedor. Sem o indice, a tela e inutilizavel.
--
--  As tres colunas na ordem do filtro; NRLANCTONF entra no fim para o ORDER BY
--  tambem sair do indice.
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_NF_FORNECED
    ON TEKNISA.NOTAFISCAL (CDFORNECED, IDENTRSAIDOP, CDTIPOOPER, NRLANCTONF);

-- ---------------------------------------------------------------------
-- 6) NOTAFISCAL por DATA DE APROVACAO   (a tela "Notas aprovadas")
--
--  Medido em 11/08/2026, a consulta exata da tela:
--     29.281 ms, TABLE ACCESS FULL NOTAFISCAL, custo 93.478
--     ... para devolver ZERO notas.
--
--  Por que o IX_NF_STATUS_TIPO nao resolve: ele comeca por CDTIPOOPER e
--  IDSTATUS, e IDSTATUS='A' casa com 1.180.745 das 1,97 milhao de linhas
--  (60% da tabela). Com essa seletividade o Oracle prefere varrer, e ele esta
--  certo. Quem discrimina aqui e a DATA DE APROVACAO, que nao esta indexada
--  em lugar nenhum (o terceiro campo daquele indice e DTEMISSAO, outra coluna).
--
--  Com a data na frente, o filtro de periodo vira range scan e a tela responde
--  na hora.
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_NF_APROVACAO
    ON TEKNISA.NOTAFISCAL (DTAPROVACAO, IDSTATUS);

-- ---------------------------------------------------------------------
-- 7) AUTOFORNE por FORNECEDOR   (a lista de candidatos do matcher)
--
--  ESTE E O MAIS IMPORTANTE PARA O ROBO, e passou despercebido antes.
--
--  A consulta que monta a lista de produtos candidatos de um fornecedor
--  (core/ai_matcher.SQL_CANDIDATOS) filtra AUTOFORNE por CDFORNECED. Nao ha
--  indice por essa coluna: os unicos sao AUTOFORNE_11 (CDFILDEST, CDFILLANC,
--  NRAUTOFORN, ...) e as chaves estrangeiras, e nenhum comeca por fornecedor.
--
--  Medido em homologacao (20/08/2026), plano real:
--     TABLE ACCESS FULL AUTOFORNE, custo 19.867, DUAS VEZES (um por lado do
--     UNION), sobre 818.136 linhas, para achar as 103 AFs do fornecedor.
--     Tempo: 137 SEGUNDOS para devolver 56 candidatos.
--
--  A consulta ja foi reescrita para varrer uma vez so (WITH ... MATERIALIZE),
--  o que ajudou bastante (um fornecedor caiu de 53 s para 6 s), mas fornecedor
--  grande continua em ~105 s. Sem o indice nao ha como evitar a varredura.
--
--  QUEM SOFRE HOJE: o auto-vinculo do robo, as sugestoes de produto do painel,
--  o teste de regra contra o historico e a tela "Como ele pensa".
--
--  CUIDADO AO LER O ACHADO 78: la esta escrito que NAO falta indice por
--  CDFORNECED em AUTOFORNE. Aquilo valia para OUTRA consulta, a de escolha de
--  AF, que tambem filtra por CDFILDEST e por isso usa AUTOFORNE_11. Esta aqui
--  filtra SO por fornecedor, e nao tem indice que sirva.
-- ---------------------------------------------------------------------
CREATE INDEX TEKNISA.IX_AUTOFORNE_FORN
    ON TEKNISA.AUTOFORNE (CDFORNECED);

-- ---------------------------------------------------------------------
-- Estatisticas: sem isto o Oracle pode continuar preferindo o plano velho.
-- ---------------------------------------------------------------------
BEGIN
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','NOTAFISCAL',
      cascade=>TRUE, degree=>4, estimate_percent=>10);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','PRODFORNXML', cascade=>TRUE, degree=>4);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','INTG_INCONSISTENCIA',
      cascade=>TRUE, degree=>4);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','INTG_NOTAFISCAL',
      cascade=>TRUE, degree=>4, estimate_percent=>10);
  DBMS_STATS.GATHER_TABLE_STATS('TEKNISA','AUTOFORNE',
      cascade=>TRUE, degree=>4, estimate_percent=>10);
END;
/

-- ---------------------------------------------------------------------
-- CONFERENCIA: rode depois e compare. Antes eram ~31000 ms.
-- ---------------------------------------------------------------------
-- SET TIMING ON
-- SELECT COUNT(*) FROM TEKNISA.NOTAFISCAL
--  WHERE NRACESSONFE = (SELECT MIN(NRACESSONFE) FROM TEKNISA.NOTAFISCAL);

-- =====================================================================
--  PARA DESFAZER (indice e reversivel, nao altera dado nenhum)
-- =====================================================================
-- DROP INDEX TEKNISA.IX_NF_ACESSONFE;
-- DROP INDEX TEKNISA.IX_PFXML_FORN;
-- DROP INDEX TEKNISA.IX_INTGINC_ACESSO;
-- DROP INDEX TEKNISA.IX_INTGNF_EMISSAO;
-- DROP INDEX TEKNISA.IX_NF_FORNECED;
-- DROP INDEX TEKNISA.IX_NF_APROVACAO;
-- DROP INDEX TEKNISA.IX_AUTOFORNE_FORN;

-- =====================================================================
--  PERMISSAO que o painel precisa (se ainda nao foi dada)
-- =====================================================================
-- GRANT SELECT ON TEKNISA.HISTXMLNFE TO AIHUB;
