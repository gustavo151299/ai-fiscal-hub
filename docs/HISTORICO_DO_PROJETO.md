# Histórico do projeto — Gestão de Recebimento de NF-e

Documento montado a partir das transcrições reais das sessões (127 mensagens suas,
de 27/07/2026 a 01/09/2026), não de memória. Onde há número, ele foi medido.

**Cliente:** um grupo de alimentacao coletiva · **Responsável:** Gustavo Dos Santos (TI/DBA)
**ERP:** Teknisa (Oracle, schema `TEKNISA`) · **Usuário do sistema:** `AIHUB`
**Nome original:** AI Fiscal Hub → renomeado a seu pedido para **Gestão de Recebimento de NF-e**

---

## 1. O problema que originou tudo

A frase da usuária da operação, que você me repassou em 27/07 e virou a espinha
dorsal do projeto:

> *"Nos dados adicionais da nota, precisa obrigatoriamente ter número de AF para
> podermos amarrar a nota. Se não, não temos como fazer o processo, aí a nota
> fica parada na tela de importação."*

O trabalho manual era: abrir nota a nota na tela de importação do Teknisa,
descobrir por que travou, vincular produto do fornecedor ao produto interno,
e confirmar o recebimento.

---

## 2. Linha do tempo

### Fase 0 — antes de 27/07 (fundação)

Antes do registro que tenho, já existiam: a validação dos 7 pontos de decisão do
robô (D1–D7) contra o banco real, o painel operacional conectado à homologação, o
matcher de produtos (`ai_matcher.py`) e a descoberta do mecanismo de importação.

Você insistiu nessa investigação quando eu disse que não havia caminho — *"você
sempre diz que não tem jeito… vamos fazer mais testes"* — e foi assim que
chegamos às procedures nativas (`GERA_NOTAFISCAL_CORPO_NFE`, `NF_BAIXA_AF`,
`VERIFICA_INCONS_NF`, `CONFIRMACAO_AUTOMATICA_NF`). **Nada foi reescrito: o robô
chama as mesmas rotinas que a tela usa.**

### Fase 1 — 27/07, tarde: usabilidade e identidade

| Hora | Seu pedido | Entrega |
|---|---|---|
| 17:07 | O que esse comando está fazendo? | Explicação |
| 17:12 | Arquivos para atualizar no servidor | Pacote |
| 17:15 | O sistema já faz sozinho o trabalho manual da usuária? | Resposta honesta: parcialmente |
| 17:22 | Filtros por AF, chave e data; **nome em português**; menus intuitivos com descrições; flags num menu de Configurações; **cores do Teknisa** | Pesquisa de padrões + reformulação completa |
| 17:42 | Clicar na nota e ver detalhes — a chave aparece cortada, quero copiar | Modal de detalhe + botão Copiar |
| 17:54 | Passo a passo para validar com a usuária em homologação | `VALIDACAO_PONTOS_CHAVE.md` |
| 17:57 | *"Se eu que criei o sistema estou com dificuldade, imagina o usuário"* | Reescrita da navegação e dos textos para leigos |
| 18:15 | Essas notas paradas ela vai arrumar dentro do Teknisa? | Esclarecimento do fluxo |
| 18:19 | **Extremamente lento**, preciso quase instantâneo | Cache, pool de conexões |
| 19:07 | Voltar produtos divergentes; **reprocesso** das notas ajustadas; **revalidação** das aprovadas; detalhe completo da nota | 3 funcionalidades novas |
| 19:28 | Validar o sistema inteiro via banco | Bateria de validação |
| 19:35 | Buscar notas com produto divergente por chave/AF/fornecedor | Busca ágil |
| 19:46 | E mostrar a sugestão nessa busca também | Sugestão integrada |
| 20:03 | As duas seções viram **submenus** de "Produtos a vincular" | Abas |
| 20:14 / 20:25 / 20:48 | Ainda lento, independente do menu | Single-flight, semáforo, paginação |
| 20:18 | Dashboard mais **profissional**; o gráfico só mostra 2 cores | Escala logarítmica + ícones distintos |
| 20:56 / 21:00 | O índice serve só pro meu sistema? Quais os riscos? | Análise de impacto |

### Fase 2 — 28/07, manhã: fila do robô e autonomia

| Hora | Seu pedido | Entrega |
|---|---|---|
| 11:31 | *"Botão de ver mais horrível e amador"*; carregar por dia/semana/mês, 50 por vez | Paginação real com Anterior/Próxima |
| 11:44 | Como funciona a parte autônoma quando vem produto divergente? | Explicação do "ensina uma vez → automatiza sempre" |
| 11:52 | Fila **separada por porcentagem**; aba de conferência do que o robô arrumou | Faixas + aba Conferência |
| 12:11 | *"Eu pedi a separação por porcentagem e você não fez"* | Refeito com destaque |
| 12:14 | As faixas como **submenus**, e me mostre antes de fazer | Mostrei o desenho, depois implementei |
| 12:26 | O sistema está completo e capaz de fazer tudo em autonomia? | Avaliação honesta: 3 travas restantes |
| 12:33 | **Vamos deixar esse índice de lado** | Acatado (voltou a doer em 31/07) |
| 12:36 | Todos os comandos de banco e instalação para produção | `INSTALACAO_PRODUCAO.sql` + guia |
| 12:59 | A conferência do robô são as notas que ele aprova sozinho? | Esclarecimento |

### Fase 3 — 28/07, tarde: o teste com IA (Gemini)

| Hora | Seu pedido | Entrega |
|---|---|---|
| 13:11 | Para que serviria uma IA aqui? | Análise de utilidade |
| 13:15 | Pesquise na web a viabilidade da API grátis rodando a cada 30 min, e o custo | Pesquisa: limites, custo ~US$5/mês, **alerta de privacidade no plano grátis** |
| 13:49 | Implementar **como teste**, podendo remover depois | `core/ai_gemini.py` isolado e removível |
| 14:01–14:43 | Passo a passo, chave da API, `.bat` correto | Guia + estrutura |
| 15:10–15:26 | Está demorando; me passe o arquivo certo | Correções em série |

Nessa fase houve quatro erros em cadeia, resolvidos um a um: tabela `AIHUB_GEMINI`
inexistente; eu afirmei **erradamente** que a chave `AQ.` era inválida (você provou
o contrário com um print, e eu corrigi); modelo `gemini-2.5-flash-lite`
descontinuado (404); e timeout de rede.

**Resultado do teste, medido:** no primeiro round a IA foi **pior** que o robô
(escolheu produto inativo, não reconheceu "moela de frango" = "frango cong moela").
Depois dos ajustes, virou o jogo: acertou tamanho de camisa (P/M/G), recusou
corretamente óleo ≠ peixe, e achou o genérico do acém. Conclusão honesta: **a IA
agrega na faixa de baixa confiança, não substitui o robô.**

### Fase 4 — 28/07, fim de tarde: o produto genérico "00"

| Hora | Seu pedido | Entrega |
|---|---|---|
| 15:35 | Dois ajustes na IA + o pedido da usuária: *"dá pra incluir o produto sem gramatura, no final 00?"* | Prompt afinado + inativos fora |
| 15:45 | Vamos achar isso via banco | Consultas de exploração |
| 16:03 | **Explorar a estrutura para não depender só da IA** — prazo até as 15h, foco total | Regra do genérico no robô |

Descoberta: `CDGENERO` e `CDARVPAI` estavam **vazios**. A hierarquia estava no
próprio código do produto (final `00` = base). Implementei distinguindo
**gramatura** (45G) de **embalagem** (5KG) — e de quebra corrigi o "óleo virou
peixe a 50%": o `0,50` virou teto, não piso.

### Fase 5 — 28/07, noite: o que o sistema chama de "erro"

| Hora | Seu pedido |
|---|---|
| 17:02 | *"Preciso saber como o sistema chegou nessa conclusão e o que ele considerou"* |
| 17:12 | *"Você ainda não me disse os critérios que você usa"* |
| 17:44 | Limitar a exibição a **mês vigente + mês anterior** (regra de negócio formal) |
| 17:49 | Nenhum informativo pode trazer notas fora desse filtro |
| 17:55 | **Checkup completo** do sistema, lógica, telas, filtros, com pesquisa na web |
| 18:03 | *"Você fez todas essas correções?"* |

Resposta que eu devia ter dado antes: **o sistema não calcula erro nenhum** — ele
lê o veredito do Teknisa em `INTG_INCONSISTENCIA` e exibe. O critério do "valor
difere" é do ERP: total do XML × soma recalculada. Provamos com número: nota de
R$ 414,58 com itens somando R$ 403,13.

O checkup encontrou **9 bloqueadores**. Quando você perguntou "você fez as
correções?", a resposta era **não** — eu só tinha diagnosticado. Você mandou
corrigir tudo, exceto login.

### Fase 6 — 31/07: a investigação longa

| Hora | Seu pedido |
|---|---|
| 15:25 | Analise tudo, rode simulações e todas as variáveis de erro |
| 15:53 | A simulação de 25 notas está muito demorada |
| 16:03 | *"Já está na versão 10, pare de teimar"* |
| 16:18 | **Validei com a usuária: no mês 6 não tem nenhuma nota pendente** |
| 17:02 | *"Pare de arrumar em picado ou tentar gambiarra pra tapar buraco"* |
| 17:18 | *"A operação tem que vir correta"* |
| 17:26 | *"Se não existe pendência nem no mês 6 nem no 5, você não está investigando de forma correta"* |

Essa foi a fase mais dura, e você estava certo em cobrar. Testei, uma a uma:
lançamento pela chave · lançamento manual por número+CNPJ · XML utilizado ·
cancelamento · denegação · recusa por manifestação · bloqueio manual. **Todas
negativas.**

Achei duas coisas verdadeiras no caminho — o **bloqueio manual** fica em
`HISTXMLNFE` com a matrícula de quem fez, e existe **desbloqueio** posterior — mas
o número não fechava.

**A causa raiz apareceu quando finalmente perguntei a coisa certa:** o banco de
homologação estava **congelado em 23/06/2026**. Última nota, último XML, última
aprovação: tudo na mesma data. As 313 de junho eram as que estavam **em andamento
no instante da cópia**; na produção elas seguiram. Eu passei o dia comparando a
produção (que a usuária vê) com uma fotografia de 23 de junho.

**Deveria ter checado a atualidade dos dados na primeira divergência.** Foi erro
meu de método. A correção virou funcionalidade: o painel agora exibe uma faixa
declarando até quando vão os dados.

### Fase 7 — 31/07 a 03/08: fechamento

| Hora | Seu pedido | Entrega |
|---|---|---|
| 15:12 | *"Não podemos ficar enchendo a pasta de coisas que não servem"* | Apaguei 19 zips obsoletos |
| 17:39 | Em produção vai trazer 2 meses e cumprir o papel? | `PRODUCAO_CHECKLIST.md` + correção do padrão para 2 meses |
| 17:55 | Quais comandos rodar no banco de produção? | `INSTALAR_PRODUCAO_COMPLETO.sql` (5 partes, idempotente) |
| 18:01 | Caminho do sistema para o modo Code | Pasta + `CLAUDE.md` |
| 18:40 | As regras de árvore 1, 2 e 3 foram consideradas? | Não soube responder — pedi o detalhe em vez de deduzir |

### Fase 8 — depois (Claude Code, v13)

O trabalho seguiu no modo Code e chegou à `v13`, com descobertas novas
documentadas no `CLAUDE.md`: a árvore de produto é `CDNVPRODUTO` + `CDARVPROD`
(5 níveis, 1–3 categorias); a **unidade** é o sinal mais forte do matcher (76,8%
de 1,64 milhão de itens); `CDTIPOOPER` não é único (precisa de `IDENTRSAIDOP`);
o XML está no BLOB; e os índices foram medidos contra o banco real
(31.000 ms → 153 ms).

---

## 3. Seus pedidos, organizados por tema

**Interface e usabilidade** — nome em português; cores do Teknisa; menus
intuitivos com descrição; detalhe da nota ao clicar; chave completa copiável;
filtros por AF, chave, fornecedor e data; paginação profissional; faixas de
confiança como submenus; dashboard mais profissional; *"que até pessoas leigas
consigam mexer"*.

**Desempenho** — cobrado 5 vezes (18:19, 20:14, 20:25, 20:48, 15:53). Foi o tema
mais recorrente do projeto.

**Automação e autonomia** — o sistema já faz o trabalho manual? como funciona
quando vem produto divergente? está completo para autonomia? por que ainda há
notas paradas? como validar sem produção?

**Confiabilidade e verdade** — quais critérios definem erro? checkup completo com
simulações; *"pare de gambiarra, ache a causa raiz"*; validação contra o que a
usuária vê.

**Regras de negócio que você definiu** — exibir mês vigente + anterior; nenhum
informativo fora desse filtro; notas com mais de 2 meses não podem acusar;
deixar o índice de lado (revisto depois); IA como teste removível; produto sem
gramatura no final `00`.

**Produção** — comandos de banco; instalação completa; o sistema vai cumprir o
papel?; caminho do projeto.

**Organização** — não encher a pasta; explicar o que cada arquivo faz e por quê.

---

## 4. Decisões suas que moldaram o sistema

| Decisão | Consequência |
|---|---|
| Índice "de lado" (28/07) | Lentidão persistiu; em 31/07 travou a simulação; medido e retomado depois |
| IA como teste removível | `ai_gemini.py` isolado — se falhar, o sistema funciona igual |
| Mês vigente + anterior | Regra formal; virou `_sql_periodo()` com 5 faixas |
| Sem login por enquanto | **Continua sendo o maior risco aberto** |
| Corrigir tudo do checkup | 9 bloqueadores fechados |

---

## 5. Descobertas que custaram caro

1. **`INTG_INCONSISTENCIA` é log, não estado** — 12.254 linhas, todas ativas, incluindo notas de 2019 já aprovadas
2. **Uma nota "segue adiante" por 6 caminhos diferentes** — verificar todos
3. **Bloqueio manual** fica em `HISTXMLNFE`, com matrícula; existe desbloqueio
4. **Classificar por código, nunca por texto** — `%RELACIONAD%` capturava motivo fiscal como se fosse produto
5. **CLOB** — `SUBSTR` sobre CLOB continua CLOB; quebrava o JSON sem código ORA
6. **Homologação congelada em 23/06** — a origem da divergência inteira
7. **Sem índice, minutos** — 864 mil linhas; `EXISTS` correlacionado é o pior caso

---

## 6. O que foi entregue

**Sistema** — painel Flask (37 rotas), interface completa num arquivo, robô
autônomo, matcher próprio, IA opcional isolada.

**Segurança** — modo consulta real; um único vínculo ativo por item; confirmação
verificada relendo o status; teto diário; parada após N erros; trava contra
rodadas simultâneas; auditoria com operador; fail-safe sempre no sentido
"desligado".

**Testes** — 5 baterias que rodam sem banco (robô, painel, tipos do Oracle,
regressões, binds SQL).

**Banco** — `INSTALAR_PRODUCAO_COMPLETO.sql` (permissões, tabelas, índices,
conferência), `CRIAR_INDICES.sql` medido contra o banco real, `CONFERENCIA_DO_MES.sql`.

**Documentação** — `CLAUDE.md`, `PRODUCAO_CHECKLIST.md`, `CHECKUP_COMPLETO.md`,
`PARECER_TECNICO.md`, e os documentos de investigação em `docs/`.

---

## 7. Pendências

1. **Login** — o painel segue sem autenticação
2. **Índices em produção** — medidos em homologação, faltam em produção
3. **Validação com dado vivo** — a simulação de autonomia só tem valor real
   contra produção ou contra uma cópia nova
