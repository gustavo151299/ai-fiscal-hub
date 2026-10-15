-- ===========================================================================
--  REGRAS ENSINADAS PELA PESSOA - o aprendizado do robo
--
--  POR QUE ESTA TABELA EXISTE
--  --------------------------
--  Ate aqui a pessoa podia corrigir o RESULTADO (dizer qual e o produto certo,
--  e o De/Para virava permanente), mas nao podia corrigir o RACIOCINIO. Quando
--  o robo lia mal a descricao do fornecedor, ele continuava lendo mal na
--  proxima nota, e a pessoa refazia o mesmo vinculo item a item.
--
--  Agora ela ensina a LEITURA, e o ensino fica aqui.
--
--  TRES TIPOS, e so tres, de proposito
--  -----------------------------------
--    SINONIMO  "quando aparecer PCT, leia PACOTE"
--              Vira substituicao no texto ANTES da comparacao. E como a lista
--              _ABREV do codigo ja funciona (BOV=BOVINO, QJO=QUEIJO), so que
--              editavel e por fornecedor.
--
--    NEGATIVA  "para este fornecedor, nunca ligue este item a este produto"
--              Tira o produto da lista de candidatos. Resolve o caso em que o
--              robo insiste num erro que o texto sozinho nao desfaz.
--
--    LIMIAR    "para este fornecedor, exija mais (ou menos) para gravar sozinho"
--              O piso padrao e 0,92. Afrouxar ja foi medido e REPROVADO tres
--              vezes no projeto, entao aqui ele so vale depois do teste
--              historico dizer que nao quebra nada.
--
--  REGRA LIVRE EM TEXTO nao vira um quarto tipo: ela e INTERPRETADA e
--  transformada em um dos tres acima, e a pessoa confirma o que o sistema
--  entendeu antes de valer. Texto que nao vira regra testavel nao entra.
--
--  NADA VALE SEM TESTE
--  -------------------
--  Toda regra nasce RASCUNHO. Antes de virar ATIVA ela e rodada contra os
--  vinculos que JA existem e estao certos (85 mil em producao), e o sistema
--  responde quantos acertos ela ganha e quantos ela quebra. Regra que quebra
--  vinculo certo nao e adotada. E o mesmo metodo que o projeto usa desde o
--  inicio: medir antes de mudar (achado 41).
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
  cria('CREATE TABLE AIHUB.AIHUB_REGRA (
          ID            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          TIPO          VARCHAR2(12)  NOT NULL,
          ESCOPO        VARCHAR2(12)  NOT NULL,
          CDFORNECED    VARCHAR2(21),
          TERMO         VARCHAR2(60),
          SIGNIFICA     VARCHAR2(60),
          CDPRODUTOXML  VARCHAR2(80),
          CDPRODUTO     VARCHAR2(10),
          VALOR         NUMBER(5,4),
          TEXTO_PEDIDO  VARCHAR2(1000),
          INTERPRETACAO VARCHAR2(1000),
          STATUS        VARCHAR2(12)  DEFAULT ''RASCUNHO'' NOT NULL,
          TESTADOS      NUMBER,
          GANHOU        NUMBER,
          QUEBROU       NUMBER,
          DT_TESTE      DATE,
          DT_INCLUSAO   DATE DEFAULT SYSDATE NOT NULL,
          CD_OPERADOR   VARCHAR2(30),
          OBS           VARCHAR2(400)
        )');

  -- ESCOPO FORNECEDOR EXIGE FORNECEDOR. Sem isto uma regra "do fornecedor"
  -- sem fornecedor viraria regra global sem ninguem perceber.
  cria('ALTER TABLE AIHUB.AIHUB_REGRA ADD CONSTRAINT CK_REGRA_ESCOPO
        CHECK ( (ESCOPO = ''GLOBAL''     AND CDFORNECED IS NULL)
             OR (ESCOPO = ''FORNECEDOR'' AND CDFORNECED IS NOT NULL) )');

  cria('ALTER TABLE AIHUB.AIHUB_REGRA ADD CONSTRAINT CK_REGRA_TIPO
        CHECK (TIPO IN (''SINONIMO'',''NEGATIVA'',''LIMIAR''))');

  cria('ALTER TABLE AIHUB.AIHUB_REGRA ADD CONSTRAINT CK_REGRA_STATUS
        CHECK (STATUS IN (''RASCUNHO'',''ATIVA'',''REPROVADA'',''DESATIVADA''))');

  -- CADA TIPO PRECISA DOS SEUS CAMPOS. Uma regra pela metade nao pode virar
  -- ATIVA e falhar em silencio na hora de aplicar.
  cria('ALTER TABLE AIHUB.AIHUB_REGRA ADD CONSTRAINT CK_REGRA_CAMPOS
        CHECK ( (TIPO = ''SINONIMO'' AND TERMO IS NOT NULL
                                     AND SIGNIFICA IS NOT NULL)
             OR (TIPO = ''NEGATIVA'' AND CDPRODUTOXML IS NOT NULL
                                     AND CDPRODUTO IS NOT NULL)
             OR (TIPO = ''LIMIAR''   AND VALOR IS NOT NULL) )');

  -- a leitura mais frequente: as regras ATIVAS de um fornecedor (mais as
  -- globais). Roda a cada item de nota, entao precisa de indice.
  cria('CREATE INDEX AIHUB.IX_REGRA_ATIVA
        ON AIHUB.AIHUB_REGRA (STATUS, ESCOPO, CDFORNECED)');

  -- NAO REPETIR A MESMA REGRA. Duas linhas iguais ativas fariam a mesma
  -- substituicao duas vezes e confundiriam o placar do teste historico.
  cria('CREATE UNIQUE INDEX AIHUB.IX_REGRA_UNICA ON AIHUB.AIHUB_REGRA (
          CASE WHEN STATUS = ''ATIVA''
               THEN TIPO || ''|'' || NVL(CDFORNECED,''*'') || ''|'' ||
                    NVL(UPPER(TERMO),''-'') || ''|'' ||
                    NVL(CDPRODUTOXML,''-'') || ''|'' || NVL(CDPRODUTO,''-'')
          END)');
END;
/

-- ---------------------------------------------------------------------------
-- CONFERENCIA
-- ---------------------------------------------------------------------------
-- SELECT TIPO, ESCOPO, STATUS, COUNT(*) FROM AIHUB.AIHUB_REGRA
--  GROUP BY TIPO, ESCOPO, STATUS ORDER BY 1,2,3;

-- ---------------------------------------------------------------------------
-- PARA DESFAZER (nao apaga vinculo nenhum: regra e so leitura de texto)
-- ---------------------------------------------------------------------------
-- DROP TABLE AIHUB.AIHUB_REGRA PURGE;
