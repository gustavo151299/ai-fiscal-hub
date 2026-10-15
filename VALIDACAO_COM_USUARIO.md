# Guia de Validação com o Usuário da Operação

Este guia serve para você conduzir a validação do AI Fiscal Hub junto com a
pessoa responsável pela operação fiscal (quem hoje trata as notas na mão).
Explica cada tela, o que o robô automatiza, e o passo a passo para validar com
segurança antes de ligar de vez.

---

## Resumo em uma frase

O robô faz o trabalho braçal e repetitivo do recebimento de NF-e — **importar as
notas que têm AF, reconhecer os itens (De/Para) e confirmar o recebimento** — e
deixa para a pessoa só o que exige decisão: os itens duvidosos e a aprovação
final. O painel mostra tudo em tempo real.

O fluxo completo:

```
NF-e chega da SEFAZ  →  Caixa de entrada
        ↓  (robô: chama a importação nativa)
     Importada  →  A processar
        ↓  (robô: reconhece itens + confirma recebimento)
     A aprovar
        ↓  (pessoa: aprova na alçada, tela do Teknisa)
     Aprovada
```

As notas que **não têm AF** (ou têm erro de cadastro) não importam — elas
aparecem na tela **"Notas travadas"** com o motivo, para a pessoa tratar.

---

## Parte 1 — As telas (o que cada uma mostra e faz)

**Painel** — a visão geral do dia. Mostra quantas notas estão em cada etapa
(caixa de entrada, a processar, a aprovar, aprovadas), o fluxo das 4 etapas e a
memória de De/Para do robô. É a tela de "como está a operação".

**Caixa de entrada** — as NF-e que chegaram da SEFAZ e **ainda não entraram no
sistema**. Mostra emissão, fornecedor, CNPJ, chave e status da manifestação. É o
que está por vir.

**Notas travadas** — as notas que **barraram na importação, com o motivo de cada
uma** (falta AF, valor divergente da AF, quantidade divergente, produto não é da
AF, etc.) e a ação sugerida. **Substitui o trabalho de abrir nota por nota na
tela do Teknisa para descobrir o porquê** — a pessoa já vê a lista pronta.

**Fila de Notas** — as notas de compra no fluxo (a processar → a aprovar →
aprovada), com filtro por estado e por fornecedor.

**Revisão de itens** — a fila dos itens em que o robô **não teve certeza** no
De/Para. Para cada item: o fornecedor, a descrição como veio na nota, e a
**sugestão do robô com o percentual de confiança**. A pessoa clica em **"Aprovar
sugestão"** ou **"Trocar produto"**. Cada aprovação ensina o robô.

**Relatórios & Auditoria** — a cobertura de De/Para por fornecedor e o histórico
de tudo que o robô processou (auditoria: o que fez, quando, com qual score).

**Controles do robô (rodapé do menu):**
- **Robô rodando** — liga/desliga o motor automático (ele roda dentro do painel).
- **A cada X min** — de quanto em quanto tempo o robô roda.
- **Grava sozinho** — se ligado, o robô grava o De/Para de alta confiança e
  confirma sozinho; se desligado, só sugere (modo treino).
- **Importar automático** — se ligado, o robô chama a rotina nativa que importa
  as notas com AF. **Nasce desligado** (gera nota fiscal); só ligar após validar.

---

## Parte 2 — O que o robô automatiza (e o que continua humano)

| Etapa | Quem faz |
|---|---|
| Importar a nota com AF (gera o lançamento) | **Robô** (rotina nativa do Teknisa) |
| Sinalizar as notas travadas com o motivo | **Robô** (mostra na tela) |
| Reconhecer os itens (De/Para) | **Robô** (auto no score alto; sugere o resto) |
| Confirmar o recebimento (A processar → A aprovar) | **Robô** |
| Decidir os itens duvidosos | **Pessoa**, no painel (1 clique) |
| Ajustar/pedir AF das notas travadas | **Pessoa** (com o fornecedor) |
| Aprovação final da nota (alçada) | **Pessoa**, na tela do Teknisa |

---

## Parte 3 — Validação junto ao usuário da operação (passo a passo)

Faça uma sessão com a pessoa responsável, na frente do painel:

**1. Passeie pelas telas.** Abra cada tela e pergunte: "os números batem com a
realidade que você vê hoje?". Confirme Painel, Caixa de entrada e Fila.

**2. Valide a tela "Notas travadas".** Compare a lista do painel com o que ela vê
hoje na tela de importação do Teknisa. Os motivos batem? É a triagem dela,
automática. Confirme que faz sentido.

**3. Valide as sugestões de De/Para.** Na "Revisão de itens", clique em "Gerar
exemplos" e mostre as sugestões do robô. Pergunte a ela: "esses percentuais altos
estão certos? o robô sugeriu o produto que você escolheria?". É assim que se ganha
confiança no automático.

**4. Valide a IMPORTAÇÃO (a parte mais importante e delicada).** Ver Parte 4.

**5. Combine o modo de operação inicial** (ver Parte 5).

---

## Parte 4 — Teste controlado da importação (com o fiscal do lado)

A importação usa a **mesma rotina nativa** que a tela do Teknisa usa. Antes de
ligar o automático, prove numa nota real, uma só, com o fiscal acompanhando:

1. A pessoa **escolhe uma nota real que ela sabe que tem AF** (uma que importaria
   normalmente).
2. Rode este bloco no SQL Developer (troque a chave pela da nota escolhida):

```sql
SET SERVEROUTPUT ON
DECLARE
  v_chave VARCHAR2(44) := 'COLE_AQUI_A_CHAVE_DE_44_DIGITOS';
  v_l VARCHAR2(20);
BEGIN
  TEKNISA.GERA_NOTAFISCAL_CORPO_NFE(P_NRACESSONFE => v_chave, P_GERAIGUALXML => 'N');
  SELECT MAX(NRLANCTONF) INTO v_l FROM TEKNISA.NOTAFISCAL WHERE NRACESSONFE = v_chave;
  DBMS_OUTPUT.PUT_LINE('Lancamento gerado -> '||NVL(v_l,'(nenhum)'));
  FOR r IN (SELECT DSINCONSIS FROM TEKNISA.INTG_INCONSISTENCIA
            WHERE NRACESSONFE = v_chave) LOOP
    DBMS_OUTPUT.PUT_LINE('Motivo -> '||r.DSINCONSIS);
  END LOOP;
END;
/
```

3. **Confira com o fiscal** que o lançamento gerado está correto (valores, itens,
   AF amarrada, impostos) — igual ao que a importação manual dela faria.

- Se estiver **certo** → a importação funciona; pode ligar o "Importar automático".
- Se der **algum motivo** ou vier errado → o fiscal trata como já faz hoje, e a
  gente não liga o automático até acertar.

> Importante: este teste **gera a nota de verdade** — é equivalente à pessoa
> importar aquela nota na mão. Por isso é uma nota só, com o fiscal do lado.

---

## Parte 5 — Como ligar aos poucos (rollout seguro)

Recomendo esta ordem, ganhando confiança em cada passo:

1. **Semana 1 — só observar.** Robô rodando, **"Grava sozinho" desligado** e
   **"Importar automático" desligado**. O robô só sugere; a pessoa acompanha a
   fila e as sugestões, e trata as travadas. Ninguém grava nada sozinho.

2. **Semana 2 — De/Para automático.** Depois de ver que os percentuais altos
   batem, ligue **"Grava sozinho"**. O robô passa a resolver e confirmar as notas
   de alta confiança; só os itens duvidosos vão para a fila.

3. **Semana 3 — importação automática.** Depois do teste da Parte 4 dar certo,
   ligue **"Importar automático"**. Aí o ciclo fica completo: o robô importa as
   notas com AF, reconhece, confirma; e a pessoa só cuida das travadas e da
   aprovação final.

A qualquer momento, qualquer botão pode ser desligado — e vale na rodada seguinte.
