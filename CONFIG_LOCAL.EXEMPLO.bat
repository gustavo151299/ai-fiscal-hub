@echo off
REM ============================================================
REM  MODELO das configuracoes locais (senha, banco, chave de IA).
REM
REM  COMO USAR:
REM    1. Copie este arquivo para  CONFIG_LOCAL.bat  (na raiz do projeto)
REM    2. Preencha os valores do SEU ambiente
REM    3. Pronto - os atalhos INICIAR_PAINEL*.bat e RODAR_ROBO.bat leem daqui
REM
REM  POR QUE ISSO EXISTE:
REM    A senha do banco estava escrita dentro de cada .bat e ate na
REM    documentacao. Quem tivesse acesso a pasta do projeto tinha o banco.
REM    Agora ela mora num arquivo SO SEU, que nao acompanha o projeto.
REM
REM  NUNCA versione o CONFIG_LOCAL.bat (ja esta no .gitignore).
REM ============================================================

REM --- Banco -------------------------------------------------
set TEKNISA_DB_USER=AIHUB
set TEKNISA_DB_PASS=
set TEKNISA_DB_DSN=

REM Exemplo de DSN (uma linha so):
REM set TEKNISA_DB_DSN=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=meu-host)(PORT=1521))(CONNECT_DATA=(SID=orcl)))

REM --- Rotulo do ambiente (so aparece na tela) ---------------
REM Deixe em branco para o painel deduzir pelo DSN.
set AIHUB_AMBIENTE=

REM --- Assistente de IA (opcional) ---------------------------
REM Chave do Google AI Studio. Em branco = add-on desligado.
set GEMINI_API_KEY=
