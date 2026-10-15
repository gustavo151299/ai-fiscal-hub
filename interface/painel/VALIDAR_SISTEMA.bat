@echo off
REM ============================================================
REM  Validacao do sistema no banco de HOMOLOGACAO (so leitura)
REM  Da dois cliques. Roda as mesmas consultas do painel e
REM  confere grants, colunas e cada tela. Nao altera nada.
REM ============================================================
cd /d "%~dp0"

REM --- carrega senha / DSN do arquivo local -------------------
set "RAIZ=%~dp0..\.."
if not exist "%RAIZ%\CONFIG_LOCAL.bat" (
  echo.
  echo  Falta o arquivo CONFIG_LOCAL.bat com a senha e o banco.
  echo  Copie CONFIG_LOCAL.EXEMPLO.bat para CONFIG_LOCAL.bat na
  echo  raiz do projeto e preencha os valores.
  echo.
  pause
  exit /b 1
)
call "%RAIZ%\CONFIG_LOCAL.bat"

if exist "interface\painel\validar_sistema.py" cd interface\painel

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe" set "PYEXE=C:\Users\ti1.SRVTEKNISA\AppData\Local\Programs\Python\Python313\python.exe"
if not defined PYEXE (
  echo.
  echo  Nao encontrei o Python. Me avise para ajustarmos o caminho.
  echo.
  pause
  exit /b
)

echo.
echo  Rodando a validacao no banco de homologacao...
echo  (somente leitura - nao gera nem altera nada)
echo ============================================================
echo.
"%PYEXE%" validar_sistema.py
echo.
pause
