# Mapeamento operacional do robô — resultado

Objetivo: mapear cada ponto onde o robô decide, para garantir que **nenhuma
nota seja jogada para o lugar errado, mesmo estando certa**. Todas as consultas
foram de leitura, contra o banco real (schema TEKNISA). O que não deu para
confirmar está marcado como "a confirmar" — nada aqui é suposição.

Resumo: dos 7 pontos de decisão, 6 estão fechados com trava no código e 1
(estado da nota) tem a trava forte pronta e falta só confirmar um código no
teste de homologação.

---

## D1 — Resolver o fornecedor (CNPJ do XML → CDFORNECED)

**Risco:** um CNPJ apontar para mais de um fornecedor e o robô escolher o errado.

**Achado:** só 2 CNPJs (num universo de ~1.744) têm cadastro duplo. Nos dois
casos é a **mesma empresa** cadastrada duas vezes, com prefixos diferentes:
`I...` (CNPJ com máscara) e `J...` (CNPJ limpo, o padrão). Todo o De/Para da base
está nos códigos `J`.

> **Nota de 03/08/2026:** este documento é o registro da investigação original e
> cita o módulo `core/erp_connector.py`, que foi **removido** (era da versão
> anterior). As travas descritas aqui continuam valendo — elas vivem hoje em
> `robo/servico_robo.py` e `interface/painel/app.py`.

**Trava embutida** (`erp_connector.resolver_fornecedor`): quando um CNPJ dá em
mais de um fornecedor, prefere o que **tem De/Para**; se nenhum tiver, prefere o
prefixo **`J`**. Determinístico. Nenhuma nota vai para o fornecedor errado.

## D2 — Localizar a AF (número → AF única)

**Risco:** o mesmo número de AF existir para fornecedores/filiais diferentes e o
robô vincular na AF errada.

**Achado:** número de AF sozinho **não** é único (há repetição entre
fornecedores). Mas **número + fornecedor é único** na base inteira (zero casos de
número+fornecedor repetido em filiais diferentes).

**Trava embutida** (`process_runner`): o robô busca a AF por número + fornecedor
+ filial; se por qualquer motivo a busca retornar mais de uma, ele **não escolhe**
— manda para o humano. Erra para o lado seguro.

## D3 — De/Para do item (item do XML → produto interno)

**Risco:** o mesmo item de um fornecedor apontar para produtos internos
diferentes (ambiguidade de destino).

**Achado:** **zero conflitos** na base inteira. Cada (fornecedor, item do XML)
aponta para um único produto interno.

## D4 — Vínculo XML × AF (RELACXMLAF)

**Risco:** havia dúvida sobre o formato do `NRAUTOFORN` (20 dígitos ali, 10 na
AUTOFORNE), que poderia levar a vincular na AF errada.

**Achado:** a tabela `RELACXMLAF` está **vazia** neste ambiente. O ERP não usa
esse caminho aqui. O robô já pega a AF direto do próprio XML, então **não depende**
dessa tabela. As referências a ela existiam só na documentação, nunca no código.

## D5 — Tipo de operação (CDTIPOOPER)

**Risco:** aprovar a nota com o tipo de operação errado (ex.: tratar uma
bonificação como compra normal).

**Achado:** o tipo confirmado da compra padrão é **`E` (entrada) + `01` — "NF
Compra (Mod 55)"**. A procedure de baixa (`NF_BAIXA_AF`, 13 parâmetros) **não**
recebe tipo de operação. Quem recebe é o `VERIFICA_INCONS_NF` (parâmetro
`cd_tipo_oper`). O tipo real da nota mora em **`NOTAFISCAL.CDTIPOOPER`**.

**Trava embutida** (`erp_connector.dados_nota_lancada`): a aprovação lê o
`CDTIPOOPER` **da própria nota**, nunca fixa "01". Assim cada nota é validada com
o tipo dela.

## D6 — Estado da nota (só processar pendente)

**Risco:** reprocessar uma nota que já foi aprovada.

**Achado:** o campo de aprovação é **`NOTAFISCAL.DTAPROVACAO`**; há também o
`IDSTATUS` (mesmo campo do parâmetro `P_IDSTATUS` da `NF_BAIXA_AF`), com códigos
de domínio (A, C, V, P, L, D, I). Os significados das letras **não foram
adivinhados**.

**Trava embutida** (`erp_connector.nota_processavel`): o robô só processa nota
**sem `DTAPROVACAO`**. Nota com data de aprovação nunca é tocada — trava forte,
independe de decifrar os códigos.

**A confirmar (1 item):** qual valor de `IDSTATUS` é o "aguardando aprovação"
(a fila legítima do robô). Será lido de uma nota real pendente no teste de
homologação e gravado em `IDSTATUS_AGUARDANDO`. Enquanto isso, vale só a trava
do `DTAPROVACAO`.

## D7 — Cobertura do De/Para por fornecedor (dimensionamento)

Sem risco de roteamento — mede o esforço humano no começo.

| Faixa de itens mapeados | Fornecedores |
|---|---|
| 200+ | 95 |
| 51–200 | 225 |
| 11–50 | 361 |
| 1–10 | 1.063 |

O volume de notas está concentrado nos ~320 fornecedores com 51+ itens — o robô
entra rodando forte neles. A cauda (1.063 com 1–10) é de baixo volume; ali o robô
se apoia mais no matcher + humano no início, e a base cresce sozinha (toda
aprovação humana grava De/Para novo).

---

## Fluxo de confirmação x aprovação (descoberto no teste de homologação)

Testando as procedures nativas numa nota de integração real e **lendo o
código-fonte** delas (como TEKNISA/DBA), descobrimos o fluxo verdadeiro:

```
I  (integrada do XML)
   │  ← ROBO: resolve De/Para + NF_BAIXA_AF + VERIFICA_INCONS_NF + CONFIRMACAO_AUTOMATICA_NF
   ▼
P  (pendente de aprovação)
   │  ← HUMANO: aprova pela alçada (tela do Teknisa)
   ▼
A  (aprovada; DTAPROVACAO preenchida)
```

Pontos confirmados:

- `NF_BAIXA_AF` e `VERIFICA_INCONS_NF` rodam limpas numa nota de integração real
  (ambas retornam `'N'` = sem erro / sem inconsistência).
- `CONFIRMACAO_AUTOMATICA_NF` **não preenche a `DTAPROVACAO`** — lendo o fonte
  (486 linhas), ela trata **recebimento/estoque** (variável `V_ESTOQAPROV`), e
  move a nota de `I` para `P`. Não é a rotina de aprovação.
- **Não existe procedure que aprove a NF.** As triggers de aprovação disparam na
  tabela `APROVALCADA` (alçada de aprovação) — ou seja, a aprovação passa por um
  **fluxo de autoridade humana**, não por uma chamada de banco.

Conclusão (escopo do robô): o robô vai até **`P` (recebimento confirmado,
pendente de aprovação)**. A aprovação final (`DTAPROVACAO`) é humana, pela
alçada — por decisão de projeto e porque é assim que o ERP funciona. Isso casa
com a filosofia "robô prepara, humano aprova".

Convenção importante confirmada: tanto `NF_BAIXA_AF` (`P_IDERRO`) quanto
`VERIFICA_INCONS_NF` (`P_ERRO`) retornam **`'N'` = OK / sem inconsistência** (não
retornam vazio). O código usa `validacao_ok()` para testar isso — nunca `IS NULL`.

## Situação dos pontos em aberto (fechados)

1. `IDSTATUS` da fila do robô: **`I`** (integrada do XML) — confirmado. Fim da
   linha do robô: **`P`**.
2. Sequência ponta a ponta pelas procedures nativas: **testada em homologação**,
   roda limpa. A aprovação final fica com a alçada humana (fora do robô).

Todos os pontos de decisão estão mapeados e travados no código.
