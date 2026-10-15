# Árvore de Produto, CFOP e Tipo de Operação — o que o Teknisa define

**Levantado em 31/07/2026** na Universidade Teknisa (manuais oficiais em PDF) e cruzado
com o código do projeto. Cada afirmação aqui tem fonte. O que **não** foi confirmado está
marcado como pergunta em aberto — não vire regra sem fechar.

Fontes (Universidade Teknisa → Manuais):
- `TECFOOD_CADASTRO_DE_PRODUTO_CADPROD01_1.pdf` (FEV/2026) — **a árvore**
- `01_GUIA_ERP_WEB_DFE_GUIAERPDFEV3.pdf` (JUN/2025, 66 pág.) — **o fluxo de entrada**
- `03_MANUAL_ERP-WEB-DOCUMENTOS-FISCAIS_CADASTRO-TIPO-OPERACAO_V1.pdf`
- `01_MANUAL_ERP-FISCAL-WEB_CADASTRO-DE-GENEROITEM-PRODUTO_V1.pdf`
- `12_MANUAL_..._CADASTRO-DE-PRODUTO-POR-FORNECEDOR_v2.pdf`

---

## 1. A árvore de produto — a estrutura do código

**Árvore de Produto** é a estrutura hierárquica de classificação. O cadastro vai
**até o Nível 4**. O Nível 4 já é o produto em si (ex.: `BATATA PALITO CONGELADA - KG`);
os níveis 1 a 3 são as ramificações de categoria.

O **Código Produto** é gerado pelo sistema: é o **Código Árvore + 2 dígitos no final**.

```
Código Árvore    1.03.02.010          -> Nível1 . Nível2 . Nível3 . Nível4
Código Produto   1.03.02.010.00       -> + 2 dígitos
                 1.03.02.010.01
                              ^^
                              estes 2 dígitos são o que importa
```

**Os 2 dígitos finais separam Produto de Estoque de Produto de Compra:**

| Final | O que é | Unidade | Fator |
|---|---|---|---|
| `00` | **Produto de Estoque** — a unidade de controle interno | KG, LT, UN | Fator de Correção = 1 |
| `01`, `02`, `03`… | **Produtos de Compra** — as embalagens que se compra do fornecedor | PCT, CX, FD, LA… | **Fator de Conversão** (quantas unidades de estoque cabem na embalagem) |

Exemplo do manual: `ARROZ AGULHINHA - KG` é o produto de estoque; `ARROZ AGULHINHA C/5KG - PC`
(fator 5) e `ARROZ AGULHINHA 6X5KG - FD` (fator 30) são produtos de compra do mesmo item.

Sem os pontos, o código tem **10 caracteres** (1+2+2+3+2). Bate com `database/models.py`
(`CDPRODUTO = String(10)`) e com os códigos reais usados nos testes:
`1010261400` = `1.01.02.614.00`.

### Padrão de nomenclatura (oficial)

- Formato: `NOME DO PRODUTO - SIGLA DA UNIDADE`
- Tudo em MAIÚSCULAS, **sem acento e sem pontuação**
- Embalagem múltipla: `NOME QUANTIDADExPESO - SIGLA` — sem espaço entre número e peso
  (`ARROZ AGULINHA 6X5KG - FD`)
- Nome do Produto de Estoque = idêntico ao Nível 4 da árvore
- Nome do Produto de Compra = inclui toda a informação da embalagem

Isto é diretamente útil ao matcher: o lado interno é previsível, e o `-` antes da sigla
de unidade é um separador confiável.

---

## 2. O que isso significa para o nosso De/Para  ⚠️

> **"Precisa criar um relacionamento sempre que for nível de produto 01, 02, 03"** — o gestor

À luz do manual, "nível de produto 01/02/03" são os **Produtos de Compra** (os 2 dígitos
finais). A regra é: **o De/Para tem que apontar para o Produto de Compra, não para o
Produto de Estoque.**

O motivo é o **Fator de Conversão**: ele mora no Produto de Compra. O fornecedor vende
a *embalagem*. Se a nota for vinculada ao produto `...00` (estoque), o sistema entende
1 caixa = 1 KG em vez de 1 caixa = 4,8 KG — e o estoque entra errado.

### O que o matcher faz hoje — e o que o dado mostra

> **Correção de uma conclusão anterior.** A primeira leitura deste documento dizia que o
> vínculo deveria *sempre* cair no Produto de Compra e que o matcher estava invertido.
> **A consulta ao banco (§5-A) mostrou que isso é forte demais:** 66,3% dos 82.563 vínculos
> ativos apontam para o Produto de **Estoque** (`...00`) e funcionam — porque o fornecedor
> entrega a granel (KG/UN), e aí fator 1 é o certo.

A regra real é sobre **unidade de entrega**, não sobre preferir sempre um lado:

| Como o fornecedor entrega | Vínculo correto | Fator |
|---|---|---|
| a granel / por unidade (KG, UN, MT) | Produto de Estoque `...00` | 1 |
| embalado (CX, FD, PC, LA, PT) | Produto de Compra `...01/02/03` | o fator da embalagem |

`core/ai_matcher.py: sim_pref_generico()` aproxima isso por **texto**: se a descrição do
fornecedor não traz gramatura, valoriza o genérico; se o candidato tem gramatura, rebaixa
(× 0,70). Direcionalmente não é absurdo — sem gramatura costuma ser granel. Mas é um palpite
sobre algo que o banco sabe de forma exata.

**A melhoria certa não é inverter, é trocar a fonte:** comparar a `uCom` (unidade comercial)
do XML com a `SGUNIDADE` do candidato, e usar `VRFATOCONV`/`CDPRODESTO` para navegar entre
o produto de estoque e suas embalagens. Isso transforma um chute de texto numa decisão
estrutural — e é o que a "lógica de árvore" do gestor de fato habilita.

---

## 3. `CDGENERO` **não** é a árvore

O manual `Cadastro de Gênero/Item do Produto` mostra que Gênero/Item é **classificação
fiscal para o SPED**, cadastrada por Livro Magnético (aba "Gênero/Item por Produto"),
com opção de cadastro automático por faixa de produtos.

Ou seja: `CDGENERO` estar vazio **não diz nada sobre a árvore**. São coisas diferentes.
A hierarquia real está na tela **Árvore de Produto**.

> **Pendência:** a afirmação do `CLAUDE.md` de que `CDGENERO` e `CDARVPAI` estão vazios
> **nunca teve consulta registrada** — não existe no repositório nenhum SELECT que tenha
> verificado isso. E a checagem, se houve, teria sido em homologação (congelada em
> 23/06/2026). `CDARVPAI` é o candidato natural a "código árvore pai" e **precisa ser
> conferido em produção** antes de qualquer decisão.

---

## 4. CFOP × Tipo de Operação — é parametrização NATIVA, não código nosso

Guia DFE, seção **24.3 — "Integração CFOP XML X Tipo de Operação"**:

> a tela permite determinar, para cada tipo de operação, quais CFOPs do arquivo XML são
> possíveis; na importação do XML o sistema verifica o CFOP e lança a nota automaticamente
> conforme o tipo de operação relacionado.

**As regras do gestor sobre CFOP se cadastram nessa tela.** Não precisam ser programadas:

| Regra do gestor | Onde vive |
|---|---|
| CFOP de bonificação → tipo `06` | Integração CFOP XML × Tipo de Operação |
| CFOP 5101 / 5102 / 6101 / 6102 → tipo `01` | idem |
| CFOP 5504 / 6504 (ST) → tipo `01` *(a confirmar)* | idem |
| Fornecedor produtor rural → tipo `08` | **não é por CFOP** — ver §6, pergunta 2 |

Parâmetro relacionado (Parâmetros de Documentos Fiscais de Entrada → aba NFe):
**"Obriga Tipo de Operação na Geração da Nota Fiscal"**. Se estiver "Sim", o sistema pede
o tipo ao gerar a nota. Com a tabela CFOP × Tipo preenchida, ele resolve sozinho.

### Os códigos 01 / 06 / 08 são do CLIENTE, não do Teknisa

O manual de Cadastro de Tipo de Operação mostra Código e Nome **livres**, cadastrados por
Entrada/Saída, com abas Fiscal, Estoque, Financeiro e Contábil. Ou seja, `01`, `06` e `08`
são a parametrização da o cliente — têm que ser lidos de `TEKNISA.TIPOOPERAC`, não
assumidos a partir de padrão do produto.

---

## 5. "Nota simplificada" × "nota manual" — os nomes oficiais

| Termo do gestor | Nome no Teknisa | Onde |
|---|---|---|
| nota simplificada | **Lançamento Simplificado** | Guia DFE §24 |
| nota manual | **Lançamento de Nota Manual** | tela e manual próprios |

O **Lançamento Simplificado** é, passo a passo, o que o nosso robô automatiza:

```
Importação/Consulta de NF (Arquivo XML)
   -> Ações > Gerar Nota Fiscal        (nosso GERA_NOTAFISCAL_CORPO_NFE)
   -> aba Inconsistências              (nosso VERIFICA_INCONS_NF)
   -> Ações > Confirmar Recebimento    (nosso CONFIRMACAO_AUTOMATICA_NF)
   -> Lançamento de NF Simplificado > Confirmar NF
   -> Aprovar Lançamento               (alçada humana — fora do robô)
```

Parâmetros por fornecedor que sustentam a regra "só simplificada":
- **Obriga Importação de XML (NF-e) no Lançamento de Nota Fiscal**
- **Obriga informar Chave de Acesso no Lançamento de Nota Fiscal**
- Realiza consistências na Importação de XML (geração de painel)
- Bloqueia Alteração de Produto Relacionado (Importação XML)
- E-mail para aviso de relacionamento de itens pendentes

Hoje o nosso robô já exclui nota manual **por acidente arquitetural**, não por regra
escrita: a fila exige `EXISTS` em `INTG_NOTAFISCAL` (`robo/servico_robo.py`), e nota
digitada não tem linha lá. Vale transformar isso em regra explícita e nomeada.

> **Furo conhecido:** a rota `/api/simular` não tem esse `EXISTS` (removido por
> desempenho), então pode listar nota digitada como candidata do robô.

---

## 5-A. O QUE O BANCO DIZ — medido em 31/07/2026 (homologação, só leitura)

> **Conferido.** O Bloco 0 de `sql/INVESTIGACAO_ARVORE_E_OPERACAO.sql` foi rodado
> independentemente no banco e devolveu `49651 / 49651 / 0 / 0 / 82563` — exatamente os
> valores desta seção. Os números abaixo são medição, não estimativa.

Números reais:

### A árvore existe, está 100% preenchida, e não é onde procuramos

| Coluna de `TEKNISA.PRODUTO` | Preenchidas (de 49.651) |
|---|---|
| **`CDNVPRODUTO`** (nível, 1 caractere) | **49.651 — 100%** |
| **`CDARVPROD`** (código da árvore, VARCHAR2(20)) | **49.651 — 100%** |
| `CDARVPAI` | **0** |
| `CDGENERO` | **0** |
| `CDCLASSE` | 0 |

`CDARVPAI` e `CDGENERO` **estão mesmo vazios** — a descoberta nº 7 do `CLAUDE.md` estava
certa no fato. O que estava errado era a conclusão: a hierarquia **não** sumiu, ela mora em
`CDNVPRODUTO` + `CDARVPROD`, que ninguém tinha olhado.

### Os 5 níveis, com o comprimento do código

| Nível | Quantos | Tam. do código | O que é | Exemplos |
|---|---|---|---|---|
| 1 | 9 | 1 | categoria raiz | `1` ALIMENTAR, `2` DESCARTAVEL, `3` LIMPEZA |
| 2 | 57 | 3 | grupo | `101` CARNES, `102` HORTIFRUTIGRANJEIROS, `103` GENEROS |
| 3 | 464 | 5 | subgrupo | `10101` AVE, `10102` BOVINO, `10103` INDUSTRIALIZADO |
| 4 | 18.302 | 8 | folha da árvore | `10101001` |
| **5** | **30.819** | **10** | **o PRODUTO** | `1010142800` FRANGO CONG COXA C/ OSSO |

`1 + 2 + 2 + 3 + 2 = 10`. Bate exatamente com o `1.03.02.010.00` do manual.

### Dentro do nível 5, os 2 últimos dígitos decidem tudo

| Final | Quantos | Fator de conversão | É |
|---|---|---|---|
| `00` | 17.708 | **1** (17.703 dos 17.708) | **Produto de Estoque** |
| `01`, `02`, `03`… | 13.111 | **média 20,7** | **Produto de Compra** |

O `CDPRODESTO` de um produto de compra aponta para outro registro de **nível 5** (o `...00`
da mesma família) — não para o nível 4.

### >>> Para onde apontam os De/Para que já funcionam <<<

**82.563 vínculos ativos. 100% apontam para nível 5.** E dentro do nível 5:

| Destino | Vínculos | % | Fator médio | Unidades típicas |
|---|---|---|---|---|
| `...00` **Produto de Estoque** | 54.768 | **66,3%** | 1 | UN, KG, MT, CT, LT |
| `...≠00` **Produto de Compra** | 27.795 | **33,7%** | 23,7 | PC, CX, FD, LA, PT, GA, FR, BD |

**A regra real não é "sempre no produto de compra".** É:

> **O vínculo cai no produto cuja UNIDADE corresponde a como o fornecedor entrega.**
> Entrega a granel / por unidade (KG, UN, MT) → vincula no `...00`, fator 1.
> Entrega embalado (CX, FD, PC, LA) → vincula no produto de compra, com o fator.

Isso é **estruturalmente decidível**: temos `SGUNIDADE` e `VRFATOCONV` no banco e `uCom`
(unidade comercial) no XML. Hoje o matcher tenta adivinhar isso pelo **texto** da descrição
(presença de gramatura). Trocar o palpite de texto pela comparação de unidade é a melhoria
mais direta que dá para fazer.

### >>> A UNIDADE É O SINAL QUE FALTAVA — 1,64 milhão de itens <<<

`TEKNISA.ITEMPRODUTO` guarda a unidade original do XML em **`SGUNIDADEXML`**, ao lado do
produto que foi vinculado. Cruzando as duas:

| Unidade no XML | Destino | Unidade interna | Fator | Itens |
|---|---|---|---|---|
| **KG** | ESTOQUE (`00`) | KG | 1 | **909.940** |
| PC | COMPRA (`≠00`) | PC | 2,51 | 112.685 |
| UN | COMPRA | PC | 1,74 | 83.947 |
| UN | COMPRA | UN | 1,81 | 78.385 |
| UN | ESTOQUE (`00`) | UN | 1 | 56.969 |
| CX | COMPRA | CX | 81,36 | 52.782 |
| PCT | COMPRA | PC | 4,65 | 27.250 |
| FD | COMPRA | FD | 21,61 | 12.226 |

**A unidade do XML bate com a do produto interno em 76,8% dos casos:**

| Unidades | Destino | Itens | % |
|---|---|---|---|
| **iguais** | ESTOQUE (`00`) | 977.494 | **59,5%** |
| diferentes | COMPRA (`≠00`) | 329.839 | 20,1% |
| **iguais** | COMPRA (`≠00`) | 283.899 | **17,3%** |
| diferentes | ESTOQUE (`00`) | 51.230 | 3,1% |

**A regra do matcher, portanto:** _casar a unidade_. Se `SGUNIDADEXML` do item bate com
`SGUNIDADE` de um candidato, esse é o produto — seja ele estoque ou compra. Isso é sinal
forte e barato, e hoje o matcher não usa. Os 3,1% de "diferentes + estoque" são o balde
suspeito, onde provavelmente moram vínculos errados.

**Cuidado com as variações de escrita.** A mesma unidade aparece com nomes diferentes no
XML: `KILO` → KG (11.593 itens), `UND` → UN (6.271), `UN1` → PC (15.214). Qualquer
comparação de unidade precisa de uma tabela de sinônimos, senão perde esses casos.

### A quantidade NÃO é convertida no item

`QTITEMPROD / QTITEMPRODXML` = **1,0** — tanto para produto de estoque quanto de compra —
mesmo com fator de conversão médio de **14,5** nos produtos de compra.

Ou seja: `ITEMPRODUTO.QTITEMPROD` guarda a quantidade **na unidade do fornecedor**, sem
conversão. O fator é aplicado depois, na entrada de estoque. Isso é bom para nós: o robô
passa `QTITEMPROD` direto para `NF_BAIXA_AF` e está fazendo o mesmo que o sistema nativo —
não há conversão faltando no nosso lado.

### As três regras do gestor, conferidas contra o dado

**1. Produtor rural → tipo 08 — CONFIRMADA, e a mecânica apareceu.**
Existe `TEKNISA.FORNECEDOR.IDFORNPRODRURAL` (VARCHAR2(1)). Hoje: 2 fornecedores com `'S'`,
7.820 com `'N'`. As notas desses 2 são **`CDTIPOOPER='08'`**.

Os dois são **pessoa física** (`IDTPIJURFORN='F'`) e — o elo que faltava — têm
**`CDTIPOFORN='008'`**. O *tipo de fornecedor* espelha o *tipo de operação*:

| `CDTIPOFORN` | Fornecedores | Tipo de operação dominante |
|---|---|---|
| `001` | 5.355 | `01` (82,6%) — e mais 17 outros tipos |
| `002` | 11 | `18` (55,9%) e `17` (44,1%) |
| `003` | 139 | `21` (50,5%) |
| `004` | 1.134 (quase todos PF) | `45` (87,1%) |
| **`008`** | **22 (20 PF, 2 produtores rurais)** | **`08`** |

**Mas é tendência, não regra fechada.** Fornecedores `001` também geram notas `06`
(295 casos). Isso encaixa: o **mesmo** fornecedor manda venda hoje e bonificação amanhã.

> **Os dois mecanismos convivem, e é por isso que o Teknisa tem as duas telas:**
> **quem vende** define o padrão (produtor rural → 08, pelo cadastro do fornecedor) e
> **o que a nota é** sobrepõe (bonificação → 06, pelo CFOP/natureza do XML).
> Uma regra só não explica o dado.

**2. Bonificação → tipo 06 — CONFIRMADA.**
Cruzando `INTG_NOTAFISCAL.DSNATOPER` (natureza da operação que vem no XML) com o tipo:
praticamente toda nota cuja natureza cita bonificação/doação/brinde está em **`06`**
(≈190 notas; só 2 exceções em `01`).

**2-B. PROVA DIRETA, lida de dentro do XML.** Extraindo o `<CFOP>` do arquivo XML
(`ARQXMLNFE.DSARQXMLNFE`, a coluna BLOB) e cruzando com o tipo de operação da nota:

| CFOP no XML | Tipo de operação | Notas |
|---|---|---|
| **5101** | **01** | 111 |
| **5102** | **01** | 90 |
| **1102** | **08** | 20 |
| 1101 | 22 | 8 |
| 5405 | 01 | 3 |

`5101` e `5102` → `01` é **exatamente** a regra do gestor, medida na fonte.

**E o `1102 → 08` explica o produtor rural por inteiro:** produtor rural em geral não emite
NF-e. Quem emite a nota é o **comprador**, como *nota de entrada* — e por isso o CFOP no
arquivo é de entrada (`1102`), não de saída (`5xxx`). As duas coisas que pareciam regras
separadas são o mesmo fenômeno visto de dois ângulos:

- fornecedor normal → o XML é a **saída dele** (CFOP `5101`/`5102`) → tipo `01`
- produtor rural → o XML é a **nossa entrada** (CFOP `1102`) → tipo `08`

**3. Venda 5101/5102/6101/6102 → tipo 01 — CONFIRMADA.**
As naturezas de maior volume são "VENDA DE MERCADORIA ADQUIRIDA OU RECEBIDA DE TERCEIROS"
(CFOP 5102/6102) e "VENDA DE PRODUÇÃO DO ESTABELECIMENTO" (5101/6101), todas em **`01`** —
mais de 90 mil notas. Uma das linhas vem literalmente como `5101-VENDA DE PROD. ESTAB.` → `01`.

> **Detalhe que vale entender:** os CFOPs que o gestor citou (5xxx/6xxx) são de **saída** —
> é o CFOP do **fornecedor**, como vem escrito no XML. O CFOP da nossa nota de entrada é
> 1xxx/2xxx. Por isso a tela chama-se "Integração **CFOP XML** × Tipo de Operação": ela lê o
> CFOP do arquivo. No banco vê-se o par: XML `5102` → nossa nota `1102` → tipo `01`.

### O catálogo oficial (`TIPOOPERAC`) — as regras do gestor pelo nome

Com o GRANT concedido em 31/07/2026, o catálogo do cliente confirma as três regras
**pelo nome do cadastro**, sem interpretação:

| Código | Nome cadastrado (entrada) | Notas |
|---|---|---|
| **`01`** | **NF Compra (Mod 55)** | 1.486.524 |
| `18` | Compras ESNF (Mod 99) | 99.501 |
| `10` | Entrada com Ticket | 43.226 |
| `17` | NF Servico Tomado | 28.933 |
| **`40`** | **NF Compra sem AF (Mod 55)** | **12.745** |
| **`08`** | **NF Produtor Rural** | 5.843 |
| **`06`** | **NF Bonificacoes (Mod 55)** | 863 |
| `29` | NF P Rural nao Movta Estoque | — |

Existe ainda a coluna `TIPOOPERAC.IDBONIFICACAO` (e `CDTIPOOPERBONIF`), hoje `'N'` em todos
os tipos consultados.

### ⚠️ `CDTIPOOPER` NÃO é único — o código se repete entre entrada e saída

O mesmo código existe nas duas direções, com significados **opostos**:

| Código | Entrada | Saída |
|---|---|---|
| `01` | **NF Compra** (Mod 55) | **NF Venda** |
| `06` | NF Bonificações | NF Devolução Compra - Crédito |
| `08` | NF Produtor Rural | NF Outras Saídas |
| `18` | Compras ESNF | NF Descarte de Produto-crédito |
| `21` | CTR Transp Bens | NF de Brinde |

A chave real é **(`IDENTRSAIDOP`, `CDTIPOOPER`)** — coluna que existe tanto na `TIPOOPERAC`
quanto na `NOTAFISCAL`.

**Consequência medida:** `WHERE CDTIPOOPER='01'` devolve **1.486.524 notas de entrada
E 152.027 notas de saída** — 9,3% de contaminação. Os 8 pontos do projeto que filtram
`='01'` sem `IDENTRSAIDOP='E'` estão contando e listando notas de **venda** como se fossem
compra. Ver §7.1.

### `40 = NF Compra sem AF` — 12.745 notas fora do desenho do robô

O robô inteiro é ancorado em AF (`NF_BAIXA_AF`, candidatos vindos de `AUTOFORNE`/`ITEMAUTF`).
Existe um tipo de operação dedicado a **compra sem AF**, com volume relevante. Essas notas
não têm AF para casar — o fluxo atual não tem o que fazer com elas, e hoje elas nem chegam
à fila porque o filtro é `='01'`. Decidir o que fazer com elas é assunto de negócio.

### Os tipos de operação em uso — e o tamanho do problema

`TEKNISA.TIPOOPERAC` **não é acessível ao AIHUB** (ORA-00942) — falta GRANT. Mas dá para
ver os tipos efetivamente usados:

| Onde | Tipos distintos | Destaques |
|---|---|---|
| `NOTAFISCAL` | **30+** | `01` (1,64 mi) · `18` (99,5 mil) · `02` · `10` · `17` · `45` · `50` · **`08` (5.859)** · **`06` (863)** |
| `INTG_NOTAFISCAL` (XML) | 17 | `01` (452 mil) · em branco (43,5 mil) · `40` · `21` · `19` · **`06` (475)** · **`08` (1)** |

**Os tipos 06 e 08 já existem e já são usados.** Como o sistema filtra `CDTIPOOPER='01'` em
8 lugares, essas notas são invisíveis para o painel e para o robô hoje.

### ⚠️ O XML mora na coluna BLOB, não na CLOB

`TEKNISA.ARQXMLNFE` tem **duas** colunas de arquivo, e o conteúdo está na errada
para quem lê o nome mais óbvio:

| Coluna | Linhas com conteúdo (de 232.026) |
|---|---|
| `DSARQXMLNFE` (**BLOB**) | **231.585** |
| `DSARQXMLNFE_CLOB` (CLOB) | **570** |

`core/erp_connector.py:65` lê `DSARQXMLNFE_CLOB` — ou seja, enxergaria 0,25% dos XMLs.
Esse arquivo é da versão antiga (não é usado pelo painel nem pelo robô), então não é bug
ativo; mas é armadilha para quem for mexer com XML daqui em diante. `sql/VERIFICACAO_TABELAS.sql`
também confere só a coluna CLOB, o que dá falsa confiança: a coluna existe, só está vazia.

### Este banco está congelado em 23/06/2026

`MAX(DTEMISSAO)`, `MAX(DTIMPORTACAOXML)` e `MAX(DTINCLUSAO)` da `INTG_NOTAFISCAL` são todos
**23/06/2026** — confirma a descoberta nº 8 do `CLAUDE.md`. Consequência prática: a tela que
o cliente mostra com notas de julho/2026 é **produção**, não este ambiente. Fornecedores que
aparecem lá (ex.: ARAU AGRO HORTIFRUTI, DOCES CORAÇÃO DE MÃE) **não existem aqui** — é
defasagem de data, não ausência de cadastro. Não confundir os dois ao investigar.

### CFOP na nota: quase vazio neste ambiente

`NOTAFISCAL.CDCFOPINFUSU` está preenchido em **2.483 de 322.001** notas de 2025 (0,8%).
`INTG_ITEMPRODUTO` **não é legível pelo AIHUB** (sem GRANT efetivo). Ou seja: **não dá para
ler o CFOP do XML pelo banco hoje.** A `DSNATOPER` (texto da natureza da operação) é a única
pista disponível — e, como visto acima, ela correlaciona muito bem com o tipo.

---

## 6. Perguntas em aberto — fechar antes de codar

Quase tudo foi respondido pelo banco. O que sobrou está no fim.

### ✅ RESOLVIDO — o bloqueio de produto de estoque está DESLIGADO

`TEKNISA.PARAMESTOQUE.IDBLOQPRESTO = 'N'`.

Era o maior risco da investigação: se estivesse ligado, os **66,3% de De/Para que apontam
para produto de estoque (`...00`)** parariam de funcionar de uma vez. **Não vão.** O desenho
atual está seguro nesse ponto.

> Vale monitorar: é um parâmetro de uma linha só (`PARAMESTOQUE`, chave `1`). Se alguém
> ligar, quebra em massa. Vale um alerta no painel se algum dia der para ler essa tabela em
> produção.

Achado de brinde: `IDBLOQFORIRR = 'S'` — **bloqueia fornecedor irregular está LIGADO**.
É candidato a explicar parte das notas travadas que hoje classificamos como problema de
produto.

### ✅ RESOLVIDO — a tabela CFOP XML × Tipo de Operação, e o que ela diz

`TEKNISA.CFOPXMLTPOPER` está populada, com **3 linhas** (e `CFOPXMLTPOPFIL`, a versão por
filial, está **vazia** — então vale a global):

| Ent/Saí | Tipo de operação | CFOP do XML |
|---|---|---|
| E | **`06`** (NF Bonificações) | **`5910`** |
| E | **`06`** (NF Bonificações) | **`6910`** |
| E | `21` (CTR Transp Bens) | `6353` |

`5910`/`6910` são os CFOPs de **"remessa em bonificação, doação ou brinde"**. Ou seja:

> **A regra "CFOP de bonificação → tipo 06" do gestor JÁ ESTÁ CONFIGURADA no sistema.**

E isso revela a lógica da tabela: **ela é de EXCEÇÕES**. O tipo `01` (NF Compra) é o padrão;
a tabela só registra os CFOPs que devem cair em outro tipo. Por isso `5101`/`5102`/`6101`/
`6102` **não estão lá** — não precisam estar, já caem no padrão.

**Consequência para a lista do gestor:** quando ele diz "5101, 5102, 6101, 6102 → tipo 01",
ele está descrevendo o **comportamento padrão**, que já funciona. E os **`5504`/`6504`**
que ele marcou "confirmar" são o que de fato falta decidir: se venda com ST tem que cair
em `01`, e o padrão já é `01`, não há nada a fazer; se tiver que cair em outro tipo, aí sim
precisa **entrar nessa tabela**.

### ✅ RESOLVIDO — parâmetros por fornecedor (`PARAMFORNE`, 7.820 fornecedores)

| Parâmetro | Ligado (`S`) | Desligado (`N`) |
|---|---|---|
| `IDBLOQALTPRODREL` — bloqueia alterar produto relacionado | **23** | 7.797 |
| `IDNGRAVARELAC` — não grava relacionamento | **2** | 7.818 |
| `IDUTILAFXMLFORN` — usa AF do XML | **0** | **7.820** |
| `IDOBRIMPXMLNF` — obriga importar XML na NF | 1.732 | 6.088 |
| `IDREACONSIMPXML` — consiste na importação (gera painel) | 3.450 | 4.372 |

Duas leituras importantes:

1. **Podemos gravar e alterar vínculos à vontade** — o bloqueio de alteração de produto
   relacionado está ligado em só 23 dos 7.820 fornecedores. Vale respeitar esses 23.
2. **`IDUTILAFXMLFORN` está `'N'` para TODOS os 7.820 fornecedores.** Se esse for mesmo o
   parâmetro que habilita ler a AF da tag do XML, ele explica por que só **1,5%** das notas
   aparecem como `AF (tag xPed)` — ninguém tem isso ligado. Ligar para os fornecedores que
   já mandam `xPed` aumentaria a automação sem escrever uma linha de código.
   **Confirmar o significado exato com o consultor Teknisa antes de agir.**

### Ainda em aberto

1. **`5504` / `6504`** — o gestor marcou "confirmar". Só ele ou o Fiscal decidem.
2. **"Utiliza Relacionamento Produto x Produto (XML) por Unidade"** — a coluna não está em
   `PARAMFORNE`; deve estar em `PARAMETRO` ou `PARAMFILIAL`, que ainda não lemos. Se estiver
   ligado, o De/Para é por filial e a tabela certa passa a ser `PRODFORNXMLFIL`, não a
   `PRODFORNXML` global que o robô usa hoje.
3. **O que fazer com as 12.745 notas de tipo `40` (NF Compra sem AF)** — decisão de negócio.

---

## 7. Impacto direto no nosso sistema

### 7.1 `CDTIPOOPER='01'` está fixo em 8 lugares — nota 06/08 fica invisível

`interface/painel/app.py` (KPIs, /api/fila, busca por AF, /api/aprovadas, /api/simular) e
`robo/servico_robo.py` (fila de importação e fila de processamento) filtram `='01'`.

**Se o cliente passar a usar 06 (bonificação) e 08 (produtor rural) conforme a regra, essas
notas não aparecem no painel nem entram na fila do robô.** Não é que sejam tratadas
errado — elas simplesmente somem.

A documentação do projeto afirma que "o tipo nunca é fixo, é lido da própria nota". Isso é
verdade só para o parâmetro passado à procedure; a **seleção** é fixa.

### 7.2 O painel já mostra o erro que essa parametrização cura

`CONSIST["064"]` = *"Tipo de operação x CFOP — O tipo de operação não está relacionado ao
CFOP do XML"*. É exatamente o que a tela 24.3 resolve. Hoje o painel exibe o erro sem
saber apontar a cura.

### 7.3 Bonificação: premissa oposta

Hoje bonificação está no motivo de arquivamento `SEM_PEDIDO` — *"nota que nunca vira
lançamento"*. Pela regra do gestor, bonificação **é recebida**, com tipo 06. Os textos de
`MOTIVOS_ARQUIVO` e da tela precisam ser revistos.

### 7.4 Coisas do fluxo nativo que o robô não trata

- **Quantidade Recebida**: o Confirmar Recebimento nativo deixa ajustar a quantidade por
  item e, para item sem quantidade, pergunta "Zerar" ou "Quantidade Total". O robô passa
  `QTITEMPROD` direto.
- **Data de entrada**: só são confirmadas notas cuja emissão seja **anterior ou igual** à
  data de entrada informada. O robô usa a hora local como data de entrada.
- **Negar Recebimento** com motivo de devolução existe nativo — é o que o nosso "arquivar"
  tenta imitar por fora.
