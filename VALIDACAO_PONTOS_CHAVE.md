# Validação com a usuária — os pontos que ela citou

Roteiro curto e prático para você conduzir a sessão em homologação, junto com a
pessoa responsável pela operação. O foco é mostrar que o sistema trata
**exatamente** os dois pontos que ela levantou.

> O que ela disse:
> *"Nos dados adicionais da nota, precisa obrigatoriamente ter número de AF para
> podermos amarrar a nota. Se não, não temos como fazer o processo, aí a nota
> fica parada na tela de importação."*

Isso vira dois casos, e o sistema tem uma resposta para cada um:

| O que ela descreveu | O que o sistema faz |
|---|---|
| Nota **tem AF** → dá pra amarrar | O robô **importa/amarra sozinho** (mesma rotina da tela de importação) |
| Nota **não tem AF** → fica parada na importação | O robô **lista essas notas** em "Notas paradas", com o motivo, pronta pra ela tratar |

---

## Antes de começar (você, sozinho)

1. Painel rodando na homologação, aberto no navegador (`http://localhost:5000`).
2. Confirme a versão: **Configurações → Sistema → Versão = 2026-07-31-v13-correcoes**.
3. Deixe a tela **Notas paradas** já aberta.

---

## Abertura (30 segundos)

Diga a ela, com as palavras dela:

> "Você me falou que a nota precisa ter o número da AF nos dados adicionais pra
> amarrar; e que sem isso ela fica parada na tela de importação. Vou te mostrar
> como o sistema trata esses dois casos — é justamente o que a gente automatizou."

---

## PONTO 1 — A nota SEM AF que fica parada (a parte que ela mais sente)

Esta é a dor principal dela: hoje ela abre nota por nota na tela de importação
pra descobrir por que travou. O sistema entrega isso pronto.

1. Abra **Notas paradas**.
2. Mostre a lista: cada nota com o **motivo do bloqueio**.
3. No filtro **Tipo de bloqueio**, escolha **"Falta AF"**. Aparecem só as notas
   travadas por não terem AF — **o caso exato que ela descreveu**.
4. **Pergunte:** *"Essas são as notas que hoje ficam paradas na sua tela de
   importação? O motivo bate com o que você vê?"* — deixe ela confirmar.
5. **Clique em cima de uma nota.** Abre o detalhe com:
   - a **chave da NF-e completa** + botão **Copiar**;
   - o **CNPJ** e o **nome do fornecedor**;
   - **todos os motivos** do bloqueio.
6. Explique: *"Em vez de abrir nota por nota pra descobrir o porquê, o sistema já
   te dá a lista pronta, com o motivo e a chave pra copiar. Aí você pede a AF ou o
   ajuste ao fornecedor, ou cancela — como você já faz hoje, só que sem garimpar."*

**O que ela valida aqui:** a triagem das notas paradas é automática e bate com a
realidade dela.

---

## PONTO 2 — A nota COM AF que precisa amarrar (a importação automática)

Este é o trabalho braçal que o robô assume: importar/amarrar as notas que **têm**
AF, usando a **mesma rotina nativa** que a tela de importação usa.

1. Abra **Configurações**.
2. Mostre a chave **"Importar automático"** (está **desligada**) e leia com ela o
   aviso de segurança na tela.
3. Explique: *"Quando ligado, o robô pega as notas que têm AF e amarra sozinho,
   no intervalo definido. Nota sem AF ele recusa com segurança e ela cai na tela
   de Notas paradas — nunca gera lançamento errado."*

**Seja honesto sobre a homologação:** aqui **não existe** nota com AF, então não
dá pra mostrar a amarração dando certo. O que já provamos em homologação é que o
robô **chama a rotina certa e recusa nota sem AF com segurança**. O teste de
"amarrou certo" é com **uma nota real com AF, em produção, com o fiscal do lado**
(passo abaixo).

---

## PONTO 3 — Achar uma nota específica (apoio no dia a dia)

1. Abra **Buscar AF / Nota**.
2. Cole a **chave** de uma nota (ou digite um **número de AF**) e clique **Buscar**.
3. Mostre que o sistema diz **onde a nota está**: na caixa de entrada, lançada, ou
   travada. Pela AF, mostra o fornecedor e os itens.

**O que ela valida aqui:** ela consegue localizar qualquer nota/AF em segundos.

---

## Fechamento — combinar o próximo passo

1. Confirme com ela: *"A lista de travadas com o motivo te ajuda? É isso que você
   faz na mão hoje?"*
2. Combine o **teste em produção**: ela escolhe **uma** nota real **com AF**; a
   gente roda a importação com o fiscal acompanhando e confere que amarrou certo.
3. Deu certo → a gente liga o **"Importar automático"** e o robô passa a amarrar as
   notas com AF sozinho; ela cuida só das travadas e da aprovação final.

---

## Resumo de uma frase (para ela levar)

> O robô **amarra sozinho as notas que têm AF** e **entrega a lista pronta das que
> não têm** (com o motivo), pra ela tratar. A aprovação final continua com ela.

---

## Checklist da sessão

- [ ] Ela confirmou que "Notas paradas" bate com o que fica parado hoje.
- [ ] Ela viu o detalhe da nota (chave completa + copiar + motivos).
- [ ] Ela entendeu o que o "Importar automático" faz e por que está desligado.
- [ ] Ela conseguiu localizar uma nota/AF na tela de Busca.
- [ ] Combinamos o teste de 1 nota com AF em produção, com o fiscal.
