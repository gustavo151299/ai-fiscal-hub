# Painel Operacional — como rodar

Interface completa do AI Fiscal Hub, conectada ao vivo no banco de homologação.
Quatro telas: **Painel**, **Fila de Notas**, **Revisão De/Para** e **Relatórios &
Auditoria**. Por padrão é **só leitura** — não grava nada no banco.

## Passo a passo (no servidor de homologação)

1. Abra o **PowerShell dentro da pasta** `interface\painel` (na barra de endereço
   do Explorer, digite `powershell` e Enter).

2. Instale as bibliotecas (uma vez):
   ```powershell
   py -m pip install flask oracledb rapidfuzz
   ```

3. Informe a conexão (mesma de sempre — usuário AIHUB):
   ```powershell
   $env:TEKNISA_DB_USER = "AIHUB"
   $env:TEKNISA_DB_PASS = "<a senha do CONFIG_LOCAL.bat>"
   $env:TEKNISA_DB_DSN  = "<o DSN do CONFIG_LOCAL.bat>"
   ```
   > A senha e o DSN ficam **só** no `CONFIG_LOCAL.bat`, na raiz do projeto — ele
   > não acompanha o código. O painel **se recusa a subir** sem os dois, para não
   > conectar no banco errado por engano.

4. Suba o painel:
   ```powershell
   py app.py
   ```

5. Abra no navegador: **http://localhost:5000**

## O que cada tela mostra

- **Painel** — indicadores do dia (integradas, pendentes, aprovadas, memória de
  De/Para), o fluxo `Integrada → Pendente → Aprovada` e a distribuição das notas.
- **Fila de Notas** — as notas de compra no fluxo, com filtro por estado e por
  fornecedor, e o estado de cada uma (badge colorido).
- **Revisão De/Para** — digite um fornecedor e veja os itens das AFs dele: quais
  já têm De/Para (o robô resolve sozinho) e quais precisam de olho humano.
- **Relatórios & Auditoria** — cobertura de De/Para por fornecedor e o histórico
  do que o robô processou (preenche conforme o robô roda).

## Gravação (opcional, desligada por padrão)

O painel nasce em **modo leitura**. Para habilitar a gravação de De/Para pela
tela (quando quiser), suba com:
```powershell
$env:AIHUB_MODO_ESCRITA = "1"
py app.py
```
Enquanto `AIHUB_MODO_ESCRITA` não for `1`, nenhuma ação grava no banco.

## Parar
`Ctrl + C` na janela do PowerShell.
