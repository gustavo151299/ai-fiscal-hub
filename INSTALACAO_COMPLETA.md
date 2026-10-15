# AI Fiscal Hub — Roteiro completo de instalação (do zero)

Siga na ordem. É o mesmo que foi feito em homologação.

> **Antes de produção, troque:**
> - a **senha** do AIHUB — use uma forte, e **não** reaproveite a de homologação
>   (ela ficou muito tempo escrita dentro dos `.bat` e da documentação, então
>   deve ser considerada conhecida);
> - o **endereço do banco** (DSN) — em produção o host/SID são outros;
> - se o schema não for `TEKNISA`, ajuste o nome nos comandos;
> - o **caminho do Python** nos `.bat` (usuário do servidor de produção).

> **Onde ficam senha e DSN:** só no `CONFIG_LOCAL.bat`, na raiz do projeto.
> Ele não acompanha o código (está no `.gitignore`). Para criar o seu, copie o
> `CONFIG_LOCAL.EXEMPLO.bat` e preencha. Os atalhos `.bat` leem daí.

---

## PARTE 1 — Banco, como DBA (uma vez)

Cria o usuário do robô e libera só o necessário.

```sql
CREATE USER AIHUB IDENTIFIED BY "TROQUE_A_SENHA"
  DEFAULT TABLESPACE USERS TEMPORARY TABLESPACE TEMP;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO AIHUB;
ALTER USER AIHUB QUOTA UNLIMITED ON USERS;

-- Leitura das tabelas que o robo/painel usam
GRANT SELECT ON TEKNISA.ARQXMLNFE          TO AIHUB;
GRANT SELECT ON TEKNISA.NOTAFISCAL         TO AIHUB;
GRANT SELECT ON TEKNISA.AUTOFORNE          TO AIHUB;
GRANT SELECT ON TEKNISA.ITEMAUTF           TO AIHUB;
GRANT SELECT ON TEKNISA.PRODUTO            TO AIHUB;
GRANT SELECT ON TEKNISA.FORNECEDOR         TO AIHUB;
GRANT SELECT ON TEKNISA.RELACXMLAF         TO AIHUB;
GRANT SELECT ON TEKNISA.PRODINCONSISTENTE  TO AIHUB;
GRANT SELECT ON TEKNISA.INTG_NOTAFISCAL    TO AIHUB;
GRANT SELECT ON TEKNISA.ITEMPRODUTO        TO AIHUB;

-- Escrita SO no De/Para (o robo aprende aqui)
GRANT SELECT, INSERT, UPDATE ON TEKNISA.PRODFORNXML TO AIHUB;

-- Executar as rotinas nativas (recebimento/validacao/confirmacao)
GRANT EXECUTE ON TEKNISA.NF_BAIXA_AF               TO AIHUB;
GRANT EXECUTE ON TEKNISA.VERIFICA_INCONS_NF        TO AIHUB;
GRANT EXECUTE ON TEKNISA.CONFIRMACAO_AUTOMATICA_NF TO AIHUB;
```

## PARTE 2 — Banco, como AIHUB (uma vez)

Cria as 3 tabelas do robô (log, fila de revisão e configuração).

```sql
-- 1) Log do que o robo processou (auditoria)
CREATE TABLE AIHUB.AIHUB_PROC_LOG (
  ID           NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  NUMERO_NF    VARCHAR2(20)  NOT NULL,
  CHAVE_NF     VARCHAR2(44),
  CNPJ_FORN    VARCHAR2(14),
  CDFORNECED   VARCHAR2(21),
  NUMERO_AF    VARCHAR2(10),
  FILIAL_AF    VARCHAR2(4),
  STATUS       VARCHAR2(40)  NOT NULL,
  DETALHE      CLOB,
  SCORE_MATCH  NUMBER(5,2),
  TEMPO_MS     NUMBER,
  DT_PROC      DATE DEFAULT SYSDATE
);

-- 2) Fila de revisao (compartilhada entre robo e painel)
CREATE TABLE AIHUB.AIHUB_PENDENCIA (
  ID            NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  CHAVE_NF      VARCHAR2(44),
  CDFILIAL      VARCHAR2(4),
  NRLANCTONF    VARCHAR2(20),
  NRSEQITPROD   VARCHAR2(10),
  CDFORNECED    VARCHAR2(21),
  NM_FORNECED   VARCHAR2(150),
  CPROD_XML     VARCHAR2(60),
  DESC_XML      VARCHAR2(300),
  CD_SUGERIDO   VARCHAR2(20),
  NM_SUGERIDO   VARCHAR2(150),
  SCORE         NUMBER(5,2),
  STATUS        VARCHAR2(12) DEFAULT 'ABERTO',
  DT_CRIACAO    DATE DEFAULT SYSDATE,
  DT_RESOLUCAO  DATE,
  CD_ESCOLHIDO  VARCHAR2(20),
  CD_OPERADOR   VARCHAR2(30)
);
CREATE INDEX AIHUB.IX_PEND_STATUS ON AIHUB.AIHUB_PENDENCIA (STATUS);

-- 3) Configuracao (o botao do painel liga/desliga o automatico aqui)
CREATE TABLE AIHUB.AIHUB_CONFIG (
  CHAVE         VARCHAR2(40) PRIMARY KEY,
  VALOR         VARCHAR2(100),
  DT_ALTERACAO  DATE DEFAULT SYSDATE
);
INSERT INTO AIHUB.AIHUB_CONFIG (CHAVE, VALOR) VALUES ('MODO_AUTO', '0');
COMMIT;
```

## PARTE 3 — Servidor: copiar o sistema e instalar o Python

1. Extraia o `ai-fiscal-hub.zip` numa pasta do servidor (ex.: `C:\ai-fiscal-hub`).
2. Instale as bibliotecas (uma vez):

```powershell
py -m pip install flask oracledb rapidfuzz SQLAlchemy
```

> Se `py` não for reconhecido, use o caminho completo do Python, ex.:
> `& "C:\Users\USUARIO\AppData\Local\Programs\Python\Python313\python.exe" -m pip install flask oracledb rapidfuzz SQLAlchemy`

## PARTE 4 — Ajustar a conexão nos atalhos

Abra e edite (botão direito → Editar) estes 3 arquivos, trocando a **senha** e o
**DSN** (host/SID de produção):

- `interface\painel\INICIAR_PAINEL.bat`
- `interface\painel\INICIAR_PAINEL_ESCRITA.bat`
- `robo\RODAR_ROBO.bat`

As linhas a ajustar em cada um:
```bat
set TEKNISA_DB_USER=AIHUB
set TEKNISA_DB_PASS=A_SENHA_DE_PRODUCAO
set TEKNISA_DB_DSN=(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=HOST_DE_PRODUCAO)(PORT=1521))(CONNECT_DATA=(SID=SID_DE_PRODUCAO)))
```

## PARTE 5 — Ligar o painel

Dois cliques em `interface\painel\INICIAR_PAINEL_ESCRITA.bat` (grava) ou
`INICIAR_PAINEL.bat` (só leitura). Abra o navegador em **http://localhost:5000**.

## PARTE 6 — Testar o robô SEM gravar (recomendado)

No PowerShell, dentro da pasta `robo`:

```powershell
$env:TEKNISA_DB_USER="AIHUB"
$env:TEKNISA_DB_PASS="A_SENHA_DE_PRODUCAO"
$env:TEKNISA_DB_DSN="(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=HOST_DE_PRODUCAO)(PORT=1521))(CONNECT_DATA=(SID=SID_DE_PRODUCAO)))"
py servico_robo.py --dry
```

`--dry` mostra quantas notas ele processaria e quantos itens iriam para a fila,
**sem gravar nem confirmar nada**. É o teste seguro.

## PARTE 7 — Agendar o robô (Tarefa Agendada do Windows)

1. Abra o **Agendador de Tarefas**.
2. **Criar Tarefa Básica** → nome “AI Fiscal Hub - Robo”.
3. Disparador: **Diariamente**; nas opções, **repetir a cada 10 minutos por 1 dia**.
4. Ação: **Iniciar um programa** → aponte para `...\ai-fiscal-hub\robo\RODAR_ROBO.bat`.
5. Marque **Executar com privilégios mais altos** e use uma conta que tenha o Python.
6. Salvar.

## PARTE 8 — Treinamento e virada de chave

- O robô começa em **“só sugere”** (`MODO_AUTO='0'`). Ele joga tudo na fila de
  revisão com o percentual; a pessoa aprova no painel.
- Quando confiar nos percentuais, ligue o **Modo automático** no botão do painel
  (rodapé do menu). A partir daí, o robô grava sozinho os itens de score alto e
  confirma o recebimento; só os duvidosos continuam indo para a fila.

---

## Comandos úteis do robô

- Uma rodada e sai (o que a Tarefa Agendada usa): `py servico_robo.py`
- Teste sem gravar: `py servico_robo.py --dry`
- Rodar continuamente (a cada `AIHUB_POLL` seg): `py servico_robo.py --loop`

Variáveis de ambiente:
- `AIHUB_LOTE=25` — notas por rodada.
- `AIHUB_POLL=300` — intervalo do `--loop` (segundos).
- `AIHUB_MODO_AUTO` — só é usado se a config `MODO_AUTO` do banco não existir
  (o botão do painel manda).
