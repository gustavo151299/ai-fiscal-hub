@echo off
REM ======================================================================
REM  CREDENCIAL DE CONSULTA DO BANCO DE PRODUCAO  (somente leitura)
REM
REM  COMO USAR: copie este arquivo para CONFIG_PRODUCAO_LEITURA.bat
REM  (sem ".EXEMPLO") e preencha. O arquivo real esta no .gitignore.
REM
REM  ATENCAO - POR QUE E UM ARQUIVO SEPARADO:
REM  O painel e o robo leem TEKNISA_DB_*. Se a credencial de producao
REM  entrar no CONFIG_LOCAL.bat, o painel e o robo passariam a apontar
REM  para PRODUCAO, e com AIHUB_MODO_ESCRITA=1 poderiam GRAVAR la.
REM  Por isso producao usa nomes PROPRIOS (AIHUB_PROD_*), que so os
REM  scripts de analise leem. Nunca renomeie para TEKNISA_DB_*.
REM ======================================================================

set AIHUB_PROD_USER=AIHUB_LEITURA
set AIHUB_PROD_PASS=coloque_a_senha_aqui
set AIHUB_PROD_DSN=servidor:1521/servico_de_producao
