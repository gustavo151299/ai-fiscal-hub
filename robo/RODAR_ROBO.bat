@echo off
REM ============================================================
REM  AI Fiscal Hub - ROBO (uma rodada). Use na Tarefa Agendada.
REM
REM  DUAS TRAVAS, as duas precisam estar ligadas para o robo agir:
REM    1. AIHUB_MODO_ESCRITA=1  (aqui embaixo)  - autoriza gravar
REM    2. "Grava sozinho" LIGADO no painel      - autoriza automatizar
REM  Faltando qualquer uma, o robo so observa e enche a fila humana.
REM
REM  Para TESTAR sem gravar: comente a linha do AIHUB_MODO_ESCRITA
REM  (ou troque a ultima linha por: "%PYEXE%" servico_robo.py --dry)
REM
REM  A senha e o banco ficam em CONFIG_LOCAL.bat, na RAIZ do projeto.
REM ============================================================
cd /d "%~dp0"

REM --- carrega senha / DSN do arquivo local -------------------
set "RAIZ=%~dp0.."
if not exist "%RAIZ%\CONFIG_LOCAL.bat" (
  echo  Falta o CONFIG_LOCAL.bat na raiz do projeto. Copie o
  echo  CONFIG_LOCAL.EXEMPLO.bat e preencha os valores.
  exit /b 1
)
call "%RAIZ%\CONFIG_LOCAL.bat"

REM autoriza a gravacao (sem isto o robo roda em simulacao)
set AIHUB_MODO_ESCRITA=1
set AIHUB_LOTE=25

if exist "robo\servico_robo.py" cd robo

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe" set "PYEXE=C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe"
if not defined PYEXE ( echo Python nao encontrado. & exit /b 1 )

"%PYEXE%" servico_robo.py
