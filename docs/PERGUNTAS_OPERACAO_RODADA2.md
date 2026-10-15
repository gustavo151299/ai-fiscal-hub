# Perguntas para a operação — rodada 2 (03/08/2026)

Formato curto, para WhatsApp. A **1 e a 2 são as que travam o trabalho**; as outras
são para não deixarmos buraco.

---

## Texto para enviar

Oi! Suas respostas ajudaram muito, obrigado. Ficaram 4 dúvidas, sendo as duas
primeiras as que estão me travando:

**1.** Você falou que o relacionamento precisa ser feito **no nível 6, do OL**.
Em qual tela você faz esse cadastro? (o nome que aparece no menu já me ajuda)

**2.** Esse relacionamento do nível 6 é entre o **produto do fornecedor e o nosso
produto** (o mesmo De/Para de sempre), ou é entre o **produto do OL e o produto da
nossa árvore normal**? São duas coisas diferentes e preciso saber qual delas.

**3.** As notas de **comodato** que chegam por XML (temos 424 aqui) — o sistema
classifica sozinho como operação 25, ou alguém precisa informar a operação na mão?

**4.** As notas de **CTe (operação 21)** e de **comodato (operação 25)** chegam pela
sua rotina, mas não são notas de compra de produto. Você quer que elas **apareçam no
painel** só para acompanhamento, ou prefere que o painel mostre só compra e
bonificação?

Se puder responder por áudio também serve. Obrigado!

---

## Por que cada pergunta

### 1 e 2 — Nível 6 / OL (bloqueio)

Ele disse: *"relacionamento precisa ser feito no nível 6 também que é do OL"*.

No banco, a árvore de produto vai **até o nível 5**. A coluna que ligaria a árvore do
Operador Logístico à árvore normal (`PRODUTO.CDPRODORIG`) está **0% preenchida**, e
nenhuma tabela de OL é legível pelo usuário do sistema.

Sabemos que o cliente **usa OL** (2.589 notas do tipo 34 "Dev NF Transf Entre Filiais
OL", a última de 2026). Então a árvore existe — só não onde conseguimos ver.

**O nome da tela é o caminho mais curto para achar a tabela.** Por isso a pergunta 1.

A pergunta 2 desfaz uma ambiguidade que muda o desenho:
- Se for **fornecedor → nosso produto**, é o De/Para que já fazemos, e o "nível 6" é só
  onde o produto está.
- Se for **produto do OL → produto da árvore normal**, é um segundo relacionamento que
  o sistema hoje **não conhece**, e aí há trabalho novo.

### 3 — Comodato

A tabela "Integração CFOP XML × Tipo de Operação" tem só 3 linhas, e **não tem os CFOPs
de comodato** (5908, 6908, 7908). Tudo que não está nela vira tipo `01 — Compra`.

Mesmo assim as 424 notas de comodato estão com o tipo 25 correto. Ou seja: **alguém está
classificando de outro jeito**, provavelmente na mão. Se deveria ser automático, é só
cadastrar as linhas naquela tela — parametrização, sem programação.

### 4 — CTe e comodato no painel

Medido: das notas que chegam pela rotina simplificada, o CTe são 5.743 e o comodato 424.
Nenhuma das duas movimenta estoque e o CTe nem tem produto para casar. O robô não vai
agir sobre elas — a pergunta é só se ele quer **enxergar** o volume.

---

## Além disso: pedido para quem tem acesso ao banco

Para achar a tabela da árvore do OL, alguém com acesso pode rodar:

```sql
SELECT owner, table_name, num_rows
FROM   all_tables
WHERE  owner = 'TEKNISA'
  AND (table_name LIKE '%OL%'     OR table_name LIKE '%OPLOG%'
    OR table_name LIKE '%LOGIST%' OR table_name LIKE '%ARVOR%')
ORDER  BY table_name;
```

Com o nome da tabela, o `GRANT SELECT` correspondente destrava o resto.
