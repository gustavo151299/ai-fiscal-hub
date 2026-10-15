# Bonificação e Produtor Rural — o que o banco já respondeu

**Levantado em 31/07/2026**, direto na base. Quase tudo que eu ia perguntar para a
operação estava no banco. Sobrou **uma** pergunta de verdade, no fim.

---

## Produtor Rural — resolvido, sem pergunta

| O que se queria saber | Resposta medida |
|---|---|
| Entram no sistema? | **Sim.** 5.843 notas, 5.798 aprovadas (99%) |
| Vêm por XML? | **Não. 100% digitadas** — zero passaram pela integração |
| Quem emite a nota? | **Nós.** Confirmado por dois caminhos: nenhuma tem XML, e as que têm arquivo trazem CFOP `1102` (entrada), não `5xxx` (saída) |
| Têm AF? | **Sim.** 5.581 notas com AF no item; 24 fornecedores, todos com AF cadastrada |
| Movimenta estoque? | **Sim** (`TIPOOPERAC.IDATUESTTPOP = 'S'`) |
| Ainda acontece? | **Caindo forte:** 1.005 (2023) → 717 (2024) → 125 (2025) → 83 (2026) |

**Conclusão: ficam fora da automação, e isso não é escolha — é constatação.**

O robô trabalha sobre notas que chegaram por **XML** (a fila exige `EXISTS` em
`INTG_NOTAFISCAL`). Nota de produtor rural é **digitada à mão**, porque o produtor
pessoa física não emite NF-e — quem emite a nota de entrada somos nós. Não existe XML
para o robô ler.

Isso bate exatamente com a regra que o próprio cliente enunciou no início do projeto:
*"o sistema só pode tratar nota simplificada; notas manuais não passam por ela."*

> **Ação:** nenhuma. Vamos deixar registrado no código que o tipo 08 fica de fora
> de propósito, para ninguém estranhar a ausência depois.

---

## Tipo 29 "NF P Rural não Movimenta Estoque" — resolvido

3 notas, a última de **2017**. `IDATUESTTPOP = 'N'` — o nome do cadastro está certo,
ela realmente não movimenta estoque. **Praticamente morto.** Sem ação.

---

## Bonificação — aqui sim há uma decisão

| O que se queria saber | Resposta medida |
|---|---|
| Entram no sistema? | **Sim.** 235 notas de entrada, 231 aprovadas (98%) |
| Vêm por XML? | **Sim, a maioria: 163 por XML**, 72 digitadas |
| Têm AF? | **No item, geralmente não** (494 itens sem AF × 60 com AF)… |
| …mas o fornecedor tem AF? | **Sim: 233 das 235 notas** vêm de fornecedores que têm AF cadastrada (72 fornecedores) |
| Os itens têm produto interno? | **Sim, 554 de 554** — 237 produtos distintos |
| Movimenta estoque? | **Sim** (`IDATUESTTPOP = 'S'`) |
| Ainda acontece? | **Sim, e crescendo:** 7 (2023) → 45 (2024) → 35 (2025) → **72 só no 1º semestre de 2026** |

**Não há impedimento técnico.** As notas chegam por XML, os fornecedores têm AF (que é de
onde o robô tira os candidatos — ele busca por fornecedor, não pela AF da nota), os itens
já são vinculados a produto e a mercadoria entra no estoque igual a uma compra.

---

## A única pergunta que sobrou

> **O robô deve receber automaticamente as notas de bonificação, do mesmo jeito que
> recebe as compras normais?**
>
> ☐ Sim, trata igual a compra
> ☐ Não, quero que continuem passando por conferência humana
> ☐ Sim, mas só sugerindo o produto — a confirmação do recebimento continua manual

**Por que isso é decisão de gente e não de dado:** bonificação é mercadoria recebida
**sem cobrança**. Confirmar o recebimento automaticamente tem efeito diferente de uma
compra paga — mexe em estoque sem contrapartida financeira, e pode haver uma conferência
física ou fiscal que a equipe faz de propósito. O banco mostra que dá para automatizar;
ele não mostra se **convém**.

Se a resposta for "sim", é ajuste pequeno: incluir o tipo `06` no filtro de entrada, do
lado do `01`. O volume é baixo (~144 notas/ano no ritmo de 2026), então o ganho é modesto
— mas o incômodo de elas ficarem invisíveis também acaba.

---

*Base de homologação, congelada em 23/06/2026. Os números de 2026 cobrem meio ano.*
