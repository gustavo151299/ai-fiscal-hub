# As regras que o gestor passou — o que apuramos e como ficou

**Apurado em 31/07/2026**, direto no banco e nos manuais oficiais da Teknisa.
Cada afirmação abaixo tem número ou fonte. O que não foi confirmado está marcado.

## Resumo

| # | Regra do gestor | Situação |
|---|---|---|
| 1 | Criar relacionamento no nível de produto 01/02/03 | ✅ Implementado — e refinado |
| 2 | Notas que têm a tag, em vez de olhar no olho | 🔎 Entendido — há ganho sem programar |
| 3 | Produtor rural → tipo de operação 08 | ✅ Confirmado — fica fora, por natureza |
| 4 | CFOP de bonificação → tipo 06 | ✅ Já estava configurado no Teknisa |
| 5 | Vendas 5101/5102/6101/6102 → tipo 01 | ✅ Já funciona sozinho |
| 6 | CFOP 5504 / 6504 → tipo 01 (a confirmar) | ⏳ **Única em aberto** |

---

## 1. "Criar um relacionamento sempre que for nível de produto 01, 02, 03"

**O que ele quis dizer.** O código do produto no Teknisa é o código da árvore mais
2 dígitos. Esses 2 dígitos finais separam duas coisas diferentes:

| Final | O que é | Exemplo |
|---|---|---|
| `00` | **Produto de Estoque** — a unidade de controle (KG, UN, LT) | ARROZ AGULHINHA - KG |
| `01`, `02`, `03`… | **Produtos de Compra** — a embalagem que se compra, com fator de conversão | ARROZ AGULHINHA 6X5KG - FD (fator 30) |

Ele está certo sobre o ponto central: **é aqui que o sistema acerta ou erra o estoque.**
Se a nota for vinculada ao produto errado, uma caixa vira um quilo.

**O que os dados mostraram.** Fomos ver os 82.563 vínculos que já existem e funcionam:

- **66,3% apontam para o `00`** (produto de estoque) — unidades KG, UN, MT
- **33,7% apontam para `01/02/03`** (produto de compra) — unidades CX, FD, PC, LA

Ou seja: a regra **não** é "sempre vincular no 01/02/03". É:

> **Vincula-se ao produto cuja unidade corresponde a como o fornecedor entrega.**
> Entrega a granel (KG) → produto de estoque. Entrega embalado (CX) → produto de compra,
> com o fator de conversão.

**Como ficou no sistema.** O robô passou a usar a **unidade de medida** para decidir —
antes ele tentava adivinhar pela descrição do produto, que vem escrita de qualquer jeito
pelo fornecedor.

A unidade do XML bate com a do produto interno em **76,8% de 1,64 milhão de itens**.
É um sinal muito mais confiável que o texto.

E o robô agora **se recusa a vincular sozinho** quando granel encontra embalagem
(KG contra CX) — manda para conferência humana. Testamos essa trava contra o histórico
todo: ela barra 1,77%, e todos os casos barrados são conflito real.

---

## 2. "Notas que têm a tag, em vez de olhar no olho"

**A tag tem nome e está na tela dele.** Na coluna "Origem Filial (XML)" da tela de
Importação/Consulta de Notas Fiscais, os valores são:

| O que aparece | O que significa | Quantidade |
|---|---|---|
| `AF (tag xPed)` | **a AF veio na tag do XML** — o sistema relaciona sozinho | 7.888 (1,5%) |
| `AF(Relacionar AF)` | não veio tag — **alguém precisa relacionar no olho** | 318.601 (61,2%) |
| `Manual` | nota digitada | 35.972 (6,9%) |

**Só 1,5% das notas têm a tag.** É exatamente a dor que ele descreveu.

**E encontramos uma possível causa.** Existe um parâmetro por fornecedor chamado
`Utiliza AF do XML` (`PARAMFORNE.IDUTILAFXMLFORN`). Ele está **desligado para todos os
7.820 fornecedores cadastrados**.

> **Oportunidade:** se esse for mesmo o parâmetro que habilita ler a AF pela tag,
> ligá-lo para os fornecedores que já mandam o número da AF no XML aumentaria a
> automação **sem escrever uma linha de código**.
>
> Vale confirmar o significado exato com o consultor Teknisa antes de mexer.

Nosso sistema já lê a tag `xPed` do XML — está preparado para quando isso for ligado.

---

## 3. "Se o fornecedor for produtor rural, o tipo de operação é 08"

**Confirmado, por três caminhos independentes:**

1. No catálogo do Teknisa, o tipo `08` de entrada chama-se literalmente **"NF Produtor Rural"**
2. Os fornecedores marcados como produtor rural têm suas notas em `08`
3. Eles têm tipo de fornecedor `008` — o cadastro espelha a operação

**Mas apareceu algo que muda o escopo:** as **5.843 notas de produtor rural são 100%
digitadas**. Nenhuma passou pela importação de XML.

O motivo é simples: produtor rural pessoa física **não emite NF-e**. Quem emite a nota
de entrada é a própria empresa. Confirmamos isso pelo CFOP — as que têm arquivo trazem
CFOP `1102` (entrada), não `5xxx` (saída, que é o que um fornecedor normal manda).

**Como ficou no sistema.** Ficam **fora da automação** — e isso não é escolha nossa, é
constatação. O robô trabalha sobre notas que chegaram por XML; aqui não existe XML para
ler. Isso bate com a própria regra do gestor: *"o sistema só trata nota simplificada;
notas manuais não passam"*. Produtor rural **é** a nota manual.

Deixamos escrito no código, com o motivo, para ninguém estranhar a ausência depois.

*(O volume também vem caindo: 1.005 notas em 2023 → 83 em 2026.)*

---

## 4. "Se o CFOP da nota for de bonificação, o tipo de operação é 06"

**Essa regra já estava configurada no Teknisa.** Existe uma tela chamada
**"Integração CFOP XML × Tipo de Operação"**, que diz ao sistema: *quando o XML chegar
com este CFOP, classifique como este tipo*. O conteúdo dela hoje:

| CFOP no XML | Vira o tipo |
|---|---|
| **5910** | **06 — NF Bonificações** |
| **6910** | **06 — NF Bonificações** |
| 6353 | 21 — CTR Transp Bens |

`5910` e `6910` são exatamente os CFOPs de **remessa em bonificação, doação ou brinde**.
Ou seja: **a regra dele já está no ar e funcionando.**

**Como ficou no nosso sistema.** As notas de bonificação eram **invisíveis** para o painel
(o filtro só olhava compra normal). Agora aparecem — são **235 notas de entrada**.

E, por decisão da operação, o robô **sugere o produto mas não confirma o recebimento**:
bonificação é mercadoria recebida sem cobrança, entra em estoque sem contrapartida
financeira, então quem confirma é uma pessoa.

---

## 5. "Quais operações são só vendas: 5101, 5102, 6101, 6102"

**Confirmado a partir do próprio XML.** Lendo o CFOP de dentro dos arquivos e cruzando
com o tipo de operação da nota:

| CFOP no XML | Tipo de operação | Notas |
|---|---|---|
| **5101** | **01 — NF Compra** | 111 |
| **5102** | **01 — NF Compra** | 90 |

**E entendemos a mecânica, que é importante:** a tabela de Integração CFOP × Tipo é uma
lista de **exceções**. Tudo que não está nela vira `01 — NF Compra`, que é o padrão.

Por isso `5101`, `5102`, `6101` e `6102` **não precisam ser cadastrados** — eles já caem
no padrão sozinhos. **Nada a fazer.**

*(Detalhe: os CFOPs 5xxx/6xxx são de saída — é o CFOP do fornecedor, como vem escrito no
XML dele. O da nossa nota de entrada é 1xxx/2xxx. Por isso a tela chama "CFOP XML".)*

---

## 6. "5504 (confirmar) se é venda de ST, e 6504 → tipo 01" — ✅ RESOLVIDO

**Resposta da operação (03/08/2026):** *"CFOP 6504 não passa por aqui, porque é uma nota
emitida por nós (unidade); por aqui é somente nota de entrada e não de saída."*

**Fora de escopo.** Não há nada a fazer, nem no Teknisa nem no nosso sistema.

---

## 7. Informações novas da operação (03/08/2026)

### 7.1 Os CFOPs, por finalidade

| Finalidade | CFOPs | Tipo de operação |
|---|---|---|
| Venda (= nossa compra) | 5101, 5102, **5405**, 6101, 6102, **6404** | **01** |
| Bonificação | **5910** | **06** |
| Estorno | 1102, 1202, 1209 | — |
| Comodato | 5908, 6908, 7908 | **25** |
| Retorno de comodato | 5909, 6909, 1909, 2909 | — |

O `5405 → 01` bate com o que medimos no XML. Os demais tipos citados:
`17` = NF de serviço, `21` = CTe, `25` = comodato.

### 7.2 ⚠️ A tabela CFOP × Tipo pode estar incompleta

A tabela `CFOPXMLTPOPER` tem hoje **3 linhas** (5910→06, 6910→06, 6353→21). Tudo que não
está nela vira **`01` — NF Compra**, que é o padrão.

**Os CFOPs de comodato (5908, 6908, 7908) não estão cadastrados.** Se uma nota de comodato
chegar por XML, ela será classificada como **compra comum**, e não como comodato. Existem
424 notas de comodato que vieram por XML — vale conferir como elas estão sendo
classificadas hoje.

> **Recomendação:** revisar essa tela com o consultor Teknisa e cadastrar os CFOPs de
> comodato e retorno de comodato. É parametrização, não programação.

### 7.3 O que de fato passa pela rotina simplificada

Medido em 03/08/2026, cruzando cada tipo com a origem da nota:

| Tipo | Nome | Por XML | Digitada | Movimenta estoque? |
|---|---|---|---|---|
| **01** | NF Compra | **443.706** | 1.042.818 | **Sim** |
| 21 | CTR Transp Bens (CTe) | 5.743 | 495 | Não |
| 25 | NF Comodato | 424 | 109 | Não |
| **06** | NF Bonificações | **163** | 72 | **Sim** |
| 17 | NF Serviço Tomado | **1** | 28.932 | Não |
| 08 | NF Produtor Rural | **0** | 5.843 | Sim |

**Só o 01 e o 06 são, ao mesmo tempo: nota de produto, que chega por XML e que alimenta
estoque.** São exatamente os dois que o robô trata.

- **17 (serviço)** — 28.932 digitadas contra **1** por XML. Sai sozinho pelo filtro de
  rotina simplificada.
- **21 (CTe)** — chega por XML em volume, mas é documento de **transporte**: não tem
  produto para casar e não movimenta estoque.
- **25 (comodato)** — chega por XML, mas é mercadoria **emprestada**: não movimenta estoque.

**✅ Resolvido (03/08/2026).** Perguntado se CTe e comodato deveriam aparecer no painel,
a operação respondeu: *"no painel pode deixar somente de bonificação mesmo"*.

Ou seja: o painel mostra **compra (01) e bonificação (06)**, e mais nada. É exatamente o
que está implementado (`TIPOS_ENTRADA = ("01", "06")`), travado pelo teste B13. **Nenhuma
mudança necessária.**

### 7.7 Comodato — não é nosso, mas há um ganho para eles

**Resposta da operação:**

> *"Precisa liberar na operação 25. Porém, como o CFOP de comodato é diferente, acredito
> que o sistema poderia vincular — só que precisa saber qual é a unidade correta para poder
> fazer a liberação. Nota de comodato não exige AF, mas sim qual a unidade."*

Três coisas ficam claras:

1. **Hoje é manual** — alguém libera na operação 25, nota a nota.
2. **Comodato não usa AF.** Usa a **unidade** (filial) de destino. É um mecanismo
   diferente do da compra, onde a AF define tudo.
3. **Parte disso dá para automatizar sem programar.**

> **Recomendação para o consultor Teknisa:** cadastrar os CFOPs de comodato
> (**5908, 6908, 7908**) na tela *Integração CFOP XML × Tipo de Operação*. Hoje ela tem só
> 3 linhas, e tudo que não está nela vira `01 — Compra`. Com esses CFOPs cadastrados, a
> classificação como operação 25 passa a ser automática.
>
> **Atenção ao limite:** isso resolve o **tipo de operação**, não a **unidade**. Pelo que a
> operação descreveu, a unidade continuaria precisando de alguém. O ganho é parcial, mas
> é ganho — e não custa linha de código.

**Fora do escopo do nosso robô**, e por dois motivos independentes: comodato não movimenta
estoque (`IDATUESTTPOP='N'`) e não usa AF, que é a régua do robô para saber quais produtos
esperar na nota.

### 7.4 ✅ "Nível 6" (Operador Logístico) — RESOLVIDO

**Resposta da operação:** *"relacionamento precisa ser feito no nível 6 também, que é do OL."*

**"Nível 6" não é um sexto nível da hierarquia. É o RAMO 6 da árvore** — o primeiro
dígito do código do produto. Os 9 ramos de nível 1 são:

| Ramo | Nome | Produtos (nível 5) |
|---|---|---|
| 1 | ALIMENTAR | 13.592 |
| 2 | DESCARTAVEL | 1.438 |
| 3 | LIMPEZA | 588 |
| 4 | INFRAESTRUTURA | 1.744 |
| 5 | DESPESA | 3.989 |
| **6** | **OPERADOR LOGÍSTICO** | **6.441** |
| 7 | FATURAMENTO | 287 |
| 8 | PRODUTO PRODUCAO | 1.683 |
| 9 | PRODUTO VENDA | 1.057 |

A árvore continua tendo **5 níveis**. O ramo 6 é onde moram os produtos do OL.

**A ponte entre as duas visões é `TEKNISA.RELACPRODOL`** (3.643 relacionamentos ativos):

| Coluna | O que é |
|---|---|
| `CDPRODARVOL` | produto na árvore do **OL** (ramo 6) |
| `CDPRARVORIG` | produto na árvore **original** |

Exemplo real: `6010127240` FRANGO CONG MOELA - CX 18 KG ↔ `1010127203` FRANGO CONG MOELA -
CX 18 KG. **É o mesmo produto, cadastrado nas duas árvores.**

*(`PRODUTO.CDPRODORIG` está 0% preenchida — não é essa a ponte, é a `RELACPRODOL`.)*

### 7.5 O que isso muda no nosso robô: nada no mecanismo

- **3.189 itens** de NF de compra que chegam **por XML** já usam produtos do ramo 6 →
  está dentro do nosso escopo
- A **AF contém 6.349 itens do ramo 6** → como os candidatos do robô saem da AF
  (`ITEMAUTF`), **ele já enxerga os produtos do OL sem nenhuma mudança de código**
- Mas o De/Para de NF-e hoje tem **13 vínculos no ramo 6** contra **82.550 nos demais**

### 7.6 O ramo 6 NÃO é assunto do nosso De/Para — e virou regra no código

> **Correção de uma conclusão anterior.** Este documento chegou a dizer que o ramo 6
> estava "sub-representado no De/Para" e que isso cresceria com o uso. **Estava errado.**

**Resposta da operação (03/08/2026):**

> *"É na própria tela de relacionamento. Eu faço no nível 1, 2 ou 3; porém na AF precisa
> estar no nível 6. Acho que essa parte pra você não muda nada, porque o processo é o
> mesmo — pode ignorar essa informação do relacionamento no nível 6."*
>
> *"No relacionamento que é feito pelo DFE é o mesmo que é feito dos outros níveis. O que
> muda é dentro do relacionamento do módulo OL, mas isso compete ao setor de Compras."*

O dado confirma. Para onde apontam os 82.563 vínculos ativos do De/Para do DFE:

| Ramo | Nome | Vínculos | % |
|---|---|---|---|
| 1 | ALIMENTAR | 35.161 | 42,6% |
| 5 | DESPESA | 29.915 | 36,2% |
| 4 | INFRAESTRUTURA | 10.021 | 12,1% |
| 2 | DESCARTAVEL | 3.937 | 4,8% |
| 3 | LIMPEZA | 3.467 | 4,2% |
| **6** | **OPERADOR LOGÍSTICO** | **13** | **0,02%** |

**São duas coisas separadas, e sempre foram:**

| Relacionamento | Onde | De quem é |
|---|---|---|
| Produto do fornecedor → nosso produto | tela do DFE, ramos 1..5 | **nosso sistema** |
| Produto do OL → produto da árvore original | módulo OL (`RELACPRODOL`) | **setor de Compras** |

**Como virou regra no matcher.** O mesmo produto existe nas duas árvores com nome
idêntico (3.643 pares), então texto e unidade **empatam** e a escolha viraria sorte:

- `6010127240` FRANGO CONG MOELA - CX 18 KG *(ramo 6, OL)*
- `1010127203` FRANGO CONG MOELA - CX 18 KG *(ramo 1, direto)*

O matcher passou a **preferir o produto direto** no empate — mesmo desempate de ordenação
que já existia para a unidade, sem mexer no score. Se **só** existir a versão do OL, ele
usa ela e não trava o item.

Coberto por 4 testes, inclusive um que inverte a ordem da lista para provar que a escolha
não depende de quem vem primeiro.

---

## Uma correção ao entendimento anterior do projeto

O gestor disse que a **regra de árvore** é de importância enorme. Ele está certo, e
estávamos com a informação errada registrada.

A documentação do projeto dizia que as colunas de árvore estavam vazias — o que é
**verdade**, mas levava à conclusão errada. A hierarquia não sumiu: ela está em **outras
duas colunas**, 100% preenchidas, que ninguém tinha olhado. São **5 níveis**, do grupo
grande até o produto final.

A coluna que parecia ser a árvore (`Gênero`) é, na verdade, classificação para o **SPED**
— coisa fiscal, sem relação com a hierarquia de produto.
