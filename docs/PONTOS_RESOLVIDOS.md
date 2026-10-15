# Pontos abertos — RESOLVIDOS com os dados do banco

Os três `[PLUG]` do primeiro documento foram fechados. Abaixo o que ficou definido
e o único cuidado que resta (homologação).

## [PLUG-1] Fornecedor — RESOLVIDO
- `FORNECEDOR.NRINSJURFORN` guarda o documento **limpo** (sem máscara).
- Pode ser CNPJ (14) ou CPF (11), conforme pessoa jurídica/física.
- `resolver_fornecedor()` já normaliza e casa os dois casos.

## [PLUG-2] Fila de XML — RESOLVIDO
- O XML fica em **`ARQXMLNFE.DSARQXMLNFE_CLOB`** (CLOB), chave `(CDFILIAL, NRLANCTONF)`.
- Identificador geral: **`NRACESSONFE`** (chave de acesso NFe, 44 dígitos).
- O `infCpl` não tem coluna própria — é lido do próprio XML (parser já faz).
- **`RELACXMLAF`** é o vínculo XML×AF: `NRACESSONFE (44)` → `NRAUTOFORN`.
  Nota: aqui `NRAUTOFORN` é VARCHAR2(20), maior que o (10) da AUTOFORNE — confirmar
  em homologação se é filial+número concatenados.
- `buscar_xmls_novos()` lê a fila filtrando `DTAPROVACAO IS NULL`.

## [PLUG-3] Aprovação — RESOLVIDO (via rotinas nativas, sem forjar)
Assinaturas confirmadas em ALL_ARGUMENTS. O robô chama as procedures do produto:

| Procedure | Papel | Parâmetros-chave |
|---|---|---|
| `VERIFICA_INCONS_NF` | valida a NF, gera inconsistências | NRACESSONFE, NRLANCTONF, CDFILIAL, CDTIPOOPER → **P_ERRO OUT** |
| `NF_BAIXA_AF` | vincula item à AF, trata substituição | CDFILIAL, NRLANCTONF, NRSEQITPROD, NEW/OLD CDPRODUTO/MARCA/QT → **P_IDERRO OUT** |
| `CONFIRMACAO_AUTOMATICA_NF` | confirmação automática nativa | NRACESSONFE, DTENTRADA, CDOPERADOR, NRORG |

Inconsistências ficam em: **`PRODINCONSISTENTE`** (item sem relacionamento — o gargalo
do vídeo), `INCONS_AF`, `INCONS_ITAF`, `ERRONFPREVALID`.

**Consequência importante:** existe aprovação automática NATIVA. O robô não precisa
(e não deve) escrever status direto na NOTAFISCAL. Ele aciona `CONFIRMACAO_AUTOMATICA_NF`,
que é o próprio mecanismo do Teknisa.

## O único cuidado que resta: HOMOLOGAÇÃO
Sabemos chamar as três procedures, mas não a ordem/commits exatos que a tela usa
internamente. Antes de produção autônoma:

1. Rodar em **base de homologação**, com uma NF real controlada.
2. Sequência a validar: `gravar_depara` → `nf_baixa_af` (por item) →
   `verifica_incons_nf` (P_ERRO vazio?) → `confirmar_nf`.
3. Conferir se `DTAPROVACAO` foi preenchida e se `GERA_CONTAPAGAR`/`IMPOSTO_ENTRADA`
   dispararam corretamente (efeitos financeiros/fiscais a jusante).
4. Confirmar o `CDTIPOOPER` real do "01 - NF Compra Mod 55".
5. Confirmar colunas de `PRODINCONSISTENTE` (o `ler_inconsistencias` está tolerante
   a isso, mas o ideal é fixar).

Só depois disso, virar `AIHUB_MODO_SOMBRA=0`.
