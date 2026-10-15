@echo off
REM ============================================================
REM  AI Fiscal Hub - Painel Operacional  (MODO ESCRITA LIGADO)
REM  Use este atalho quando quiser que os botoes GRAVEM De/Para.
REM  O INICIAR_PAINEL.bat normal continua so leitura (mais seguro).
REM
REM  A senha e o banco ficam em CONFIG_LOCAL.bat, na RAIZ do projeto -
REM  fora deste arquivo, de proposito.
REM ============================================================
cd /d "%~dp0"

REM --- carrega senha / DSN / chave de IA do arquivo local -----
set "RAIZ=%~dp0..\.."
if not exist "%RAIZ%\CONFIG_LOCAL.bat" (
  echo.
  echo  Falta o arquivo CONFIG_LOCAL.bat com a senha e o banco.
  echo.
  echo  Como resolver: copie CONFIG_LOCAL.EXEMPLO.bat para
  echo  CONFIG_LOCAL.bat, na raiz do projeto, e preencha os valores.
  echo.
  pause
  exit /b 1
)
call "%RAIZ%\CONFIG_LOCAL.bat"

REM autoriza a gravacao (esta e a diferenca para o atalho normal)
set AIHUB_MODO_ESCRITA=1

REM --- Assistente de IA (opcional) ----------------------------
REM A chave do Google AI Studio vem do CONFIG_LOCAL.bat, carregado
REM acima. Ela NUNCA entra neste arquivo: este acompanha o codigo.
REM Sem chave, o assistente fica desligado e o sistema roda igual.

if exist "interface\painel\app.py" cd interface\painel

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe" set "PYEXE=C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe"
if not defined PYEXE ( echo Python nao encontrado. & pause & exit /b )

echo.
echo  Painel com GRAVACAO LIGADA. Abra: http://localhost:5000
echo ============================================================
echo.
"%PYEXE%" -m pip install waitress --quiet 2>nul
"%PYEXE%" app.py
pause
