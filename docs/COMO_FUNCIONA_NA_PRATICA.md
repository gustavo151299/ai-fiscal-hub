# AI Fiscal Hub — Como funciona na prática

## O que faz
Substitui o operador que hoje, de 2 em 2 horas, abre cada NF de entrada, acha a AF
nos dados adicionais, relaciona, resolve o "produto sem relacionamento" na mão e
libera. O robô faz isso sozinho a cada 5 minutos, usando as rotinas nativas do
Teknisa — não força aprovação em nenhuma tabela.

## O fluxo, por nota
1. Lê o XML (CLOB em `ARQXMLNFE`).
2. Acha a AF: primeiro na tag `<xPed>`, senão no texto de `<infCpl>` (trata os 6
   padrões de preenchimento). Devolve filial + número, porque a AF não é única só
   pelo número.
3. Resolve o fornecedor (CNPJ do XML → CDFORNECED).
4. Para cada item sem De/Para: compara com os itens da AF.
   - ≥ 95% de semelhança → grava o De/Para em `PRODFORNXML` e segue.
   - abaixo disso → não decide; manda card no Teams com a sugestão.
5. Vincula e valida pelas procedures nativas (`NF_BAIXA_AF`, `VERIFICA_INCONS_NF`).
6. Confirma pela `CONFIRMACAO_AUTOMATICA_NF` (aprovação automática que o Teknisa
   já tem).

## Modo sombra x autônomo
- Sombra (padrão): lê tudo, grava o De/Para e sugere, mas não confirma. Aprovação
  fica com o operador.
- Autônomo: também confirma. Só ligar depois de homologar.

## Para o teste (homologação)
Rodar numa base de teste, com uma NF real e um item de propósito sem De/Para, na
sequência: gravar De/Para → `NF_BAIXA_AF` → `VERIFICA_INCONS_NF` → se `P_ERRO` vier
vazio, `CONFIRMACAO_AUTOMATICA_NF`. Conferir se `DTAPROVACAO` preencheu e se o título
a pagar e os impostos saíram iguais ao fluxo manual. Os blocos PL/SQL prontos estão
no arquivo detalhado, se precisar.

## Ainda a definir com o DBA
Servidor onde roda, credenciais, o `CDTIPOOPER` de compra, e validar a ordem das
procedures no teste controlado antes de qualquer produção.
