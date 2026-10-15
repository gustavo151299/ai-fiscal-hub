# AI Fiscal Hub — Dossiê da investigação no banco

Documento para análise técnica. Registra a especificação original, as correções
feitas para o Teknisa, e o histórico completo das consultas ao banco: para cada
uma, o objetivo, o SQL executado, o resultado recebido e a conclusão.

---

## 1. Objetivo do projeto

Automatizar o recebimento de NF de entrada hoje feito manualmente a cada 2 horas:
localizar a AF nos dados adicionais do XML, relacionar, resolver o "produto sem
relacionamento" e liberar a nota. O robô faria isso a cada 5 minutos, usando as
rotinas nativas do Teknisa — sem forçar aprovação.

## 2. Ponto de partida e a primeira correção

A especificação (PRD) recebida foi escrita para um ERP genérico (Totvs/Datasul/
Senior/Linx) e banco PostgreSQL. O ambiente real é Teknisa, Oracle, schema TEKNISA.
Antes de qualquer código, foi preciso aterrar tudo na realidade do banco. As
correções estruturais:

| PRD original | Realidade Teknisa |
|---|---|
| Banco PostgreSQL | Oracle 12c+, schema TEKNISA |
| Criar tabela `product_mappings` do zero | O De/Para já existe: `PRODFORNXML` |
| AF identificada só pelo número | PK de `AUTOFORNE` = filial + filial + número |
| Fornecedor = CNPJ (14) | Teknisa usa `CDFORNECED` (21) |
| "IA aprova a NF" por escrita direta | Aprovação é rotina nativa do produto |

Para confirmar cada um desses pontos e fechar os detalhes de integração, rodamos as
consultas abaixo diretamente no banco.

---

## 3. Histórico das consultas (SQL executado × resultado × conclusão)

### Consulta 1 — Onde fica o CNPJ do fornecedor
**Objetivo:** o XML traz CNPJ (14 dígitos); o Teknisa usa `CDFORNECED`. Precisávamos
saber por qual coluna resolver um no outro.

```sql
SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH
FROM   ALL_TAB_COLUMNS
WHERE  OWNER='TEKNISA' AND TABLE_NAME='FORNECEDOR'
AND    (COLUMN_NAME LIKE '%INSJUR%' OR COLUMN_NAME LIKE '%CNPJ%'
        OR COLUMN_NAME LIKE '%CGC%' OR COLUMN_NAME LIKE '%INSC%');

SELECT CDFORNECED, NMRAZSOCFORN, NRINSJURFORN
FROM   TEKNISA.FORNECEDOR WHERE ROWNUM <= 5;
```

**Resultado recebido:**
```
NRINSJURFORN     VARCHAR2  20
NRINSCSUFRAMAFO  VARCHAR2   9

F00005330254  ALCIANY MARQUES DE VILHENA  <matricula>
F00061326003  LISIANE DE ABREU PEREIRA... <matricula>
F00067221300  GILMA BORGES FEITOSA        <matricula>
F00102728950  DIOBALDO JOSE DUARTE        <matricula>
F00133100030  ELISANDRO MANETTI VAGHETTI  <matricula>
```

**Conclusão:** o documento fica em `NRINSJURFORN`, gravado **limpo** (sem máscara).
Pode ser CNPJ (14) ou CPF (11), conforme pessoa jurídica ou física. O robô normaliza
e casa os dois. **[PLUG-1 resolvido.]**

---

### Consulta 2 — Onde o XML importado fica (a fila de notas)
**Objetivo:** achar a tabela de staging do XML de entrada e onde está o texto de
`<infCpl>` (dados adicionais) que o operador lê para achar a AF.

```sql
SELECT TABLE_NAME FROM ALL_TABLES
WHERE  OWNER='TEKNISA'
AND    (TABLE_NAME LIKE '%XML%' OR TABLE_NAME LIKE '%DFE%'
        OR TABLE_NAME LIKE '%NFENT%' OR TABLE_NAME LIKE '%IMPXML%');

SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_LENGTH FROM ALL_TAB_COLUMNS
WHERE  OWNER='TEKNISA'
AND    (COLUMN_NAME LIKE '%INFCPL%' OR COLUMN_NAME LIKE '%INFADIC%'
        OR COLUMN_NAME LIKE '%OBSCPL%' OR COLUMN_NAME LIKE '%DADOSADIC%');
```

**Resultado recebido (trechos relevantes):**
```
ARQXMLNFE, ARQXMLNF, RELACXMLAF, PRODFORNXML, PRODFORNXMLFIL,
PREVALIDXML, CONSISTXML, HISTXMLNFE ... (lista extensa)

(infCpl) nenhuma coluna própria da NF de entrada — só campos de
MDFE, IMPOSTO e PRODUTO.DSINFADICPROD
```

**Conclusão:** o XML fica em `ARQXMLNFE`. O `infCpl` não tem coluna própria — está
dentro do XML, então o robô lê o XML e faz o parse. Apareceu também `RELACXMLAF`
(vínculo XML×AF) e `PRODFORNXMLFIL` (De/Para por filial, além do global). **[PLUG-2
encaminhado; detalhado nas consultas 6 e 7.]**

---

### Consulta 3 — As procedures de aprovação/vínculo da NF
**Objetivo:** descobrir a rotina do produto que aprova a NF e vincula a AF (a mesma
por trás do botão "Relacionar AF").

```sql
SELECT DISTINCT NAME, TYPE FROM ALL_SOURCE
WHERE  OWNER='TEKNISA' AND UPPER(TEXT) LIKE '%AUTOFORN%'
AND    (UPPER(NAME) LIKE '%NOTA%' OR UPPER(NAME) LIKE '%NF%'
        OR UPPER(NAME) LIKE '%APROV%' OR UPPER(NAME) LIKE '%ENTR%'
        OR UPPER(NAME) LIKE '%GERA%' OR UPPER(NAME) LIKE '%VINCUL%');
```

**Resultado recebido (trechos relevantes):**
```
CONFERE_ERROS_NF            PROCEDURE
CONFIRMACAO_AUTOMATICA_NF   PROCEDURE
GERA_CONTAPAGAR             PROCEDURE
IMPOSTO_ENTRADA             PROCEDURE
NF_BAIXA_AF                 PROCEDURE
VERIFICA_INCONS_NF          PROCEDURE
... (entre outras)
```

**Conclusão:** três nomes explicam o fluxo. `NF_BAIXA_AF` vincula a NF à AF;
`VERIFICA_INCONS_NF` gera as inconsistências (o "Operação não realizada");
`CONFIRMACAO_AUTOMATICA_NF` confirma — **o Teknisa já tem aprovação automática
nativa.** `GERA_CONTAPAGAR` e `IMPOSTO_ENTRADA` são os efeitos financeiros/fiscais a
jusante. **[PLUG-3 encaminhado; assinaturas na consulta 5.]**

---

### Consulta 4 — Colunas de status da NOTAFISCAL
**Objetivo:** entender como a NF sinaliza inconsistência e aprovação.

```sql
SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH FROM ALL_TAB_COLUMNS
WHERE  OWNER='TEKNISA' AND TABLE_NAME='NOTAFISCAL'
AND    (COLUMN_NAME LIKE '%ERRO%' OR COLUMN_NAME LIKE '%STATUS%'
        OR COLUMN_NAME LIKE '%SITU%' OR COLUMN_NAME LIKE '%UTILIZ%'
        OR COLUMN_NAME LIKE '%APROV%');
```

**Resultado recebido (trechos relevantes):**
```
IDERROS         VARCHAR2  30
DTAPROVACAO     DATE       7
IDSTATUS        VARCHAR2   1
IDSTATUSNFE     VARCHAR2   1
DSOBSAPROVAAUTO VARCHAR2 4000
... (entre outras)
```

**Conclusão:** `IDERROS` marca inconsistência; `DTAPROVACAO` é preenchida quando a
NF é aprovada; existe `DSOBSAPROVAAUTO` ("observação aprovação automática"),
reforçando que há um caminho nativo de aprovação automática. O sinal de sucesso do
robô é o `DTAPROVACAO` preenchido.

---

### Consulta 5 — Assinaturas exatas das procedures
**Objetivo:** ter os parâmetros para o robô chamar as procedures corretamente.

```sql
SELECT OBJECT_NAME, POSITION, ARGUMENT_NAME, IN_OUT, DATA_TYPE, DATA_LENGTH
FROM   ALL_ARGUMENTS
WHERE  OWNER='TEKNISA' AND PACKAGE_NAME IS NULL
AND    OBJECT_NAME IN ('NF_BAIXA_AF','CONFIRMACAO_AUTOMATICA_NF',
                       'VERIFICA_INCONS_NF','CONFERE_ERROS_NF')
ORDER  BY OBJECT_NAME, POSITION;
```

**Resultado recebido (resumo):**
```
CONFIRMACAO_AUTOMATICA_NF: P_NRACESSONFE, P_DTENTRADA, P_CDOPERADOR, P_NRORG
NF_BAIXA_AF: P_CDFILIAL, P_NRLANCTONF, P_NRSEQITPROD, P_IDSTATUS,
             P_NEWCDPRODEMIS, P_OLDCDPRODEMIS, P_NEWCDPRODUTO, P_OLDCDPRODUTO,
             P_NEWCDMARCA, P_OLDCDMARCA, P_NEWQTITEMPROD, P_OLDQTITEMPROD,
             P_IDERRO (OUT)
VERIFICA_INCONS_NF: P_NRACESSONFE, P_IDGERNFIGUALXML, P_LANCAMENTO, P_NRLANCTONF,
             P_CDFILIAL, P_LIBERAXML, P_CDTIPOOPER, P_LIBERAOPER, P_ERRO (OUT)
CONFERE_ERROS_NF: P_CDFILIAL, P_NRLANCTONF, P_VRLANCAMENTO
```

**Conclusão:** assinaturas fechadas. O connector foi ligado nas três procedures reais.
`NF_BAIXA_AF` trata substituição de produto (parâmetros NEW/OLD) e devolve `P_IDERRO`;
`VERIFICA_INCONS_NF` devolve `P_ERRO` (vazio = sem inconsistência). **[PLUG-3
resolvido.]**

---

### Consulta 6 — Onde ficam as inconsistências
**Objetivo:** achar a tabela por trás da aba "Inconsistências" (o gargalo do
"produto sem relacionamento").

```sql
SELECT TABLE_NAME FROM ALL_TABLES
WHERE  OWNER='TEKNISA'
AND    (TABLE_NAME LIKE '%INCONS%' OR TABLE_NAME LIKE '%ERRONF%'
        OR TABLE_NAME LIKE '%NFINCON%');
```

**Resultado recebido:**
```
ERRONFPREVALID, ERRONFPREVALID_AUX, INCONS_AF, INCONS_ITAF, INCONSMRP,
INCONSPEDIDO, INTG_INCONSISTENCIA, LOGINCONSXML, PRODINCONSISTENTE
```

**Conclusão:** `PRODINCONSISTENTE` é o item sem relacionamento (o gargalo do vídeo);
`INCONS_AF` / `INCONS_ITAF` são inconsistências de AF. O robô lê essas tabelas para
saber exatamente o que falta.

---

### Consulta 7 — Estrutura do XML e do vínculo XML×AF
**Objetivo:** achar o campo que guarda o XML (para o robô ler) e como o XML se liga
à AF.

```sql
SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH FROM ALL_TAB_COLUMNS
WHERE  OWNER='TEKNISA' AND TABLE_NAME='ARQXMLNFE' ORDER BY COLUMN_ID;

SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH FROM ALL_TAB_COLUMNS
WHERE  OWNER='TEKNISA' AND TABLE_NAME='RELACXMLAF' ORDER BY COLUMN_ID;
```

**Resultado recebido (resumo):**
```
ARQXMLNFE:  CDFILIAL(4), NRLANCTONF(10), DSARQXMLNFE_CLOB(CLOB), ... IDATIVO(1)
RELACXMLAF: NRACESSONFE(44), NRAUTOFORN(20), NRORG, IDATIVO, ...
```

**Conclusão:** o XML fica em `ARQXMLNFE.DSARQXMLNFE_CLOB`, com chave
`CDFILIAL + NRLANCTONF`. O identificador geral é `NRACESSONFE` (chave de acesso de 44
dígitos). `RELACXMLAF` liga a chave de 44 dígitos à AF. **[PLUG-2 resolvido.]**
Observação: em `RELACXMLAF` o `NRAUTOFORN` tem 20 dígitos (maior que os 10 da
AUTOFORNE) — confirmar em homologação se é filial + número concatenados.

---

## 4. Fluxo final definido (a partir dos resultados)

1. Lê o XML do CLOB em `ARQXMLNFE`.
2. Acha a AF: tag `<xPed>`; senão, texto de `<infCpl>` (regex tratando 6 padrões).
   Devolve filial + número.
3. Resolve o fornecedor: documento do XML → `CDFORNECED` (via `NRINSJURFORN`).
4. Para cada item sem De/Para em `PRODFORNXML`: compara com os itens da AF
   (`ITEMAUTF`). Score ≥ 95% grava o De/Para; abaixo, manda card no Teams.
5. Vincula e valida: `NF_BAIXA_AF` e `VERIFICA_INCONS_NF`.
6. Confirma: `CONFIRMACAO_AUTOMATICA_NF` (aprovação nativa).
7. Registra em `AIHUB_PROC_LOG`.

## 5. Segurança e postura

- O robô **não força aprovação** — usa as procedures nativas.
- Escreve apenas no De/Para (`PRODFORNXML`, cadastro reversível) e no próprio log.
- Roda com usuário técnico de permissão mínima (schema AIHUB): leitura no TEKNISA,
  escrita só em PRODFORNXML, execução das 3 procedures. Sem DELETE fiscal, sem DDL.
- **Modo sombra por padrão:** sugere e o operador aprova, até a homologação validar
  a sequência.

## 6. Como será executado

Serviço Python instalado num servidor ligado 24h (Serviço do Windows ou systemd),
em laço de 5 em 5 minutos, conectado ao Oracle. O usuário no Teknisa não vê o robô;
apenas percebe as notas sendo liberadas e a fila do Painel de Documentos Fiscais
esvaziando.

## 7. Pontos a definir/validar com o DBA (homologação)

1. Rodar a sequência (gravar De/Para → `NF_BAIXA_AF` → `VERIFICA_INCONS_NF` →
   `CONFIRMACAO_AUTOMATICA_NF`) com uma NF real controlada em base de homologação.
2. Confirmar `DTAPROVACAO` preenchida e efeitos a jusante (`GERA_CONTAPAGAR`,
   `IMPOSTO_ENTRADA`) iguais ao fluxo manual.
3. Confirmar o `CDTIPOOPER` do "01 - NF Compra Mod 55" e o valor de `P_IDSTATUS`.
4. Confirmar o formato de `NRAUTOFORN` em `RELACXMLAF`.
5. Definir o servidor onde o serviço vai rodar e o cofre das credenciais.
