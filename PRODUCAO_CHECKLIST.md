# Produção — o que precisa estar pronto

**Versão:** 2026-07-31-v13-correcoes
**Resposta curta:** sim, o sistema cumpre o papel em produção — **desde que os 4 itens obrigatórios abaixo sejam feitos**. Nenhum deles depende de mais programação.

---

## 1. O que o sistema vai mostrar

**Notas paradas** e **Notas com produto divergente**: por padrão, as notas do **mês vigente + mês anterior** — a regra que você definiu. Os botões permitem ir para "Este mês", "Mês anterior", "Histórico" e "Tudo" sem perder nada de vista.

Nota com mais de 2 meses **não entra** na visão padrão. Ela continua acessível em 1 clique, no Histórico.

**Caixa de entrada**: últimos 30 dias.
**Notas aprovadas**: mês vigente + anterior por padrão (busca por chave/AF/fornecedor ignora o corte).
**Fila do robô** e **Conferência**: sem corte de data — é trabalho aberto, não histórico.

---

## 2. OBRIGATÓRIO antes de ligar

### 2.1 Banco: tabelas, permissões e índices — **um script só**
```
sql\INSTALAR_PRODUCAO_COMPLETO.sql
```
Faz tudo, na ordem, e **pode rodar mais de uma vez** sem erro. As partes A e C
são de DBA; a B e a D, do usuário `AIHUB`; a E é a conferência final (tudo tem
que dar "OK").

> Prefira sempre o `INSTALAR_PRODUCAO_COMPLETO.sql` — ele cobre tudo. O
> `sql\CRIAR_ARQUIVADAS.sql` existe à parte só para quem **já instalou o resto**
> e precisa apenas criar a tabela `AIHUB_ARQUIVADA` (o "Não se aplica"): é
> pequeno e seguro de rodar de novo.

### 2.2 Permissão que mais faz falta
```sql
GRANT SELECT ON TEKNISA.HISTXMLNFE TO AIHUB;   -- sem isso, nota bloqueada
                                               -- na mão volta para a lista
```
Já está na PARTE A do script único. Se faltar alguma permissão, o painel avisa no
console (`(aviso) sem acesso a ...`) e **continua funcionando** sem aquele
recurso — não quebra a tela.

### 2.3 Índices — **este é o item de maior risco**
São 6, na PARTE C do script único (ou em `sql\CRIAR_INDICES.sql`, que agora tem
os mesmos 6). Sem eles, as consultas varrem tabelas inteiras. Medido em
homologação: indicadores 118–273 s; simulação de 5 notas 420 s. **Em produção,
com mais dados e mais gente usando ao mesmo tempo, será pior.** São criados
`ONLINE` (não travam a tabela) e os arquivos trazem os `DROP` para reverter.

> Ligado a isso: as rotas pesadas são as que estouram quando falta índice. Elas
> já devolvem a conexão ao pool mesmo quando dão erro, então um timeout derruba
> só aquela tela — não o painel inteiro.

### 2.4 Configuração local (`CONFIG_LOCAL.bat`, na raiz)
Copie o `CONFIG_LOCAL.EXEMPLO.bat` e preencha:
```
set TEKNISA_DB_DSN=<DSN de PRODUÇÃO>
set TEKNISA_DB_PASS=<senha NOVA, não a de homologação>
set AIHUB_AMBIENTE=Produção
```
Esse arquivo **não acompanha o código** — é onde o segredo mora. O
`AIHUB_MODO_ESCRITA=1` fica nos atalhos (`INICIAR_PAINEL_ESCRITA.bat` e
`RODAR_ROBO.bat`), não aqui: assim o atalho de leitura continua sendo leitura.

O painel **se recusa a subir** sem DSN e senha — proteção contra apontar para o
banco errado por engano.

---

## 3. Como ligar (em fases, não tudo de uma vez)

| Fase | O que ligar | O que observar |
|---|---|---|
| 1 | Painel em **modo consulta** (`AIHUB_MODO_ESCRITA=0`) | Os números batem com o que a área vê? |
| 2 | Modo escrita + robô ligado, **modo automático DESLIGADO** | O robô enche a fila; a pessoa aprova. Conferir por 1 semana |
| 3 | **Modo automático** ligado | Teto diário (200) e parada após 5 erros protegem. Conferir por amostragem |
| 4 | **Importação automática** | Só depois de tudo estável — **essa etapa gera nota fiscal** |

Cada fase tem volta: os toggles ficam em Configurações e desligam na hora.

---

## 4. Riscos honestos

**Sem login.** O painel sobe sem autenticação e, em modo escrita, qualquer
pessoa na rede pode ligar a automação e aprovar vínculos. **É o maior risco
aberto.** Recomendo resolver antes da Fase 2.

**Nunca rodou contra produção.** O comportamento foi validado (56 testes
automáticos, cenários simulados), mas colunas, grants e volume de produção
podem revelar diferenças. A Fase 1, em modo consulta, existe exatamente para
isso — nada é gravado.

**Procedures nativas são caixa-preta.** `NF_BAIXA_AF`, `VERIFICA_INCONS_NF` e
`CONFIRMACAO_AUTOMATICA_NF` são do Teknisa. Verificamos o retorno e relemos o
status da nota depois, mas não controlamos o que fazem por dentro.

---

## 5. O que o robô resolve — e o que não resolve

Ele decide **vínculo de produto** e **confirmação de recebimento**.

Ele **não** cria AF, não negocia preço, não parametriza imposto. Pelos códigos
oficiais do Teknisa, o trabalho se divide assim:

- **Robô**: produto sem vínculo / vinculado errado (códigos 071, 032, 014, 056)
- **Compras**: sem AF, preço, quantidade, vencimento (031, 055, 054, 050)
- **Fiscal**: imposto, CST, CFOP, inscrição (006, 008, 009, 025, 027, 052…)

O painel mostra **quem resolve** em cada nota e permite filtrar por isso. A
lista não vai a zero só com o robô — parte dela depende de Compras e do Fiscal.

---

## 6. Primeira semana — o que acompanhar

1. **Simular autonomia** (25 notas) no primeiro dia: o % que aparecer é o que
   esperar do automático.
2. **Conferência do robô**: revisar por amostragem os vínculos criados.
3. **Relatórios**: acompanhar o log do robô.
4. **Notas paradas**: o número dos 2 meses deve cair e se manter baixo. Se
   subir, é sinal de que algo na origem mudou (fornecedor novo, produto novo).
