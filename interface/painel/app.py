"""
interface/painel/app.py - Painel operacional do AI Fiscal Hub (Flask).

Conecta AO VIVO no banco de homologacao e serve o frontend (index.html).
Endpoints JSON (somente leitura por padrao):
  /api/kpis      -> indicadores do painel
  /api/fila      -> fila de notas (I -> P -> aprovada)
  /api/revisao   -> revisao de De/Para por fornecedor (com sugestao do matcher)
  /api/relatorio -> cobertura por fornecedor + log do robo (auditoria)

Gravacao de De/Para (/api/gravar-depara) fica DESLIGADA por padrao. Para ligar,
defina a variavel de ambiente AIHUB_MODO_ESCRITA=1. Assim, nada e gravado por
acidente durante a operacao/demonstracao.

Como rodar (no servidor de homologacao):
  1) py -m pip install flask oracledb rapidfuzz
  2) defina as variaveis de conexao (usuario AIHUB, senha, DSN)
  3) py app.py
  4) abra http://localhost:5000
"""
from __future__ import annotations
import os
import re
import sys
import time
import threading
from flask import Flask, jsonify, request, send_from_directory

import oracledb

# tenta usar o matcher do proprio sistema para sugerir De/Para
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
try:
    from core.ai_matcher import melhor_match, LIMIAR_AUTO, LIMIAR_SUGESTAO
    _HAS_MATCHER = True
except Exception:
    _HAS_MATCHER = False
    LIMIAR_AUTO, LIMIAR_SUGESTAO = 0.92, 0.60

# Add-on de TESTE (removivel): assistente de IA (Gemini). Se o modulo/chave
# nao existirem, o sistema segue igual.
try:
    from core import ai_gemini
    _HAS_GEMINI = True
except Exception:
    _HAS_GEMINI = False

# --------------------------------------------------------------------------- #
# Conexao (mesmas variaveis do resto do sistema). O DSN padrao ja e o do
# homologacao AWS (SID orcl); pode sobrescrever por variavel de ambiente.
# --------------------------------------------------------------------------- #
DB_USER = os.getenv("TEKNISA_DB_USER", "AIHUB")
DB_PASS = os.getenv("TEKNISA_DB_PASS", "")
DB_DSN = os.getenv("TEKNISA_DB_DSN", "").strip()
MODO_ESCRITA = os.getenv("AIHUB_MODO_ESCRITA", "0") == "1"

# SEGURANCA DE AMBIENTE: nao existe mais DSN padrao apontando para homologacao.
# Antes, subir sem a variavel conectava silenciosamente no banco de homologacao -
# em producao isso seria um desastre silencioso. Agora o painel se recusa a subir.
if not DB_DSN or not DB_PASS:
    _falta = []
    if not DB_DSN:
        _falta.append("TEKNISA_DB_DSN")
    if not DB_PASS:
        _falta.append("TEKNISA_DB_PASS")
    print("\n" + "=" * 62)
    print(" NAO CONSIGO INICIAR: falta configurar %s" % " e ".join(_falta))
    print(" Abra o INICIAR_PAINEL_ESCRITA.bat e preencha essas linhas.")
    print(" (isso evita conectar no banco errado por engano)")
    print("=" * 62 + "\n")
    raise SystemExit(1)

# Rotulo do ambiente, so para exibir no painel (nao muda comportamento).
AMBIENTE = os.getenv("AIHUB_AMBIENTE", "").strip() or (
    "Homologação" if "homolog" in DB_DSN.lower() else "Produção")
E_HOMOLOG = AMBIENTE.lower().startswith("homolog")

# Recursos de TESTE (ex.: "Gerar exemplos", que insere pendencias ficticias na
# fila real) so existem em homologacao. Em producao ficam bloqueados no servidor
# e escondidos na tela - nao adianta alguem chamar a rota na mao.
DEMO_LIBERADA = E_HOMOLOG and os.getenv("AIHUB_DEMO", "1") == "1"

app = Flask(__name__, static_folder=None)

# Estado do motor automatico do robo (roda dentro do painel, em segundo plano).
_robo = {"ultima": None, "rodando": False, "resumo": None, "proxima_em": None,
         # pedido de rodada IMEDIATA feito pelo painel (botao "Rodar agora").
         # Sem isto so dava para ver o robo trabalhar esperando o intervalo -
         # ruim para operar e impossivel de demonstrar para alguem.
         "agora": False}

# Cache simples (guarda resultados pesados por alguns segundos -> painel rapido).
_cache = {}


# Instrumentacao: imprime no console quanto tempo cada consulta levou. Assim da
# para VER qual endpoint esta lento (o numero e em milissegundos).
@app.before_request
def _timer_inicio():
    request._t0 = time.time()


@app.after_request
def _timer_fim(resp):
    try:
        if request.path.startswith("/api/"):
            ms = (time.time() - getattr(request, "_t0", time.time())) * 1000
            marca = "  <== LENTO" if ms > 800 else ""
            print(" %7.0f ms  %s%s" % (ms, request.full_path.rstrip("?"), marca), flush=True)
    except Exception:
        pass
    return resp


# ---- Anti-tempestade -------------------------------------------------------
# 1) single-flight: se a MESMA consulta ja esta rodando, as outras esperam o
#    resultado dela em vez de rodar de novo (evita 6x /api/kpis ao mesmo tempo).
# 2) semaforo: no maximo 2 consultas PESADAS ao banco por vez, para nao ter
#    varias varreduras da tabela grande competindo (o que faz o tempo explodir).
_locks = {}
_locks_guard = threading.Lock()
_pesadas = threading.Semaphore(2)


def _key_lock(chave):
    with _locks_guard:
        lk = _locks.get(chave)
        if lk is None:
            lk = threading.Lock()
            _locks[chave] = lk
        return lk


def _cached(chave, ttl, fn):
    hit = _cache.get(chave)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    with _key_lock(chave):
        hit = _cache.get(chave)                       # re-checa apos pegar o lock
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        with _pesadas:                                # limita varreduras concorrentes
            val = fn()
        _cache[chave] = (time.time(), val)
        return val


def _cfg_get(cur, chave, padrao):
    try:
        cur.execute("SELECT VALOR FROM AIHUB.AIHUB_CONFIG WHERE CHAVE=:k", k=chave)
        r = cur.fetchone()
        return str(r[0]).strip() if r and r[0] is not None else padrao
    except Exception:
        return padrao


# --------------------------------------------------------------------------- #
# Utilitarios de ESCRITA segura. Antes, cada rota de escrita fazia commit sem
# rollback no erro e sem fechar a conexao no caminho de falha - alguns erros
# esgotavam o pool (max=12) e derrubavam o painel inteiro.
# --------------------------------------------------------------------------- #
def _rollback(con):
    try:
        if con is not None:
            con.rollback()
    except Exception:
        pass


def _fechar(cur, con):
    try:
        if cur is not None:
            cur.close()
    except Exception:
        pass
    try:
        if con is not None:
            con.close()          # devolve a conexao ao pool
    except Exception:
        pass


def _com_conexao(fn):
    """Roda fn(cur) abrindo a conexao e SEMPRE devolvendo-a ao pool, inclusive
    quando fn levanta excecao.

    Existe para as consultas pesadas que rodam dentro de _cached(): elas ficam
    numa funcao interna, e se a consulta estourasse (timeout de tabela sem
    indice, por exemplo) a conexao vazava - com pool max=12, uma dezena de
    erros derrubava o painel inteiro, nao so a tela que falhou."""
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        return fn(cur)
    finally:
        _fechar(cur, con)


# TODA coluna de operador do Teknisa tem 12 caracteres. Conferido: sao 78
# colunas CDOPER% no schema e todas com CHAR_LENGTH 12 (as tres excecoes do
# PARAMGERAL tem 2, menores ainda). Nas NOSSAS tabelas cabe mais
# (AIHUB_PENDENCIA.CD_OPERADOR tem 30), entao os dois limites sao diferentes e
# nao dao para unificar.
_MAX_OPER_TEKNISA = 12


def _operador():
    """Quem fez a acao, para as NOSSAS tabelas (ate 30 caracteres).

    Sem login ainda: usa o usuario da maquina/rede que o navegador informa, com
    fallback no IP. Assim a auditoria deixa de gravar so 'PAINEL' para todo
    mundo."""
    u = (request.headers.get("X-Operador") or "").strip()
    if not u:
        u = (request.remote_addr or "painel")
    return ("PAINEL:" + u)[:20]


def _operador_teknisa():
    """O mesmo operador, cortado para caber nas colunas do TEKNISA (12).

    POR QUE ISTO EXISTE. O _operador devolve ate 20 caracteres e o
    PRODFORNXML.CDOPERINCLUSAO aceita 12: aprovar um vinculo pelo painel
    estourava com ORA-12899 ('value too large for column') no INSERT. Passou
    despercebido porque as duas unicas gravacoes anteriores couberam por acaso
    ('PAINEL:DEMO' tem 11 e 'AIHUB_PAINEL' tem 12); com o IP no meio
    ('PAINEL:127.0.0.1' tem 16) nao cabe mais.

    Cortar 'PAINEL:127.0.0.1' em 12 daria 'PAINEL:127.0', que nao identifica
    ninguem. Preferimos o SUFIXO: fica '127.0.0.1' inteiro, ou os 12 ultimos
    caracteres do usuario de rede, que e a parte que distingue. O robo continua
    gravando 'AIHUB_ROBO', que ja cabe."""
    u = (request.headers.get("X-Operador") or "").strip()
    if not u:
        u = (request.remote_addr or "painel")
    return u[-_MAX_OPER_TEKNISA:]


def _erro_amigavel(e):
    """Mensagem que o usuario leigo entende. O detalhe tecnico vai no console
    do servidor (para a TI), nunca cru na tela.
    O CODIGO do erro (ex.: ORA-00904) vai junto entre parenteses: sem ele, a
    mensagem generica nao dava nenhuma pista de onde estava o problema."""
    txt = str(e)
    print(" ERRO:", txt[:400], flush=True)
    baixo = txt.lower()
    m = re.search(r"(ORA-\d{5}|DPY-\d{4})", txt, re.I)
    codigo = (" (código %s)" % m.group(1).upper()) if m else ""
    if "ora-00904" in baixo:
        return ("Uma coluna usada nesta consulta não existe nesta versão do "
                "banco. Avise a TI" + codigo + ".")
    if "ora-00942" in baixo:
        return ("Uma tabela do sistema não foi encontrada. Avise a TI "
                "(pode faltar permissão ou a instalação do banco).")
    if "ora-01017" in baixo or "invalid username" in baixo:
        return "Usuário ou senha do banco incorretos. Avise a TI."
    if "ora-00001" in baixo:
        return "Esse registro já existe."
    if "ora-01031" in baixo or "insufficient privileges" in baixo:
        return "Faltou permissão no banco para esta ação. Avise a TI."
    if "timeout" in baixo or "ora-12170" in baixo:
        return "O banco demorou demais para responder. Tente de novo em instantes."
    if "dpy-" in baixo or "ora-03113" in baixo or "ora-12541" in baixo:
        return "Perdi a conexão com o banco. Tente de novo; se persistir, avise a TI."
    return "Não consegui concluir agora. Tente de novo ou avise a TI." + codigo


def _gravar_vinculo(cur, cd_produto, cd_forn, cprod_xml, desc_xml, operador):
    """Grava o De/Para garantindo UM unico vinculo ativo por (fornecedor, item).
    Mesma regra usada pelo robo (robo/servico_robo.py:gravar_depara).

    A TRAVA de linha existe pelo mesmo motivo de la: duas sessoes gravando o
    MESMO item do MESMO fornecedor com produtos diferentes (o robo e uma pessoa
    aqui, por exemplo) nao enxergam o UPDATE uma da outra, e as duas inserem -
    o item fica com DOIS vinculos ativos.

    Aqui NAO usamos WAIT: quem esta do outro lado e uma pessoa esperando a tela
    responder, entao e melhor aguardar o robo terminar (que leva instantes) do
    que devolver erro. No robo e o contrario: la o WAIT 3 evita congelar a
    rodada inteira."""
    cur.execute("""SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML
                   WHERE CDFORNECED=:forn AND CDPRODUTOXML=:cx
                   FOR UPDATE""", forn=cd_forn, cx=cprod_xml)
    cur.fetchall()
    cur.execute("""
        UPDATE TEKNISA.PRODFORNXML SET IDATIVO='N'
         WHERE CDFORNECED=:forn AND CDPRODUTOXML=:cx
           AND IDATIVO='S' AND CDPRODUTO<>:cprod
    """, forn=cd_forn, cx=cprod_xml, cprod=cd_produto)
    cur.execute("""
        MERGE INTO TEKNISA.PRODFORNXML d
        USING (SELECT :cprod CDPRODUTO, :forn CDFORNECED, :cx CDPRODUTOXML FROM dual) s
           ON (d.CDPRODUTO=s.CDPRODUTO AND d.CDFORNECED=s.CDFORNECED
               AND d.CDPRODUTOXML=s.CDPRODUTOXML)
         WHEN MATCHED THEN
           UPDATE SET d.IDATIVO='S', d.NMPRODUTOXML=:nx
         WHEN NOT MATCHED THEN
           INSERT (CDPRODUTO,CDFORNECED,CDPRODUTOXML,NMPRODUTOXML,
                   NRORG,IDATIVO,DTINCLUSAO,CDOPERINCLUSAO)
           VALUES (:cprod,:forn,:cx,:nx,1,'S',SYSDATE,:op)
    """, cprod=cd_produto, forn=cd_forn, cx=cprod_xml,
         # OS CORTES SAO OS TAMANHOS REAIS DAS COLUNAS, conferidos no schema:
         # NMPRODUTOXML tem 80 (nao 240, que era o corte antigo e estourava com
         # descricao longa) e CDOPERINCLUSAO tem 12.
         nx=(desc_xml or "")[:80], op=(operador or "")[-12:])


def _cond_chave(coluna, valor, binds, nome="ch"):
    """Condição de busca por chave da NF-e SEM matar o índice.

    O curinga no COMEÇO ('%chave%') impede o banco de usar índice: ele é
    obrigado a ler a coluna inteira. Medido em homologação, contando a mesma
    nota na NOTAFISCAL:
        LIKE '%chave%'  3.958 ms   INDEX FAST FULL SCAN, custo 4.677
        LIKE 'chave%'     201 ms   INDEX RANGE SCAN,     custo 3
        = chave           143 ms   INDEX RANGE SCAN,     custo 3
    Era isso que fazia /api/buscar por chave levar 95 SEGUNDOS no validador,
    mesmo com o índice IX_NF_ACESSONFE criado: o índice existia e a consulta
    não deixava ele ser usado.

    A igualdade é segura porque a chave tem 44 dígitos e as pessoas colam ela
    inteira. Medido: das 840.851 chaves da NOTAFISCAL, 840.829 são 44 dígitos
    limpos (99,997%); INTG_NOTAFISCAL e NOTADESTINADA estão 100% limpas. As
    poucas restantes são registros defeituosos (uma tem só '0', outras têm 40
    caracteres). Quem colar apenas o começo continua achando, pelo prefixo.
    """
    v = (valor or "").strip()
    if len(v) == 44 and v.isdigit():
        binds[nome] = v
        return "%s = :%s" % (coluna, nome)
    binds[nome] = v + "%"
    return "%s LIKE :%s" % (coluna, nome)


def _auditar(cur, acao, detalhe, chave=None):
    """Trilha de auditoria: registra QUEM fez O QUE e QUANDO.

    NUMERO_NF vai com '-' e nao NULL de proposito: em algumas instalacoes a
    coluna foi criada NOT NULL (script antigo), e um INSERT com NULL falhava com
    ORA-01400 - silenciosamente, porque isto e try/except. Resultado: TODA a
    auditoria do painel (arquivar, corrigir vinculo, chave da IA) nunca gravava.
    Um '-' passa nos dois casos (coluna NULL ou NOT NULL)."""
    try:
        cur.execute("""
            INSERT INTO AIHUB.AIHUB_PROC_LOG
              (NUMERO_NF, CHAVE_NF, STATUS, DETALHE)
            VALUES ('-', :ch, :st, :det)
        """, ch=(chave or "-")[:60], st=("AUDIT_" + acao)[:30],
             det=("[%s] %s" % (_operador(), detalhe))[:3900])
    except Exception as e:
        print(" AVISO: falhou gravar auditoria:", str(e)[:120], flush=True)


def _robo_loop():
    """Motor automatico: enquanto o painel roda, dispara o robo no intervalo
    definido no painel (AIHUB_CONFIG). Liga/desliga e tempo vem da tela."""
    time.sleep(20)  # espera o painel subir
    while True:
        ligado, intervalo = False, 10
        con = cur = None
        try:
            con = _conn(); cur = con.cursor()
            ligado = _cfg_get(cur, "ROBO_LIGADO", "0") == "1"
            intervalo = int(_cfg_get(cur, "INTERVALO_MIN", "10") or 10)
        except Exception:
            pass
        finally:
            _fechar(cur, con)
        # RODAR AGORA: pedido manual feito pelo painel. Vale mesmo com o motor
        # desligado - e o caso de "quero ver uma rodada acontecer", sem deixar
        # o automatico ligado depois. Consome o pedido aqui para nao repetir.
        agora = bool(_robo.get("agora"))
        # AS OPCOES SAO DA RODADA PEDIDA, e so dela. Se ficassem no dicionario,
        # a proxima rodada AUTOMATICA herdaria o recorte de 3 notas que alguem
        # usou para testar - e o robo pararia de trabalhar sem ninguem entender.
        lim_pedido = _robo.get("limite") if agora else None
        lote_pedido = _robo.get("lote") if agora else None
        _robo["agora"] = False
        _robo["limite"] = None
        _robo["lote"] = None
        if not ligado and not agora:
            _robo["proxima_em"] = None
            time.sleep(20)
            continue
        try:
            from robo.servico_robo import rodada
            _robo["rodando"] = True
            # MODO LEITURA DE VERDADE: se o painel subiu sem AIHUB_MODO_ESCRITA=1,
            # o robo roda em simulacao (dry) e NAO grava nada. Antes ele gravava
            # mesmo com o painel "somente leitura" - falsa sensacao de seguranca.
            _robo["resumo"] = rodada(dry=not MODO_ESCRITA,
                                     limite=lim_pedido, lote=lote_pedido)
            _robo["somente_simulacao"] = not MODO_ESCRITA
            _robo["ultima"] = time.strftime("%d/%m/%Y %H:%M")
        except Exception as e:
            _robo["resumo"] = {"erro": str(e)[:200]}
        finally:
            _robo["rodando"] = False
        # rodada avulsa com o motor desligado: nao agenda a proxima
        if not ligado:
            _robo["proxima_em"] = None
            continue
        # dorme o intervalo, mas em passos curtos para reagir a mudancas
        restante = max(1, intervalo) * 60
        _robo["proxima_em"] = restante
        while restante > 0:
            time.sleep(15)
            restante -= 15
            _robo["proxima_em"] = restante
            if _robo.get("agora"):     # pediram para rodar antes da hora
                break


# --------------------------------------------------------------------------- #
# POOL de conexoes: o que deixava o painel LENTO era abrir uma conexao nova a
# cada clique (handshake de rede/TLS com o Oracle na AWS custa segundos). O pool
# mantem conexoes prontas e as reaproveita -> cada consulta fica quase instantanea.
# --------------------------------------------------------------------------- #
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = oracledb.create_pool(
                    user=DB_USER, password=DB_PASS, dsn=DB_DSN,
                    min=3, max=12, increment=2, ping_interval=60)
    return _pool


def _conn():
    """Pega uma conexao do pool (rapido). Se o pool falhar, cai para conexao
    direta para nao derrubar o painel."""
    try:
        return _get_pool().acquire()
    except Exception:
        return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)


# Conserta acentos embaralhados: no banco o texto veio como UTF-8 gravado num
# campo WE8MSWIN1252 (dupla codificacao). Uma unica transformacao resolve, de
# forma consistente. Se nao aplicar, mantem o original.
def _fix(v):
    if not isinstance(v, str) or v.isascii():
        return v
    try:
        return v.encode("cp1252").decode("utf-8")
    except (UnicodeError, LookupError):
        return v


def _valor_json(v):
    """Converte QUALQUER valor vindo do Oracle para algo que vira JSON.

    O caso que quebrava o painel: colunas CLOB (texto longo). O oracledb
    devolve um objeto LOB, e no Oracle o SUBSTR sobre CLOB continua CLOB -
    entao nem truncar resolvia. Ao serializar dava
    'TypeError: Object of type LOB is not JSON serializable', um erro de
    Python SEM codigo ORA, que virava mensagem generica sem pista nenhuma."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return _fix(v)
    if hasattr(v, "read"):                      # LOB (CLOB/BLOB)
        try:
            v = v.read()
            return _fix(v) if isinstance(v, str) else str(v)
        except Exception:
            return None
    if hasattr(v, "strftime"):                  # date / datetime
        try:
            return v.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return str(v)
    try:                                        # Decimal e afins
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
    except Exception:
        pass
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", "ignore")
        except Exception:
            return None
    return str(v)


def _rows(cur):
    cols = [c[0] for c in cur.description]
    return [{c: _valor_json(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


# ----------------------------- paginas ------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(os.path.dirname(__file__), "index.html")


@app.route("/api/config")
def config():
    return jsonify({"modo_escrita": MODO_ESCRITA, "matcher": _HAS_MATCHER,
                    "gemini_disponivel": bool(_HAS_GEMINI and ai_gemini.disponivel()),
                    "ambiente": AMBIENTE, "demo": DEMO_LIBERADA,
                    "app": "Gestao de Recebimento de NF-e",
                    "versao": "2026-08-04-v14-origem-manual"})


# --------------------------------------------------------------------------- #
# Deteccao de colunas (uma vez, com cache). Como os nomes exatos de algumas
# colunas do Teknisa variam por versao, descobrimos qual existe e usamos essa -
# assim os filtros nunca quebram por causa de um nome de coluna.
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# TIPOS DE OPERACAO DE ENTRADA que o sistema atende.
# ESPELHO de POLITICA_TIPO em robo/servico_robo.py - se um mudar, o outro tem
# que mudar junto, senao o painel mostra o que o robo nao trata (ou o contrario).
# Existe teste de regressao que compara os dois (regressao_correcoes.py, B11).
#   01 = NF Compra (Mod 55)      -> automacao completa
#   06 = NF Bonificacoes (Mod 55)-> robo so sugere produto; quem confirma e humano
# Fora de proposito: 08 (produtor rural, 100% digitada, sem XML), 29 (morto),
# 40 (compra sem AF - nao ha AF contra o que casar).
# --------------------------------------------------------------------------- #
TIPOS_ENTRADA = ("01", "06")
_SQL_TIPOS = ",".join("'%s'" % t for t in TIPOS_ENTRADA)   # constantes do codigo


def _so_simplificada(alias="N"):
    """Fragmento SQL: so notas da ROTINA SIMPLIFICADA (que vieram por XML).

    Decisao da operacao (31/07/2026): "da manual nao precisa aparecer, somente o
    que e da rotina simplificada mesmo".

    Sem isto o painel mostra tambem as notas DIGITADAS a mao, que sao a maioria:
    medido em 31/07/2026, de 1.486.524 notas de compra apenas 443.706 vieram por
    XML - as outras 1.042.818 sao manuais. Ou seja, 70% do que a tela exibia nao
    era assunto deste sistema, e ainda inflava todos os indicadores.

    E o MESMO criterio que o robo usa para montar a fila (ver buscar_notas_novas),
    entao painel e robo passam a falar da mesma populacao de notas.

    DESEMPENHO: este EXISTS depende do indice IX_INTGNF_FIL_LANC
    (CDFILIAL, NRLANCTONF) em INTG_NOTAFISCAL - esta em sql/CRIAR_INDICES.sql."""
    return (" AND EXISTS (SELECT 1 FROM TEKNISA.INTG_NOTAFISCAL G"
            " WHERE G.CDFILIAL=%s.CDFILIAL AND G.NRLANCTONF=%s.NRLANCTONF)"
            % (alias, alias))

_col_cache = {}


def _col_disponivel(cur, tabela, candidatos, cache_key):
    if cache_key in _col_cache:
        return _col_cache[cache_key]
    achou = None
    try:
        cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS "
                    "WHERE OWNER='TEKNISA' AND TABLE_NAME=:t", t=tabela)
        cols = {r[0] for r in cur.fetchall()}
        for c in candidatos:
            if c in cols:
                achou = c
                break
    except Exception:
        pass
    _col_cache[cache_key] = achou
    return achou


def _col_data_nf(cur):
    """Coluna de data da NOTAFISCAL para o filtro de periodo."""
    return _col_disponivel(cur, "NOTAFISCAL",
                           ["DTLANCTO", "DTEMISSAO", "DTENTRADA", "DTAPROVACAO"],
                           "data_nf")


def _col_af_item(cur):
    """Coluna que liga o item da NF (ITEMPRODUTO) ao numero da AF, se existir."""
    return _col_disponivel(cur, "ITEMPRODUTO",
                           ["NRAUTOFORN", "NRAUTORIZFORN", "NRAUTORIZACAO", "NRAF"],
                           "af_item")


def _col_val_item(cur):
    """Coluna de valor total do item (para o detalhe da nota)."""
    return _col_disponivel(cur, "ITEMPRODUTO",
                           ["VRTOTAITEM", "VRTOTAITEMXML", "VRBRUTITEM", "VRUNITITEM",
                            "VLITEMPROD", "VLTOTITEM", "VLTOTAL"], "val_item")


def _col_val_dest(cur):
    """Coluna de valor total da nota na NOTADESTINADA."""
    return _col_disponivel(cur, "NOTADESTINADA",
                           ["VRNOTAFISC", "VLTOTALNF", "VLTOTALNOTA", "VLNOTA"], "val_dest")


def _col_val_nf(cur):
    """Coluna de valor total da nota na NOTAFISCAL."""
    return _col_disponivel(cur, "NOTAFISCAL",
                           ["VRNOTAFISC", "VRPRODUTOS", "VLTOTALNF", "VLNOTA"], "val_nf")


def _cfg_upsert(cur, chave, valor):
    cur.execute("""
        MERGE INTO AIHUB.AIHUB_CONFIG c
        USING (SELECT :k K FROM dual) s ON (c.CHAVE = s.K)
        WHEN MATCHED THEN UPDATE SET VALOR=:v, DT_ALTERACAO=SYSDATE
        WHEN NOT MATCHED THEN INSERT (CHAVE, VALOR) VALUES (:k, :v)
    """, k=chave, v=str(valor))


@app.route("/api/modo", methods=["GET", "POST"])
def modo():
    """Modos do robo: 'auto' (grava sozinho) e 'importa' (chama a GERA nativa).
    O robo le a cada rodada; o botao vale na proxima rodada."""
    # Mudar estes modos LIGA automacao que grava no ERP -> exige modo escrita
    # e fica registrado na auditoria (quem ligou o quê e quando).
    if request.method == "POST" and not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if request.method == "POST":
            dados = request.get_json(force=True, silent=True) or {}
            for campo, chave in (("auto", "MODO_AUTO"), ("importa", "MODO_IMPORTA"),
                                 ("gemini", "USA_GEMINI")):
                if campo in dados:
                    novo = "1" if dados.get(campo) else "0"
                    _cfg_upsert(cur, chave, novo)
                    _auditar(cur, "CONFIG", "%s = %s" % (chave, novo))
            con.commit()
        auto = _cfg_get(cur, "MODO_AUTO", "0") == "1"
        importa = _cfg_get(cur, "MODO_IMPORTA", "0") == "1"
        gemini = _cfg_get(cur, "USA_GEMINI", "0") == "1"
        return jsonify({"auto": auto, "importa": importa, "gemini": gemini,
                        "gemini_disponivel": bool(_HAS_GEMINI and ai_gemini.disponivel())})
    except Exception as e:
        _rollback(con)
        return jsonify({"auto": False, "importa": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# (removida a funcao _cfg_set, que era copia identica de _cfg_upsert)


def _porque_recusou(cur, chave):
    """O QUE O TEKNISA DISSE quando recusou a nota, em vez de 'não passou nas
    regras dele'.

    Sem isto a pessoa fica adivinhando: a tela dizia so que foi recusada e
    mandava procurar em outra tela. O motivo esta em INTG_INCONSISTENCIA, e o
    CONSIST (mapa de 30 codigos) traduz o codigo para uma frase util - o texto
    cru vem com acento quebrado e as vezes generico demais.
    """
    try:
        cur.execute("""
            SELECT CDCONSISTXML, DSINCONSIS
            FROM   TEKNISA.INTG_INCONSISTENCIA
            WHERE  NRACESSONFE = :c AND IDATIVO = 'S'
            ORDER  BY CDCONSISTXML
            FETCH FIRST 8 ROWS ONLY
        """, c=chave)
        saida = []
        for (cd, ds) in cur.fetchall():
            cod = (cd or "").strip()
            # CONSIST guarda (quem_resolve, titulo, acao) - pegar info[0] punha
            # "ROBO" no lugar do motivo, que e info[1].
            info = CONSIST_N.get(_norm_cod(cod))
            cru = _fix(_valor_json(ds) or "")[:200]
            saida.append({
                "codigo": cod,
                "quem": (info[0] if info else "OUTRO"),
                "titulo": (info[1] if info else "") or cru[:120],
                "acao": (info[2] if info else ""),
                "detalhe": cru,
            })
        return saida
    except Exception:
        return []


def _abrir_pend_intg(cur, chave):
    """Poe na fila 'Falta o produto' os itens SEM De\\Para de uma nota que ainda
    esta na INTEGRACAO (ou seja, antes de importar).

    POR QUE ISTO EXISTE. A fila de revisao so era alimentada pelo robo, em
    processar_nota, que roda sobre NOTAFISCAL com IDSTATUS='I'. Pelo achado 75
    esse status praticamente nao ocorre: a nota nasce em 'L'. Consequencia
    medida na pratica: o Teknisa recusa a importacao com o codigo 014
    ("produtos do XML nao relacionados"), que e EXATAMENTE o que este sistema
    resolve, e o item nunca aparecia na tela para alguem resolver. A pessoa
    lia "recusado", ia ate a fila e encontrava a fila vazia.

    O gargalo real e na IMPORTACAO, nao na confirmacao (achado 40: 8 de 25
    casos de "nao achou AF" eram item sem produto vinculado). Entao a fila
    passa a ser alimentada de onde o trabalho existe.

    Devolve quantos itens entraram na fila. Nao faz commit: quem chama controla
    a transacao. Fail-safe: qualquer erro devolve 0 e nao interrompe a resposta
    da rota - abrir pendencia e um extra, nao pode derrubar o fluxo principal.
    """
    if not _HAS_MATCHER:
        return 0
    try:
        from core.af_matcher import itens_do_xml, traduzir_para_interno
        from core.ai_matcher import escolher
        cur.execute("""SELECT CDFILIAL, NRLANCTONF, CDFORNECED
                       FROM   TEKNISA.INTG_NOTAFISCAL
                       WHERE  NRACESSONFE = :c""", c=chave)
        r = cur.fetchone()
        if not r:
            return 0
        cd_fil, nr_lanc, forn = r[0], r[1], r[2]
        cur.execute("SELECT NMRAZSOCFORN FROM TEKNISA.FORNECEDOR WHERE CDFORNECED=:f",
                    f=forn)
        f = cur.fetchone()
        nm_forn = _fix((f[0] if f else forn) or "")[:150]

        itens = traduzir_para_interno(cur, forn, itens_do_xml(cur, chave))
        sem = [i for i in itens if not i.get("cd_interno")]
        if not sem:
            return 0
        # A AF ENCOLHE A LISTA DE CANDIDATOS, e muito. Medido em 8
        # fornecedores: de 2.502 candidatos para 77 (-96,9%), com casos de 800
        # para 2. Como o achado 44 mostrou que a ambiguidade se resolve pela
        # LISTA e nao pelo score, sugerir a partir dos produtos da propria AF
        # e o que faz a sugestao ficar util em vez de generica.
        #
        # A AF vem de onde ja estiver: da nota (alguem vinculou) ou, quando nao
        # houver, do que o fornecedor escreveu no XML.
        cur.execute("""SELECT MAX(NRAUTOFORN) FROM TEKNISA.INTG_ITEMPRODUTO
                       WHERE NRACESSONFE=:c AND NRAUTOFORN IS NOT NULL""",
                    c=chave)
        r = cur.fetchone()
        af_nota = r[0] if r else None
        cands = _cands_af(cur, forn, af_nota, cd_fil)
        n = 0
        for it in sem:
            # nao duplica: o mesmo item pode passar por aqui varias vezes,
            # porque a pessoa costuma tentar importar mais de uma vez
            cur.execute("""SELECT COUNT(*) FROM AIHUB.AIHUB_PENDENCIA
                           WHERE CHAVE_NF=:c AND NRSEQITPROD=:s AND STATUS='ABERTO'""",
                        c=chave, s=it["seq"])
            if cur.fetchone()[0]:
                continue
            desc = _fix(it.get("desc_xml") or "")
            best, _sug = escolher(desc, cands, it.get("un")) if cands else (None, None)
            cur.execute("""
                INSERT INTO AIHUB.AIHUB_PENDENCIA
                  (CHAVE_NF,CDFILIAL,NRLANCTONF,NRSEQITPROD,CDFORNECED,NM_FORNECED,
                   CPROD_XML,DESC_XML,CD_SUGERIDO,NM_SUGERIDO,SCORE,STATUS)
                VALUES (:chave,:f,:n,:s,:forn,:nmf,:cx,:dx,:cds,:nms,:sc,'ABERTO')
            """, chave=chave, f=cd_fil, n=nr_lanc, s=it["seq"], forn=forn,
                 nmf=nm_forn, cx=it.get("cprod_xml"), dx=desc[:300],
                 cds=(best[0] if best else None),
                 nms=(_fix(best[1] or "")[:150] if best else None),
                 sc=(round(best[2] * 100, 2) if best else 0))
            n += 1
        return n
    except Exception:
        return 0


def _recusa_de_produto(chave, st, cur=None):
    """A recusa que NOS detectamos antes de chamar a nativa (status
    SEM_PRODUTO). Montada aqui, e nao lida da INTG_INCONSISTENCIA, porque a
    procedure nem chegou a rodar: nao existe linha nova la, e a linha VELHA fica
    ativa para sempre (achado 1), entao ela mentiria sobre o estado de hoje.

    DIZ QUAIS ITENS FALTAM, e isso nao e detalhe. Caso real: nota de 4 itens em
    que a pessoa vinculou um, voltou para importar e ouviu de novo 'produto sem
    vinculo'. Ela achou que o vinculo nao tinha pego - mas faltava OUTRO item.
    Sem a lista, a mensagem certa parece um defeito."""
    if st != "SEM_PRODUTO":
        return []
    faltam = []
    if cur is not None:
        try:
            from robo.servico_robo import itens_sem_depara
            faltam = itens_sem_depara(cur, chave)
        except Exception:
            faltam = []
    if faltam:
        quais = "; ".join("item %s: %s" % (s, _fix(_valor_json(n) or ""))
                          for s, _c, n in faltam[:6])
        acao = ("Falta%s %d item(ns): %s. Vincule em 'Falta o produto' e "
                "importe em seguida." % ("m" if len(faltam) > 1 else "",
                                         len(faltam), quais))
    else:
        acao = ("Vincule o produto em 'Falta o produto' e importe em seguida. "
                "O sistema conferiu antes de chamar o Teknisa, para você não "
                "esperar a validação inteira.")
    return [{"codigo": "014", "quem": "ROBO",
             "titulo": "Produto sem vínculo (ou marca diferente)",
             "acao": acao,
             "itens": [{"seq": s, "desc": _fix(_valor_json(n) or "")}
                       for s, _c, n in faltam],
             "detalhe": ""}]


def _abrir_ocorrencias(con, cur, chave, recusa):
    """Registra cada apontamento do Teknisa como OCORRENCIA, para nada sumir.

    POR QUE ISTO EXISTE. O motivo da recusa aparecia num modal: a pessoa lia,
    fechava, e acabava. Nao ficava registro, ninguem sabia quantas notas
    estavam paradas por preco, nem se alguem tinha ido atras.

    Caso real (NF 9667827): a recusa trouxe SETE apontamentos de uma vez - um
    051 de vencimento, dois 055 de preco e quatro 056 de produto fora da AF.
    Tres areas envolvidas, e tudo isso morria ao fechar a janela.

    Uma linha por apontamento, com QUEM RESOLVE. So sai da fila quando alguem
    disser que resolveu.

    NAO DUPLICA: o indice unico e sobre (chave, codigo, detalhe) das ABERTAS,
    entao clicar 'Importar' dez vezes nao gera setenta linhas. O detalhe entra
    na chave porque o mesmo codigo aparece varias vezes na mesma nota com
    textos diferentes - os quatro 056 eram produtos distintos, e cada um e um
    problema separado.

    Commit proprio: a ocorrencia tem de sobreviver mesmo que o resto falhe.
    Fail-safe: erro aqui nao derruba a resposta da rota - registrar e um
    extra, e nao pode custar a operacao."""
    if not (MODO_ESCRITA and chave and recusa):
        return 0
    try:
        cur.execute("""SELECT G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE,
                              F.NMRAZSOCFORN
                       FROM   TEKNISA.INTG_NOTAFISCAL G
                       LEFT   JOIN TEKNISA.FORNECEDOR F
                              ON F.CDFORNECED = G.CDFORNECED
                       WHERE  G.NRACESSONFE = :c""", c=chave)
        r = cur.fetchone() or (None, None, None, None)
        nrnf, forn, filial, nmforn = r[0], r[1], r[2], _fix(_valor_json(r[3]))
        n = 0
        for o in recusa:
            try:
                cur.execute("""
                    INSERT INTO AIHUB.AIHUB_OCORRENCIA
                      (CHAVE_NF, NUMERO_NF, CDFORNECED, NM_FORNECED, CDFILIAL,
                       CODIGO, QUEM, TITULO, ACAO, DETALHE)
                    VALUES (:ch, :nf, :fo, :nm, :fi, :cd, :qm, :ti, :ac, :de)
                """, ch=chave[:44], nf=(nrnf or "")[:20], fo=(forn or "")[:21],
                     nm=(nmforn or "")[:150], fi=str(filial or "")[:10],
                     cd=(o.get("codigo") or "?")[:10],
                     qm=(o.get("quem") or "OUTRO")[:10],
                     ti=(o.get("titulo") or "")[:150],
                     ac=(o.get("acao") or "")[:400],
                     de=(o.get("detalhe") or "")[:400])
                n += 1
            except Exception:
                # ORA-00001: ja existe uma ABERTA igual. E o comportamento
                # desejado, nao erro - segue para o proximo apontamento.
                pass
        if n:
            con.commit()
        return n
    except Exception:
        _rollback(con)
        return 0


def _fechar_ocorrencias(cur, chave):
    """Fecha as ocorrencias de uma nota que finalmente entrou.

    Sem isto a fila so cresceria: a nota importa e os apontamentos antigos
    continuariam ali, fazendo alguem ir atras de problema ja resolvido."""
    try:
        cur.execute("""UPDATE AIHUB.AIHUB_OCORRENCIA
                       SET    STATUS='RESOLVIDO', DT_RESOLUCAO=SYSDATE,
                              CD_OPERADOR='(importou)'
                       WHERE  CHAVE_NF=:c AND STATUS='ABERTO'""", c=chave)
        return cur.rowcount
    except Exception:
        return 0


def _fila_produto(con, cur, chave, recusa, st=None):
    """Se a recusa for de PRODUTO (o que este sistema resolve), poe os itens na
    fila e devolve quantos. Commit proprio: a pendencia tem de sobreviver mesmo
    que o resto da resposta falhe depois.

    Exige MODO_ESCRITA como todas as rotas que gravam - AIHUB_PENDENCIA e
    tabela nossa, mas "modo consulta" significa que NADA muda."""
    if not MODO_ESCRITA:
        return 0
    if st != "SEM_PRODUTO" and \
       not any((r.get("quem") == "ROBO") for r in (recusa or [])):
        return 0
    try:
        n = _abrir_pend_intg(cur, chave)
        if n:
            con.commit()
        return n
    except Exception:
        _rollback(con)
        return 0


def _msg_import(st):
    """Traduz o status do importar_uma para uma frase em portugues.
    As mesmas explicacoes que /api/reprocessar ja dava, agora reaproveitadas."""
    return {
        "IMPORTADA": "Nota importada.",
        "MODO_CONSULTA": ("A importação foi recusada porque o modo escrita está "
                          "desligado para a rotina de importação. Confira o "
                          "AIHUB_MODO_ESCRITA no atalho que inicia o painel."),
        "AF_VENCIDA": ("A AF que está nesta nota não está mais em aberto com "
                       "saldo (outra entrega já a consumiu). Importar agora "
                       "baixaria um pedido já atendido."),
        "BLOQUEADA": ("O Teknisa recusou a importação: a nota não passou nas "
                      "regras dele. Veja o motivo em Notas paradas."),
        "SEM_PRODUTO": ("Esta nota tem item sem produto vinculado, e o Teknisa "
                        "sempre recusa nesse caso (código 014). Vincule o "
                        "produto em 'Falta o produto' e importe em seguida."),
        "ERRO": "Não consegui importar agora. Tente de novo em instantes.",
    }.get(st, "Não foi possível importar (%s)." % st)


@app.route("/api/robo-atividade")
def robo_atividade():
    """As ultimas coisas que o robo fez, em linguagem de gente.

    POR QUE ISTO EXISTE: o painel mostrava numeros do dia, mas nao mostrava o
    robo TRABALHANDO. Quem abre a tela precisa ver, linha a linha, o que ele
    acabou de fazer - e' o que responde "ele esta funcionando?" sem depender de
    ninguem explicar. Le so a NOSSA tabela de trilha, nada do Teknisa.
    """
    lim = min(50, max(1, request.args.get("limite", default=8, type=int)))
    # traducao dos status internos para o que a pessoa entende
    FRASE = {
        "AF_VINCULO_ROBO":   ("vinculou a AF",              "ok"),
        "CONFIRMADA":        ("concluiu, foi para aprovação", "ok"),
        "IMPORTADA":         ("importou a nota",            "ok"),
        "PENDENTE_HUMANO":   ("parou: produto não reconhecido", "warn"),
        "PENDENTE_CONVERSAO":("parou: precisa conferir a unidade", "warn"),
        "AF_VENCIDA":        ("parou: a AF venceu",         "warn"),
        "ERRO_CONFIRMA":     ("erro ao confirmar",          "err"),
        "MODO_CONSULTA":     ("não gravou: modo consulta",  "warn"),
    }
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM (
                -- DETALHE E CLOB (achado 5), e ler CLOB custa uma IDA AO
              -- BANCO POR LINHA: medido, 24 linhas levaram 4.164 ms contra
              -- 472 ms com TO_CHAR - mesmo conteudo, 8,8x. Cuidado: SUBSTR
              -- sobre CLOB CONTINUA CLOB, entao envolver em SUBSTR nao
              -- resolve; quem converte e o TO_CHAR. O limite de 1300 nao
              -- corta nada real (o maior DETALHE da base tem 211 caracteres)
              -- e mantem a conversao abaixo do teto de 4000 BYTES do Oracle.
              SELECT DT_PROC, STATUS, NUMERO_NF, CHAVE_NF,
                     TO_CHAR(SUBSTR(DETALHE,1,1300)) DETALHE
              FROM   AIHUB.AIHUB_PROC_LOG
              WHERE  STATUS NOT LIKE 'AUDIT%'
              ORDER  BY DT_PROC DESC
            ) WHERE ROWNUM <= :n
        """, n=lim)
        linhas = []
        for (dt, st, nf, chave, det) in cur.fetchall():
            frase, tom = FRASE.get((st or "").strip(), ((st or "").lower(), "info"))
            det = _valor_json(det) or ""
            # a AF citada no detalhe entra na frase, que e o que interessa ver
            m = re.search(r"AF\s+([0-9]{6,})", det or "")
            if m and tom == "ok" and "AF" in frase:
                frase = "%s %s" % (frase, m.group(1))
            linhas.append({
                "hora": dt.strftime("%H:%M") if dt else "",
                "data": dt.strftime("%d/%m") if dt else "",
                "nota": (nf or "").lstrip("0") or None,
                "chave": chave,
                "frase": frase,
                "tom": tom,
            })
        return jsonify({"itens": linhas})
    except Exception as e:
        return jsonify({"itens": [], "msg": _erro_amigavel(e)}), 200
    finally:
        _fechar(cur, con)


@app.route("/api/ensaio")
def ensaio_robo():
    """O ROBÔ DECIDINDO, NOTA A NOTA, SEM GRAVAR NADA.

    Existe para responder "o sistema funciona?" olhando, em vez de confiando.
    Pega a MESMA fila que o robô pegaria (`buscar_para_importar`) e chama as
    MESMAS funções de decisão que ele chama, em modo simulação: o que aparece
    aqui é o raciocínio dele, não uma reconstrução.

    Isso é deliberado. A tela "Simular" que existia ensaia a etapa de
    CONFIRMAÇÃO (`IDSTATUS='I'`), estado com 4 notas em toda a base e nenhuma no
    último ano (achado 75): ela ensaiava o que o robô não faz. E, por
    reimplementar a regra por fora, podia divergir dele sem ninguém notar.

    Não grava: é GET, não exige MODO_ESCRITA e não chama procedure nativa.
    """
    limite = min(20, max(1, request.args.get("limite", default=5, type=int)))
    con = cur = None
    t0 = time.time()
    try:
        from robo.servico_robo import (buscar_para_importar, itens_sem_depara,
                                       resolver_depara_do_xml)
        from core.af_matcher import (resolver, af_ainda_valida,
                                     AUTOMATICA, AMBIGUA)
        con = _conn()
        cur = con.cursor()
        # A BASE ESTA ATUAL? Homologacao e uma copia congelada (achado 8), e o
        # ensaio corria o risco de virar conclusao errada: as 5 notas do ensaio
        # de 11/08/2026 apareciam "pendentes" aqui e ja estavam RESOLVIDAS em
        # producao desde 24 e 25/06 - a copia foi tirada em 23/06, antes de a
        # equipe trata-las. Sem este aviso, quem olha conclui que a operacao
        # tem pendencia que ela nao tem.
        #
        # REAPROVEITA O CACHE DE /api/atualidade (1 hora). Fazer a conta aqui
        # custava um MAX(DTEMISSAO) sobre a INTG_NOTAFISCAL inteira a cada
        # ensaio, sem indice: medido, o ensaio de 3 notas subiu de 16s para
        # 66s. O numero muda no maximo uma vez por dia.
        _at = _cache.get("atualidade")
        atual = _at[1] if (_at and (time.time() - _at[0]) < 3600) else {}
        # a fila devolve (chave, pode_importar): 'pode_importar' e False na
        # nota fresca, alcancada pelo CFOP, que o robo vincula mas ainda nao
        # importa. O ensaio precisa dizer isso, senao promete o que nao ocorre.
        fila = buscar_para_importar(cur, limite)
        notas = []
        for ch, pode_importar in fila:
            cur.execute("""SELECT G.NRNOTAFISC, G.CDFORNECED, F.NMRAZSOCFORN,
                                  G.CDFILIRECE, G.DTEMISSAO, G.VRNOTAFISC
                           FROM   TEKNISA.INTG_NOTAFISCAL G
                           LEFT   JOIN TEKNISA.FORNECEDOR F
                                  ON F.CDFORNECED = G.CDFORNECED
                           WHERE  G.NRACESSONFE = :c""", c=ch)
            r = cur.fetchone() or (None,) * 6
            n = {"chave": ch,
                 "numero": (r[0] or "").lstrip("0") if r[0] else "",
                 "cd_fornecedor": r[1],
                 "fornecedor": _valor_json(r[2]) or r[1],
                 "filial": r[3],
                 "emissao": r[4].strftime("%d/%m/%Y") if r[4] else None,
                 "valor": float(r[5]) if r[5] is not None else None}

            # ETAPA 1 - De/Para. Roda a FUNÇÃO DO ROBÔ em dry, e ela devolve o
            # que decidiu item a item.
            passos = []
            n["depara"] = passos
            criou = faltando = 0
            try:
                criou, faltando = resolver_depara_do_xml(con, ch, True, passos)
            except Exception as e:
                n["erro_depara"] = str(e)[:200]
            n["depara_criaria"] = criou
            n["depara_humano"] = faltando
            # os que já tinham vínculo antes de tudo
            n["itens_sem_vinculo_hoje"] = len(itens_sem_depara(cur, ch))

            # ETAPA 2 - AF. Estado ATUAL: em dry o De/Para da etapa 1 não foi
            # gravado, então quando ainda falta produto esta resposta é
            # provisória, e a tela precisa dizer isso em vez de fingir certeza.
            try:
                d = resolver(cur, ch)
            except Exception as e:
                d = {"decisao": "erro", "motivo": str(e)[:200], "opcoes": []}
            n["af_decisao"] = d.get("decisao")
            n["af_motivo"] = d.get("motivo")
            n["af_numero"] = (d.get("af") or {}).get("af")
            n["af_opcoes"] = len(d.get("opcoes") or [])
            # A NOTA QUE JÁ TEM AF não é "nenhuma AF": ela tem, e alguém já
            # relacionou. A tela dizia "Nenhuma AF escolhida" bem em cima de
            # "A AF 0001445341 já está relacionada". O número vem do DADO
            # (a integração), não de ler o texto do motivo.
            n["af_ja_estava"] = False
            if not n["af_numero"] and "já está relacionada" in (d.get("motivo") or ""):
                cur.execute("""SELECT DISTINCT NRAUTOFORN
                               FROM   TEKNISA.INTG_ITEMPRODUTO
                               WHERE  NRACESSONFE = :c
                                 AND  NRAUTOFORN IS NOT NULL""", c=ch)
                achadas = [a[0] for a in cur.fetchall()]
                if achadas:
                    n["af_numero"] = ", ".join(achadas)
                    n["af_ja_estava"] = True
            n["af_plano"] = [{"seq": p.get("seq"), "af": p.get("af"),
                              "desc": p.get("desc")}
                             for p in (d.get("plano") or [])]

            # ETAPA 3 - o que a importação faria. As mesmas duas travas que o
            # `importar_uma` confere antes de chamar a nativa.
            try:
                viva, mortas = af_ainda_valida(cur, ch)
            except Exception:
                viva, mortas = False, []
            n["af_viva"] = bool(viva)
            n["afs_mortas"] = list(mortas or [])
            # QUEM LEVOU O PEDIDO. Medido em produção: quando a AF está sem
            # saldo, em 100% dos casos outra nota já lançada a consumiu, quase
            # sempre do mesmo fornecedor e com 1 ou 2 dias de diferença. Dizer
            # só "AF vencida" deixa a pessoa sem saber o que fazer.
            n["af_levada_por"] = None
            if not viva:
                from core.af_matcher import quem_baixou_af
                for _af in (mortas or []):
                    _nf, _dt = quem_baixou_af(cur, _af)
                    if _nf:
                        n["af_levada_por"] = {"af": _af,
                                              "nota": str(_nf).lstrip("0"),
                                              "quando": _dt}
                        break

            # ETAPA 4 - O QUE O TEKNISA JÁ DISSE SOBRE ESTA NOTA.
            #
            # Faltava, e era a falha mais séria do ensaio: ele conferia só o
            # que NÓS controlamos (produto, AF, AF viva) e dava "importaria
            # sozinho" numa nota que o Teknisa já tinha recusado por preço fora
            # da AF e vencimento no passado. A tela "Falta a AF" já mostrava
            # esses apontamentos na conferência prévia; o ensaio não. Duas
            # telas nossas discordando sobre a mesma nota.
            #
            # Cuidado de leitura (achado 1): INTG_INCONSISTENCIA é LOG, a linha
            # nunca é limpa. Em nota NÃO importada ela é o motivo da última
            # recusa, e não prova que ainda vale - por isso o texto da tela diz
            # "última tentativa", não "vai barrar".
            aponta = []
            try:
                cur.execute("""SELECT TRIM(LEADING '0' FROM CDCONSISTXML), DSINCONSIS
                               FROM   TEKNISA.INTG_INCONSISTENCIA
                               WHERE  NRACESSONFE = :c AND IDATIVO = 'S'
                               ORDER  BY CDCONSISTXML""", c=ch)
                for cod, txt in cur.fetchall():
                    info = CONSIST_N.get(_norm_cod(cod))
                    aponta.append({
                        "codigo": cod,
                        "quem": info[0] if info else "OUTRO",
                        "titulo": info[1] if info else "Apontamento do Teknisa",
                        "acao": info[2] if info else "",
                        "texto": _valor_json(txt) or ""})
            except Exception as e:
                n["erro_apontamentos"] = str(e)[:200]
            n["apontamentos"] = aponta

            # VEREDITO. Honesto sobre o que dá e o que não dá para saber sem
            # gravar: com item sem De/Para, a AF só se decide depois do vínculo.
            # Os casos vêm separados porque significam trabalho DIFERENTE, e
            # jogar todos em "sem AF" foi o que a primeira versão fez: nota
            # parcialmente vinculada aparecia como se não tivesse AF nenhuma,
            # quando na verdade tem AF demais (achado 28).
            mot = d.get("motivo") or ""
            if faltando:
                n["veredito"] = "HUMANO_PRODUTO"
            elif criou:
                n["veredito"] = "DEPARA_DEPOIS_AF"
            elif d.get("decisao") == AUTOMATICA or "já está relacionada" in mot:
                if not viva:
                    n["veredito"] = "AF_VENCIDA"
                elif aponta:
                    # o nosso lado está pronto, mas o Teknisa já recusou
                    n["veredito"] = "APONTAMENTO"
                else:
                    n["veredito"] = "IMPORTARIA"
            elif "parcialmente vinculada" in mot:
                n["veredito"] = "PARCIAL"
            elif d.get("decisao") == AMBIGUA:
                n["veredito"] = "HUMANO_AF"
            elif "filial" in mot and "não informa" in mot:
                n["veredito"] = "SEM_FILIAL"
            elif "data de emissão" in mot:
                n["veredito"] = "SEM_DATA"
            else:
                n["veredito"] = "SEM_AF"
            notas.append(n)
        return jsonify({"notas": notas, "limite": limite,
                        "modo_escrita": MODO_ESCRITA,
                        "dados_ate": atual.get("data_max"),
                        "atraso_dias": atual.get("dias"),
                        "ms": int((time.time() - t0) * 1000)})
    except Exception as e:
        return jsonify({"notas": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/conferir-rodada")
def conferir_rodada():
    """A PROVA: o que o TEKNISA diz sobre as notas que o robô tocou.

    A trilha do robô é a palavra dele. Isto lê o banco do ERP de volta, nota a
    nota, e responde com número: a AF ficou gravada em quantos itens, a nota
    virou lançamento, e a baixa da AF apareceu na NFRELACAF. Se um passo não
    aconteceu, aparece aqui mesmo que o log diga que deu certo.
    """
    horas = min(720, max(1, request.args.get("horas", default=24, type=int)))
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if not _tem_tabela(cur, "AIHUB.AIHUB_PROC_LOG", "trilha"):
            return jsonify({"notas": [], "msg": "A trilha do robô ainda não foi "
                                                "criada neste banco."})
        # as notas que o robô tocou na janela pedida.
        # DETALHE FICA FORA DO GROUP BY: a coluna é CLOB, e MAX() sobre CLOB
        # devolve ORA-00932 (a mesma família de armadilha do achado 5). Ele é
        # lido depois, por nota, no laço que já existe.
        cur.execute("""SELECT CHAVE_NF, MAX(DT_PROC)
                       FROM   AIHUB.AIHUB_PROC_LOG
                       WHERE  DT_PROC >= SYSDATE - (:h/24)
                         AND  STATUS NOT LIKE 'AUDIT%'
                       GROUP  BY CHAVE_NF
                       ORDER  BY MAX(DT_PROC) DESC
                       FETCH  FIRST 50 ROWS ONLY""", h=horas)
        alvos = cur.fetchall()
        notas = []
        for chave, quando in alvos:
            cur.execute("""SELECT TO_CHAR(SUBSTR(DETALHE,1,1300))
                           FROM   AIHUB.AIHUB_PROC_LOG
                           WHERE  CHAVE_NF = :c AND STATUS = 'AF_VINCULO_ROBO'
                           ORDER  BY DT_PROC DESC
                           FETCH  FIRST 1 ROWS ONLY""", c=chave)
            _d = cur.fetchone()
            detalhe = _valor_json(_d[0]) if _d else ""
            # 1) a AF ficou gravada na integração?
            cur.execute("""SELECT COUNT(*), COUNT(I.NRAUTOFORN),
                                  COUNT(DISTINCT I.NRAUTOFORN)
                           FROM   TEKNISA.INTG_ITEMPRODUTO I
                           WHERE  I.NRACESSONFE = :c""", c=chave)
            itens, com_af, afs = cur.fetchone()
            # 2) a nota virou lançamento?
            cur.execute("""SELECT G.NRNOTAFISC, G.CDFILIAL, G.NRLANCTONF,
                                  G.DTUTILIZAXML, F.NMRAZSOCFORN
                           FROM   TEKNISA.INTG_NOTAFISCAL G
                           LEFT   JOIN TEKNISA.FORNECEDOR F
                                  ON F.CDFORNECED = G.CDFORNECED
                           WHERE  G.NRACESSONFE = :c""", c=chave)
            g = cur.fetchone()
            numero = (g[0] or "").lstrip("0") if g and g[0] else ""
            filial, lanc = (g[1], g[2]) if g else (None, None)
            # 3) existe mesmo na NOTAFISCAL (a nota fiscal de verdade)?
            cur.execute("""SELECT CDFILIAL, NRLANCTONF, IDSTATUS
                           FROM   TEKNISA.NOTAFISCAL
                           WHERE  NRACESSONFE = :c AND IDENTRSAIDOP='E'""", c=chave)
            nf = cur.fetchone()
            # 4) a AF foi baixada? (a amarração real, item a item)
            baixas = 0
            if nf:
                cur.execute("""SELECT COUNT(*) FROM TEKNISA.NFRELACAF
                               WHERE CDFILIAL=:f AND NRLANCTONF=:l""",
                            f=nf[0], l=nf[1])
                baixas = cur.fetchone()[0]
            passos = [
                {"ok": bool(com_af), "passo": "AF gravada na integração",
                 "achado": "%d de %d item(ns), %d AF(s)" % (com_af, itens, afs)
                           if itens else "a nota não tem itens"},
                {"ok": bool(nf), "passo": "Nota criada no Teknisa",
                 "achado": ("lançamento %s na filial %s, status %s"
                            % (nf[1], nf[0], nf[2])) if nf
                           else "ainda não virou lançamento"},
                {"ok": bool(baixas), "passo": "AF baixada (NFRELACAF)",
                 "achado": ("%d linha(s) de baixa" % baixas) if baixas
                           else ("sem baixa" if nf else "depende da importação")},
            ]
            notas.append({
                "chave": chave, "numero": numero,
                "fornecedor": _valor_json(g[4]) if g else None,
                "filial": filial, "lancamento": lanc,
                "quando": quando.strftime("%d/%m/%Y %H:%M") if quando else "",
                "af_do_robo": detalhe or "",
                "passos": passos,
                "tudo_ok": all(p["ok"] for p in passos)})
        return jsonify({"notas": notas, "horas": horas})
    except Exception as e:
        return jsonify({"notas": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/robo", methods=["GET", "POST"])
def robo():
    """Controle do motor automatico (ligado/desligado e intervalo em minutos)."""
    if request.method == "POST" and not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if request.method == "POST":
            dados = request.get_json(force=True, silent=True) or {}
            if "ligado" in dados:
                novo = "1" if dados.get("ligado") else "0"
                _cfg_upsert(cur, "ROBO_LIGADO", novo)
                _auditar(cur, "CONFIG", "ROBO_LIGADO = %s" % novo)
            if "intervalo" in dados:
                iv = max(1, int(dados.get("intervalo") or 10))
                _cfg_upsert(cur, "INTERVALO_MIN", iv)
                _auditar(cur, "CONFIG", "INTERVALO_MIN = %s" % iv)
            # RODAR AGORA: dispara UMA rodada imediata, sem mexer no liga/desliga.
            # Passa pelo mesmo MODO_ESCRITA exigido no topo da rota, e o robo
            # ainda respeita as travas dele (MODO_AUTO, teto diario, lock).
            if dados.get("agora"):
                if _robo.get("rodando"):
                    return jsonify({"ok": False, "ligado": None,
                                    "msg": "Já existe uma rodada em curso. "
                                           "Aguarde ela terminar."}), 409
                # VALIDAR EM PEDACOS. Quem confere o sistema pela primeira vez
                # nao quer soltar o robo na fila inteira: escolhe 3 notas, ou o
                # lote das 11h, confere, e so entao continua. O teto e 200 para
                # nao virar caminho de contornar o AIHUB_LOTE por acidente.
                qtd = dados.get("quantidade")
                _robo["limite"] = (max(1, min(200, int(qtd))) if qtd else None)
                _robo["lote"] = (dados.get("lote") or "").strip() or None
                _robo["agora"] = True
                _auditar(cur, "CONFIG",
                         "RODAR_AGORA pedido pelo painel (%s%s)"
                         % ("%s nota(s)" % _robo["limite"] if _robo["limite"]
                            else "lote padrao",
                            ", lote de chegada %s" % _robo["lote"]
                            if _robo["lote"] else ""))
            con.commit()
        ligado = _cfg_get(cur, "ROBO_LIGADO", "0") == "1"
        intervalo = int(_cfg_get(cur, "INTERVALO_MIN", "10") or 10)
        return jsonify({"ligado": ligado, "intervalo": intervalo,
                        "rodando": _robo["rodando"], "ultima": _robo["ultima"],
                        "resumo": _robo["resumo"],
                        "proxima_em": _robo["proxima_em"],
                        "modo_escrita": MODO_ESCRITA,
                        "somente_simulacao": _robo.get("somente_simulacao", not MODO_ESCRITA)})
    except Exception as e:
        _rollback(con)
        return jsonify({"ligado": False, "intervalo": 10, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# --------------------------------------------------------------------------- #
# ATUALIDADE DOS DADOS
#
# Descoberto em 28/07/2026, depois de um dia inteiro de divergencia com a area:
# o banco de HOMOLOGACAO e uma copia congelada em 23/06/2026. As notas que o
# painel mostrava como "paradas" eram as que estavam EM ANDAMENTO no instante
# da copia - na producao elas ja tinham sido resolvidas.
# Ninguem tinha como perceber isso olhando a tela. Agora o painel diz, no topo,
# ate quando os dados vao. Numero de painel sem data de referencia engana.
# --------------------------------------------------------------------------- #
@app.route("/api/atualidade")
def atualidade():
    def _calc():
        con = _conn()
        cur = con.cursor()
        try:
            def _max(sql):
                try:
                    cur.execute(sql)
                    r = cur.fetchone()
                    return r[0] if r and r[0] else None
                except Exception:
                    return None
            datas = [d for d in [
                _max("SELECT MAX(DTEMISSAO) FROM TEKNISA.NOTADESTINADA"),
                _max("SELECT MAX(DTEMISSAO) FROM TEKNISA.INTG_NOTAFISCAL"),
                _max("SELECT MAX(DTAPROVACAO) FROM TEKNISA.NOTAFISCAL"),
            ] if d]
            if not datas:
                return {"data_max": None, "dias": None}
            ult = max(datas)
            cur.execute("SELECT TRUNC(SYSDATE) - TRUNC(:d) FROM dual", d=ult)
            dias = int(cur.fetchone()[0] or 0)
            return {"data_max": ult.strftime("%d/%m/%Y"), "dias": dias,
                    "ambiente": AMBIENTE}
        finally:
            _fechar(cur, con)

    try:
        # cache longo: isso muda no maximo uma vez por dia
        return jsonify(_cached("atualidade", 3600, _calc))
    except Exception as e:
        return jsonify({"data_max": None, "dias": None,
                        "msg": _erro_amigavel(e)}), 200


# ----------------------------- KPIs ---------------------------------------- #
# --------------------------------------------------------------------------- #
# INDICADORES PESADOS: cache proprio + recalculo em segundo plano.
#
# Sao os dois que faziam o dashboard levar 334 s. Cada um cruza tabelas grandes
# por EXISTS correlacionado e nao tem indice que resolva. Como mudam pouco ao
# longo do dia, a tela passa a ler o ultimo valor conhecido e a disparar o
# recalculo por tras, em vez de esperar.
#
# 'None' significa "ainda nao calculei", e a TELA MOSTRA ISSO como calculando -
# nunca como zero. Zero seria mentira e faria a pessoa concluir que nao ha
# trabalho quando so nao deu tempo de contar.
# --------------------------------------------------------------------------- #
_PESADO_TTL = 900          # 15 min: o numero muda pouco ao longo do dia
_pesado_rodando = set()    # evita disparar dois recalculos da mesma coisa

def _pesado_em_background(chave, fn):
    """Devolve o valor em cache (ou None) e agenda o recalculo se estiver velho."""
    hit = _cache.get(chave)
    fresco = bool(hit) and (time.time() - hit[0]) < _PESADO_TTL
    if not fresco and chave not in _pesado_rodando:
        _pesado_rodando.add(chave)

        def _bg():
            con = None
            try:
                con = _conn()
                _cache[chave] = (time.time(), fn(con.cursor()))
                print(" indicador '%s' recalculado." % chave, flush=True)
            except Exception as e:
                print(" (aviso) indicador '%s' falhou: %s" % (chave, str(e)[:150]),
                      flush=True)
            finally:
                if con is not None:
                    try: con.close()
                    except Exception: pass
                _pesado_rodando.discard(chave)

        threading.Thread(target=_bg, daemon=True).start()
    return hit[1] if hit else None


def _kpi_pesado_status():
    """Contagem por situacao. Medido: 78,4 s por causa do EXISTS do XML."""
    def calc(cur):
        cur.execute("""
            SELECT IDSTATUS, COUNT(*) QT
            FROM   TEKNISA.NOTAFISCAL N
            WHERE  N.IDENTRSAIDOP = 'E' AND N.CDTIPOOPER IN (""" + _SQL_TIPOS + """)
        """ + _so_simplificada("N") + """
            GROUP  BY IDSTATUS
        """)
        return {(r["IDSTATUS"] or "?"): int(r["QT"]) for r in _rows(cur)}
    return _pesado_em_background("kpi:status", calc) or {}


def _kpi_pesado_paradas(cur_atual, sql_base):
    """Notas paradas nas tres faixas. Medido: 71,1 s."""
    def calc(cur):
        out = {}
        for faixa in ("recentes", "atrasadas", "historico"):
            try:
                cur.execute(sql_base + _sql_periodo("I", faixa))
                out[faixa] = int(cur.fetchone()[0])
            except Exception:
                out[faixa] = None
        return out
    v = _pesado_em_background("kpi:paradas", calc)
    return v or {"recentes": None, "atrasadas": None, "historico": None}


@app.route("/api/kpis")
def kpis():
    hit = _cache.get("kpis")
    if hit and (time.time() - hit[0]) < 300:
        return jsonify(hit[1])
    lk = _key_lock("kpis"); lk.acquire(); _pesadas.acquire()
    con = cur = None
    try:
        hit = _cache.get("kpis")               # single-flight: outra thread ja calculou?
        if hit and (time.time() - hit[0]) < 300:
            return jsonify(hit[1])
        con = _conn()
        cur = con.cursor()

        # distribuicao de status das NF de compra (mesmo agregado ja validado)
        #
        # IDENTRSAIDOP='E' NAO E OPCIONAL. O CDTIPOOPER se repete entre entrada e
        # saida com significados OPOSTOS: na TIPOOPERAC, '01' e "NF Compra" na
        # entrada e "NF Venda" na saida. Filtrar so por '01' trazia 152.027 notas
        # de VENDA junto com as 1.486.524 de compra - 9,3% de contaminacao nos
        # indicadores. A chave real e (IDENTRSAIDOP, CDTIPOOPER).
        # OS DOIS NUMEROS PESADOS NAO PODEM SEGURAR A TELA.
        #
        # Medido em homologacao (06/08/2026), consulta a consulta:
        #    status das notas (com o EXISTS do XML) .... 78,4 s
        #    notas paradas ............................ 71,1 s
        #    todo o resto somado ...................... menos de 5 s
        # Os dois cruzam tabelas grandes por EXISTS correlacionado
        # (NOTAFISCAL 1,97 mi x INTG_NOTAFISCAL 520 mil, e a INTG_INCONSISTENCIA
        # contra NOTAFISCAL e NOTADESTINADA). Nao ha indice que resolva: e o
        # cruzamento em si.
        #
        # Como esses dois mudam pouco ao longo do dia, eles passam a ter cache
        # PROPRIO de 15 min e a ser recalculados EM SEGUNDO PLANO. Quem abre a
        # tela recebe na hora tudo o que e barato; os dois pesados chegam
        # preenchidos na proxima leitura, e ate la a tela mostra "calculando"
        # em vez de zero. E o mesmo mecanismo que a simulacao ja usava.
        status = _kpi_pesado_status()

        # De/Para (memoria do robo)
        cur.execute("""
            SELECT COUNT(*) N, COUNT(DISTINCT CDFORNECED) F
            FROM   TEKNISA.PRODFORNXML WHERE IDATIVO='S'
        """)
        d = cur.fetchone()
        depara_total, forn_total = int(d[0]), int(d[1])

        # HONESTIDADE NOS INDICADORES: antes, falha de consulta virava "0" na
        # tela - o usuario concluia que nao havia trabalho quando na verdade a
        # consulta quebrou. Agora um indicador que falhou vem como null e entra
        # na lista de avisos, e a tela mostra "—" em vez de zero.
        avisos = []

        def _num(sql, rotulo):
            try:
                cur.execute(sql)
                return int(cur.fetchone()[0])
            except Exception as e:
                print(" KPI '%s' falhou: %s" % (rotulo, str(e)[:180]), flush=True)
                avisos.append(rotulo)
                return None

        intg = _num("SELECT COUNT(*) FROM TEKNISA.INTG_NOTAFISCAL", "notas na integração")

        caixa = _num("""SELECT COUNT(*) FROM TEKNISA.NOTADESTINADA
                        WHERE NRLANCTONF IS NULL AND DTEMISSAO >= TRUNC(SYSDATE)-30""",
                     "caixa de entrada")

        # Notas paradas: so as que AINDA precisam de acao - nao lancadas, nao
        # canceladas/denegadas e do mes vigente + anterior.
        # criterio unificado (ver _sql_ainda_parada): nao basta ter log de erro,
        # a nota tem que continuar sem ter seguido adiante por NENHUM caminho.
        # FILA DO DIA = mes vigente. Atrasadas e historico tem contador proprio,
        # para o numero do topo refletir o trabalho de hoje (e nao o acumulo).
        _cond = " I.IDATIVO='S' " + _sql_ainda_parada(cur, "I")
        _paradas_sql = ("SELECT COUNT(DISTINCT I.NRACESSONFE) "
                        "FROM TEKNISA.INTG_INCONSISTENCIA I WHERE " + _cond)

        # mesmo tratamento do 'status': pesado, vai para cache proprio e
        # recalculo em segundo plano (ver _kpi_pesado_paradas)
        _p = _kpi_pesado_paradas(cur, _paradas_sql)
        paradas, atrasadas, historico = _p["recentes"], _p["atrasadas"], _p["historico"]

        # Fila humana de produto (so o que e realmente vinculo de produto:
        # o codigo oficial manda; texto so para codigo ainda nao mapeado)
        _prod_cod = ",".join("'%s'" % c.lstrip("0") for c in COD_PRODUTO)
        _conhec = ",".join("'%s'" % c for c in sorted(CONSIST_N))
        divergentes = _num("SELECT COUNT(DISTINCT I.NRACESSONFE) "
                           "FROM TEKNISA.INTG_INCONSISTENCIA I WHERE " + _cond
                           + _sql_periodo("I", "recentes") +
                           " AND (TRIM(LEADING '0' FROM I.CDCONSISTXML) IN (" + _prod_cod + ")"
                           "   OR (TRIM(LEADING '0' FROM I.CDCONSISTXML) NOT IN (" + _conhec + ")"
                           "       AND (UPPER(I.DSINCONSIS) LIKE '%PRODUTO%'"
                           "         OR UPPER(I.DSINCONSIS) LIKE '%PODUTO%')))",
                           "notas com produto divergente")

        a_vincular = _num("""SELECT COUNT(*) FROM AIHUB.AIHUB_PENDENCIA
                             WHERE STATUS='ABERTO'""", "produtos a vincular")

        # O CONTADOR DE "A REVISAR" PRECISA VIR DAQUI, nao da propria tela: o
        # badge do menu tem que estar certo para quem NUNCA abriu a tela - e
        # justamente essa pessoa que esqueceria a fila. Conta NOTAS distintas,
        # nao apontamentos, para bater com o que a lista mostra.
        a_revisar = _num("""SELECT COUNT(DISTINCT CHAVE_NF)
                            FROM   AIHUB.AIHUB_OCORRENCIA
                            WHERE  STATUS='ABERTO'""", "notas a revisar")

        conferir = _num("""SELECT COUNT(*) FROM TEKNISA.PRODFORNXML D
                           LEFT JOIN AIHUB.AIHUB_CONFERIDO C
                                  ON C.CDFORNECED=D.CDFORNECED
                                 AND C.CDPRODUTOXML=D.CDPRODUTOXML
                           WHERE D.IDATIVO='S' AND C.OK IS NULL
                             AND (D.CDOPERINCLUSAO='AIHUB_ROBO'
                               OR D.CDOPERINCLUSAO='AIHUB_PAINEL'
                               OR D.CDOPERINCLUSAO LIKE 'PAINEL%')""",
                        "vínculos a conferir")

        # O VINCULO DA AF, QUE E A RAZAO DE EXISTIR DO SISTEMA, PRECISA APARECER.
        #
        # O diretor apontou que na propria IT a funcao principal nao estava
        # citada, e no painel ela tambem nao tinha lugar: so acontecia dentro de
        # um modal. Agora e o primeiro numero do Inicio.
        # Fonte: a trilha que o robo grava em AIHUB_PROC_LOG (_auditar_af).
        # Coluna de data e DT_PROC (nao DT_PROCESSAMENTO - ver CLAUDE.md).
        af_hoje = _num("""SELECT COUNT(*) FROM AIHUB.AIHUB_PROC_LOG
                          WHERE STATUS='AF_VINCULO_ROBO'
                            AND DT_PROC >= TRUNC(SYSDATE)""",
                       "AFs vinculadas hoje")
        af_total = _num("""SELECT COUNT(*) FROM AIHUB.AIHUB_PROC_LOG
                           WHERE STATUS='AF_VINCULO_ROBO'""",
                        "AFs vinculadas no total")

        result = {
            "af_hoje": af_hoje,
            "af_total": af_total,
            "caixa": caixa,
            "paradas": paradas,
            "atrasadas": atrasadas,
            "historico_nao_tratadas": historico,
            "divergentes": divergentes,
            "a_vincular": a_vincular,
            "a_revisar": a_revisar,
            "a_conferir": conferir,
            "integradas": status.get("I", 0),
            "pendentes": status.get("P", 0),
            "aprovadas": status.get("A", 0),
            "outras": sum(v for k, v in status.items() if k not in ("I", "P", "A")),
            "depara_total": depara_total,
            "fornecedores_depara": forn_total,
            "notas_integracao": intg,
            "status": status,
            "avisos": avisos,
            "ambiente": AMBIENTE,
            "modo_escrita": MODO_ESCRITA,
        }
        # avisa a tela que os pesados ainda estao sendo apurados, para ela
        # mostrar "calculando" em vez de zero
        result["calculando"] = [k for k, v in (("situação das notas", status),
                                               ("notas paradas", paradas))
                                if v is None or v == {}]
        # so guarda no cache da rota quando TUDO ja foi apurado; senao a
        # proxima leitura ficaria 5 min presa a um resultado incompleto
        if not result["calculando"]:
            _cache["kpis"] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)
        _pesadas.release(); lk.release()


# ----------------------------- Fila de notas ------------------------------- #
@app.route("/api/fila")
def fila():
    forn = (request.args.get("forn") or "").strip()
    status = (request.args.get("status") or "").strip().upper()
    af = (request.args.get("af") or "").strip()
    chave = (request.args.get("chave") or "").strip()
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # entrada + compra (ver comentario em /api/kpis: o codigo '01' tambem
        # existe na SAIDA, onde significa "NF Venda")
        where = ["N.IDENTRSAIDOP='E'", "N.CDTIPOOPER IN (%s)" % _SQL_TIPOS,
                 "1=1" + _so_simplificada("N")]
        binds = {}
        # 'L' PRECISA ESTAR AQUI. Medido em producao (achado 75): a nota que o
        # robo importa nasce em 'L' (lancada, DTLANCAMENNF preenchido e
        # DTAPROVACAO vazio) e vira 'A' quando alguem aprova. O status 'I' tem
        # 4 notas em TODA a base e zero no ultimo ano. Aceitando so I/P/A, o
        # filtro escondia justamente as notas que o robo acabou de concluir, e
        # o padrao ('I','P') deixava a tela praticamente vazia em producao.
        if status in ("I", "P", "A", "L"):
            where.append("N.IDSTATUS = :st")
            binds["st"] = status
        else:
            where.append("N.IDSTATUS IN ('I','P','L')")   # fila operacional
        if forn:
            where.append("N.CDFORNECED = :fn")
            binds["fn"] = forn
        if chave:
            where.append(_cond_chave("N.NRACESSONFE", chave, binds))
        # filtro por numero de AF: exato se o item tiver a coluna de AF;
        # senao, pelos fornecedores daquela AF (aproximado, mas sempre funciona).
        if af:
            col_af = _col_af_item(cur)
            if col_af:
                where.append("EXISTS (SELECT 1 FROM TEKNISA.ITEMPRODUTO IP "
                             "WHERE IP.CDFILIAL=N.CDFILIAL AND IP.NRLANCTONF=N.NRLANCTONF "
                             "AND IP.%s = :af)" % col_af)
                binds["af"] = af
            else:
                where.append("N.CDFORNECED IN (SELECT DISTINCT CDFORNECED "
                             "FROM TEKNISA.AUTOFORNE WHERE NRAUTOFORN = :af)")
                binds["af"] = af
        # RECORTE PADRAO DE PERIODO, e ele e o que torna a tela usavel.
        #
        # Medido em homologacao (06/08/2026): pedir "as 50 notas mais recentes"
        # com IDSTATUS='A' faz o Oracle percorrer as 997.361 aprovadas e
        # ORDENAR TODAS para devolver 50 (plano: WINDOW SORT PUSHED RANK).
        # Custa 19,4 SEGUNDOS. Com IDSTATUS='L', que tem 481 linhas, a mesma
        # consulta leva 323 ms - ou seja, o tempo e do VOLUME, nao da consulta,
        # e nenhum indice resolve enquanto a tela pedir um milhao de linhas
        # ordenadas.
        #
        # Ninguem rola um milhao de notas aprovadas: quem procura uma nota
        # antiga usa a busca por chave, AF ou fornecedor, que tem indice. Entao
        # o padrao passa a ser os ultimos 180 dias, e a pessoa amplia pela tela
        # quando precisar. Se ja veio filtro de data, respeita o que ela pediu.
        col_dt = _col_data_nf(cur) if (dt_ini or dt_fim) else None
        if not (dt_ini or dt_fim) and not (chave or af or forn):
            _cd = _col_data_nf(cur)
            if _cd:
                where.append("N.%s >= TRUNC(SYSDATE) - :jan" % _cd)
                binds["jan"] = 180
        if col_dt and dt_ini:
            where.append("N.%s >= TO_DATE(:di,'YYYY-MM-DD')" % col_dt)
            binds["di"] = dt_ini
        if col_dt and dt_fim:
            where.append("N.%s < TO_DATE(:df,'YYYY-MM-DD')+1" % col_dt)
            binds["df"] = dt_fim
        binds["off"] = pag * PG
        binds["lim"] = PG + 1   # busca 1 a mais so para saber se ha proxima pagina
        sql = f"""
            SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.IDSTATUS,
                   N.CDFORNECED, F.NMRAZSOCFORN, N.DTAPROVACAO
            FROM   TEKNISA.NOTAFISCAL N
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED = N.CDFORNECED
            WHERE  {' AND '.join(where)}
            ORDER  BY N.NRLANCTONF DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """
        cur.execute(sql, binds)
        notas = _rows(cur)
        tem_mais = len(notas) > PG
        notas = notas[:PG]
        return jsonify({"notas": notas, "pag": pag, "tem_mais": tem_mais})
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e), "notas": []}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Caixa de entrada (NOTADESTINADA) ------------ #
@app.route("/api/caixa")
def caixa():
    """Notas destinadas que ainda NAO foram importadas (NRLANCTONF nulo).
    Filtros: periodo (data de emissao), CNPJ do fornecedor, chave NF-e."""
    dias = request.args.get("dias", default=30, type=int)
    forn = (request.args.get("forn") or "").strip()
    chave = (request.args.get("chave") or "").strip()
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    ckey = "caixa:%s:%s:%s:%s:%s:%s" % (dias, forn, chave, dt_ini, dt_fim, pag)
    hit = _cache.get(ckey)
    if hit and (time.time() - hit[0]) < 300:
        return jsonify(hit[1])
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        where = ["NRLANCTONF IS NULL"]
        binds = {}
        # periodo: se vier data explicita usa o intervalo; senao os ultimos N dias
        if dt_ini or dt_fim:
            if dt_ini:
                where.append("DTEMISSAO >= TO_DATE(:di,'YYYY-MM-DD')")
                binds["di"] = dt_ini
            if dt_fim:
                where.append("DTEMISSAO < TO_DATE(:df,'YYYY-MM-DD')+1")
                binds["df"] = dt_fim
        else:
            where.append("DTEMISSAO >= TRUNC(SYSDATE) - :d")
            binds["d"] = dias
        if forn:
            where.append("REGEXP_REPLACE(NRINSJUREMIT,'[^0-9]','') = :fn")
            binds["fn"] = forn
        if chave:
            where.append(_cond_chave("NRACESSONFE", chave, binds))
        w = " AND ".join(where)
        # total so na primeira pagina (para o contador); nas seguintes reaproveita
        total = None
        if pag == 0:
            cur.execute(f"SELECT COUNT(*) FROM TEKNISA.NOTADESTINADA WHERE {w}", binds)
            total = int(cur.fetchone()[0])
        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        cur.execute(f"""
            SELECT TO_CHAR(DTEMISSAO,'DD/MM/YYYY') EMISSAO, NRACESSONFE, NRINSJUREMIT,
                   NMRAZSOCEMIT, CDSTATUSMANIF,
                   -- descricao em texto que o proprio Teknisa grava: e mais
                   -- confiavel que traduzir o codigo (que varia entre cStat do
                   -- evento e tipo de evento)
                   SUBSTR(DSOBSSTATUSMANIF,1,60) AS MANIF_TXT
            FROM   TEKNISA.NOTADESTINADA
            WHERE  {w}
            ORDER  BY DTEMISSAO DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """, binds)
        notas = _rows(cur)
        tem_mais = len(notas) > PG
        notas = notas[:PG]
        result = {"notas": notas, "total": total, "dias": dias, "pag": pag, "tem_mais": tem_mais}
        _cache[ckey] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"notas": [], "total": 0, "erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ---------------------- XML nao utilizado (aguardando lancamento) ---------- #
def _origem_amigavel(tag):
    """Traduz DSTAGORIGFILI (a coluna 'Origem Filial (XML)' da tela de
    Importacao de XML do Teknisa) para rotulo de tela."""
    t = (tag or "").strip()
    if not t:
        return "Automatica"
    if t == "Manual":
        return "Manual"
    if t.upper().startswith("AF"):        # 'AF(Relacionar AF)', 'AF (tag xPed)'
        return "Relacionar AF"
    return t


def _checklist(n, origem):
    """O QUE FALTA PARA ESTA NOTA IMPORTAR - antes de alguem clicar.

    POR QUE ISTO EXISTE. A pessoa clicava em Importar e so entao descobria o
    que faltava, as vezes depois de minutos, porque a nativa roda a validacao
    inteira antes de recusar. Pior: em nota "Aguardando AF" o clique era aposta
    perdida - nao ha o que importar sem AF.

    Devolve uma lista de {ok, item, falta}, na ORDEM em que se resolve. A ordem
    importa: sem produto interno nao ha ponte para a AF (achado 40), entao nao
    adianta mandar procurar AF antes de vincular produto.

    NAO PROMETE QUE VAI PASSAR. So confere o que da para conferir sem chamar o
    Teknisa. Preco fora da AF (055), quantidade (054) e vencimento (051) so
    aparecem quando ele roda - e por isso o ultimo item da lista diz isso, em
    vez de deixar a pessoa achar que checou tudo."""
    def item(ok, texto, falta):
        return {"ok": bool(ok), "item": texto, "falta": ("" if ok else falta)}

    if origem == "Manual":
        return [item(False, "Origem Manual",
                     "Nota manual fica com a gerente, que lança no recebimento "
                     "físico. O robô não importa por decisão da operação.")]
    sem_prod = int(n.get("SEM_PROD") or 0)
    incons = int(n.get("INCONS") or 0)
    lista = [
        item(n.get("CDFILIRECE"), "Filial de recebimento",
             "A nota não diz em qual filial entra. Ajuste no Teknisa."),
        item(sem_prod == 0, "Produtos vinculados",
             "%d item(ns) sem De/Para. Resolva em 'Falta o produto'." % sem_prod),
        item(n.get("TEM_AF"), "AF vinculada",
             "Nenhum item tem AF. Use 'Vincular AF' antes de importar."),
        item((n.get("CDTIPOOPER") or "").strip(), "Tipo de operação",
             "Sem tipo, o Teknisa recusa com o código 064. Ele costuma vir "
             "junto quando a AF é relacionada."),
    ]
    if incons:
        lista.append(item(False, "Apontamentos do Teknisa",
                          "%d apontamento(s) em aberto. Veja em 'A revisar'."
                          % incons))
    else:
        lista.append(item(True, "Sem apontamento em aberto", ""))
    return lista


@app.route("/api/naoutilizadas")
def naoutilizadas():
    """XML que ja chegou mas ainda NAO virou lancamento (Status 'Nao utilizada'
    da tela de Importacao de XML). E o estagio ANTES da fila: aqui o robo ainda
    nao atua. Mostra a ORIGEM (Manual / Relacionar AF / Automatica) e a
    PRONTIDAO: nota com tipo de operacao ja definido esta pronta para importar;
    sem tipo, esta aguardando ser relacionada a uma AF.

    'Nao utilizada' = DTUTILIZAXML nula (mesmo criterio do filtro Status do
    Teknisa). Nao usa subconsulta correlacionada, entao e leve."""
    forn = (request.args.get("forn") or "").strip()
    chave = (request.args.get("chave") or "").strip()
    origem = (request.args.get("origem") or "").strip()   # manual|af|auto|''
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    # LOTE DE CHEGADA: as notas nao entram de pouco em pouco, entram em bloco a
    # cada 2 horas (medido em producao, sempre por volta do minuto 27). Este
    # recorte e por DTINCLUSAO, que e QUANDO A NOTA CHEGOU - diferente de
    # dt_ini/dt_fim, que sao a EMISSAO, a data do fornecedor.
    lote = (request.args.get("lote") or "").strip()     # 'YYYY-MM-DD HH'
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    nkey = "nao:%s:%s:%s:%s:%s:%s:%s" % (forn, chave, origem, dt_ini, dt_fim,
                                         lote, pag)
    hit = _cache.get(nkey)
    if hit and (time.time() - hit[0]) < 120:
        return jsonify(hit[1])
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # a coluna de origem e o coracao desta tela: se a versao nao a tiver,
        # a tela ainda funciona, so sem a distincao de origem
        col_org = _col_disponivel(cur, "INTG_NOTAFISCAL", ["DSTAGORIGFILI"], "intg_orig")
        where = ["G.DTUTILIZAXML IS NULL"]
        binds = {}
        if forn:
            where.append("REGEXP_REPLACE(G.NRINSJURNF,'[^0-9]','') = :fn")
            binds["fn"] = forn
        if chave:
            where.append(_cond_chave("G.NRACESSONFE", chave, binds))
        if lote:
            # a hora fechada do lote: de HH:00:00 ate HH:59:59 daquele dia
            where.append("G.DTINCLUSAO >= TO_DATE(:lt,'YYYY-MM-DD HH24') "
                         "AND G.DTINCLUSAO < TO_DATE(:lt,'YYYY-MM-DD HH24') + 1/24")
            binds["lt"] = lote[:13]
        if col_org and origem == "manual":
            where.append("G.%s = 'Manual'" % col_org)
        elif col_org and origem == "af":
            where.append("UPPER(G.%s) LIKE 'AF%%'" % col_org)
        elif col_org and origem == "auto":
            where.append("(G.%s IS NULL OR (G.%s <> 'Manual' AND UPPER(G.%s) NOT LIKE 'AF%%'))"
                         % (col_org, col_org, col_org))
        if dt_ini:
            where.append("G.DTEMISSAO >= TO_DATE(:di,'YYYY-MM-DD')"); binds["di"] = dt_ini
        if dt_fim:
            where.append("G.DTEMISSAO < TO_DATE(:df,'YYYY-MM-DD')+1"); binds["df"] = dt_fim
        # sem periodo explicito -> mes vigente + anterior (regra de negocio).
        # MAS quem busca por chave ou fornecedor quer achar a nota onde ela
        # estiver: limitar por data ali devolvia ZERO para uma chave que existe
        # (o dado de homologacao para em 23/06, entao a janela padrao fica vazia).
        # O LOTE E UM FILTRO, entao ele tambem dispensa o periodo padrao.
        # Sem isto, escolher um lote de 23 notas devolvia ZERO: o recorte
        # de emissao (mes vigente + anterior) escondia notas que CHEGARAM
        # no lote mas foram EMITIDAS antes. Sao datas diferentes, e a
        # pessoa nao tem como adivinhar essa briga.
        if not (dt_ini or dt_fim or chave or forn or lote):
            where.append("G.DTEMISSAO >= TRUNC(ADD_MONTHS(SYSDATE,-1),'MM')")
        w = " AND ".join(where)
        total = None
        if pag == 0:
            cur.execute("SELECT COUNT(*) FROM TEKNISA.INTG_NOTAFISCAL G WHERE " + w, binds)
            total = int(cur.fetchone()[0])
        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        _sel_org = ("G.%s" % col_org) if col_org else "NULL"
        # A CONFERENCIA VEM JUNTO COM A LISTA, e nao numa consulta por linha.
        #
        # POR QUE. A pessoa clicava em Importar e so entao descobria o que
        # faltava - as vezes depois de MINUTOS, porque a procedure nativa roda a
        # validacao inteira antes de recusar. Agora cada linha ja chega dizendo
        # o que tem e o que falta, e o clique deixa de ser aposta.
        #
        # Sao quatro EXISTS sobre a mesma nota, todos por indice. Fazer uma
        # consulta por linha custaria 50 idas ao banco por pagina, e cada ida
        # custa ~150 ms de rede (o banco e um RDS na AWS).
        cur.execute(f"""
            SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDSERIEENTR, G.CDFILIRECE,
                   G.CDFORNECED, F.NMRAZSOCFORN, G.VRNOTAFISC,
                   TO_CHAR(G.DTEMISSAO,'DD/MM/YYYY') EMISSAO,
                   TO_CHAR(G.DTIMPORTACAOXML,'DD/MM/YYYY') IMPORTACAO,
                   {_sel_org} AS ORIGEM, G.CDTIPOOPER,
                   CASE WHEN EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                                      WHERE I.NRACESSONFE=G.NRACESSONFE
                                        AND I.NRAUTOFORN IS NOT NULL)
                        THEN 1 ELSE 0 END AS TEM_AF,
                   (SELECT COUNT(*) FROM TEKNISA.INTG_ITEMPRODUTO I2
                     WHERE I2.NRACESSONFE=G.NRACESSONFE
                       AND NOT EXISTS (SELECT 1 FROM TEKNISA.PRODFORNXML D
                                        WHERE D.CDFORNECED=G.CDFORNECED
                                          AND D.CDPRODUTOXML=I2.CDPRODUTO
                                          AND D.IDATIVO='S')) AS SEM_PROD,
                   (SELECT COUNT(*) FROM TEKNISA.INTG_INCONSISTENCIA X
                     WHERE X.NRACESSONFE=G.NRACESSONFE AND X.IDATIVO='S') AS INCONS
            FROM   TEKNISA.INTG_NOTAFISCAL G
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=G.CDFORNECED
            WHERE  {w}
            ORDER  BY G.DTEMISSAO DESC NULLS LAST
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """, binds)
        linhas = _rows(cur)
        tem_mais = len(linhas) > PG
        linhas = linhas[:PG]
        # rotula origem e prontidao (o robo nao mexe em manual; sem tipo = sem AF)
        for n in linhas:
            org = _origem_amigavel(n.get("ORIGEM"))
            n["ORIGEM_LABEL"] = org
            tipo = (n.get("CDTIPOOPER") or "").strip()
            if org == "Manual":
                n["PRONTA"] = "Manual (a gerente lanca)"
            elif tipo:
                n["PRONTA"] = "Pronta para importar"
            else:
                n["PRONTA"] = "Aguardando AF"
            n["CHECK"] = _checklist(n, org)
        result = {"notas": linhas, "total": total, "pag": pag, "tem_mais": tem_mais}
        _cache[nkey] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"notas": [], "total": 0, "erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Busca (AF ou chave NF-e) -------------------- #
@app.route("/api/buscar")
def buscar():
    """Localiza uma AF (pelo numero) ou uma nota (pela chave NF-e), dizendo
    ONDE ela esta: caixa de entrada, lancada no sistema, ou travada."""
    af = (request.args.get("af") or "").strip()
    chave = (request.args.get("chave") or "").strip()
    if not af and not chave:
        return jsonify({"msg": "Informe o numero da AF ou a chave da NF-e."})
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        out = {"af": None, "af_itens": [], "notas": [], "chave": chave}

        # ------- busca por chave: onde a nota esta -------
        if chave:
            # UMA condicao por tabela, cada uma com o seu alias. Antes era um
            # LIKE '%chave%' unico, e o curinga inicial proibia o indice: esta
            # rota levava 95 SEGUNDOS no validador (ver _cond_chave).
            bc = {}
            cond_nf = _cond_chave("N.NRACESSONFE", chave, bc, "c")
            cond_nd = _cond_chave("NRACESSONFE", chave, bc, "c")
            like = bc["c"]
            # 1) lancada (NOTAFISCAL)
            cur.execute("""
                SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.IDSTATUS,
                       N.CDFORNECED, F.NMRAZSOCFORN, N.DTAPROVACAO
                FROM   TEKNISA.NOTAFISCAL N
                LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=N.CDFORNECED
                WHERE  """ + cond_nf + """
                FETCH FIRST 30 ROWS ONLY
            """, c=like)
            for r in _rows(cur):
                b = {"I": "A processar", "P": "A aprovar", "A": "Aprovada"}.get(r.get("IDSTATUS"), "Lancada")
                out["notas"].append({"onde": "Lancada no sistema", "situacao": b,
                                     "filial": r.get("CDFILIAL"), "lancto": r.get("NRLANCTONF"),
                                     "chave": r.get("NRACESSONFE"),
                                     "fornecedor": r.get("NMRAZSOCFORN") or r.get("CDFORNECED"),
                                     "aprovacao": r.get("DTAPROVACAO")})
            # 2) na caixa (NOTADESTINADA, ainda nao lancada)
            try:
                cur.execute("""
                    SELECT TO_CHAR(DTEMISSAO,'DD/MM/YYYY') EMISSAO, NRACESSONFE,
                           NMRAZSOCEMIT, CDSTATUSMANIF
                    FROM   TEKNISA.NOTADESTINADA
                    WHERE  """ + cond_nd + """
                      AND  NRLANCTONF IS NULL
                    FETCH FIRST 30 ROWS ONLY
                """, c=like)
                for r in _rows(cur):
                    out["notas"].append({"onde": "Caixa de entrada", "situacao": "Aguardando entrada",
                                         "emissao": r.get("EMISSAO"), "chave": r.get("NRACESSONFE"),
                                         "fornecedor": r.get("NMRAZSOCEMIT"),
                                         "manifestacao": r.get("CDSTATUSMANIF")})
            except Exception:
                pass
            # 3) travada (INTG_INCONSISTENCIA)
            try:
                cur.execute("""
                    SELECT NRACESSONFE, LISTAGG(DSINCONSIS,'  |  ') WITHIN GROUP (ORDER BY DSINCONSIS) MOT
                    FROM (SELECT DISTINCT NRACESSONFE, SUBSTR(DSINCONSIS,1,120) DSINCONSIS
                          FROM TEKNISA.INTG_INCONSISTENCIA
                          WHERE IDATIVO='S' AND """ + cond_nd + """)
                    GROUP BY NRACESSONFE FETCH FIRST 20 ROWS ONLY
                """, c=like)
                for r in _rows(cur):
                    out["notas"].append({"onde": "Travada na importacao", "situacao": "Bloqueada",
                                         "chave": r.get("NRACESSONFE"), "motivo": r.get("MOT")})
            except Exception:
                pass

        # ------- busca por AF: cabecalho + itens + fornecedor -------
        if af:
            cur.execute("""
                SELECT A.NRAUTOFORN, A.CDFILLANC, A.CDFORNECED, F.NMRAZSOCFORN
                FROM   TEKNISA.AUTOFORNE A
                LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=A.CDFORNECED
                WHERE  A.NRAUTOFORN = :af
                FETCH FIRST 10 ROWS ONLY
            """, af=af)
            cab = _rows(cur)
            if cab:
                out["af"] = {"numero": af,
                             "filial": cab[0].get("CDFILLANC"),
                             "cd_forneced": cab[0].get("CDFORNECED"),
                             "fornecedor": cab[0].get("NMRAZSOCFORN") or cab[0].get("CDFORNECED")}
                cur.execute("""
                    SELECT DISTINCT I.CDPRODUTO, P.NMPRODUTO
                    FROM   TEKNISA.AUTOFORNE A
                    JOIN   TEKNISA.ITEMAUTF I ON I.CDFILDEST=A.CDFILDEST
                           AND I.CDFILLANC=A.CDFILLANC AND I.NRAUTOFORN=A.NRAUTOFORN
                    JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO=I.CDPRODUTO
                    WHERE  A.NRAUTOFORN = :af
                    FETCH FIRST 200 ROWS ONLY
                """, af=af)
                out["af_itens"] = _rows(cur)
                # notas de compra desse fornecedor no sistema
                cur.execute("""
                    SELECT * FROM (
                      SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.IDSTATUS,
                             N.DTAPROVACAO
                      FROM   TEKNISA.NOTAFISCAL N
                      WHERE  N.IDENTRSAIDOP='E' AND N.CDTIPOOPER IN (""" + _SQL_TIPOS + """)
                        AND  N.CDFORNECED=:f
                    """ + _so_simplificada("N") + """
                      ORDER  BY N.NRLANCTONF DESC
                    ) WHERE ROWNUM <= 40
                """, f=out["af"]["cd_forneced"])
                for r in _rows(cur):
                    b = {"I": "A processar", "P": "A aprovar", "A": "Aprovada"}.get(r.get("IDSTATUS"), "Outro")
                    out["notas"].append({"onde": "Lancada no sistema", "situacao": b,
                                         "filial": r.get("CDFILIAL"), "lancto": r.get("NRLANCTONF"),
                                         "chave": r.get("NRACESSONFE"), "aprovacao": r.get("DTAPROVACAO")})
        out["total"] = len(out["notas"])
        return jsonify(out)
    except Exception as e:
        return jsonify({"msg": _erro_amigavel(e), "notas": []}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Detalhe de UMA nota (abrir p/ ver) ---------- #
@app.route("/api/nota")
def nota():
    """Detalhe completo de uma nota pela chave: cabecalho, itens (com valor,
    quando existe), situacao e motivos (se estiver parada). Diz ONDE ela esta."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"msg": "Informe a chave da NF-e."})
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        out = {"chave": chave, "onde": None, "cabecalho": None,
               "itens": [], "motivos": [], "parada": False}

        # 1) ja lancada no sistema (NOTAFISCAL)?
        cur.execute("""
            SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.IDSTATUS, N.CDFORNECED,
                   F.NMRAZSOCFORN, N.DTAPROVACAO, N.CDTIPOOPER
            FROM   TEKNISA.NOTAFISCAL N
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED = N.CDFORNECED
            WHERE  N.NRACESSONFE = :c
            FETCH FIRST 1 ROWS ONLY
        """, c=chave)
        r = _rows(cur)
        ja_lancada = bool(r)
        cancelada = False
        if r:
            h = r[0]
            st = {"I": "A processar", "P": "A aprovar", "A": "Aprovada"}.get(h.get("IDSTATUS"), "No sistema")
            out["onde"] = "Lançada no sistema"
            tipo = h.get("CDTIPOOPER")
            out["situacao"] = st
            out["cabecalho"] = {
                "fornecedor": h.get("NMRAZSOCFORN") or h.get("CDFORNECED"),
                "cd_forneced": h.get("CDFORNECED"),
                "filial": h.get("CDFILIAL"),
                "lancto": h.get("NRLANCTONF"),
                "situacao": st,
                "aprovacao": h.get("DTAPROVACAO"),
                "tipo": ("Compra (NF-e mod. 55)" if tipo == "01" else tipo),
            }
            # valor total da nota (coluna VR detectada na NOTAFISCAL)
            vcol = _col_val_nf(cur)
            if vcol:
                try:
                    cur.execute("SELECT " + vcol + " FROM TEKNISA.NOTAFISCAL "
                                "WHERE CDFILIAL=:f AND NRLANCTONF=:n",
                                f=h.get("CDFILIAL"), n=h.get("NRLANCTONF"))
                    rv = cur.fetchone()
                    if rv and rv[0] is not None:
                        out["cabecalho"]["valor"] = float(rv[0])
                except Exception:
                    pass
            valcol = _col_val_item(cur)
            afcol = _col_af_item(cur)
            selval = ((", " + valcol + " AS VALOR") if valcol else ", NULL AS VALOR")
            selaf = ((", " + afcol + " AS NRAF") if afcol else ", NULL AS NRAF")
            try:
                cur.execute("SELECT NRSEQITPROD, NMPRODUTOXML, CDPRODUTO, QTITEMPROD" + selval + selaf +
                            " FROM TEKNISA.ITEMPRODUTO WHERE CDFILIAL=:f AND NRLANCTONF=:n "
                            "ORDER BY NRSEQITPROD", f=h.get("CDFILIAL"), n=h.get("NRLANCTONF"))
                out["itens"] = _rows(cur)
                tot, has, afs = 0.0, False, []
                for it in out["itens"]:
                    if it.get("VALOR") is not None:
                        try:
                            tot += float(it["VALOR"]); has = True
                        except Exception:
                            pass
                    a = it.get("NRAF")
                    if a and str(a) not in afs:
                        afs.append(str(a))
                if has and not out["cabecalho"].get("valor"):
                    out["cabecalho"]["valor"] = round(tot, 2)
                if afs:
                    out["cabecalho"]["afs"] = afs
            except Exception:
                pass
        else:
            # 2) na caixa de entrada (NOTADESTINADA, ainda nao lancada)?
            valcol = _col_val_dest(cur)
            selval = ((", " + valcol + " AS VALOR") if valcol else ", NULL AS VALOR")
            try:
                cur.execute("SELECT TO_CHAR(DTEMISSAO,'DD/MM/YYYY') AS EMISSAO, NRACESSONFE, "
                            "NRINSJUREMIT, NMRAZSOCEMIT, CDSTATUSMANIF, IDSTATUSNFE, "
                            "SUBSTR(DSOBSSTATUSMANIF,1,60) AS MANIF_TXT" + selval +
                            " FROM TEKNISA.NOTADESTINADA WHERE NRACESSONFE=:c FETCH FIRST 1 ROWS ONLY", c=chave)
                rd = _rows(cur)
                if rd:
                    d = rd[0]
                    # IDSTATUSNFE: 1=Autorizada, 2=Denegada, 3=Cancelada (comprovado
                    # empiricamente: status 1 vira nota em 95%; status 2/3 quase nunca)
                    _lbl = {"3": "Cancelada", "2": "Denegada"}.get(str(d.get("IDSTATUSNFE") or ""))
                    cancelada = bool(_lbl)  # nota morta -> nao e "parada"
                    out["onde"] = _lbl or "Caixa de entrada"
                    if _lbl:
                        out["situacao"] = _lbl
                    out["cabecalho"] = {
                        "fornecedor": d.get("NMRAZSOCEMIT"),
                        "cnpj": d.get("NRINSJUREMIT"),
                        "emissao": d.get("EMISSAO"),
                        "manifestacao": d.get("CDSTATUSMANIF"),
                        "manifestacao_txt": d.get("MANIF_TXT"),
                        "situacao": _lbl,
                        "valor": d.get("VALOR"),
                    }
            except Exception:
                pass

        # 3) HISTORICO DO XML: quem importou, quem bloqueou e quando.
        # E aqui que aparece "Arquivo XML bloqueado pelo usuario" - a forma como
        # a equipe marca "essa nota nao vamos usar". Mostrar isso evita a duvida
        # de "por que essa nota sumiu/aparece".
        #
        # ORDEM IMPORTA: este bloco tem que rodar ANTES do bloco 4 (motivos),
        # porque e ele que define "bloqueada_por_pessoa" - e o bloco 4 consulta
        # essa chave para NAO rotular como "parada" uma nota que a equipe ja
        # decidiu bloquear. Antes o 4 vinha primeiro e lia a chave antes de ela
        # existir: o teste era sempre falso e a nota bloqueada na mao aparecia
        # como "Parada na importacao", com todos os motivos - exatamente a
        # confusao que a leitura do HISTXMLNFE existe para evitar.
        try:
            cur.execute("""
                SELECT TO_CHAR(H.DTHISTORICO,'DD/MM/YYYY') AS DT,
                       H.IDSTATUS, SUBSTR(H.DSOPERACAO,1,80) AS OPERACAO,
                       H.CDOPERADOR
                FROM   TEKNISA.HISTXMLNFE H
                WHERE  H.NRACESSONFE = :c
                ORDER  BY H.NRSEQHISTOR
            """, c=chave)
            hist = _rows(cur)
            if hist:
                out["historico_xml"] = hist
                ult = hist[-1]
                if (ult.get("IDSTATUS") == "B"
                        and (ult.get("CDOPERADOR") or "AUTOMATICO") != "AUTOMATICO"):
                    out["bloqueada_por_pessoa"] = True
                    out["bloqueio"] = {"quando": ult.get("DT"),
                                       "operador": ult.get("CDOPERADOR")}
        except Exception as e:
            print(" aviso ao ler historico do XML:", str(e)[:120], flush=True)

        # 4) esta parada? (motivos)
        try:
            cur.execute("""
                SELECT DISTINCT SUBSTR(DSINCONSIS,1,200) AS M,
                       TRIM(LEADING '0' FROM CDCONSISTXML) AS COD
                FROM   TEKNISA.INTG_INCONSISTENCIA
                WHERE  IDATIVO='S' AND NRACESSONFE=:c
                FETCH FIRST 40 ROWS ONLY
            """, c=chave)
            _lin = _rows(cur)
            mot = [x["M"] for x in _lin if x.get("M")]
            # nota bloqueada por uma pessoa nao e "parada": a decisao ja foi tomada
            if out.get("bloqueada_por_pessoa"):
                mot = []
                out["onde"] = "Bloqueada pela equipe"
                out["situacao"] = "Bloqueada pela equipe"
            # ACAO POR CODIGO OFICIAL, motivo a motivo. Antes o painel adivinhava
            # pelo texto e errava feio: motivo fiscal ("sem inscricao municipal")
            # aparecia com a acao "vincular o produto", mandando a pessoa para a
            # tela errada.
            det = []
            for x in _lin:
                if not x.get("M"):
                    continue
                quem, titulo, acao = _classifica(str(x.get("COD") or ""))
                det.append({"motivo": x["M"], "cod": x.get("COD"),
                            "quem": quem, "titulo": titulo, "acao": acao})
            if det:
                out["motivos_det"] = det
            if mot and not ja_lancada and not cancelada:
                # so e "parada" se a nota AINDA nao foi lancada NEM cancelada.
                # Ja lancada (NOTAFISCAL) ou cancelada (IDSTATUSNFE=3) = resolvida.
                out["motivos"] = mot
                out["parada"] = True
                if not out["onde"]:
                    out["onde"] = "Parada na importação"
            elif mot and (ja_lancada or cancelada):
                out["motivos_hist"] = mot  # informativo: teve inconsistencia, ja resolvida
        except Exception:
            pass

        # 3b) Nota PARADA por "deve possuir AF": o motivo do Teknisa e generico e
        # confunde - muitas vezes a nota JA referencia uma AF, e o problema e que
        # nem todos os itens estao amarrados nela. Aqui procuramos as AFs do
        # fornecedor com o mesmo valor da nota, para a pessoa saber onde olhar.
        if out.get("parada") and any("AF" in (m or "") for m in out.get("motivos", [])):
            try:
                # DUAS CORRECOES AQUI, e as duas doiam:
                #
                # 1) A coluna NAO e DTAUTORIZA (nao existe na AUTOFORNE): e
                #    DTAUTOFORN. A consulta estourava ORA-00904, caia no except
                #    e o bloco inteiro era codigo morto - a dica de AF nunca
                #    apareceu para ninguem.
                # 2) REGEXP_REPLACE SOBRE A COLUNA impede qualquer indice: o
                #    Oracle tem de calcular a funcao linha a linha nas 831.840
                #    AFs. Usamos o CDFORNECED que a propria nota ja traz (out),
                #    que e a chave indexada; so caimos no CNPJ da chave quando a
                #    nota nem cabecalho tem.
                forn_nf = out.get("CDFORNECED") or out.get("cd_forneced")
                if forn_nf:
                    cur.execute("""
                        SELECT A.NRAUTOFORN, A.CDFILLANC,
                               TO_CHAR(A.DTAUTOFORN,'DD/MM/YYYY') AS DT
                        FROM   TEKNISA.AUTOFORNE A
                        WHERE  A.CDFORNECED = :f
                          AND  A.DTAUTOFORN >= SYSDATE - 120
                        ORDER  BY A.DTAUTOFORN DESC
                        FETCH  FIRST 5 ROWS ONLY
                    """, f=forn_nf)
                else:
                    cnpj = chave[6:20] if len(chave) >= 20 else ""
                    cur.execute("""
                        SELECT A.NRAUTOFORN, A.CDFILLANC,
                               TO_CHAR(A.DTAUTOFORN,'DD/MM/YYYY') AS DT
                        FROM   TEKNISA.AUTOFORNE A
                        WHERE  A.DTAUTOFORN >= SYSDATE - 120
                          AND  REGEXP_REPLACE(A.CDFORNECED,'[^0-9]','') = :c
                        ORDER  BY A.DTAUTOFORN DESC
                        FETCH  FIRST 5 ROWS ONLY
                    """, c=cnpj)
                afs = _rows(cur)
                if afs:
                    out["afs_do_fornecedor"] = afs
                    out["dica_af"] = ("Este fornecedor tem AF (Autorização de "
                                      "Fornecimento) recente. Confira se a nota está "
                                      "relacionada à AF certa e se TODOS os itens "
                                      "estão amarrados nela.")
            except Exception as e:
                print(" aviso ao buscar AFs do fornecedor:", str(e)[:120], flush=True)

        # se nao achou cabecalho (nota so na integracao), monta o minimo a partir
        # da chave: CNPJ (posicoes 7-20) + nome do fornecedor, se cadastrado.
        if not out["cabecalho"] and len(chave) >= 20:
            cnpj = chave[6:20]
            nome = None
            try:
                cur.execute("SELECT NMRAZSOCFORN FROM TEKNISA.FORNECEDOR "
                            "WHERE REGEXP_REPLACE(CDFORNECED,'[^0-9]','')=:c FETCH FIRST 1 ROWS ONLY", c=cnpj)
                rr = cur.fetchone()
                nome = _fix(rr[0]) if rr and rr[0] else None
            except Exception:
                pass
            out["cabecalho"] = {"fornecedor": nome or "(não cadastrado)", "cnpj": cnpj}

        return jsonify(out)
    except Exception as e:
        return jsonify({"msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


def _conferir_apos_reprocesso(cur, chave):
    """Confere o que REALMENTE aconteceu com a nota depois do reprocesso.

    O reprocesso chama a importacao nativa, e ela pode: entrar, continuar barrada,
    ou entrar deixando pendencia. Antes o painel so dizia "ainda parada, confira o
    motivo no detalhe" e a pessoa tinha que ir atras. Agora o motivo volta junto,
    ja classificado - que e a parte de "verificar se ficou correto"."""
    out = {"lancada": False, "situacao": None, "filial": None,
           "lancto": None, "pendencias": []}
    try:
        cur.execute("""
            SELECT CDFILIAL, NRLANCTONF, IDSTATUS
            FROM   TEKNISA.NOTAFISCAL
            WHERE  NRACESSONFE = :c
            FETCH  FIRST 1 ROWS ONLY
        """, c=chave)
        r = cur.fetchone()
        if r:
            out["lancada"] = True
            out["filial"], out["lancto"] = r[0], r[1]
            out["situacao"] = {"I": "A processar", "P": "A aprovar",
                               "A": "Aprovada"}.get(r[2], r[2])
    except Exception as e:
        print(" aviso ao reler a nota apos reprocesso:", str(e)[:120], flush=True)

    # o que AINDA barra a nota (mesmo criterio da tela de paradas)
    try:
        cur.execute("""
            SELECT DISTINCT TRIM(LEADING '0' FROM CDCONSISTXML) AS COD,
                   SUBSTR(DSINCONSIS,1,200) AS M
            FROM   TEKNISA.INTG_INCONSISTENCIA
            WHERE  IDATIVO='S' AND NRACESSONFE=:c
            FETCH  FIRST 20 ROWS ONLY
        """, c=chave)
        for x in _rows(cur):
            if not x.get("M"):
                continue
            quem, titulo, acao = _classifica(str(x.get("COD") or ""))
            out["pendencias"].append({"cod": x.get("COD"), "motivo": x["M"],
                                      "quem": quem, "titulo": titulo, "acao": acao})
    except Exception as e:
        print(" aviso ao reler inconsistencias apos reprocesso:", str(e)[:120], flush=True)
    return out


# --------------------- Vinculo automatico da AF na nota -------------------- #
# E A FUNCAO QUE ORIGINOU O PROJETO. No Teknisa esse passo e MANUAL: a pessoa
# abre a lista de AFs em aberto do fornecedor e marca a certa no checkbox.
# Aqui o sistema decide sozinho quando NAO ha duvida, e so pergunta quando ha.
# Medido em producao (149 notas ja importadas): quando decide sozinho acerta
# 100% (zero escolhas erradas); nas duvidosas devolve a lista para 1 clique.
@app.route("/api/af-sugestao")
def af_sugestao():
    """Qual AF corresponde a esta nota? SO LEITURA: nao grava nada."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"erro": "Chave não informada."}), 400
    con = cur = None
    try:
        from core.af_matcher import resolver
        con = _conn()
        cur = con.cursor()
        out = resolver(cur, chave)
        nota = out.get("nota") or {}
        return jsonify({
            "chave": chave,
            "decisao": out["decisao"],
            "motivo": out["motivo"],
            "af": out.get("af"),
            "opcoes": out.get("opcoes") or [],
            # O PLANO POR ITEM PRECISA VIR. A tela tem um ramo inteiro para ele
            # (a distribuicao pronta, que a pessoa confirma em um clique, e o
            # caso da nota que cumpre mais de um pedido), mas esta rota nao
            # mandava o campo: o ramo nunca era alcancado e a pessoa so via a
            # lista generica de AFs. 'desc' passa pelo _fix como o resto.
            "por_item": bool(out.get("por_item")),
            "pronta_para_confirmar": bool(out.get("pronta_para_confirmar")),
            "plano": [{"seq": p.get("seq"), "produto": p.get("produto"),
                       "desc": _fix(p.get("desc") or ""), "qt": p.get("qt"),
                       "af": p.get("af"), "af_sugerida": p.get("af_sugerida"),
                       "opcoes": p.get("opcoes") or []}
                      for p in (out.get("plano") or [])],
            "nota": {"numero": nota.get("NRNOTAFISC"),
                     "fornecedor": nota.get("CDFORNECED"),
                     "filial": nota.get("CDFILIRECE"),
                     "origem": nota.get("ORIGEM")},
            "itens": [{"seq": i.get("seq"), "cprod": i.get("cprod_xml"),
                       "desc": _fix(i.get("desc_xml") or ""), "un": i.get("un"),
                       "qt": i.get("qt"), "interno": i.get("cd_interno"),
                       "af_atual": i.get("af_atual")}
                      for i in (out.get("itens") or [])],
        })
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/af-vincular", methods=["POST"])
def af_vincular():
    """Grava a AF escolhida em INTG_ITEMPRODUTO.NRAUTOFORN (o mesmo campo que a
    tela nativa preenche quando a pessoa marca a AF) e, opcionalmente, importa.

    GRAVA EM TABELA DO TEKNISA -> exige MODO_ESCRITA e GRANT de UPDATE em
    TEKNISA.INTG_ITEMPRODUTO. Sem o grant, responde em portugues dizendo o que
    falta, em vez de estourar erro tecnico.

    Se 'af' nao vier, usa a decisao do motor - e SO segue se ela for automatica.
    Nunca escolhe no lugar da pessoa quando ha ambiguidade."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "Vincular a AF grava no Teknisa e o painel está em "
                               "modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    chave = (dados.get("chave") or "").strip()
    af_pedida = (dados.get("af") or "").strip()
    importar = bool(dados.get("importar"))
    if not chave:
        return jsonify({"ok": False, "msg": "Chave não informada."}), 400
    con = cur = None
    try:
        from core.af_matcher import resolver, AUTOMATICA
        con = _conn()
        cur = con.cursor()
        out = resolver(cur, chave)

        # TRAVA: nota que ja tem AF em ALGUM item nao recebe outra AF.
        # Sem isto a nota terminaria com duas AFs diferentes (o UPDATE preenche
        # so os itens vazios), baixando dois pedidos distintos. Caso real
        # encontrado em homologacao na NF 43263. Vale mesmo quando a pessoa
        # escolhe na tela, por isso a checagem fica aqui e nao so no motor.
        # Nota que ja tem AF em ALGUM item so pode ser COMPLETADA com a MESMA AF
        # (o motor devolve 'plano' nesse caso). Vincular outra deixaria a nota
        # com duas AFs, baixando dois pedidos - caso real da NF 43263.
        ja = [i for i in (out.get("itens") or []) if i.get("af_atual")]
        # NOTA JA COMPLETA: todos os itens com AF, nao ha o que vincular.
        #
        # Antes isto caia no 409 de baixo, com a mensagem "a AF nao cobre os
        # demais" - errado, porque nao falta item nenhum. E a pessoa ficava sem
        # saida: a AF estava certa e a tela nao deixava importar.
        # Agora, se ela pediu para importar, o sistema importa direto.
        itens_todos = out.get("itens") or []
        if itens_todos and len(ja) == len(itens_todos):
            if not importar:
                return jsonify({"ok": True, "af": ", ".join(
                    sorted({i["af_atual"] for i in ja})),
                    "msg": "A AF já está nesta nota. Nada foi alterado."})
            from robo.servico_robo import importar_uma
            st = importar_uma(con, chave)
            ok = (st == "IMPORTADA")
            rec = ([] if ok else (_recusa_de_produto(chave, st, cur)
                                  or _porque_recusou(cur, chave)))
            # NADA SE PERDE: entrou, fecha as ocorrencias; nao entrou, abre
            if ok:
                _fechar_ocorrencias(cur, chave)
                con.commit()
            n_ocor = _abrir_ocorrencias(con, cur, chave, rec)
            return jsonify({"ok": ok, "status": st,
                            "af": ", ".join(sorted({i["af_atual"] for i in ja})),
                            # o motivo da recusa, dito pelo proprio Teknisa
                            "recusa": rec, "ocorrencias": n_ocor,
                            "na_fila": _fila_produto(con, cur, chave, rec, st),
                            "msg": ("Nota importada." if ok else
                                    _msg_import(st))}), (200 if ok else 409)
        if ja and not out.get("plano"):
            afs = sorted({i["af_atual"] for i in ja})
            return jsonify({"ok": False,
                            "msg": ("Esta nota já tem a AF %s em %d item(ns) e ela "
                                    "não cobre os demais. Não vou vincular outra "
                                    "AF, senão a nota ficaria com duas. Confira no "
                                    "Teknisa." % (", ".join(afs), len(ja)))}), 409

        if af_pedida == "*POR_ITEM*":
            # a pessoa confirmou, na tela, a distribuicao que o sistema propos
            # (itens vindos de AFs diferentes). O plano e recalculado aqui, nao
            # vem do navegador: assim ninguem forja a distribuicao pela rota.
            if not (out.get("plano") and out.get("por_item")):
                return jsonify({"ok": False,
                                "msg": ("A distribuição por item não se aplica a esta "
                                        "nota. Recarregue a tela.")}), 409
            if any(not p.get("af") for p in out["plano"]):
                return jsonify({"ok": False,
                                "msg": ("Ainda há item sem AF definida. Não vou "
                                        "gravar pela metade.")}), 409
            af = "*POR_ITEM*"
        elif af_pedida:
            # a pessoa escolheu na tela: confere que a AF esta entre as candidatas
            #
            # A LISTA DE CANDIDATAS E A FRONTEIRA DE SEGURANCA. Antes, lista
            # vazia deixava passar QUALQUER numero de AF ("validas and ..."):
            # bastava a tela estar velha, ou alguem chamar a rota na mao, para
            # gravar uma AF que o motor nunca aprovou - e ela iria para TODOS
            # os itens da nota. Sem candidata, a resposta certa e recusar.
            validas = {o["af"] for o in (out.get("opcoes") or [])}
            if af_pedida not in validas:
                return jsonify({"ok": False,
                                "msg": ("A AF %s não está entre as que servem para "
                                        "esta nota. Recarregue a tela." % af_pedida)}), 409
            af = af_pedida
        else:
            if out["decisao"] != AUTOMATICA:
                return jsonify({"ok": False, "decisao": out["decisao"],
                                "opcoes": out.get("opcoes") or [],
                                "plano": out.get("plano") or [],
                                "msg": out["motivo"]}), 409
            # 'automatica' vem de dois caminhos: uma AF para a nota toda, ou
            # uma AF por item (nota que cumpre mais de um pedido)
            af = "*POR_ITEM*" if out.get("por_item") else out["af"]["af"]

        # GRAVACAO ATOMICA (core.af_matcher.gravar_af): trava as linhas antes de
        # decidir, para que uma pessoa relacionando outra AF pela tela do
        # Teknisa no meio do caminho nao deixe a nota com duas AFs.
        from core.af_matcher import gravar_af, itens_do_xml
        if af == "*POR_ITEM*":
            plano = [p for p in (out.get("plano") or []) if p.get("af")]
        else:
            plano = [{"seq": i["seq"], "af": af} for i in itens_do_xml(cur, chave)]
        n, recusa = gravar_af(cur, chave, plano)
        if recusa:
            _rollback(con)
            return jsonify({"ok": False, "msg": "Não vinculei: " + recusa}), 409
        af = ", ".join(sorted({p["af"] for p in plano}))
        _auditar(cur, "AF_VINCULO",
                 "AF %s vinculada em %d item(ns) | %s" % (af, n, out["motivo"][:200]),
                 chave)
        con.commit()
        _cache.clear()   # as telas de parada/nao utilizada mudaram

        msg = "AF %s vinculada em %d item(ns)." % (af, n)
        resultado = {"ok": True, "af": af, "itens": n, "msg": msg}

        if importar:
            from robo.servico_robo import importar_uma
            st = importar_uma(con, chave)
            conf = {}
            try:
                conf = _conferir_apos_reprocesso(cur, chave)
                con.commit()
            except Exception:
                pass
            resultado["importacao"] = st
            resultado["lancada"] = conf.get("lancada")
            if st == "IMPORTADA" and conf.get("lancada"):
                resultado["msg"] = (msg + " A nota entrou no sistema (%s)."
                                    % (conf.get("situacao") or "lançada"))
                # a nota entrou: o que estava aberto para ela nao e mais
                # problema de ninguem
                _fechar_ocorrencias(cur, chave)
                con.commit()
            else:
                # A AF FOI VINCULADA; quem recusou foi a importacao. Dizer so
                # "não concluiu, confira a nota" obriga a pessoa a adivinhar.
                # O motivo esta registrado pelo proprio Teknisa: mostra ele.
                resultado["recusa"] = (_recusa_de_produto(chave, st, cur)
                                       or _porque_recusou(cur, chave))
                resultado["na_fila"] = _fila_produto(con, cur, chave,
                                                     resultado["recusa"], st)
                resultado["msg"] = (msg + " " + _msg_import(st))
                resultado["ok"] = False
        return jsonify(resultado)
    except Exception as e:
        _rollback(con)
        txt = str(e)
        if "ORA-01031" in txt or "ORA-00942" in txt:
            return jsonify({"ok": False,
                            "msg": ("Falta permissão de escrita na tabela de "
                                    "integração do Teknisa. Peça à TI: "
                                    "GRANT UPDATE ON TEKNISA.INTG_ITEMPRODUTO TO AIHUB;")}), 403
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Reprocessar nota ajustada ------------------- #
@app.route("/api/reprocessar", methods=["POST"])
def reprocessar():
    """Depois que a pessoa AJUSTOU a nota parada (no Teknisa / com o fornecedor),
    reprocessa: chama a mesma importacao nativa. Se passar, a nota entra e o robo
    segue com o restante sozinho. Como GERA nota fiscal, exige modo escrita.

    O AJUSTE em si e feito no Teknisa - nao aqui. O sistema so tem GRANT de escrita
    em PRODFORNXML; nota fiscal quem monta e a rotina nativa. O papel do painel e
    (1) disparar o reprocesso e (2) CONFERIR e dizer, em portugues, se resolveu."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O reprocesso gera nota fiscal e o painel está em "
                               "modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    chave = (dados.get("chave") or "").strip()
    if not chave:
        return jsonify({"ok": False, "msg": "Chave não informada."}), 400
    con = None
    try:
        from robo.servico_robo import importar_uma
        con = _conn()
        # GUARDA: nota de origem MANUAL nao entra pela importacao automatica.
        # Regra da operacao: na entrada manual e a gerente que lanca, no momento
        # do recebimento fisico. Importar aqui daria entrada em produto que ainda
        # nao chegou. Vale para o robo E para este botao.
        cur0 = con.cursor()
        try:
            col_org = _col_disponivel(cur0, "INTG_NOTAFISCAL", ["DSTAGORIGFILI"], "intg_orig")
            if col_org:
                cur0.execute("SELECT %s FROM TEKNISA.INTG_NOTAFISCAL "
                             "WHERE NRACESSONFE=:c FETCH FIRST 1 ROWS ONLY" % col_org, c=chave)
                r0 = cur0.fetchone()
                if r0 and (r0[0] or "").strip() == "Manual":
                    return jsonify({"ok": False, "manual": True,
                                    "msg": "Esta nota é de origem Manual. A entrada "
                                           "manual é lançada pela gerente no Teknisa, no "
                                           "recebimento físico. O robô não importa nota manual."}), 409
        finally:
            cur0.close()
        st = importar_uma(con, chave)
        # CONFERE DEPOIS: nunca confiar so no retorno da procedure
        conf = {"lancada": False, "pendencias": []}
        try:
            cur = con.cursor()
            conf = _conferir_apos_reprocesso(cur, chave)
            _auditar(cur, "REPROCESSA",
                     "resultado: %s | lancada=%s | pendencias=%d"
                     % (st, conf.get("lancada"), len(conf.get("pendencias") or [])),
                     chave)
            con.commit()
            cur.close()
        except Exception:
            pass
        _cache.pop("travadas", None)  # a lista de paradas mudou
        _cache.pop("kpis", None)
        for k in [k for k in list(_cache) if k.startswith("div:")]:
            _cache.pop(k, None)

        pend = conf.get("pendencias") or []
        if st == "IMPORTADA" and conf.get("lancada") and not pend:
            msg = ("Resolvido! A nota entrou no sistema (%s) e não sobrou nenhuma "
                   "pendência. O robô segue com o restante automaticamente."
                   % (conf.get("situacao") or "lançada"))
        elif st == "IMPORTADA" and conf.get("lancada"):
            msg = ("A nota entrou no sistema (%s), mas ainda há %d pendência(s) "
                   "registrada(s) — veja abaixo."
                   % (conf.get("situacao") or "lançada", len(pend)))
        elif st == "IMPORTADA" and not conf.get("lancada"):
            # a procedure disse que importou, mas a nota nao aparece
            msg = ("A importação respondeu OK, mas a nota não apareceu no sistema. "
                   "Confira no Teknisa antes de tentar de novo.")
        elif st == "MODO_CONSULTA":
            # a trava propria do importar_uma recusou. Se a rota chegou aqui, a
            # variavel de ambiente do PAINEL esta ligada e a do ROBO nao - sao
            # o mesmo AIHUB_MODO_ESCRITA, entao e desalinhamento de atalho.
            msg = ("A importação foi recusada porque o modo escrita está "
                   "desligado para a rotina de importação. Confira a variável "
                   "AIHUB_MODO_ESCRITA no atalho que inicia o painel.")
        elif st == "AF_VENCIDA":
            # a AF gravada nesta nota nao esta mais em aberto com saldo: outra
            # entrega consumiu. Importar agora baixaria pedido ja atendido.
            msg = ("A AF que está nesta nota não está mais em aberto com saldo "
                   "(outra entrega já a consumiu). Importar agora baixaria um "
                   "pedido já atendido. Verifique a AF no Teknisa.")
        elif st == "BLOQUEADA":
            msg = ("Ainda parada: o ajuste não passou nas regras." +
                   (" Falta resolver: " + pend[0]["titulo"] if pend else
                    " O Teknisa não registrou o motivo."))
        else:
            msg = "Não foi possível reprocessar agora."

        return jsonify({"ok": bool(st == "IMPORTADA" and conf.get("lancada") and not pend),
                        "status": st, "msg": msg,
                        "lancada": conf.get("lancada"),
                        "situacao": conf.get("situacao"),
                        "filial": conf.get("filial"), "lancto": conf.get("lancto"),
                        "pendencias": pend})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(None, con)


# ----------------------------- Notas aprovadas (revalidacao) --------------- #
@app.route("/api/aprovadas")
def aprovadas():
    """Notas de compra ja aprovadas. Filtros: fornecedor, AF, chave, periodo
    (por data de aprovacao)."""
    forn = (request.args.get("forn") or "").strip()
    af = (request.args.get("af") or "").strip()
    chave = (request.args.get("chave") or "").strip()
    dt_ini = (request.args.get("dt_ini") or "").strip()
    dt_fim = (request.args.get("dt_fim") or "").strip()
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    akey = "apr:%s:%s:%s:%s:%s:%s" % (forn, af, chave, dt_ini, dt_fim, pag)
    hit = _cache.get(akey)
    if hit and (time.time() - hit[0]) < 60:
        return jsonify(hit[1])
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        where = ["N.IDENTRSAIDOP='E'", "N.CDTIPOOPER IN (%s)" % _SQL_TIPOS,
                 "N.IDSTATUS='A'", "1=1" + _so_simplificada("N")]
        binds = {}
        if forn:
            where.append("N.CDFORNECED=:fn"); binds["fn"] = forn
        if chave:
            where.append(_cond_chave("N.NRACESSONFE", chave, binds))
        if af:
            col_af = _col_af_item(cur)
            if col_af:
                where.append("EXISTS (SELECT 1 FROM TEKNISA.ITEMPRODUTO IP WHERE "
                             "IP.CDFILIAL=N.CDFILIAL AND IP.NRLANCTONF=N.NRLANCTONF AND IP.%s=:af)" % col_af)
            else:
                where.append("N.CDFORNECED IN (SELECT DISTINCT CDFORNECED FROM TEKNISA.AUTOFORNE WHERE NRAUTOFORN=:af)")
            binds["af"] = af
        if dt_ini:
            where.append("N.DTAPROVACAO >= TO_DATE(:di,'YYYY-MM-DD')"); binds["di"] = dt_ini
        if dt_fim:
            where.append("N.DTAPROVACAO < TO_DATE(:df,'YYYY-MM-DD')+1"); binds["df"] = dt_fim
        # sem filtro explicito -> so mes vigente + anterior (nao poluir a lista).
        # se a pessoa buscar por chave/AF/fornecedor/data, ai traz o periodo pedido.
        if not (dt_ini or dt_fim or chave or af or forn):
            where.append("N.DTAPROVACAO >= TRUNC(ADD_MONTHS(SYSDATE,-1),'MM')")
        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        sql = f"""
            SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.CDFORNECED,
                   F.NMRAZSOCFORN, N.DTAPROVACAO
            FROM   TEKNISA.NOTAFISCAL N
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=N.CDFORNECED
            WHERE  {' AND '.join(where)}
            ORDER  BY N.DTAPROVACAO DESC NULLS LAST, N.NRLANCTONF DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """
        cur.execute(sql, binds)
        notas = _rows(cur)
        tem_mais = len(notas) > PG
        notas = notas[:PG]
        result = {"notas": notas, "pag": pag, "tem_mais": tem_mais}
        _cache[akey] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e), "notas": []}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Revalidar (so-leitura) ---------------------- #
@app.route("/api/revalidar")
def revalidar():
    """Revalidacao SOMENTE-LEITURA de uma nota: confere inconsistencias ativas,
    itens sem produto interno e aprovacao. Nao altera nada no banco."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"msg": "Chave nao informada."})
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        achados = []
        cur.execute("""SELECT CDFILIAL, NRLANCTONF, DTAPROVACAO, IDSTATUS
                       FROM TEKNISA.NOTAFISCAL WHERE NRACESSONFE=:c FETCH FIRST 1 ROWS ONLY""", c=chave)
        r = cur.fetchone()
        if not r:
            return jsonify({"ok": False, "achados": ["Nota nao encontrada no sistema."]})
        fil, lan, dtap, stx = r[0], r[1], r[2], r[3]
        try:
            cur.execute("SELECT COUNT(*) FROM TEKNISA.INTG_INCONSISTENCIA WHERE IDATIVO='S' AND NRACESSONFE=:c", c=chave)
            ni = int(cur.fetchone()[0])
            if ni:
                achados.append("Ha %d inconsistencia(s) ainda registrada(s) para esta nota." % ni)
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM TEKNISA.ITEMPRODUTO WHERE CDFILIAL=:f AND NRLANCTONF=:n AND CDPRODUTO IS NULL", f=fil, n=lan)
            ns = int(cur.fetchone()[0])
            if ns:
                achados.append("%d item(ns) sem produto interno vinculado." % ns)
        except Exception:
            pass
        if not dtap:
            achados.append("Nota sem data de aprovacao.")
        return jsonify({"ok": len(achados) == 0, "achados": achados,
                        "situacao": {"I": "A processar", "P": "A aprovar", "A": "Aprovada"}.get(stx, stx)})
    except Exception as e:
        return jsonify({"ok": False, "achados": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Sugestoes de vinculo de UMA nota ------------ #
# --------------------------------------------------------------------------- #
# REGRAS ENSINADAS - a pessoa corrigindo o RACIOCÍNIO, não só o resultado
#
# O ciclo é sempre o mesmo, e a ordem importa:
#   1. escrever      -> /api/regra-interpretar diz O QUE ENTENDEU
#   2. conferir      -> a pessoa lê e corrige o texto se for o caso
#   3. testar        -> /api/regra-testar roda contra os vínculos JÁ certos
#   4. adotar        -> /api/regra-salvar, só depois de ver ganho e quebra
#
# Nenhuma regra pula o passo 3. Regra sem teste é chute com aparência de
# aprendizado, e o projeto já reprovou três afrouxamentos por medição.
# --------------------------------------------------------------------------- #
@app.route("/api/regras")
def regras_listar():
    """As regras ensinadas, com o resultado do teste de cada uma."""
    status = (request.args.get("status") or "").strip().upper()
    forn = (request.args.get("forn") or "").strip()
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if not _tem_tabela(cur, "AIHUB.AIHUB_REGRA", "regras"):
            return jsonify({"regras": [], "msg":
                            "A tabela de regras ainda não foi criada. Peça à "
                            "TI para rodar sql/CRIAR_REGRAS.sql."})
        where, binds = ["1=1"], {}
        if status in ("RASCUNHO", "ATIVA", "REPROVADA", "DESATIVADA"):
            where.append("STATUS = :st")
            binds["st"] = status
        if forn:
            where.append("(CDFORNECED = :f OR ESCOPO = 'GLOBAL')")
            binds["f"] = forn
        cur.execute("""SELECT ID, TIPO, ESCOPO, CDFORNECED, TERMO, SIGNIFICA,
                              CDPRODUTOXML, CDPRODUTO, VALOR, TEXTO_PEDIDO,
                              INTERPRETACAO, STATUS, TESTADOS, GANHOU, QUEBROU,
                              TO_CHAR(DT_TESTE,'DD/MM/YYYY HH24:MI'),
                              TO_CHAR(DT_INCLUSAO,'DD/MM/YYYY HH24:MI'),
                              CD_OPERADOR
                       FROM   AIHUB.AIHUB_REGRA
                       WHERE  """ + " AND ".join(where) + """
                       ORDER  BY DT_INCLUSAO DESC
                       FETCH  FIRST 200 ROWS ONLY""", binds)
        return jsonify({"regras": _rows(cur)})
    except Exception as e:
        return jsonify({"regras": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/regra-interpretar", methods=["POST"])
def regra_interpretar():
    """O QUE O SISTEMA ENTENDEU do que a pessoa escreveu.

    Não grava nada. É o passo em que ela confere se o entendimento faz sentido
    antes de qualquer coisa valer. Deliberadamente conservador: prefere dizer
    "não entendi" a inventar uma regra que ninguém pediu.
    """
    d = request.get_json(force=True, silent=True) or {}
    texto = (d.get("texto") or "").strip()
    try:
        from core.regras import interpretar
        return jsonify(interpretar(texto))
    except Exception as e:
        return jsonify({"ok": False, "entendi": _erro_amigavel(e)}), 500


@app.route("/api/regra-testar", methods=["POST"])
def regra_testar():
    """RODA A REGRA CONTRA OS VÍNCULOS QUE JÁ ESTÃO CERTOS.

    O gabarito são os próprios De/Para ativos: para aquele fornecedor e aquele
    item, o produto correto já é conhecido. Então dá para perguntar, item a
    item, se a regra faria o robô continuar acertando.

    Não grava: é leitura pura, mesmo exigindo alguns segundos. Regra de
    fornecedor costuma responder em segundos; regra global custa mais porque
    muda o comportamento para todo mundo e por isso mede uma amostra ampla.
    """
    d = request.get_json(force=True, silent=True) or {}
    regra = _regra_do_pedido(d)
    if isinstance(regra, tuple):
        return regra
    con = cur = None
    try:
        from core.regras import testar
        con = _conn()
        cur = con.cursor()
        return jsonify(testar(cur, regra))
    except Exception as e:
        return jsonify({"msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


def _regra_do_pedido(d):
    """Valida o que veio da tela e devolve a regra, ou a resposta de erro.

    ESCOPO E OBRIGATÓRIO E EXPLÍCITO: quem ensina escolhe, a cada correção, se
    vale só para aquele fornecedor ou para todos. Não existe padrão silencioso,
    porque global é justamente o que espalha um erro pela base inteira.
    """
    tipo = (d.get("tipo") or "").strip().upper()
    escopo = (d.get("escopo") or "").strip().upper()
    forn = (d.get("forn") or "").strip()
    if tipo not in ("SINONIMO", "NEGATIVA", "LIMIAR"):
        return jsonify({"msg": "Tipo de regra desconhecido."}), 400
    if escopo not in ("FORNECEDOR", "GLOBAL"):
        return jsonify({"msg": "Escolha se a regra vale só para o fornecedor "
                               "ou para todos."}), 400
    if escopo == "FORNECEDOR" and not forn:
        return jsonify({"msg": "Regra de fornecedor precisa do fornecedor."}), 400
    regra = {"tipo": tipo, "escopo": escopo,
             "forn": forn if escopo == "FORNECEDOR" else None,
             "termo": (d.get("termo") or "").strip().upper() or None,
             "significa": (d.get("significa") or "").strip().upper() or None,
             "cprod_xml": (d.get("cprod_xml") or "").strip() or None,
             "cd_produto": (d.get("cd_produto") or "").strip() or None,
             "valor": d.get("valor")}
    if tipo == "SINONIMO" and not (regra["termo"] and regra["significa"]):
        return jsonify({"msg": "Faltou o termo ou o significado."}), 400
    if tipo == "SINONIMO":
        # UNIDADE DE MEDIDA NAO E NOME DE PRODUTO. A guarda vale aqui tambem,
        # e nao so na interpretacao do texto livre: senao bastaria preencher o
        # formulario a mao para furar a protecao.
        from core.regras import conflito_unidade
        _p = conflito_unidade(regra["termo"], regra["significa"])
        if _p:
            return jsonify({"msg": _p.replace("<b>", "").replace("</b>", "")}), 400
    if tipo == "NEGATIVA" and not (regra["cprod_xml"] and regra["cd_produto"]):
        return jsonify({"msg": "Faltou o item do fornecedor ou o produto."}), 400
    if tipo == "LIMIAR":
        try:
            regra["valor"] = float(regra["valor"])
        except (TypeError, ValueError):
            return jsonify({"msg": "Faltou o percentual."}), 400
        if not 0.50 <= regra["valor"] <= 1.0:
            return jsonify({"msg": "O percentual precisa ficar entre 50% e "
                                   "100%."}), 400
    return regra


@app.route("/api/regra-salvar", methods=["POST"])
def regra_salvar():
    """ADOTA a regra, depois de testada.

    EXIGE O TESTE. A tela manda o resultado junto; sem ele, ou com quebra de
    vínculo certo, a regra não é adotada. É a mesma disciplina do resto do
    projeto: nada muda o comportamento sem medição antes.
    """
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta, então não posso "
                               "ensinar nada ao robô. Fale com a TI."}), 403
    d = request.get_json(force=True, silent=True) or {}
    regra = _regra_do_pedido(d)
    if isinstance(regra, tuple):
        return regra
    teste = d.get("teste") or {}
    if not teste:
        return jsonify({"ok": False,
                        "msg": "Teste a regra contra o histórico antes de "
                               "adotá-la."}), 400
    quebrou = int(teste.get("quebrou") or 0)
    if quebrou and not d.get("mesmo_quebrando"):
        return jsonify({"ok": False,
                        "msg": "Esta regra quebra %d vínculo(s) que hoje estão "
                               "certos. Se ainda assim quiser adotar, confirme "
                               "explicitamente." % quebrou}), 409
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""INSERT INTO AIHUB.AIHUB_REGRA
            (TIPO, ESCOPO, CDFORNECED, TERMO, SIGNIFICA, CDPRODUTOXML,
             CDPRODUTO, VALOR, TEXTO_PEDIDO, INTERPRETACAO, STATUS,
             TESTADOS, GANHOU, QUEBROU, DT_TESTE, CD_OPERADOR, OBS)
            VALUES (:tp, :es, :fo, :te, :si, :cx, :cp, :vl, :txt, :itp,
                    'ATIVA', :ts, :ga, :qb, SYSDATE, :op, :ob)""",
            tp=regra["tipo"], es=regra["escopo"], fo=regra["forn"],
            te=regra["termo"], si=regra["significa"], cx=regra["cprod_xml"],
            cp=regra["cd_produto"], vl=regra["valor"],
            txt=(d.get("texto") or "")[:1000],
            itp=(d.get("entendi") or "")[:1000],
            ts=int(teste.get("testados") or 0),
            ga=int(teste.get("ganhou") or 0), qb=quebrou,
            op=_operador()[:30], ob=(d.get("obs") or "")[:400])
        _auditar(cur, "REGRA", "ensinou %s (%s): %s"
                 % (regra["tipo"], regra["escopo"], (d.get("entendi") or "")[:150]))
        con.commit()
        from core.regras import esquecer
        esquecer()          # o robô passa a usar a regra na próxima leitura
        return jsonify({"ok": True, "msg": "Regra adotada. O robô já usa na "
                                           "próxima nota."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/regra-desativar", methods=["POST"])
def regra_desativar():
    """Desliga uma regra. Não apaga: a trilha de quem ensinou o quê continua."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta."}), 403
    d = request.get_json(force=True, silent=True) or {}
    rid = d.get("id")
    if not rid:
        return jsonify({"ok": False, "msg": "Faltou a regra."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""UPDATE AIHUB.AIHUB_REGRA SET STATUS='DESATIVADA',
                              OBS=:ob
                       WHERE  ID=:i AND STATUS='ATIVA'""",
                    ob=(d.get("obs") or "")[:400], i=rid)
        if not cur.rowcount:
            return jsonify({"ok": False,
                            "msg": "Esta regra já não estava ativa."}), 409
        _auditar(cur, "REGRA", "desativou a regra %s" % rid)
        con.commit()
        from core.regras import esquecer
        esquecer()
        return jsonify({"ok": True, "msg": "Regra desligada."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/raciocinio")
def raciocinio():
    """POR QUE O ROBÔ DECIDIU ASSIM, item a item.

    A pessoa via a conclusão e não via o caminho. Sem o caminho ela não julga
    se a conclusão faz sentido, e muito menos aponta onde o raciocínio errou.

    Usa `core.explicador`, que percorre as MESMAS funções do matcher. Nada é
    recalculado por fora: se a regra mudar no matcher, a explicação muda junto.
    Rota de leitura, não grava nada.
    """
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"itens": [], "msg": "Chave não informada."})
    if not _HAS_MATCHER:
        return jsonify({"itens": [], "msg": "Matcher indisponível."})
    con = cur = None
    try:
        from core.explicador import explicar
        con = _conn()
        cur = con.cursor()
        # A TELA PROMETE "numero OU chave", entao a rota tem de aceitar os dois.
        # O numero NAO e unico (fornecedores diferentes repetem numeracao), por
        # isso pega a mais recente e diz qual foi - escolher em silencio entre
        # duas notas seria mostrar o raciocinio da nota errada.
        so_dig = "".join(c for c in chave if c.isdigit())
        aviso = None
        if not (len(so_dig) == 44):
            cur.execute("""SELECT NRACESSONFE, COUNT(*) OVER () QTD
                           FROM   TEKNISA.INTG_NOTAFISCAL
                           WHERE  TRIM(LEADING '0' FROM NRNOTAFISC) = :n
                           ORDER  BY DTEMISSAO DESC NULLS LAST
                           FETCH  FIRST 1 ROWS ONLY""",
                        n=so_dig.lstrip("0") or "0")
            achou = cur.fetchone()
            if not achou:
                return jsonify({"itens": [],
                                "msg": "Não achei nota com esse número. Use a "
                                       "chave de 44 dígitos se souber."})
            chave = achou[0]
            if int(achou[1] or 1) > 1:
                aviso = ("Existem %d notas com este número. Mostrando a mais "
                         "recente." % int(achou[1]))
        cur.execute("""SELECT G.CDFORNECED, G.NRNOTAFISC, F.NMRAZSOCFORN,
                              G.CDFILIRECE, G.DTEMISSAO
                       FROM   TEKNISA.INTG_NOTAFISCAL G
                       LEFT   JOIN TEKNISA.FORNECEDOR F
                              ON F.CDFORNECED = G.CDFORNECED
                       WHERE  G.NRACESSONFE = :c""", c=chave)
        r = cur.fetchone()
        if not r:
            return jsonify({"itens": [], "msg": "Nota não encontrada na integração."})
        forn = r[0]
        cab = {"numero": (r[1] or "").lstrip("0"), "cd_forneced": forn,
               "fornecedor": _valor_json(r[2]) or forn, "filial": r[3],
               "emissao": r[4].strftime("%d/%m/%Y") if r[4] else None,
               "chave": chave}

        # a AF, quando existe, estreita a lista de candidatos em ~97%. O robô
        # usa a mesma lista, então a explicação tem de usar também.
        cur.execute("""SELECT DISTINCT NRAUTOFORN FROM TEKNISA.INTG_ITEMPRODUTO
                       WHERE NRACESSONFE = :c AND NRAUTOFORN IS NOT NULL""", c=chave)
        afs = [a[0] for a in cur.fetchall()]
        cands = _cands_af(cur, forn, afs[0] if len(afs) == 1 else None, cab["filial"])
        un_de = {c[0]: (c[2] if len(c) > 2 else None) for c in (cands or [])}

        cur.execute("""SELECT I.NRSEQITPROD, I.CDPRODUTO, I.NMPRODUTO,
                              I.SGUNIDADE, I.NRAUTOFORN, D.CDPRODUTO
                       FROM   TEKNISA.INTG_ITEMPRODUTO I
                       LEFT   JOIN TEKNISA.PRODFORNXML D
                              ON D.CDFORNECED = :f AND D.CDPRODUTOXML = I.CDPRODUTO
                             AND D.IDATIVO = 'S'
                       WHERE  I.NRACESSONFE = :c
                       ORDER  BY I.NRSEQITPROD""", f=forn, c=chave)
        itens = []
        for seq, cprod, nome, un, af, ja in cur.fetchall():
            desc = _fix(_valor_json(nome) or "")
            e = explicar(desc, cands, un, un_de)
            e.update({"seq": seq, "cod_fornecedor": cprod, "af_no_item": af,
                      "ja_vinculado": ja})
            if ja:
                # item que JA tem De/Para: o robô nem chega a comparar texto.
                # Dizer isso é mais honesto que mostrar um ranking que ele não usou.
                e["regra"] = ("Este item já tem De/Para gravado (produto %s). O "
                              "robô usa o vínculo existente e não compara texto. "
                              "O raciocínio abaixo mostra o que ele faria se o "
                              "vínculo não existisse." % ja)
                e["decisao"] = "ja_vinculado"
            itens.append(e)
        return jsonify({"cabecalho": cab, "itens": itens, "aviso": aviso,
                        "chave": chave,
                        "candidatos_da_af": bool(afs and len(afs) == 1)})
    except Exception as e:
        return jsonify({"itens": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/lotes")
def lotes():
    """OS LOTES DE CHEGADA DAS NOTAS.

    Medido em produção: as notas NÃO chegam continuamente. Elas entram em lote
    a cada 2 horas, sempre por volta do minuto 27 (01:27, 03:27, 05:27...), com
    16 a 33 notas de cada vez; durante o expediente há inclusões avulsas por
    cima disso. Pedir "das 14h às 15h" pode devolver zero legitimamente.

    Por isso a tela mostra os lotes que existem, em vez de a pessoa adivinhar
    o horário. O recorte livre continua disponível em `/api/naoutilizadas`.

    A coluna é DTINCLUSAO: tem hora em 100% das linhas e é idêntica à
    DTIMPORTACAOXML nas 2.004 conferidas.
    """
    dias = min(120, max(1, request.args.get("dias", default=2, type=int)))
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # a coluna de origem varia por versao: sem ela a tela ainda funciona,
        # so nao separa a nota manual (que fica fora da automacao, achado 18)
        col_org = _col_disponivel(cur, "INTG_NOTAFISCAL", ["DSTAGORIGFILI"],
                                  "intg_orig")
        nao_manual = ("NVL(G.%s,'x') <> 'Manual'" % col_org) if col_org else "1=1"
        cur.execute("""
            SELECT TO_CHAR(G.DTINCLUSAO,'YYYY-MM-DD') DIA,
                   TO_CHAR(G.DTINCLUSAO,'HH24') HORA,
                   COUNT(*) TOTAL,
                   SUM(CASE WHEN G.DTUTILIZAXML IS NULL THEN 1 ELSE 0 END) PARADAS,
                   SUM(CASE WHEN G.DTUTILIZAXML IS NULL
                             AND G.CDTIPOOPER IN (""" + _SQL_TIPOS + """)
                             AND """ + nao_manual + """
                            THEN 1 ELSE 0 END) DO_ROBO,
                   MIN(TO_CHAR(G.DTINCLUSAO,'HH24:MI')) INICIO,
                   MAX(TO_CHAR(G.DTINCLUSAO,'HH24:MI')) FIM
            FROM   TEKNISA.INTG_NOTAFISCAL G
            WHERE  G.DTINCLUSAO >= TRUNC(SYSDATE) - :d
            GROUP  BY TO_CHAR(G.DTINCLUSAO,'YYYY-MM-DD'),
                      TO_CHAR(G.DTINCLUSAO,'HH24')
            ORDER  BY 1 DESC, 2 DESC""", d=dias)
        saida = []
        for dia, hora, tot, par, robo, ini, fim in cur.fetchall():
            saida.append({"dia": dia, "hora": hora,
                          "rotulo": "%s às %s" % (ini, fim) if ini != fim else ini,
                          "total": int(tot), "paradas": int(par or 0),
                          "do_robo": int(robo or 0),
                          "de": "%s %s:00" % (dia, hora),
                          "ate": "%s %s:59" % (dia, hora)})
        return jsonify({"lotes": saida, "dias": dias})
    except Exception as e:
        return jsonify({"lotes": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/nota-sugestoes")
def nota_sugestoes():
    """Para uma nota (pela chave), le os itens e, para cada um, sugere o produto
    interno a vincular (matcher contra os produtos da AF do fornecedor) - do
    mesmo jeito que a fila de 'Produtos a vincular'. Nao grava nada."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"itens": [], "msg": "Chave nao informada."})
    if not _HAS_MATCHER:
        return jsonify({"itens": [], "msg": "Matcher indisponivel (instale rapidfuzz)."})
    con = cur = None
    try:
        from core.ai_matcher import escolher, faixa as _faixa
        con = _conn()
        cur = con.cursor()
        origem = "lancamento"
        cur.execute("""SELECT CDFILIAL, NRLANCTONF, CDFORNECED FROM TEKNISA.NOTAFISCAL
                       WHERE NRACESSONFE=:c FETCH FIRST 1 ROWS ONLY""", c=chave)
        r = cur.fetchone()
        if r:
            fil, lan, forn = r[0], r[1], r[2]
            # SGUNIDADEXML: a unidade que o fornecedor usou no XML. E o mesmo
            # sinal que o robo usa para desempatar - painel e robo tem que
            # sugerir a MESMA coisa, senao a pessoa ve uma sugestao e o robo
            # grava outra.
            cur.execute("""SELECT NRSEQITPROD, CDPRODEMIS, NMPRODUTOXML, CDPRODUTO,
                                  SGUNIDADEXML
                           FROM TEKNISA.ITEMPRODUTO WHERE CDFILIAL=:f AND NRLANCTONF=:n
                           ORDER BY NRSEQITPROD""", f=fil, n=lan)
            itens = cur.fetchall()
        else:
            # A nota AINDA NAO foi lancada (e o caso das "Notas com produto
            # divergente"!). Antes esta tela nao conseguia sugerir NADA justamente
            # para as notas que ela mesma lista. Agora lemos os itens direto da
            # integracao/XML.
            itens, forn = _itens_da_integracao(cur, chave)
            origem = "integracao"
            if not itens:
                return jsonify({"itens": [], "msg":
                                "Não consegui ler os itens desta nota (ela ainda não "
                                "entrou no sistema e não achei os itens na integração). "
                                "Reprocesse a nota ou vincule manualmente."})
        cands = _cands_af(cur, forn) if forn else []
        out = []
        for _linha in itens:
            # a origem "lancamento" traz 5 campos (com a unidade do XML); a
            # origem "integracao" traz 4 - a tabela de itens da integracao e
            # descoberta em runtime e nem toda versao tem a coluna de unidade.
            seq, cprod, nmxml, cdprod = _linha[0], _linha[1], _linha[2], _linha[3]
            un_xml = _linha[4] if len(_linha) > 4 else None
            nmxml = _fix(nmxml or "")
            jj = None
            if forn and cprod:
                cur.execute("""SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML
                               WHERE CDFORNECED=:f AND CDPRODUTOXML=:c AND IDATIVO='S'
                               ORDER BY DTINCLUSAO DESC NULLS LAST
                               FETCH FIRST 1 ROWS ONLY""", f=forn, c=cprod)
                jj = cur.fetchone()
            best = best_sug = None
            if nmxml and cands:
                best, best_sug = escolher(nmxml, cands, un_xml)
            out.append({
                "seq": seq, "cprod_xml": cprod, "desc_xml": nmxml,
                "cd_atual": cdprod, "ja_vinculado": (jj[0] if jj else None),
                # mostra a sugestao "humana" (prefere generico), mas a FAIXA vem
                # do score bruto - mesmo criterio do robo
                "sug_cd": (best_sug[0] if best_sug else None),
                "sug_nome": (_fix(best_sug[1] or "") if best_sug else None),
                "score": (round(best[2], 3) if best else 0),
                "faixa": (_faixa(best[2]) if best else "baixa"),
            })
        return jsonify({"itens": out, "cd_forneced": forn, "total": len(out),
                        "origem": origem})
    except Exception as e:
        return jsonify({"itens": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Produto divergente (busca agil) ------------- #
@app.route("/api/divergencias")
def divergencias():
    """Notas PARADAS por PRODUTO DIVERGENTE (produto nao corresponde com a AF /
    nao e oriundo de AF / deve estar relacionado). Busca por chave, AF ou
    fornecedor. Usada dentro de 'Produtos a vincular'."""
    chave = (request.args.get("chave") or "").strip()
    af = (request.args.get("af") or "").strip()
    forn = (request.args.get("forn") or "").strip()
    cnpj = "".join(c for c in forn if c.isdigit())
    # faixa: atual (fila do dia) | atrasadas (mes anterior) | historico | tudo
    faixa = (request.args.get("faixa") or "recentes").strip().lower()
    if (request.args.get("tudo") or "") == "1":
        faixa = "tudo"
    if faixa not in ("recentes", "atual", "atrasadas", "historico", "tudo"):
        faixa = "recentes"
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    key = "div:%s:%s:%s:%s:%s" % (chave, cnpj, af, faixa, pag)

    def _calc(cur):
        # fragmentos ASCII (robustos a acento) que marcam divergencia de produto
        # Filtro por CODIGO (CDCONSISTXML) - muito mais confiavel que procurar
        # trechos de texto, que vem com acento quebrado. Mantem o LIKE como rede
        # de seguranca para codigos que ainda nao mapeamos.
        # Filtro por CODIGO oficial + rede de seguranca SO com as palavras que
        # realmente indicam problema de PRODUTO.
        # CUIDADO: '%RELACIONAD%' e '%CORRESPONDE%' pareciam inofensivos mas
        # capturavam motivos FISCAIS - "filial nao RELACIONADa a inscricao
        # estadual" caia na aba de produto divergente. Removidos.
        # REGRA: o CODIGO manda. O texto so decide para codigos que ainda nao
        # mapeamos (rede de seguranca), senao motivos de preco/fiscal que citam
        # a palavra "produto" (ex.: 055 "valor unitario DO PRODUTO difere")
        # acabavam nesta aba, que e so de vinculo de produto.
        _prod = ",".join("'%s'" % c.lstrip("0") for c in COD_PRODUTO)
        _conhecidos = ",".join("'%s'" % c for c in sorted(CONSIST_N))
        base = ("SELECT DISTINCT NRACESSONFE, SUBSTR(DSINCONSIS,1,180) AS DSINCONSIS "
                "FROM TEKNISA.INTG_INCONSISTENCIA II WHERE II.IDATIVO='S' AND ("
                "TRIM(LEADING '0' FROM II.CDCONSISTXML) IN (" + _prod + ") "
                "OR (TRIM(LEADING '0' FROM II.CDCONSISTXML) NOT IN (" + _conhecidos + ") "
                "    AND (UPPER(DSINCONSIS) LIKE '%PRODUTO%' "
                "      OR UPPER(DSINCONSIS) LIKE '%PODUTO%')))"
                # mesma triagem das 'Notas paradas' (criterio unificado)
                + _sql_ainda_parada(cur, "II"))
        base += _sql_periodo("II", faixa)   # padrao: mes vigente (fila do dia)
        binds = {}
        if chave:
            base += " AND " + _cond_chave("NRACESSONFE", chave, binds)
        if cnpj:
            base += " AND SUBSTR(NRACESSONFE,7,14) = :cnpj"
            binds["cnpj"] = cnpj
        if af:
            base += (" AND SUBSTR(NRACESSONFE,7,14) IN (SELECT DISTINCT "
                     "REGEXP_REPLACE(CDFORNECED,'[^0-9]','') FROM TEKNISA.AUTOFORNE WHERE NRAUTOFORN=:af)")
            binds["af"] = af
        # total (contagem de notas distintas) so na 1a pagina - o resto reaproveita
        total = None
        if pag == 0:
            cur.execute("SELECT COUNT(*) FROM (SELECT DISTINCT NRACESSONFE FROM (" + base + "))", binds)
            total = int(cur.fetchone()[0])
        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        # traz o NOME do fornecedor (antes so o CNPJ cru) e a competencia
        sql = ("SELECT T.NRACESSONFE, T.CNPJ, T.MOTIVOS, F.NMRAZSOCFORN AS FORNECEDOR, "
               "TO_CHAR(TO_DATE(SUBSTR(T.NRACESSONFE,3,4),'YYMM'),'MM/YYYY') AS COMPETENCIA "
               "FROM (SELECT NRACESSONFE, SUBSTR(NRACESSONFE,7,14) AS CNPJ, "
               "LISTAGG(DSINCONSIS,'  |  ') WITHIN GROUP (ORDER BY RN) AS MOTIVOS FROM ("
               "SELECT NRACESSONFE, DSINCONSIS, ROW_NUMBER() OVER (PARTITION BY NRACESSONFE "
               "ORDER BY DSINCONSIS) RN FROM (" + base + ")) WHERE RN <= 6 "
               "GROUP BY NRACESSONFE, SUBSTR(NRACESSONFE,7,14) ORDER BY NRACESSONFE) T "
               "LEFT JOIN TEKNISA.FORNECEDOR F "
               "ON REGEXP_REPLACE(F.CDFORNECED,'[^0-9]','') = T.CNPJ "
               "OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY")
        cur.execute(sql, binds)
        rows = _rows(cur)
        tem_mais = len(rows) > PG
        rows = rows[:PG]
        return {"notas": rows, "total": total, "pag": pag,
                "tem_mais": tem_mais, "faixa": faixa}

    try:
        return jsonify(_cached(key, 180, lambda: _com_conexao(_calc)))
    except Exception as e:
        return jsonify({"notas": [], "total": 0, "erro": _erro_amigavel(e)}), 500


# ----------------------------- Notas travadas (integracao) ----------------- #
@app.route("/api/travadas")
def travadas():
    """Notas paradas na integracao com o MOTIVO (INTG_INCONSISTENCIA).
    Automatiza a triagem que hoje e feita nota a nota na tela.
    Agora com FILTROS e PAGINACAO: antes o contador mostrava o total real mas a
    lista trazia so 100 - quem buscasse uma nota fora dessas 100 concluia que
    ela 'nao existia'."""
    chave = (request.args.get("chave") or "").strip()
    cnpj = "".join(c for c in (request.args.get("cnpj") or "") if c.isdigit())
    # cod: filtra por UM motivo especifico (o codigo do ERP). E o que faz o
    # agrupamento por motivo funcionar de verdade - sem isto, clicar num grupo de
    # 1.322 notas filtraria so as 50 da pagina carregada, e a pessoa concluiria
    # que "so tem essas".  So aceita digito, e o valor entra por bind.
    cod = "".join(c for c in (request.args.get("cod") or "") if c.isdigit())[:3]
    # faixa: atual (fila do dia) | atrasadas (mes anterior) | historico | tudo
    faixa = (request.args.get("faixa") or "recentes").strip().lower()
    if (request.args.get("tudo") or "") == "1":
        faixa = "tudo"
    if faixa not in ("recentes", "atual", "atrasadas", "historico", "tudo"):
        faixa = "recentes"
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    key = "trav:%s:%s:%s:%s:%s" % (chave, cnpj, faixa, cod, pag)

    def _calc(cur):
        cond = ["I.IDATIVO='S'"]
        binds = {}
        if chave:
            if len(chave) == 44 and chave.isdigit():
                cond.append(_cond_chave("I.NRACESSONFE", chave, binds))
        if cnpj:
            cond.append("SUBSTR(I.NRACESSONFE,7,14) = :cnpj"); binds["cnpj"] = cnpj
        if cod:
            # so as notas que tem ESTE motivo (o codigo vem sem zeros a esquerda)
            cond.append("EXISTS (SELECT 1 FROM TEKNISA.INTG_INCONSISTENCIA C"
                        " WHERE C.NRACESSONFE = I.NRACESSONFE AND C.IDATIVO='S'"
                        "   AND TRIM(LEADING '0' FROM C.CDCONSISTXML) = :cod)")
            binds["cod"] = cod
        w = (" AND ".join(cond) + _sql_ainda_parada(cur, "I")
             + _sql_periodo("I", faixa))   # padrao: mes vigente (fila do dia)

        total = None
        if pag == 0:
            cur.execute("SELECT COUNT(DISTINCT I.NRACESSONFE) "
                        "FROM TEKNISA.INTG_INCONSISTENCIA I WHERE " + w, binds)
            total = int(cur.fetchone()[0])

        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        # No maximo 4 motivos distintos por nota -> nunca estoura o limite (ORA-01489)
        # Traz tambem o NOME do fornecedor (antes so aparecia o CNPJ cru) e a
        # data de emissao, para dar para priorizar a mais antiga.
        cur.execute("""
            SELECT T.NRACESSONFE, T.CNPJ, T.MOTIVOS, T.CODIGOS,
                   F.NMRAZSOCFORN AS FORNECEDOR,
                   TO_CHAR(TO_DATE(SUBSTR(T.NRACESSONFE,3,4),'YYMM'),'MM/YYYY') AS COMPETENCIA
            FROM (
              SELECT NRACESSONFE, SUBSTR(NRACESSONFE,7,14) AS CNPJ,
                     LISTAGG(DSINCONSIS, '  |  ') WITHIN GROUP (ORDER BY RN) AS MOTIVOS,
                     LISTAGG(COD, ',') WITHIN GROUP (ORDER BY RN) AS CODIGOS
              FROM (
                SELECT NRACESSONFE, DSINCONSIS, COD,
                       ROW_NUMBER() OVER (PARTITION BY NRACESSONFE ORDER BY DSINCONSIS) RN
                FROM (
                  SELECT DISTINCT I.NRACESSONFE, SUBSTR(I.DSINCONSIS,1,120) AS DSINCONSIS,
                         TRIM(LEADING '0' FROM I.CDCONSISTXML) AS COD
                  FROM   TEKNISA.INTG_INCONSISTENCIA I
                  WHERE  """ + w + """
                )
              )
              WHERE RN <= 4
              GROUP BY NRACESSONFE, SUBSTR(NRACESSONFE,7,14)
              ORDER BY NRACESSONFE
            ) T
            LEFT JOIN TEKNISA.FORNECEDOR F
                   ON REGEXP_REPLACE(F.CDFORNECED,'[^0-9]','') = T.CNPJ
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """, binds)
        rows = _rows(cur)
        tem_mais = len(rows) > PG
        rows = rows[:PG]
        for r in rows:
            m = (r.get("MOTIVOS") or "")
            cods = r.get("CODIGOS") or ""
            r["SEM_AF"] = ("31" in [c.strip() for c in cods.split(",")]
                           or "possuir AF" in m)
            quem, titulo, acao = _classifica(cods)
            r["QUEM_RESOLVE"] = quem      # ROBO / COMPRAS / FISCAL / SISTEMA
            r["TITULO"] = titulo          # ex.: "Item a mais ou diferente do pedido"
            r["ACAO"] = acao              # o que a pessoa tem que fazer
        return {"notas": rows, "total": total, "pag": pag,
                "tem_mais": tem_mais, "faixa": faixa}
    try:
        return jsonify(_cached(key, 180, lambda: _com_conexao(_calc)))
    except Exception as e:
        return jsonify({"notas": [], "total": 0, "erro": _erro_amigavel(e)}), 500


# --------------------------------------------------------------------------- #
# DICIONARIO DOS MOTIVOS DE BLOQUEIO (TEKNISA.INTG_INCONSISTENCIA.CDCONSISTXML)
# Levantado do proprio banco em 28/07/2026. Classificar por CODIGO e MUITO mais
# confiavel do que procurar trechos de frase: o texto vem com acento quebrado
# ("Ã¡rvore", "Operac?o") e muda entre versoes.
#   quem_resolve: ROBO   -> o nosso robo resolve (vinculo de produto)
#                 COMPRAS-> pedido/AF, preco, quantidade
#                 FISCAL -> impostos, CST, CFOP, cadastro da filial
#                 SISTEMA-> ja resolvido / parametrizacao
# --------------------------------------------------------------------------- #
CONSIST = {
    # --- vinculo de produto: e o que o nosso robo faz ---
    "071": ("ROBO",    "Produto vinculado errado",
            "O produto vinculado não corresponde ao da AF. Corrija o vínculo em "
            "'Produtos a vincular'."),
    "032": ("ROBO",    "Produto sem vínculo",
            "Vincule os produtos da nota em 'Produtos a vincular'."),
    "014": ("ROBO",    "Produto sem vínculo (ou marca diferente)",
            "Vincule o produto em 'Produtos a vincular' e confira a marca."),
    # --- pedido / AF: compras ---
    "031": ("COMPRAS", "Nota sem AF",
            "A nota não tem AF (Autorização de Fornecimento). Crie/relacione a AF "
            "ou recuse a nota."),
    "056": ("COMPRAS", "Item a mais ou diferente do pedido",
            "Veio um item que não estava na AF. Inclua na AF ou devolva o item ao "
            "fornecedor."),
    "055": ("COMPRAS", "Preço diferente do pedido",
            "O valor unitário difere da AF. Confira o preço com o fornecedor ou "
            "ajuste a AF."),
    "054": ("COMPRAS", "Quantidade diferente do pedido",
            "A quantidade difere da AF. Confira a entrega e ajuste a AF."),
    "020": ("COMPRAS", "AF baixada ou inexistente",
            "A AF informada no XML não existe ou já foi baixada. Verifique com "
            "compras."),
    "017": ("COMPRAS", "AF fora do período",
            "A AF não está cadastrada ou está fora do prazo permitido."),
    "029": ("COMPRAS", "Filial diferente da AF",
            "A filial do XML é diferente da filial da AF."),
    # --- datas / vencimento ---
    "050": ("COMPRAS", "Vencimento diferente da AF",
            "A data de vencimento difere da programada na AF. Ajuste a AF ou "
            "combine com o fornecedor."),
    "013": ("FISCAL",  "Vencimento do XML difere do calculado",
            "Revise a regra de prazo/vencimento com a equipe fiscal."),
    "047": ("FISCAL",  "Parcelas não fecham com o total",
            "A soma dos vencimentos difere do total da nota."),
    "051": ("FISCAL",  "Vencimento anterior à data de processamento",
            "A data de vencimento está no passado."),
    # --- impostos / fiscal ---
    "6":   ("FISCAL",  "Imposto obrigatório ausente",
            "Falta a incidência de um imposto obrigatório no item. Revise a "
            "parametrização fiscal."),
    "008": ("FISCAL",  "Valor do imposto diferente do calculado",
            "Revise a parametrização do imposto para este item."),
    "009": ("FISCAL",  "Alíquota diferente da parametrizada",
            "Revise a alíquota cadastrada para este item."),
    "052": ("FISCAL",  "CST inválido",
            "Código de situação tributária inválido no item."),
    "058": ("FISCAL",  "CST inválido no imposto",
            "Situação tributária do imposto inválida."),
    "053": ("FISCAL",  "CFOP inválido",
            "Código fiscal de operação inválido no item."),
    "059": ("FISCAL",  "CFOP inválido no imposto",
            "Código fiscal de operação inválido no imposto do item."),
    "064": ("FISCAL",  "Tipo de operação x CFOP",
            "O tipo de operação não está relacionado ao CFOP do XML."),
    "005": ("FISCAL",  "Tipo de operação não relacionado",
            "Relacione o tipo de operação."),
    "046": ("FISCAL",  "Total não fecha com os itens",
            "O total da nota difere da soma dos itens. Confira com o fornecedor."),
    # --- cadastro da filial/empresa ---
    "025": ("FISCAL",  "Inscrição estadual não relacionada",
            "A filial/empresa não está relacionada à inscrição estadual do XML."),
    "027": ("FISCAL",  "Sem inscrição municipal",
            "Falta inscrição municipal para a filial/inscrição estadual."),
    "021": ("FISCAL",  "Filial inativa ou não informada",
            "Filial inativa ou ausente no XML."),
    "061": ("FISCAL",  "Tipo de conta a pagar não parametrizado",
            "Parametrize o tipo de conta a pagar."),
    "062": ("FISCAL",  "Centro de custo não parametrizado",
            "Parametrize o centro de custo da filial/empresa."),
    # --- ja resolvido ---
    "030": ("SISTEMA", "Nota já cadastrada",
            "O próprio sistema informa que esta nota já foi cadastrada."),
}
# Codigos que indicam PROBLEMA DE PRODUTO (a aba "Notas com produto divergente")
COD_PRODUTO = ("071", "032", "014", "056")
# Codigos que dizem que a nota JA ESTA no sistema -> nao e pendencia.
# Usado por _sql_ainda_parada() para nao listar como "parada" uma nota que o
# proprio Teknisa ja deu como cadastrada.
COD_JA_RESOLVIDO = ("030",)


def _norm_cod(c):
    """'071' e '71' sao o mesmo codigo. O SQL ja tira os zeros a esquerda, mas
    o dicionario foi escrito como vem na tela do Teknisa - normaliza os dois."""
    c = (c or "").strip()
    return c.lstrip("0") or c


# indice normalizado, montado uma vez
CONSIST_N = {_norm_cod(k): v for k, v in CONSIST.items()}


def _classifica(codigos):
    """Recebe os codigos de inconsistencia de uma nota e devolve
    (quem_resolve, titulo, acao) do motivo mais relevante.
    Prioridade: produto (nosso robo) > compras > fiscal > sistema.

    SO POR CODIGO, de proposito: havia um parametro 'textos' que nunca foi
    usado no corpo. Classificar pelo texto e justamente o erro que este
    dicionario existe para evitar - o texto vem com acento quebrado e palavra
    generica engana (ver o caso do '%RELACIONAD%' nos comentarios acima)."""
    lista = [_norm_cod(c) for c in (codigos or "").split(",") if c.strip()]
    for grupo in ("ROBO", "COMPRAS", "FISCAL", "SISTEMA"):
        for c in lista:
            info = CONSIST_N.get(c)
            if info and info[0] == grupo:
                return info
    return ("OUTRO", "Bloqueio na importação",
            "Abra a nota para ver o motivo registrado pelo Teknisa.")


def _calc_paradas_con(con):
    """Conta as notas paradas agrupadas por QUEM RESOLVE (robo / compras /
    fiscal). E a consulta mais pesada do painel - por isso roda em segundo
    plano e fica em cache."""
    cur = con.cursor()
    try:
        d = {"ROBO": 0, "COMPRAS": 0, "FISCAL": 0, "SISTEMA": 0, "OUTRO": 0}
        cur.execute("""
            SELECT TRIM(LEADING '0' FROM I.CDCONSISTXML) AS COD,
                   COUNT(DISTINCT I.NRACESSONFE) AS QT
            FROM   TEKNISA.INTG_INCONSISTENCIA I
            WHERE  I.IDATIVO='S'
              AND  SUBSTR(I.NRACESSONFE,3,4) IN
                   (TO_CHAR(SYSDATE,'YYMM'), TO_CHAR(ADD_MONTHS(SYSDATE,-1),'YYMM'))
              """ + _sql_ainda_parada(cur, "I") + """
            GROUP BY TRIM(LEADING '0' FROM I.CDCONSISTXML)
        """)
        for cod, qt in cur.fetchall():
            quem = (CONSIST_N.get(_norm_cod(cod)) or ("OUTRO",))[0]
            d[quem] = d.get(quem, 0) + int(qt)
        return d
    finally:
        try:
            cur.close()
        except Exception:
            pass


# ----------------------------- Notas paradas AGRUPADAS POR MOTIVO ---------- #
@app.route("/api/paradas-por-motivo")
def paradas_por_motivo():
    """As notas paradas agrupadas pelo MOTIVO, com quantas notas em cada um.

    POR QUE ESTA TELA EXISTE: o trabalho e repetitivo por motivo, nao por nota.
    Medido em 03/08/2026: 1.322 notas com "valor unitario difere", 830 com
    "quantidade difere", 755 com "vencimento difere". Numa lista plana de 7.488
    notas isso nao aparece - a pessoa rola a tela sem perceber que esta
    resolvendo a MESMA coisa dezenas de vezes.

    Agrupado, ela ve o tamanho de cada bolo, ataca o maior, e sabe de quem e:
    59,6% do que trava e de Compras, 27,4% do Fiscal e 12,8% do nosso robo."""
    faixa = (request.args.get("faixa") or "recentes").strip().lower()
    if faixa not in ("recentes", "atual", "atrasadas", "historico", "tudo"):
        faixa = "recentes"
    key = "pmot:%s" % faixa

    def _calc(cur):
        cur.execute("""
            SELECT TRIM(LEADING '0' FROM I.CDCONSISTXML) AS COD,
                   COUNT(DISTINCT I.NRACESSONFE) AS QT,
                   SUBSTR(MIN(I.DSINCONSIS),1,180) AS EXEMPLO
            FROM   TEKNISA.INTG_INCONSISTENCIA I
            WHERE  I.IDATIVO='S'
              """ + _sql_periodo("I", faixa) + """
              """ + _sql_ainda_parada(cur, "I") + """
            GROUP BY TRIM(LEADING '0' FROM I.CDCONSISTXML)
        """)
        grupos, total = [], 0
        por_quem = {}
        for r in _rows(cur):
            cod = str(r.get("COD") or "")
            qt = int(r.get("QT") or 0)
            quem, titulo, acao = _classifica(cod)
            total += qt
            por_quem[quem] = por_quem.get(quem, 0) + qt
            grupos.append({"cod": cod, "notas": qt, "quem": quem,
                           "titulo": titulo, "acao": acao,
                           "exemplo": _fix(r.get("EXEMPLO") or "")})
        # maior bolo primeiro: e a ordem em que compensa atacar
        grupos.sort(key=lambda g: -g["notas"])
        return {"grupos": grupos, "total": total, "por_quem": por_quem,
                "faixa": faixa}

    try:
        return jsonify(_cached(key, 180, lambda: _com_conexao(_calc)))
    except Exception as e:
        return jsonify({"grupos": [], "total": 0,
                        "erro": _erro_amigavel(e)}), 500


# --------------------------------------------------------------------------- #
# SIMULACAO DE AUTONOMIA  -  "o que o robo faria, sem fazer"
# 100% LEITURA: nao grava nada, nao chama procedure, nao confirma nota.
# Serve para validar o comportamento autonomo SEM usar producao e sem risco.
# --------------------------------------------------------------------------- #
@app.route("/api/simular")
def simular():
    limite = min(100, max(1, request.args.get("limite", default=10, type=int)))
    con = cur = None
    t0 = time.time()
    try:
        from core.ai_matcher import escolher, pode_auto, faixa as _faixa
        con = _conn()
        cur = con.cursor()
        rel = {"limite": limite, "notas": [], "somente_leitura": True, "tempos": {}}

        # ---- ETAPA 1: notas que nem chegaram a entrar (paradas na importacao)
        # NAO segura a simulacao esperando essa contagem. Ela e a consulta mais
        # pesada do sistema; se ainda nao estiver em cache, a simulacao responde
        # sem ela e o calculo roda em segundo plano para a proxima vez.
        # (antes, a simulacao inteira esperava por isso e estourava o tempo)
        hit = _cache.get("sim:paradas")
        if hit and (time.time() - hit[0]) < 900:
            rel["paradas_por_responsavel"] = hit[1]
        else:
            rel["paradas_por_responsavel"] = None
            rel["paradas_calculando"] = True

            def _bg():
                c = None
                try:
                    c = _conn()
                    val = _calc_paradas_con(c)
                    _cache["sim:paradas"] = (time.time(), val)
                    print(" contagem de paradas por responsavel pronta.", flush=True)
                except Exception as e:
                    print(" (aviso) contagem de paradas falhou:", str(e)[:150], flush=True)
                finally:
                    _fechar(None, c)

            threading.Thread(target=_bg, daemon=True).start()
        rel["tempos"]["paradas_ms"] = int((time.time() - t0) * 1000)
        rel["modo_importa"] = _cfg_get(cur, "MODO_IMPORTA", "0") == "1"
        rel["modo_auto"] = _cfg_get(cur, "MODO_AUTO", "0") == "1"
        rel["robo_ligado"] = _cfg_get(cur, "ROBO_LIGADO", "0") == "1"

        # ---- ETAPA 2: notas JA no sistema ("A processar") - o ciclo do robo
        # SEM ORDER BY: ordenar obrigaria o banco a varrer e classificar TODAS as
        # notas antes de pegar as primeiras. Como aqui e uma AMOSTRA para simular,
        # qualquer conjunto serve - e fica muito mais rapido.
        # O EXISTS contra INTG_NOTAFISCAL VOLTOU, e agora e obrigatorio: sem ele
        # a simulacao pegava nota DIGITADA a mao como se fosse candidata do robo,
        # e o robo nunca vai processar essas (a fila dele exige o mesmo EXISTS).
        # A simulacao mentia sobre o proprio robo.
        #
        # Ele havia sido removido por desempenho: sem indice, o Oracle varria as
        # ~864 mil linhas da NOTAFISCAL e, para cada uma, varria a outra tabela -
        # a simulacao de 5 notas levava 7 minutos. O que segura isso e o indice
        # IX_INTGNF_FIL_LANC (sql/CRIAR_INDICES.sql). O recorte por data e o
        # FETCH FIRST continuam limitando o esforco.
        t1 = time.time()
        cur.execute("""
            SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.CDFORNECED, N.CDTIPOOPER
            FROM   TEKNISA.NOTAFISCAL N
            WHERE  N.IDENTRSAIDOP='E' AND N.IDSTATUS='I'
              AND  N.CDTIPOOPER IN (""" + _SQL_TIPOS + """)
              AND  N.DTAPROVACAO IS NULL
              AND  N.DTEMISSAO >= ADD_MONTHS(TRUNC(SYSDATE), -3)
        """ + _so_simplificada("N") + """
            FETCH  FIRST :lim ROWS ONLY
        """, lim=limite)
        notas = _rows(cur)
        rel["tempos"]["buscar_notas_ms"] = int((time.time() - t1) * 1000)
        # nome do fornecedor: uma consulta so, para todos de uma vez
        _fornec = {}
        _cods = sorted({n["CDFORNECED"] for n in notas if n.get("CDFORNECED")})
        if _cods:
            try:
                _b = {("f%d" % i): c for i, c in enumerate(_cods)}
                _in = ",".join(":" + k for k in _b)
                cur.execute("SELECT CDFORNECED, NMRAZSOCFORN FROM TEKNISA.FORNECEDOR "
                            "WHERE CDFORNECED IN (%s)" % _in, _b)
                _fornec = {r[0]: r[1] for r in cur.fetchall()}
            except Exception as e:
                print(" (aviso) nomes de fornecedor:", str(e)[:120], flush=True)
        for n in notas:
            n["NMRAZSOCFORN"] = _fornec.get(n.get("CDFORNECED"))

        resumo = {"confirmaria": 0, "esperaria_humano": 0, "sem_itens": 0,
                  "itens_total": 0, "itens_ja_ok": 0, "itens_auto": 0,
                  "itens_humano": 0}
        cache_cands = {}
        cache_dep = {}
        t2 = time.time()
        for n in notas:
            fil, lan = n["CDFILIAL"], n["NRLANCTONF"]
            forn = n["CDFORNECED"]
            # a unidade do XML entra aqui tambem: a simulacao tem que dar o MESMO
            # resultado do robo, senao ela deixa de servir para prever o que ele fara
            cur.execute("""SELECT NRSEQITPROD, CDPRODEMIS, NMPRODUTOXML, SGUNIDADEXML
                           FROM TEKNISA.ITEMPRODUTO
                           WHERE CDFILIAL=:f AND NRLANCTONF=:n
                           ORDER BY NRSEQITPROD""", f=fil, n=lan)
            itens = _rows(cur)
            if forn not in cache_cands:
                try:
                    cache_cands[forn] = _cands_af(cur, forn)
                except Exception:
                    cache_cands[forn] = []
                # DE/PARA DO FORNECEDOR DE UMA VEZ SO.
                # Antes era 1 consulta POR ITEM (25 notas x 8 itens = 200 idas ao
                # banco). Agora e 1 consulta por fornecedor, lida para memoria.
                try:
                    cur.execute("""SELECT CDPRODUTOXML, MAX(CDPRODUTO) KEEP
                                          (DENSE_RANK LAST ORDER BY DTINCLUSAO
                                           NULLS FIRST) AS CDPRODUTO
                                   FROM TEKNISA.PRODFORNXML
                                   WHERE CDFORNECED=:f AND IDATIVO='S'
                                   GROUP BY CDPRODUTOXML""", f=forn)
                    cache_dep[forn] = {r[0]: r[1] for r in cur.fetchall()}
                except Exception as e:
                    print(" simulacao de/para:", str(e)[:120], flush=True)
                    cache_dep[forn] = {}
            cands = cache_cands[forn]
            depara = cache_dep.get(forn, {})

            det, pend = [], 0
            for it in itens:
                cprod = it.get("CDPRODEMIS")
                desc = _fix(it.get("NMPRODUTOXML") or "")
                resumo["itens_total"] += 1
                # ja tem vinculo? -> consulta a memoria, nao o banco
                _v = depara.get(cprod)
                jj = (_v,) if _v else None
                if jj:
                    resumo["itens_ja_ok"] += 1
                    det.append({"item": it.get("NRSEQITPROD"), "desc": desc,
                                "acao": "JA_RECONHECIDO", "cd": jj[0],
                                "texto": "já reconhecido (vínculo existente)"})
                    continue
                un_xml = it.get("SGUNIDADEXML")
                best, best_sug = (None, None)
                if desc and cands:
                    best, best_sug = escolher(desc, cands, un_xml)
                _un_cand = next((c[2] for c in cands
                                 if best and c[0] == best[0] and len(c) > 2), None)
                # 'cands' habilita a excecao do nome generico (ver ai_matcher);
                # a simulacao tem que decidir igual ao robo, senao mente
                if pode_auto(desc, best, un_xml, _un_cand, cands):
                    resumo["itens_auto"] += 1
                    det.append({"item": it.get("NRSEQITPROD"), "desc": desc,
                                "acao": "VINCULARIA_SOZINHO", "cd": best[0],
                                "nome": _fix(best[1] or ""),
                                "score": round(best[2], 3),
                                "texto": "o robô vincularia sozinho"})
                else:
                    pend += 1
                    resumo["itens_humano"] += 1
                    det.append({"item": it.get("NRSEQITPROD"), "desc": desc,
                                "acao": "IRIA_PARA_FILA",
                                "cd": (best_sug[0] if best_sug else None),
                                "nome": (_fix(best_sug[1] or "") if best_sug else None),
                                "score": (round(best[2], 3) if best else 0),
                                "faixa": (_faixa(best[2]) if best else "baixa"),
                                "texto": "iria para a fila (precisa de você)"})
            if not itens:
                resultado, resumo_k = "SEM_ITENS", "sem_itens"
            elif pend == 0:
                resultado, resumo_k = "CONFIRMARIA", "confirmaria"
            else:
                resultado, resumo_k = "ESPERARIA_HUMANO", "esperaria_humano"
            resumo[resumo_k] += 1
            rel["notas"].append({
                "filial": fil, "lancamento": lan, "chave": n.get("NRACESSONFE"),
                "fornecedor": _fix(n.get("NMRAZSOCFORN") or forn or ""),
                "itens": det, "pendentes": pend, "resultado": resultado})

        rel["tempos"]["simular_ms"] = int((time.time() - t2) * 1000)
        rel["tempos"]["total_ms"] = int((time.time() - t0) * 1000)
        n_notas = len(notas)
        resumo["notas"] = n_notas
        resumo["autonomia_pct"] = (round(100.0 * resumo["confirmaria"] / n_notas)
                                   if n_notas else 0)
        it_t = resumo["itens_total"]
        resumo["reconhecimento_pct"] = (
            round(100.0 * (resumo["itens_ja_ok"] + resumo["itens_auto"]) / it_t)
            if it_t else 0)
        rel["resumo"] = resumo
        return jsonify(rel)
    except Exception as e:
        return jsonify({"msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Por que a nota nao anda? -------------------- #
@app.route("/api/diagnostico-nota")
def diagnostico_nota():
    """Explica, em portugues, por que uma nota continua em 'A processar'.
    Antes so dava para descobrir isso garimpando o log no banco."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"msg": "Chave não informada."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        out = {"chave": chave, "bloqueios": [], "itens_pendentes": [], "log": []}

        # 1) estado do robo e dos modos
        ligado = _cfg_get(cur, "ROBO_LIGADO", "0") == "1"
        auto = _cfg_get(cur, "MODO_AUTO", "0") == "1"
        out["robo_ligado"] = ligado
        out["modo_auto"] = auto
        out["modo_escrita"] = MODO_ESCRITA
        if not ligado:
            out["bloqueios"].append("O robô está DESLIGADO. Ligue em Configurações.")
        if not auto:
            out["bloqueios"].append(
                "O modo automático está DESLIGADO: o robô até reconhece os "
                "produtos, mas não confirma a nota. Ligue em Configurações.")
        if not MODO_ESCRITA:
            out["bloqueios"].append(
                "O painel está em modo consulta: o robô roda em simulação e não "
                "grava nada. Fale com a TI.")

        # A PARTIR DAQUI, CADA PASSO E ISOLADO: um passo que falhar nao pode
        # derrubar o diagnostico inteiro (uma ferramenta de diagnostico que
        # some quando da erro e justamente o que nao ajuda ninguem).
        out["avisos"] = []

        def _passo(nome, fn):
            try:
                return fn()
            except Exception as e:
                print(" diagnostico: passo '%s' falhou: %s" % (nome, str(e)[:200]),
                      flush=True)
                out["avisos"].append("Não consegui conferir: %s." % nome)
                return None

        # 2) a nota existe e em que status?
        def _cabecalho():
            cur.execute("""SELECT CDFILIAL, NRLANCTONF, IDSTATUS, CDFORNECED
                           FROM TEKNISA.NOTAFISCAL WHERE NRACESSONFE=:c
                           FETCH FIRST 1 ROWS ONLY""", c=chave)
            return cur.fetchone()

        r = _passo("a situação da nota", _cabecalho)
        if not r:
            out["bloqueios"].append(
                "A nota ainda não tem lançamento no sistema (ou não consegui "
                "localizá-la pela chave).")
            return jsonify(out)
        fil, lan, st, forn = r[0], r[1], r[2], r[3]
        out["filial"], out["lancamento"] = fil, lan
        out["situacao"] = {"I": "A processar", "P": "A aprovar",
                           "A": "Aprovada"}.get(st, st)
        if st != "I":
            out["bloqueios"].append(
                "Esta nota já saiu de 'A processar' (situação atual: %s)."
                % out["situacao"])

        # 3) itens sem vinculo -> e o motivo mais comum
        def _itens():
            cur.execute("""SELECT I.NRSEQITPROD, I.CDPRODEMIS, I.NMPRODUTOXML
                           FROM   TEKNISA.ITEMPRODUTO I
                           WHERE  I.CDFILIAL=:f AND I.NRLANCTONF=:n
                             AND  NOT EXISTS (SELECT 1 FROM TEKNISA.PRODFORNXML D
                                              WHERE D.CDFORNECED=:fo
                                                AND D.CDPRODUTOXML=I.CDPRODEMIS
                                                AND D.IDATIVO='S')
                           ORDER BY I.NRSEQITPROD""", f=fil, n=lan, fo=forn)
            return _rows(cur)

        faltando = _passo("os itens sem vínculo", _itens) or []
        out["itens_pendentes"] = faltando
        if faltando:
            out["bloqueios"].append(
                "%d item(ns) da nota ainda não têm vínculo com produto interno. "
                "Resolva em 'Produtos a vincular'." % len(faltando))

        # 4) motivos que o proprio Teknisa registrou (com a acao recomendada)
        def _motivos():
            cur.execute("""SELECT DISTINCT SUBSTR(DSINCONSIS,1,160) AS M,
                                  TRIM(LEADING '0' FROM CDCONSISTXML) AS COD
                           FROM   TEKNISA.INTG_INCONSISTENCIA
                           WHERE  NRACESSONFE=:c AND IDATIVO='S'
                           FETCH FIRST 10 ROWS ONLY""", c=chave)
            return _rows(cur)

        mot = _passo("os motivos registrados pelo Teknisa", _motivos) or []
        if mot:
            out["motivos"] = mot
            cods = ",".join(str(x.get("COD") or "") for x in mot)
            quem, titulo, acao = _classifica(cods)
            out["quem_resolve"], out["titulo"], out["acao"] = quem, titulo, acao
            out["bloqueios"].append("%s — %s" % (titulo, acao))

        # 5) o que o robo registrou sobre ela
        def _log():
            cur.execute("""SELECT STATUS, TO_CHAR(SUBSTR(DETALHE,1,300)) DETALHE,
                                  TO_CHAR(DT_PROC,'DD/MM/YYYY HH24:MI') DT
                           FROM   AIHUB.AIHUB_PROC_LOG
                           WHERE  CHAVE_NF=:c
                           ORDER  BY DT_PROC DESC
                           FETCH  FIRST 5 ROWS ONLY""", c=chave)
            return _rows(cur)

        out["log"] = _passo("o histórico do robô", _log) or []
        if not out["log"]:
            out["bloqueios"].append(
                "O robô ainda não processou esta nota nenhuma vez. Se ela é "
                "antiga, pode estar atrás de outras na fila.")

        if not out["bloqueios"]:
            out["bloqueios"].append(
                "Não encontrei impedimento: a nota deve ser confirmada na "
                "próxima rodada do robô.")
        return jsonify(out)
    except Exception as e:
        # devolve TAMBEM o detalhe tecnico. Sem ele, um erro de Python (que nao
        # tem codigo ORA) vira uma mensagem generica sem nenhuma pista - foi
        # exatamente o que travou o diagnostico deste botao.
        import traceback
        return jsonify({"msg": _erro_amigavel(e),
                        "detalhe": "%s: %s" % (type(e).__name__, str(e))[:400],
                        "onde": traceback.format_exc().strip().split("\n")[-3:]}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Arquivar nota parada ------------------------ #
# O Teknisa nunca limpa a INTG_INCONSISTENCIA. Uma nota que a empresa decidiu
# NAO receber (bonificacao, amostra, remessa, CNPJ errado) nunca vira lancamento
# e por isso ficaria eternamente na lista. Aqui a pessoa registra a decisao e a
# nota sai da lista de trabalho - sem tocar em nada do Teknisa.
MOTIVOS_ARQUIVO = {
    "SEM_PEDIDO":   "Sem pedido de compra (bonificação, amostra, remessa)",
    "NAO_E_NOSSA":  "Não é para esta empresa/filial (CNPJ errado)",
    "DEVOLVIDA":    "Recusada/devolvida ao fornecedor",
    "JA_RESOLVIDA": "Já resolvida por fora do sistema",
    "OUTRO":        "Outro motivo",
}


def _tem_tabela(cur, tabela, cache_key):
    """A tabela existe E o usuario tem permissao de leitura nela?
    Testa uma vez e guarda. Sem isso, uma tabela sem GRANT derruba a consulta
    inteira - e o painel some com a tela em vez de funcionar sem aquele detalhe."""
    if cache_key not in _col_cache:
        try:
            cur.execute("SELECT COUNT(*) FROM %s WHERE 1=0" % tabela)
            cur.fetchone()
            _col_cache[cache_key] = True
        except Exception as e:
            _col_cache[cache_key] = False
            print(" (aviso) sem acesso a %s (%s). O painel segue sem esse "
                  "recurso." % (tabela, str(e)[:80]), flush=True)
    return _col_cache[cache_key]


def _tem_arquivadas(cur):
    """A tabela de arquivadas existe? (o painel funciona com ou sem ela)"""
    if "arquivadas" not in _col_cache:
        try:
            cur.execute("SELECT COUNT(*) FROM AIHUB.AIHUB_ARQUIVADA WHERE 1=0")
            _col_cache["arquivadas"] = True
        except Exception:
            _col_cache["arquivadas"] = False
            print(" (aviso) AIHUB_ARQUIVADA nao existe - rode sql\\CRIAR_ARQUIVADAS.sql "
                  "(cria so essa tabela, seguro rodar de novo) para poder arquivar "
                  "notas.", flush=True)
    return _col_cache["arquivadas"]


def _sql_nao_arquivada(cur, alias="I"):
    """Fragmento SQL que exclui as notas ja arquivadas. Vazio se a tabela nao
    existir (assim o painel nunca quebra por falta dela)."""
    if not _tem_arquivadas(cur):
        return ""
    return (" AND NOT EXISTS (SELECT 1 FROM AIHUB.AIHUB_ARQUIVADA Q"
            " WHERE Q.NRACESSONFE = %s.NRACESSONFE)" % alias)


# --------------------------------------------------------------------------- #
# "ESTA REALMENTE PARADA?"  -  criterio por MULTIPLOS SINAIS
#
# Descoberta da investigacao (28/07/2026): a TEKNISA.INTG_INCONSISTENCIA e um
# LOG QUE NUNCA E LIMPO - as 12.254 linhas da tabela estao com IDATIVO='S',
# nenhuma com 'N', inclusive as de notas ja lancadas ha anos. Ou seja, ela diz
# POR QUE a nota travou, mas NAO diz se ainda esta travada.
#
# Por isso a lista de paradas NAO pode se apoiar so nesse log. Aqui juntamos
# todos os sinais de "a nota ja seguiu adiante". Se QUALQUER um deles apontar
# que a nota foi usada, ela sai da lista de trabalho:
#   1. existe lancamento com essa chave           (NOTAFISCAL.NRACESSONFE)
#   2. a caixa de entrada ja aponta um lancamento (NOTADESTINADA.NRLANCTONF)
#   3. o XML consta como utilizado / lancado      (INTG_NOTAFISCAL)
#   4. a nota foi cancelada ou denegada           (IDSTATUSNFE 2/3)
#   5. a equipe arquivou ("nao se aplica")        (AIHUB_ARQUIVADA)
# --------------------------------------------------------------------------- #
def _sql_periodo(alias="I", faixa="atual"):
    """Recorte de tempo da FILA DE TRABALHO.

    Regra de operacao (definida em 28/07/2026, depois de constatar que a fila
    acumulava residuo historico que ninguem trataria):
      atual     -> mes vigente. E o trabalho do dia; tem que tender a zero.
      atrasadas -> mes anterior, ainda em aberto. Contador separado, para nao
                   sumir em silencio.
      historico -> mais antigo. Sai da fila SOZINHO (ninguem precisa clicar
                   nota por nota), mas continua registrado e reversivel.
    O mes vem da propria chave da NF-e (posicoes 3-6 = AAMM da emissao), entao
    nao custa join nenhum."""
    a = alias
    if faixa == "tudo":
        return ""
    if faixa == "atual":            # so o mes vigente
        return " AND SUBSTR(%s.NRACESSONFE,3,4) = TO_CHAR(SYSDATE,'YYMM')" % a
    if faixa == "atrasadas":        # so o mes anterior
        return (" AND SUBSTR(%s.NRACESSONFE,3,4) = "
                "TO_CHAR(ADD_MONTHS(SYSDATE,-1),'YYMM')" % a)
    if faixa == "historico":        # mais antigo que o mes anterior
        return (" AND SUBSTR(%s.NRACESSONFE,3,4) < "
                "TO_CHAR(ADD_MONTHS(SYSDATE,-1),'YYMM')" % a)
    # PADRAO ("recentes") = mes vigente + mes anterior.
    # E a regra de negocio definida pelo cliente: a area trata a pendencia
    # dentro do mes corrente ou, no maximo, no mes anterior.
    return (" AND SUBSTR(%s.NRACESSONFE,3,4) IN (TO_CHAR(SYSDATE,'YYMM'),"
            " TO_CHAR(ADD_MONTHS(SYSDATE,-1),'YYMM'))" % a)


def _sql_ainda_parada(cur, alias="I"):
    """Condicoes que mantem a nota na lista de trabalho. Sempre comeca com AND."""
    a = alias
    partes = [
        # 1. nao virou lancamento (pela chave)
        " AND NOT EXISTS (SELECT 1 FROM TEKNISA.NOTAFISCAL N"
        " WHERE N.NRACESSONFE = %s.NRACESSONFE)" % a,
        # 2. a caixa de entrada nao aponta lancamento, e nao esta cancelada/denegada
        " AND NOT EXISTS (SELECT 1 FROM TEKNISA.NOTADESTINADA D"
        " WHERE D.NRACESSONFE = %s.NRACESSONFE"
        "   AND (D.NRLANCTONF IS NOT NULL OR D.IDSTATUSNFE IN ('2','3')))" % a,
    ]
    # 3. o XML nao consta como utilizado/lancado na integracao.
    #    As DUAS colunas sao conferidas, cada uma por si: antes so a
    #    DTUTILIZAXML era checada, mas a DTLANCAMENNF entrava na consulta do
    #    mesmo jeito - numa versao do Teknisa sem essa segunda coluna a tela
    #    inteira quebraria com ORA-00904.
    _sinais = [c for c in (
        _col_disponivel(cur, "INTG_NOTAFISCAL", ["DTUTILIZAXML"], "intg_utilxml"),
        _col_disponivel(cur, "INTG_NOTAFISCAL", ["DTLANCAMENNF"], "intg_lancnf"),
    ) if c]
    if _sinais:
        partes.append(
            " AND NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_NOTAFISCAL G"
            " WHERE G.NRACESSONFE = %s.NRACESSONFE"
            "   AND (%s))"
            % (a, " OR ".join("G.%s IS NOT NULL" % c for c in _sinais)))
    # 5. nao foi arquivada pela equipe
    partes.append(_sql_nao_arquivada(cur, a))
    # 6. o proprio Teknisa nao disse que a nota JA esta cadastrada (codigo 030)
    _ja_resolvido = ",".join("'%s'" % _norm_cod(c) for c in COD_JA_RESOLVIDO)
    partes.append(
        " AND NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_INCONSISTENCIA J"
        " WHERE J.NRACESSONFE = %s.NRACESSONFE"
        "   AND TRIM(LEADING '0' FROM J.CDCONSISTXML) IN (%s))" % (a, _ja_resolvido))
    # 7. NINGUEM BLOQUEOU O XML NA MAO.
    #    Na TEKNISA.HISTXMLNFE, quando uma PESSOA bloqueia o XML (fornecedor
    #    cancelou, compra recusada) fica DSOPERACAO='Arquivo XML bloqueado pelo
    #    usuario', IDSTATUS='B' e CDOPERADOR com a MATRICULA de quem fez; os
    #    eventos automaticos vem com CDOPERADOR='AUTOMATICO'.
    #    So aplica se o usuario do painel TIVER acesso a essa tabela (senao a
    #    consulta inteira quebraria por falta de grant).
    #    Nota: nao checamos "se e o ultimo evento" de proposito - aquele
    #    subselect com MAX por linha deixava a consulta lentissima. Se a nota
    #    tiver sido desbloqueada e reprocessada, ela vira lancamento e ja sai
    #    pela regra 1.
    if _tem_tabela(cur, "TEKNISA.HISTXMLNFE", "histxml"):
        # ATENCAO: existe "Arquivo XML DESbloqueado pelo usuario". Se a nota foi
        # desbloqueada DEPOIS do bloqueio, ela voltou a ser trabalho e TEM de
        # aparecer. Por isso o bloqueio so vale se nao houver desbloqueio
        # posterior (comparando a sequencia do historico).
        partes.append(
            " AND NOT EXISTS (SELECT 1 FROM TEKNISA.HISTXMLNFE H"
            " WHERE H.NRACESSONFE = %s.NRACESSONFE"
            "   AND H.IDSTATUS = 'B'"
            "   AND NVL(H.CDOPERADOR,'AUTOMATICO') <> 'AUTOMATICO'"
            "   AND NOT EXISTS (SELECT 1 FROM TEKNISA.HISTXMLNFE H3"
            "                   WHERE H3.NRACESSONFE = H.NRACESSONFE"
            "                     AND H3.NRSEQHISTOR > H.NRSEQHISTOR"
            "                     AND UPPER(H3.DSOPERACAO) LIKE '%%DESBLOQ%%'))" % a)
    return "".join(partes)


@app.route("/api/arquivar", methods=["POST"])
def arquivar():
    """Marca uma nota parada como tratada/nao se aplica -> sai da lista."""
    # grava so no AIHUB (nao toca no Teknisa), mas "modo consulta" tem que
    # significar QUE NADA MUDA - inclusive as nossas proprias tabelas.
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    chave = (dados.get("chave") or "").strip()
    motivo = (dados.get("motivo") or "OUTRO").strip().upper()
    obs = (dados.get("obs") or "").strip()
    if not chave:
        return jsonify({"ok": False, "msg": "Chave não informada."}), 400
    if motivo not in MOTIVOS_ARQUIVO:
        motivo = "OUTRO"
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if not _tem_arquivadas(cur):
            return jsonify({"ok": False,
                            "msg": "Recurso não instalado: peça à TI para rodar "
                                   "sql\\CRIAR_ARQUIVADAS.sql (cria só essa tabela)."}), 501
        cur.execute("""
            MERGE INTO AIHUB.AIHUB_ARQUIVADA a
            USING (SELECT :ch NRACESSONFE FROM dual) s
               ON (a.NRACESSONFE = s.NRACESSONFE)
             WHEN MATCHED THEN UPDATE SET MOTIVO=:mo, OBS=:ob,
                  OPERADOR=:op, DT_ARQUIVO=SYSDATE
             WHEN NOT MATCHED THEN
                  INSERT (NRACESSONFE, MOTIVO, OBS, OPERADOR)
                  VALUES (:ch, :mo, :ob, :op)
        """, ch=chave, mo=motivo, ob=obs[:300], op=_operador())
        _auditar(cur, "ARQUIVA", "motivo=%s %s" % (motivo, obs[:120]), chave)
        con.commit()
        _cache.clear()          # as listas mudaram
        return jsonify({"ok": True, "msg": "Nota arquivada. Ela sai da lista de pendências."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/desarquivar", methods=["POST"])
def desarquivar():
    """Desfaz o arquivamento: a nota volta para a lista de pendências."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    chave = (dados.get("chave") or "").strip()
    if not chave:
        return jsonify({"ok": False, "msg": "Chave não informada."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("DELETE FROM AIHUB.AIHUB_ARQUIVADA WHERE NRACESSONFE=:c", c=chave)
        n = cur.rowcount
        if n:
            _auditar(cur, "DESARQUIVA", "nota devolvida para a lista", chave)
        con.commit()
        _cache.clear()
        return jsonify({"ok": True, "n": n,
                        "msg": ("Nota devolvida para a lista." if n
                                else "Esta nota não estava arquivada.")})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/arquivadas")
def arquivadas():
    """Lista as notas arquivadas (para rever/desfazer)."""
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if not _tem_arquivadas(cur):
            return jsonify({"notas": [], "total": 0, "instalado": False})
        cur.execute("SELECT COUNT(*) FROM AIHUB.AIHUB_ARQUIVADA")
        total = int(cur.fetchone()[0])
        cur.execute("""
            SELECT A.NRACESSONFE, A.MOTIVO, A.OBS, A.OPERADOR,
                   TO_CHAR(A.DT_ARQUIVO,'DD/MM/YYYY HH24:MI') AS DT,
                   SUBSTR(A.NRACESSONFE,7,14) AS CNPJ, F.NMRAZSOCFORN AS FORNECEDOR
            FROM   AIHUB.AIHUB_ARQUIVADA A
            LEFT   JOIN TEKNISA.FORNECEDOR F
                   ON REGEXP_REPLACE(F.CDFORNECED,'[^0-9]','') = SUBSTR(A.NRACESSONFE,7,14)
            ORDER  BY A.DT_ARQUIVO DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """, off=pag * PG, lim=PG + 1)
        rows = _rows(cur)
        tem_mais = len(rows) > PG
        for r in rows[:PG]:
            r["MOTIVO_TXT"] = MOTIVOS_ARQUIVO.get(r.get("MOTIVO"), r.get("MOTIVO"))
        return jsonify({"notas": rows[:PG], "total": total, "pag": pag,
                        "tem_mais": tem_mais, "instalado": True,
                        "motivos": MOTIVOS_ARQUIVO})
    except Exception as e:
        return jsonify({"notas": [], "total": 0, "erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Nome do fornecedor por CNPJ ----------------- #
@app.route("/api/fornecedor-nome")
def fornecedor_nome():
    """Nome do fornecedor a partir do CNPJ (14 digitos). Usado para enriquecer
    o detalhe de uma nota travada (que so tem o CNPJ, vindo da chave)."""
    cnpj = "".join(ch for ch in (request.args.get("cnpj") or "") if ch.isdigit())
    if len(cnpj) < 8:
        return jsonify({"nome": None})
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""
            SELECT NMRAZSOCFORN FROM TEKNISA.FORNECEDOR
            WHERE  REGEXP_REPLACE(CDFORNECED,'[^0-9]','') = :c
            FETCH FIRST 1 ROWS ONLY
        """, c=cnpj)
        r = cur.fetchone()
        return jsonify({"nome": _fix(r[0]) if r and r[0] else None})
    except Exception as e:
        return jsonify({"nome": None, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Revisao De/Para ----------------------------- #
@app.route("/api/revisao")
def revisao():
    """Cobertura do De/Para de um fornecedor: quantos produtos da AF o robo ja
    conhece e quantos faltam ensinar."""
    # antes havia um fornecedor FIXO como padrao: sem parametro, a tela mostrava
    # os dados de um fornecedor especifico como se fosse o panorama geral.
    forn = (request.args.get("forn") or "").strip()
    if not forn:
        return jsonify({"erro": "Informe o fornecedor.", "itens": []}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""
            SELECT A.CDFILLANC   FILIAL,
                   A.NRAUTOFORN  AF,
                   I.CDPRODUTO   PRODUTO,
                   P.NMPRODUTO   NOME,
                   CASE WHEN D.CDPRODUTO IS NULL THEN 'SEM' ELSE 'TEM' END DEPARA
            FROM   TEKNISA.AUTOFORNE A
            JOIN   TEKNISA.ITEMAUTF  I ON I.CDFILDEST=A.CDFILDEST
                   AND I.CDFILLANC=A.CDFILLANC AND I.NRAUTOFORN=A.NRAUTOFORN
            JOIN   TEKNISA.PRODUTO   P ON P.CDPRODUTO=I.CDPRODUTO
            LEFT   JOIN TEKNISA.PRODFORNXML D ON D.CDPRODUTO=I.CDPRODUTO
                   AND D.CDFORNECED=A.CDFORNECED AND D.IDATIVO='S'
            WHERE  A.CDFORNECED = :forn
              AND  NVL(P.IDATIVO,'S')<>'N'
              AND  UPPER(P.NMPRODUTO) NOT LIKE '%INATIVO%'
            FETCH  FIRST 300 ROWS ONLY
        """, forn=forn)
        itens = _rows(cur)

        # deduplica por produto
        vistos, uniq = set(), []
        for it in itens:
            if it["PRODUTO"] in vistos:
                continue
            vistos.add(it["PRODUTO"])
            uniq.append(it)

        tem = sum(1 for i in uniq if i["DEPARA"] == "TEM")
        sem = len(uniq) - tem
        return jsonify({"fornecedor": forn, "itens": uniq,
                        "tem": tem, "sem": sem, "total": len(uniq),
                        "cobertura": (round(100.0 * tem / len(uniq)) if uniq else 0)})
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e), "itens": []}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Sugestao do robo (matcher) ------------------ #
@app.route("/api/sugerir")
def sugerir():
    """Dado um fornecedor e a descricao do item como vem na nota, roda o
    matcher contra os produtos internos das AFs desse fornecedor e devolve as
    melhores sugestoes com score. Nao grava nada."""
    forn = (request.args.get("forn") or "").strip()
    desc = (request.args.get("desc") or "").strip()
    if not forn or not desc:
        return jsonify({"sugestoes": [], "msg": "Informe fornecedor e descricao."})
    if not _HAS_MATCHER:
        return jsonify({"sugestoes": [], "msg": "Matcher indisponivel (instale rapidfuzz)."})
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        itens = _cands_af(cur, forn)          # fonte unica (mesma do robo)
        return jsonify(_ranquear(desc, itens))
    except Exception as e:
        return jsonify({"sugestoes": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Relatorio / auditoria ----------------------- #
@app.route("/api/relatorio")
def relatorio():
    hit = _cache.get("relatorio")
    if hit and (time.time() - hit[0]) < 300:
        return jsonify(hit[1])
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""
            SELECT D.CDFORNECED, F.NMRAZSOCFORN, COUNT(*) ITENS
            FROM   TEKNISA.PRODFORNXML D
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=D.CDFORNECED
            WHERE  D.IDATIVO='S'
            GROUP  BY D.CDFORNECED, F.NMRAZSOCFORN
            ORDER  BY ITENS DESC
            FETCH  FIRST 15 ROWS ONLY
        """)
        top = _rows(cur)

        log = []
        try:
            cur.execute("""
                SELECT NUMERO_NF, STATUS, SCORE_MATCH,
                       TO_CHAR(DT_PROC,'DD/MM/YYYY HH24:MI') DT
                FROM   AIHUB.AIHUB_PROC_LOG
                ORDER  BY ID DESC
                FETCH  FIRST 50 ROWS ONLY
            """)
            log = _rows(cur)
        except Exception:
            pass

        result = {"top_fornecedores": top, "log": log}
        _cache["relatorio"] = (time.time(), result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Gravar De/Para (protegido) ------------------ #
@app.route("/api/gravar-depara", methods=["POST"])
def gravar_depara():
    if not MODO_ESCRITA:
        # 403 como TODAS as outras rotas de escrita. Esta devolvia 200, o que
        # fazia "recusado por modo leitura" chegar ao navegador como sucesso.
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Para gravar, "
                               "inicie pelo atalho de escrita."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    obrig = ("cd_produto", "cd_forneced", "cprod_xml", "nprod_xml")
    if not all(dados.get(k) for k in obrig):
        return jsonify({"ok": False, "msg": "Faltou preencher algum campo."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # usa a MESMA funcao do robo: garante um unico vinculo ativo por
        # (fornecedor, item do XML) - nunca deixa dois vinculos concorrentes.
        _gravar_vinculo(cur, dados["cd_produto"], dados["cd_forneced"],
                        dados["cprod_xml"], dados["nprod_xml"],
                        _operador_teknisa())
        con.commit()
        return jsonify({"ok": True, "msg": "Vínculo gravado."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Fila de revisao (pendencias) ---------------- #
# Fila persistida em AIHUB.AIHUB_PENDENCIA (compartilhada com o robo).

def _cands_af(cur, forn, nr_af=None, cd_filial=None):
    """Candidatos do fornecedor. Fonte UNICA: core.ai_matcher.candidatos - a
    mesma lista que o robo e a IA usam (antes eram 3 consultas diferentes).

    Com a AF informada, o proprio candidatos() tenta a LISTA CURTA (so os
    produtos daquela AF) e cai na completa se ela vier vazia. Medido: de 2.502
    candidatos para 77 em 8 fornecedores."""
    from core.ai_matcher import candidatos
    return candidatos(cur, forn, nr_af, cd_filial)


_intg_item = {"tabela": None, "descoberto": False}


def _itens_da_integracao(cur, chave):
    """Itens de uma nota que AINDA NAO virou lancamento (esta so na integracao).
    Descobre a tabela de itens em tempo de execucao (o nome varia por versao do
    Teknisa) e devolve (itens, cd_forneced) no mesmo formato da ITEMPRODUTO:
    (seq, cprod_xml, desc_xml, cd_produto)."""
    if not _intg_item["descoberto"]:
        _intg_item["descoberto"] = True
        try:
            cur.execute("""
                SELECT TABLE_NAME FROM ALL_TABLES
                WHERE OWNER='TEKNISA' AND TABLE_NAME LIKE 'INTG%'
                  AND (TABLE_NAME LIKE '%ITEM%' OR TABLE_NAME LIKE '%PROD%')
                ORDER BY TABLE_NAME""")
            nomes = [r[0] for r in cur.fetchall()]
            for n in nomes:                     # precisa ter as colunas que usamos
                cur.execute("""SELECT COUNT(*) FROM ALL_TAB_COLUMNS
                               WHERE OWNER='TEKNISA' AND TABLE_NAME=:t
                                 AND COLUMN_NAME IN ('NRACESSONFE','CDPRODEMIS',
                                                     'NMPRODUTOXML')""", t=n)
                if int(cur.fetchone()[0]) >= 3:
                    _intg_item["tabela"] = n
                    print(" itens da integracao: usando TEKNISA.%s" % n, flush=True)
                    break
        except Exception as e:
            print(" nao consegui descobrir a tabela de itens da integracao:",
                  str(e)[:150], flush=True)

    tab = _intg_item["tabela"]
    if not tab:
        return [], None

    seqcol = _col_disponivel(cur, tab, ["NRSEQITPROD", "NRSEQITEM", "NRSEQ"], "intg_seq")
    seqsel = (seqcol or "ROWNUM") + " AS SEQ"
    try:
        cur.execute("""SELECT %s, CDPRODEMIS, NMPRODUTOXML
                       FROM TEKNISA.%s WHERE NRACESSONFE=:c
                       ORDER BY 1""" % (seqsel, tab), c=chave)
        itens = [(r[0], r[1], r[2], None) for r in cur.fetchall()]
    except Exception as e:
        print(" falha lendo itens da integracao:", str(e)[:150], flush=True)
        return [], None

    forn = None
    try:  # fornecedor: pela nota da integracao ou pelo CNPJ da chave
        cur.execute("""SELECT CDFORNECED FROM TEKNISA.INTG_NOTAFISCAL
                       WHERE NRACESSONFE=:c FETCH FIRST 1 ROWS ONLY""", c=chave)
        rr = cur.fetchone()
        forn = rr[0] if rr else None
    except Exception:
        pass
    if not forn and len(chave) >= 20:
        try:
            cur.execute("""SELECT CDFORNECED FROM TEKNISA.FORNECEDOR
                           WHERE REGEXP_REPLACE(CDFORNECED,'[^0-9]','')=:c
                           FETCH FIRST 1 ROWS ONLY""", c=chave[6:20])
            rr = cur.fetchone()
            forn = rr[0] if rr else None
        except Exception:
            pass
    return itens, forn


def _ranquear(desc, itens, n=6, un_xml=None):
    """Ordena os candidatos para EXIBIR e classifica a faixa.

    Tem que decidir IGUAL ao robo (core.ai_matcher.escolher/pode_auto), senao a
    pessoa ve uma sugestao no topo e o robo grava outra. Duas coisas que antes
    divergiam e foram corrigidas:

    1. ORDEM: alem de sim_pref_generico, aplica a MESMA penalidade do ramo 6 (OL)
       que o escolher usa. Sem isso, o produto do Operador Logistico (gemeo, nome
       identico ao direto) subia para o topo e a pessoa vinculava o errado.
    2. FAIXA: nao rotula 'auto' o que o pode_auto barraria (gramatura, granel x
       embalagem). Antes classificava so pelo score bruto e mentia 'alta
       confianca' em item que o robo mandaria para a fila."""
    from core.ai_matcher import (_sim, sim_pref_generico, faixa, _e_do_ol,
                                 pode_auto)
    ranked = []
    for r in itens:
        cd, nome = r[0], (r[1] or "")
        un_cand = r[2] if len(r) > 2 else None
        s = _sim(desc, nome)
        ordem = sim_pref_generico(desc, nome) - (0.02 if _e_do_ol(cd) else 0.0)
        # a faixa mostrada e a que o robo REALMENTE usaria: se pode_auto barra
        # (gramatura ou unidade), nao pode aparecer como 'auto'
        fx = faixa(s)
        if fx == "auto" and not pode_auto(desc, (cd, nome, s), un_xml, un_cand, itens):
            fx = "sugestao"
        elif fx != "auto" and pode_auto(desc, (cd, nome, s), un_xml, un_cand, itens):
            # excecao do nome generico: o robo gravaria sozinho mesmo com score
            # abaixo do limiar, entao a tela nao pode dizer que e so sugestao
            fx = "auto"
        ranked.append({"cd": cd, "nome": _fix(nome),
                       "score": round(s, 3), "ordem": round(ordem, 4), "faixa": fx})
    ranked.sort(key=lambda x: x["ordem"], reverse=True)
    return {"sugestoes": ranked[:n], "candidatos": len(itens)}


@app.route("/api/pendencias")
def pendencias():
    """Fila de revisao humana. Agora com FILTROS (fornecedor, busca por
    descricao, faixa) e PAGINACAO - antes truncava em 200 itens sem aviso e sem
    nenhum filtro, virando um scroll infinito."""
    forn = (request.args.get("forn") or "").strip()
    busca = (request.args.get("busca") or "").strip()
    nota = (request.args.get("nota") or "").strip()
    banda = (request.args.get("faixa") or "").strip().lower()
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    con = cur = None
    try:
        con=_conn(); cur=con.cursor()
        where = ["P.STATUS='ABERTO'"]
        binds = {}
        if forn:
            where.append("(P.CDFORNECED = :fn OR UPPER(P.NM_FORNECED) LIKE :fnl)")
            binds["fn"] = forn
            binds["fnl"] = "%" + forn.upper() + "%"
        if busca:
            where.append("UPPER(P.DESC_XML) LIKE :bu")
            binds["bu"] = "%" + busca.upper() + "%"
        # BUSCA PELA NOTA - por chave OU por numero.
        #
        # POR QUE ISTO EXISTE: quando a importacao e recusada por falta de
        # produto, a pessoa vem para esta tela atras DAQUELA nota. Filtrando so
        # por fornecedor, ela recebia todos os itens pendentes do fornecedor
        # (a FORNECEDOR A tem dezenas) e tinha de garimpar. Agora cola a chave, ou
        # digita o numero, e ve so o que interessa.
        #
        # O numero da nota nao esta na pendencia: vem da INTG_NOTAFISCAL pela
        # chave. Aceitamos os dois num campo so porque quem usa nao quer
        # escolher qual e qual - chave tem 44 digitos, numero tem poucos.
        if nota:
            so_dig = "".join(ch for ch in nota if ch.isdigit())
            if len(so_dig) >= 20:                      # e chave de acesso
                where.append("P.CHAVE_NF LIKE :nt")
                binds["nt"] = "%" + so_dig + "%"
            else:                                      # e numero da nota
                where.append("""P.CHAVE_NF IN (
                    SELECT G.NRACESSONFE FROM TEKNISA.INTG_NOTAFISCAL G
                    WHERE TRIM(LEADING '0' FROM G.NRNOTAFISC) = :nt)""")
                binds["nt"] = so_dig.lstrip("0") or "0"
        if banda == "auto":
            where.append("P.SCORE >= :s1"); binds["s1"] = LIMIAR_AUTO * 100
        elif banda == "sugestao":
            where.append("P.SCORE >= :s2 AND P.SCORE < :s3")
            binds["s2"] = LIMIAR_SUGESTAO * 100; binds["s3"] = LIMIAR_AUTO * 100
        elif banda == "baixa":
            where.append("P.SCORE < :s4"); binds["s4"] = LIMIAR_SUGESTAO * 100
        w = " AND ".join(where)

        total = None
        if pag == 0:
            cur.execute("SELECT COUNT(*) FROM AIHUB.AIHUB_PENDENCIA P WHERE " + w, binds)
            total = int(cur.fetchone()[0])
            # contagem por faixa: alimenta os numeros das abas Alta/Media/Baixa
            cur.execute("""SELECT SUM(CASE WHEN SCORE>=:a THEN 1 ELSE 0 END) ALTA,
                                  SUM(CASE WHEN SCORE>=:b AND SCORE<:a THEN 1 ELSE 0 END) MEDIA,
                                  SUM(CASE WHEN SCORE<:b THEN 1 ELSE 0 END) BAIXA
                           FROM AIHUB.AIHUB_PENDENCIA WHERE STATUS='ABERTO'""",
                        a=LIMIAR_AUTO * 100, b=LIMIAR_SUGESTAO * 100)
            fx = cur.fetchone()
            faixas = {"auto": int(fx[0] or 0), "sugestao": int(fx[1] or 0),
                      "baixa": int(fx[2] or 0)}
        else:
            faixas = None

        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        # "evidence pack": alem da sugestao, traz DESDE QUANDO esta na fila e a
        # nota de origem - o revisor decide em segundos, sem investigar.
        sel = ("""SELECT P.ID,P.CDFORNECED,P.NM_FORNECED,P.CPROD_XML,P.DESC_XML,
                         P.CD_SUGERIDO,P.NM_SUGERIDO,P.SCORE,
                         TO_CHAR(P.DT_CRIACAO,'DD/MM/YYYY') DT_FILA,
                         TRUNC(SYSDATE - P.DT_CRIACAO) DIAS_NA_FILA,
                         P.CDFILIAL, P.NRLANCTONF, P.CHAVE_NF, %s
                  FROM   AIHUB.AIHUB_PENDENCIA P %s
                  WHERE  """ + w + """
                  ORDER  BY P.SCORE ASC, P.ID ASC
                  OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY""")
        try:  # com a opiniao da IA (se a tabela AIHUB_GEMINI existir)
            cur.execute(sel % ("G.CD_SUGERIDO GCD, G.NM_SUGERIDO GNM, G.MOTIVO GMOT",
                               "LEFT JOIN AIHUB.AIHUB_GEMINI G ON G.PEND_ID = P.ID"), binds)
            rows=_rows(cur)
        except Exception:  # sem o add-on de IA -> consulta simples
            cur.execute(sel % ("NULL GCD, NULL GNM, NULL GMOT", ""), binds)
            rows=_rows(cur)
        tem_mais = len(rows) > PG
        rows = rows[:PG]
        out=[{"id":r["ID"],"cd_forneced":r["CDFORNECED"],"nm_forneced":r["NM_FORNECED"],
              "cprod_xml":r["CPROD_XML"],"desc_xml":r["DESC_XML"],
              "cd_sugerido":r["CD_SUGERIDO"],"nm_sugerido":r["NM_SUGERIDO"],
              "score":(float(r["SCORE"])/100.0 if r["SCORE"] is not None else 0),
              "dt_fila":r.get("DT_FILA"),"dias_na_fila":r.get("DIAS_NA_FILA"),
              "filial":r.get("CDFILIAL"),"nota":r.get("NRLANCTONF"),
              "chave":r.get("CHAVE_NF"),
              "gemini_cd":r.get("GCD"),"gemini_nome":r.get("GNM"),"gemini_motivo":r.get("GMOT")} for r in rows]
        return jsonify({"pendencias":out,"total":total,"faixas":faixas,
                        "pag":pag,"tem_mais":tem_mais})
    except Exception as e:
        return jsonify({"pendencias":[],"total":0,"msg":_erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/gerar-fila-demo", methods=["POST"])
def gerar_fila_demo():
    """Popula a fila com exemplos REAIS (De/Para reais + sugestao do matcher).
    So para demonstrar o fluxo em HOMOLOGACAO - nunca em producao."""
    # GUARDA: esta rota INSERE pendencias na fila real. Se aprovadas, viram
    # De/Para de verdade. So pode rodar em homologacao e em modo escrita.
    if not DEMO_LIBERADA:
        return jsonify({"ok": False,
                        "msg": "Recurso de teste desativado neste ambiente."}), 403
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI."}), 403
    if not _HAS_MATCHER:
        return jsonify({"ok":False,"msg":"Matcher indisponivel (instale rapidfuzz)."}), 200
    con = cur = None
    try:
        from core.ai_matcher import escolher
        con=_conn(); cur=con.cursor()
        # SAMPLE le so uma amostra de blocos -> rapido (nao varre a tabela toda)
        cur.execute("""
            SELECT CDFORNECED, CDPRODUTOXML, NMPRODUTOXML
            FROM   TEKNISA.PRODFORNXML SAMPLE(3)
            WHERE  IDATIVO='S' AND NMPRODUTOXML IS NOT NULL
            FETCH  FIRST 6 ROWS ONLY
        """)
        base=cur.fetchall(); n=0
        _cache_cands = {}
        for forn,cpx,nmx in base:
            if forn not in _cache_cands:        # evita repetir a consulta pesada
                _cache_cands[forn] = _cands_af(cur, forn)
            _, best = escolher(nmx, _cache_cands[forn])   # mostra a sugestao "humana"
            cur.execute("SELECT NMRAZSOCFORN FROM TEKNISA.FORNECEDOR WHERE CDFORNECED=:f",f=forn)
            fn=cur.fetchone()
            cur.execute("""
                INSERT INTO AIHUB.AIHUB_PENDENCIA
                  (CDFORNECED,NM_FORNECED,CPROD_XML,DESC_XML,CD_SUGERIDO,NM_SUGERIDO,SCORE,STATUS)
                VALUES (:forn,:nmf,:cx,:dx,:cds,:nms,:sc,'ABERTO')
            """, forn=forn, nmf=((fn[0] if fn else forn) or "")[:150], cx=cpx,
                 dx=(nmx or "")[:300], cds=(best[0] if best else None),
                 nms=((best[1] or "")[:150] if best else None),
                 sc=(round(best[2]*100,2) if best else 0))
            n+=1
        con.commit()
        return jsonify({"ok":True,"n":n})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok":False,"msg":_erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Ocorrencias: nada se perde ------------------ #
@app.route("/api/ocorrencias")
def ocorrencias():
    """As notas que nao passaram, separadas por QUEM RESOLVE.

    E a tela que faltava: antes o motivo da recusa vivia num modal e sumia. Aqui
    ele fica ate alguem dizer que resolveu."""
    quem = (request.args.get("quem") or "").strip().upper()
    nota = (request.args.get("nota") or "").strip()
    forn = (request.args.get("forn") or "").strip()
    status = (request.args.get("status") or "ABERTO").strip().upper()
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        if not _tem_tabela(cur, "AIHUB.AIHUB_OCORRENCIA", "ocorrencia"):
            return jsonify({"itens": [], "por_quem": {},
                            "msg": "A fila de ocorrências ainda não foi criada. "
                                   "Peça à TI para rodar sql/CRIAR_OCORRENCIAS.sql."})
        where = ["1=1"]
        binds = {}
        if status in ("ABERTO", "RESOLVIDO"):
            where.append("STATUS = :st")
            binds["st"] = status
        if quem in ("ROBO", "COMPRAS", "FISCAL", "SISTEMA", "OUTRO"):
            where.append("QUEM = :qm")
            binds["qm"] = quem
        if forn:
            where.append("(CDFORNECED = :fn OR UPPER(NM_FORNECED) LIKE :fnl)")
            binds["fn"] = forn
            binds["fnl"] = "%" + forn.upper() + "%"
        if nota:
            so_dig = "".join(c for c in nota if c.isdigit())
            if len(so_dig) >= 20:
                where.append("CHAVE_NF LIKE :nt")
                binds["nt"] = "%" + so_dig + "%"
            else:
                where.append("TRIM(LEADING '0' FROM NUMERO_NF) = :nt")
                binds["nt"] = so_dig.lstrip("0") or "0"
        w = " AND ".join(where)

        # o placar por responsavel: e o que diz onde esta a fila parada.
        # O TOTAL VEM DO BANCO, nunca de somar as areas: uma nota com um
        # apontamento de Compras e outro do Fiscal aparece nas duas, e somar
        # dizia "3 notas" onde havia 2. ROLLUP faz o distinto de verdade, na
        # mesma ida ao banco (a linha do total vem com QUEM nulo).
        por_quem = {}
        total = {}
        if pag == 0:
            cur.execute("""SELECT NVL(QUEM,'*'), COUNT(*), COUNT(DISTINCT CHAVE_NF)
                           FROM   AIHUB.AIHUB_OCORRENCIA
                           WHERE  STATUS='ABERTO'
                           GROUP  BY ROLLUP(QUEM)""")
            for q, n, nn in cur.fetchall():
                alvo = {"apontamentos": int(n), "notas": int(nn)}
                if q == "*":
                    total = alvo
                else:
                    por_quem[q] = alvo

        cur.execute("""SELECT * FROM (
            SELECT a.*, ROWNUM rn FROM (
              SELECT ID, CHAVE_NF, NUMERO_NF, CDFORNECED, NM_FORNECED, CDFILIAL,
                     CODIGO, QUEM, TITULO, ACAO, DETALHE, STATUS,
                     TO_CHAR(DT_ABERTURA,'DD/MM/YYYY HH24:MI') ABERTA,
                     TO_CHAR(DT_RESOLUCAO,'DD/MM/YYYY HH24:MI') RESOLVIDA,
                     CD_OPERADOR, OBS
              FROM   AIHUB.AIHUB_OCORRENCIA
              WHERE  """ + w + """
              ORDER  BY DT_ABERTURA DESC, CHAVE_NF, CODIGO) a
            WHERE ROWNUM <= :fim)
            WHERE rn > :ini""",
                    dict(binds, ini=pag * PG, fim=(pag + 1) * PG + 1))
        linhas = _rows(cur)
        tem_mais = len(linhas) > PG
        return jsonify({"itens": linhas[:PG], "por_quem": por_quem,
                        "total": total, "tem_mais": tem_mais, "pag": pag})
    except Exception as e:
        return jsonify({"itens": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/ocorrencia-resolver", methods=["POST"])
def ocorrencia_resolver():
    """Marca a ocorrencia como tratada, com quem e por quê.

    NAO some sem explicacao: a observacao e o registro de que alguem olhou.
    Sem ela, 'resolvido' vira um botao de esconder."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta, então não posso "
                               "alterar a fila. Fale com a TI."}), 403
    d = request.get_json(force=True, silent=True) or {}
    oid = d.get("id")
    obs = (d.get("obs") or "").strip()
    if not oid:
        return jsonify({"ok": False, "msg": "Faltou a ocorrência."}), 400
    if len(obs) < 3:
        return jsonify({"ok": False,
                        "msg": "Escreva o que foi feito. Sem isso, 'resolvido' "
                               "vira só um jeito de esconder o problema."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""UPDATE AIHUB.AIHUB_OCORRENCIA
                       SET    STATUS='RESOLVIDO', DT_RESOLUCAO=SYSDATE,
                              CD_OPERADOR=:op, OBS=:ob
                       WHERE  ID=:i AND STATUS='ABERTO'""",
                    op=_operador()[:30], ob=obs[:400], i=oid)
        if not cur.rowcount:
            return jsonify({"ok": False,
                            "msg": "Esta ocorrência já tinha sido tratada."}), 409
        _auditar(cur, "OCORRENCIA", "ocorrencia %s tratada: %s" % (oid, obs[:120]))
        con.commit()
        return jsonify({"ok": True, "msg": "Ocorrência tratada."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/alternativas")
def alternativas():
    pid=request.args.get("id",type=int)
    if not _HAS_MATCHER: return jsonify({"sugestoes":[]})
    con = cur = None   # senao o finally estoura (UnboundLocalError) se _conn() falha
    try:
        con=_conn(); cur=con.cursor()
        cur.execute("""SELECT CDFORNECED, DESC_XML, CHAVE_NF
                       FROM AIHUB.AIHUB_PENDENCIA WHERE ID=:i""", i=pid)
        r=cur.fetchone()
        if not r:
            return jsonify({"sugestoes":[]})
        forn, desc, chave = r[0], r[1], r[2]
        # ESTA E A TELA ONDE A PESSOA ESCOLHE O PRODUTO, entao e onde a lista
        # curta mais rende. Sabendo a AF, os candidatos caem de centenas para
        # poucos (medido: 2.502 -> 77 em 8 fornecedores), e a pessoa passa a
        # escolher entre 2 ou 3 opcoes em vez de rolar uma lista de 800.
        af = filial = None
        if chave:
            try:
                cur.execute("""SELECT G.CDFILIRECE,
                                 (SELECT MAX(I.NRAUTOFORN)
                                    FROM TEKNISA.INTG_ITEMPRODUTO I
                                   WHERE I.NRACESSONFE = G.NRACESSONFE
                                     AND I.NRAUTOFORN IS NOT NULL)
                               FROM TEKNISA.INTG_NOTAFISCAL G
                               WHERE G.NRACESSONFE = :c""", c=chave)
                q = cur.fetchone()
                if q:
                    filial, af = q[0], q[1]
            except Exception:
                pass          # sem a AF a lista completa continua valendo
        out = _ranquear(desc, _cands_af(cur, forn, af, filial))
        if af and filial:
            out["af"] = af   # a tela explica de onde vieram as opcoes
        return jsonify(out)
    except Exception as e:
        return jsonify({"sugestoes":[],"msg":_erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/resolver-pendencia", methods=["POST"])
def resolver_pendencia():
    # MODO LEITURA: antes, o item era marcado RESOLVIDO e SUMIA da fila mesmo
    # sem gravar o vinculo - o trabalho da pessoa era perdido silenciosamente.
    # Agora a acao e recusada com uma explicacao clara.
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta, então não posso "
                               "gravar o vínculo. Fale com a TI para liberar."}), 403
    dados=request.get_json(force=True,silent=True) or {}
    pid=dados.get("id"); cd=dados.get("cd_produto")
    if not pid or not cd:
        return jsonify({"ok":False,"msg":"Faltou escolher o produto."}), 400
    con = cur = None
    try:
        con=_conn(); cur=con.cursor()
        cur.execute("""SELECT CDFORNECED,CPROD_XML,DESC_XML,STATUS,CHAVE_NF
                       FROM AIHUB.AIHUB_PENDENCIA WHERE ID=:i""", i=pid)
        r=cur.fetchone()
        if not r or r[3]!="ABERTO":
            return jsonify({"ok":False,"msg":"Este item já saiu da fila (outra pessoa pode ter resolvido)."}), 409
        forn,cprod,desc,chave=r[0],r[1],r[2],r[4]
        op = _operador()
        # O PRODUTO TEM DE BATER COM A AF, e quem exige isso e o Teknisa.
        #
        # Caso real (NF 9668877): a pessoa vinculou 'MUSSARELA BUFALA' num item
        # cuja AF pediu 'MUSSARELA INTEIRO'. Gravou sem reclamacao, e so na
        # importacao veio a recusa com o codigo 071. Recusar aqui, com a lista
        # do que a AF pediu, e melhor que deixar salvar e falhar depois.
        if chave and _HAS_MATCHER:
            try:
                from core.ai_matcher import produto_bate_com_af
                cur.execute("""SELECT G.CDFILIRECE,
                                 (SELECT MAX(I.NRAUTOFORN)
                                    FROM TEKNISA.INTG_ITEMPRODUTO I
                                   WHERE I.NRACESSONFE = G.NRACESSONFE
                                     AND I.NRAUTOFORN IS NOT NULL)
                               FROM TEKNISA.INTG_NOTAFISCAL G
                               WHERE G.NRACESSONFE = :c""", c=chave)
                q = cur.fetchone()
                if q and q[1]:
                    bate, da_af = produto_bate_com_af(cur, cd, q[1], q[0])
                    if not bate:
                        return jsonify({
                            "ok": False, "af": q[1],
                            "produtos_af": [{"cd": a, "nm": _fix(_valor_json(b))}
                                            for a, b in da_af],
                            "msg": ("Este produto não é o da AF %s, e o Teknisa "
                                    "recusa a importação nesse caso (código "
                                    "071). Escolha um dos produtos que a AF "
                                    "pediu." % q[1])}), 409
            except Exception:
                pass      # conferir e um extra; falha aqui nao pode travar
        # duas colunas, dois tamanhos: a do Teknisa aceita 12, a nossa 30
        _gravar_vinculo(cur, cd, forn, cprod, desc, _operador_teknisa())
        cur.execute("""UPDATE AIHUB.AIHUB_PENDENCIA SET STATUS='RESOLVIDO',DT_RESOLUCAO=SYSDATE,
                       CD_ESCOLHIDO=:cd, CD_OPERADOR=:op WHERE ID=:i""", cd=cd, op=op[:20], i=pid)
        # A CHAVE VAI JUNTO. Sem ela a trilha entrava com CHAVE_NF='-' e a
        # aprovacao humana ficava impossivel de achar pela nota: quem abrisse a
        # ficha da NF via o vinculo do robo e nao via o da pessoa, como se
        # ninguem tivesse decidido nada. A chave ja estava em maos (r[4]).
        _auditar(cur, "VINCULO",
                 "pendencia %s: item %s -> produto %s (decisao de uma pessoa"
                 " na fila de revisao)" % (pid, cprod, cd), chave)
        con.commit()
        # FECHA O CICLO. Sem isto a pessoa resolve o produto e fica sem saber que
        # a nota ja pode entrar - teria de lembrar a chave, trocar de tela e
        # procurar a nota de novo. Se este era o ULTIMO item pendente daquela
        # nota, a tela passa a oferecer a importacao ali mesmo.
        pronta = False
        if chave:
            cur.execute("""SELECT COUNT(*) FROM AIHUB.AIHUB_PENDENCIA
                           WHERE CHAVE_NF=:c AND STATUS='ABERTO'""", c=chave)
            pronta = (int(cur.fetchone()[0] or 0) == 0)
        return jsonify({"ok":True,"gravou":True,"chave":chave,"pronta":pronta,
                        "msg":("Vínculo gravado. O robô já aprendeu."
                               + (" Esta nota não tem mais item pendente."
                                  if pronta else ""))})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok":False,"msg":_erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Conferencia dos vinculos do robo ------------ #
@app.route("/api/robo-resolvidos")
def robo_resolvidos():
    """Vinculos (De/Para) que o ROBO criou sozinho, para conferencia/auditoria.
    Mostra: descricao como veio na nota -> produto interno escolhido pelo robo."""
    forn = (request.args.get("forn") or "").strip()
    # por padrao mostra so o que AINDA NAO foi conferido (o trabalho feito sai
    # da frente); "todos=1" mostra o historico completo.
    so_pendentes = (request.args.get("todos") or "") != "1"
    pag = max(0, request.args.get("pag", default=0, type=int))
    PG = 50
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # Inclui o que o ROBO criou E o que foi aprovado pelo PAINEL. Antes, so
        # os do robo apareciam - o vinculo aprovado por uma pessoa nao podia ser
        # conferido nem corrigido por nenhuma tela.
        where = ["D.IDATIVO='S'",
                 "(D.CDOPERINCLUSAO='AIHUB_ROBO' OR D.CDOPERINCLUSAO LIKE 'PAINEL%'"
                 " OR D.CDOPERINCLUSAO='AIHUB_PAINEL')"]
        binds = {}
        if forn:
            where.append("D.CDFORNECED=:fn")
            binds["fn"] = forn
        if so_pendentes:
            where.append("C.OK IS NULL")      # ainda nao conferidos
        binds["off"] = pag * PG
        binds["lim"] = PG + 1
        cur.execute(f"""
            SELECT D.CDFORNECED, F.NMRAZSOCFORN, D.CDPRODUTOXML, D.NMPRODUTOXML,
                   D.CDPRODUTO, P.NMPRODUTO,
                   TO_CHAR(D.DTINCLUSAO,'DD/MM/YYYY HH24:MI') AS DT,
                   C.OK AS CONFERIDO
            FROM   TEKNISA.PRODFORNXML D
            LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED=D.CDFORNECED
            LEFT   JOIN TEKNISA.PRODUTO   P ON P.CDPRODUTO=D.CDPRODUTO
            LEFT   JOIN AIHUB.AIHUB_CONFERIDO C
                   ON C.CDFORNECED=D.CDFORNECED AND C.CDPRODUTOXML=D.CDPRODUTOXML
            WHERE  {' AND '.join(where)}
            ORDER  BY D.DTINCLUSAO DESC
            OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """, binds)
        rows = _rows(cur)
        tem_mais = len(rows) > PG
        rows = rows[:PG]
        return jsonify({"itens": rows, "pag": pag, "tem_mais": tem_mais,
                        "so_pendentes": so_pendentes})
    except Exception as e:
        return jsonify({"itens": [], "erro": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/conferir", methods=["POST"])
def conferir():
    """Marca um vinculo do robo como CONFERIDO (grava so no schema AIHUB)."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    forn = (dados.get("forn") or "").strip()
    cx = (dados.get("cprod_xml") or "").strip()
    if not forn or not cx:
        return jsonify({"ok": False, "msg": "Dados incompletos."}), 400
    con = cur = None
    try:
        op = _operador()
        con = _conn()
        cur = con.cursor()
        cur.execute("""
            MERGE INTO AIHUB.AIHUB_CONFERIDO c
            USING (SELECT :f CDFORNECED, :cx CDPRODUTOXML FROM dual) s
               ON (c.CDFORNECED=s.CDFORNECED AND c.CDPRODUTOXML=s.CDPRODUTOXML)
             WHEN MATCHED THEN UPDATE SET OK='S', DT_CONF=SYSDATE, OPERADOR=:op
             WHEN NOT MATCHED THEN INSERT (CDFORNECED, CDPRODUTOXML, OK, OPERADOR)
                  VALUES (:f, :cx, 'S', :op)
        """, f=forn, cx=cx, op=op[:20])
        con.commit()
        return jsonify({"ok": True, "msg": "Marcado como conferido."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


@app.route("/api/corrigir-vinculo", methods=["POST"])
def corrigir_vinculo():
    """Desfaz um vinculo (do robo OU aprovado no painel), para revincular.
    Mexe no Teknisa, entao exige modo escrita."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    forn = (dados.get("forn") or "").strip()
    cx = (dados.get("cprod_xml") or "").strip()
    if not forn or not cx:
        return jsonify({"ok": False, "msg": "Dados incompletos."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # antes so desativava os do robo; o vinculo aprovado por uma pessoa
        # ficava impossivel de corrigir por qualquer tela.
        cur.execute("""UPDATE TEKNISA.PRODFORNXML SET IDATIVO='N'
                       WHERE CDFORNECED=:f AND CDPRODUTOXML=:cx AND IDATIVO='S'
                         AND (CDOPERINCLUSAO='AIHUB_ROBO'
                              OR CDOPERINCLUSAO='AIHUB_PAINEL'
                              OR CDOPERINCLUSAO LIKE 'PAINEL%')""",
                    f=forn, cx=cx)
        n = cur.rowcount
        # DESFAZER COMPLETO: alem de desativar o vinculo, devolve o item para a
        # fila de revisao (se existir uma pendencia ja resolvida para ele).
        # Sem isso o item sumia da fila e nunca mais voltava.
        reabertas = 0
        try:
            cur.execute("""UPDATE AIHUB.AIHUB_PENDENCIA
                              SET STATUS='ABERTO', DT_RESOLUCAO=NULL, CD_ESCOLHIDO=NULL
                            WHERE CDFORNECED=:f AND CPROD_XML=:cx AND STATUS='RESOLVIDO'""",
                        f=forn, cx=cx)
            reabertas = cur.rowcount
        except Exception as e:
            print(" aviso ao reabrir pendencia:", str(e)[:120], flush=True)
        if n or reabertas:
            _auditar(cur, "DESFAZ_VINCULO",
                     "fornecedor %s, item %s (%d vinculo(s), %d pendencia(s) reaberta(s))"
                     % (forn, cx, n, reabertas))
        con.commit()
        if n and reabertas:
            msg = "Vínculo desfeito e item devolvido para a fila."
        elif n:
            msg = "Vínculo desfeito."
        else:
            msg = "Não havia vínculo ativo para desfazer."
        return jsonify({"ok": True, "n": n, "reabertas": reabertas, "msg": msg})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Reabrir pendencia (desfazer) ---------------- #
@app.route("/api/reabrir-pendencia", methods=["POST"])
def reabrir_pendencia():
    """DESFAZER: devolve para a fila um item que foi resolvido por engano e
    desativa o vinculo que ele criou. Antes nao havia como voltar atras."""
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Fale com a TI para liberar."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    pid = dados.get("id")
    if not pid:
        return jsonify({"ok": False, "msg": "Dados incompletos."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute("""SELECT CDFORNECED, CPROD_XML, CD_ESCOLHIDO, STATUS
                       FROM AIHUB.AIHUB_PENDENCIA WHERE ID=:i""", i=pid)
        r = cur.fetchone()
        if not r:
            return jsonify({"ok": False, "msg": "Item não encontrado."}), 404
        if r[3] != "RESOLVIDO":
            return jsonify({"ok": False, "msg": "Este item não está resolvido."}), 409
        forn, cx = r[0], r[1]
        cur.execute("""UPDATE TEKNISA.PRODFORNXML SET IDATIVO='N'
                       WHERE CDFORNECED=:f AND CDPRODUTOXML=:cx AND IDATIVO='S'
                         AND (CDOPERINCLUSAO='AIHUB_ROBO'
                              OR CDOPERINCLUSAO='AIHUB_PAINEL'
                              OR CDOPERINCLUSAO LIKE 'PAINEL%')""", f=forn, cx=cx)
        cur.execute("""UPDATE AIHUB.AIHUB_PENDENCIA
                          SET STATUS='ABERTO', DT_RESOLUCAO=NULL, CD_ESCOLHIDO=NULL
                        WHERE ID=:i""", i=pid)
        _auditar(cur, "REABRE", "pendencia %s devolvida para a fila" % pid)
        con.commit()
        return jsonify({"ok": True, "msg": "Item devolvido para a fila e vínculo desfeito."})
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


# ----------------------------- Disparar a IA AGORA (teste) ----------------- #
@app.route("/api/gemini-agora", methods=["POST"])
def gemini_agora():
    """Roda uma passada da IA na hora (em segundo plano), pra ver o resultado
    sem esperar os 30 min. So funciona com a chave presente e o teste ligado."""
    # MODO CONSULTA TEM QUE SIGNIFICAR QUE NADA MUDA - inclusive as NOSSAS
    # tabelas. Esta rota grava em AIHUB_GEMINI, entao respeita a mesma trava das
    # demais rotas de escrita. Antes era a unica excecao: em modo consulta ela
    # gravava assim mesmo, numa thread de fundo.
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel está em modo consulta. Para a IA gravar a "
                               "opinião dela, inicie pelo atalho de escrita."}), 403
    if not (_HAS_GEMINI and ai_gemini.disponivel()):
        return jsonify({"ok": False,
                        "msg": "Sem chave da IA. Defina GEMINI_API_KEY no atalho e reinicie."})
    con = cur = None
    try:
        con = _conn(); cur = con.cursor()
        ligado = _cfg_get(cur, "USA_GEMINI", "0") == "1"
    except Exception as e:
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)
    if not ligado:
        return jsonify({"ok": False, "msg": "Ligue o 'Assistente de IA' em Configuracoes primeiro."})

    def _run():
        c = None
        try:
            c = _conn()
            _gemini_processa(c, limite=12, pausa=1.0)   # lote menor e agil no on-demand
        except Exception as e:
            print(" (aviso) gemini-agora:", str(e)[:140], flush=True)
        finally:
            _fechar(None, c)
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "msg": "IA avaliando em segundo plano. Atualizo a lista em instantes."})


@app.route("/api/gemini-testar")
def gemini_testar():
    """Diagnostico: faz uma chamada minima a IA e devolve se conectou + o erro
    real (se houver). Serve para saber na hora por que a IA nao respondeu."""
    if not _HAS_GEMINI:
        return jsonify({"ok": False, "detalhe": "Modulo de IA nao carregado no servidor."})
    ok, det = ai_gemini.testar()
    return jsonify({"ok": bool(ok), "detalhe": det,
                    "modelo": getattr(ai_gemini, "MODEL", "")})


@app.route("/api/gemini-chave", methods=["GET", "POST"])
def gemini_chave():
    """GET  -> diz SE ha chave e de onde vem, mostrando so o valor MASCARADO.
    POST -> grava a chave nova em AIHUB_CONFIG e aplica na hora (sem reiniciar).

    A chave fica em AIHUB.AIHUB_CONFIG (chave GEMINI_KEY). Nasce dai a cada
    arranque (ver carga em _prewarm). Assim trocar a chave e um campo na tela,
    nao uma edicao de arquivo no servidor."""
    if not _HAS_GEMINI:
        return jsonify({"ok": False, "msg": "Modulo de IA nao carregado."})
    if request.method == "GET":
        return jsonify({"tem_chave": ai_gemini.disponivel(),
                        "mascarada": ai_gemini.chave_mascarada(),
                        "origem": getattr(ai_gemini, "ORIGEM_CHAVE", "nenhuma")})
    # POST grava - so em modo escrita, como toda rota que altera o banco
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "O painel esta em modo consulta. Para salvar a "
                               "chave, inicie pelo atalho de escrita."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    chave = (dados.get("chave") or "").strip()
    # sanidade minima: as chaves do Google sao "AQ." ou "AIza", sem espacos.
    # Nao valida o formato todo - a Google faz isso; so barra colagem obviamente
    # errada e o campo vazio quando a intencao era gravar.
    if chave and (" " in chave or len(chave) < 20):
        return jsonify({"ok": False,
                        "msg": "Isso nao parece uma chave da API. Cole a chave "
                               "inteira, sem espacos."}), 400
    con = cur = None
    try:
        con = _conn(); cur = con.cursor()
        _cfg_upsert(cur, "GEMINI_KEY", chave)     # vazio = apagar (volta ao atalho)
        _auditar(cur, "GEMINI_CHAVE",
                 "chave " + ("definida pelo painel" if chave else "removida (volta ao atalho)"))
        con.commit()
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)
    # aplica AGORA, sem reiniciar
    ai_gemini.definir_chave(chave, "painel" if chave else "atalho")
    # e ja testa, para a pessoa saber na hora se a chave presta
    ok, det = ai_gemini.testar() if chave else (False, "chave removida")
    return jsonify({"ok": True, "salvo": True,
                    "tem_chave": ai_gemini.disponivel(),
                    "mascarada": ai_gemini.chave_mascarada(),
                    "teste_ok": bool(ok), "teste_detalhe": det})


@app.route("/api/gemini-modelos")
def gemini_modelos():
    """Lista os modelos que a CHAVE atual pode usar. Antidoto para nome de modelo
    que envelhece: em vez de cravar um nome que a Google pode descontinuar, o
    painel pergunta a ela o que esta conta oferece."""
    if not (_HAS_GEMINI and ai_gemini.disponivel()):
        return jsonify({"modelos": [], "erro": "Configure a chave da IA primeiro."})
    nomes, erro = ai_gemini.listar_modelos()
    return jsonify({"modelos": nomes or [], "erro": erro,
                    "atual": getattr(ai_gemini, "MODEL", "")})


@app.route("/api/gemini-modelo", methods=["POST"])
def gemini_modelo():
    """Grava o modelo escolhido em AIHUB_CONFIG e aplica na hora."""
    if not _HAS_GEMINI:
        return jsonify({"ok": False, "msg": "Modulo de IA nao carregado."})
    if not MODO_ESCRITA:
        return jsonify({"ok": False,
                        "msg": "Modo consulta. Inicie pelo atalho de escrita."}), 403
    dados = request.get_json(force=True, silent=True) or {}
    modelo = (dados.get("modelo") or "").strip().replace("models/", "")
    con = cur = None
    try:
        con = _conn(); cur = con.cursor()
        _cfg_upsert(cur, "GEMINI_MODEL", modelo)
        _auditar(cur, "GEMINI_MODELO", "modelo = " + (modelo or "(padrao)"))
        con.commit()
    except Exception as e:
        _rollback(con)
        return jsonify({"ok": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)
    ai_gemini.definir_modelo(modelo, "painel" if modelo else "padrao")
    ok, det = ai_gemini.testar()
    return jsonify({"ok": True, "modelo": getattr(ai_gemini, "MODEL", ""),
                    "teste_ok": bool(ok), "teste_detalhe": det})


# ----------------------------- Loop do assistente de IA (teste) ------------ #
def _gemini_processa(con, limite=20, pausa=1.2):
    cur = con.cursor()
    cur.execute("""
        SELECT P.ID, P.CDFORNECED, P.DESC_XML
        FROM   AIHUB.AIHUB_PENDENCIA P
        LEFT   JOIN AIHUB.AIHUB_GEMINI G ON G.PEND_ID = P.ID
        WHERE  P.STATUS='ABERTO' AND P.DESC_XML IS NOT NULL
          AND  (G.PEND_ID IS NULL OR G.MOTIVO = '(a IA nao respondeu)')
        FETCH FIRST %d ROWS ONLY
    """ % int(limite))
    itens = cur.fetchall()
    cur.close()
    n = 0
    for pid, forn, desc in itens:
        cur = con.cursor()
        cands = _cands_af(cur, forn)
        cur.close()
        op = ai_gemini.sugerir(_fix(desc or ""),
                               [(c[0], _fix(c[1] or "")) for c in cands])
        cur = con.cursor()
        cur.execute("""
            MERGE INTO AIHUB.AIHUB_GEMINI g
            USING (SELECT :i PEND_ID FROM dual) s ON (g.PEND_ID = s.PEND_ID)
            WHEN MATCHED THEN UPDATE SET CD_SUGERIDO=:cd, NM_SUGERIDO=:nm, MOTIVO=:mo, DT=SYSDATE
            WHEN NOT MATCHED THEN INSERT (PEND_ID,CD_SUGERIDO,NM_SUGERIDO,MOTIVO)
                 VALUES (:i,:cd,:nm,:mo)
        """, i=pid,
             cd=(op.get("cd") if op else None),
             nm=((op.get("nome") or "")[:150] if op else None),
             mo=(("(a IA nao respondeu)" if op is None else (op.get("motivo") or ""))[:400]))
        con.commit()   # grava JA (o painel mostra assim que atualiza)
        cur.close()
        n += 1
        time.sleep(pausa)   # respeita o limite de requisicoes do plano gratis
    if n:
        print(" Gemini: %d item(ns) avaliado(s)." % n, flush=True)


def _gemini_loop():
    """A cada 30 min, se o teste estiver LIGADO, pede a opiniao da IA para os
    itens da fila que ainda nao tem opiniao. Opcional e isolado.

    So roda em MODO ESCRITA: grava em AIHUB_GEMINI, e painel em modo consulta
    nao pode alterar nada - nem as nossas proprias tabelas."""
    if not MODO_ESCRITA:
        return
    time.sleep(45)
    while True:
        con = cur = None
        try:
            if _HAS_GEMINI and ai_gemini.disponivel():
                con = _conn(); cur = con.cursor()
                ligado = _cfg_get(cur, "USA_GEMINI", "0") == "1"
                cur.close(); cur = None
                if ligado:
                    _gemini_processa(con)
        except Exception as e:
            print(" (aviso) gemini loop:", str(e)[:140], flush=True)
        finally:
            _fechar(cur, con)
        time.sleep(1800)


def _prewarm():
    """Aquece o pool + o cache de esquema + os indicadores pesados no start,
    para que o PRIMEIRO clique do usuario ja venha rapido."""
    time.sleep(1)
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        _col_data_nf(cur)
        _col_af_item(cur)
        _col_val_item(cur)
        _col_val_dest(cur)
        _col_val_nf(cur)     # tambem usada no detalhe da nota
        # chave e modelo da IA gravados pelo painel vencem o atalho (se houver)
        if _HAS_GEMINI:
            k = _cfg_get(cur, "GEMINI_KEY", "")
            if k:
                ai_gemini.definir_chave(k, "painel")
                print(" IA: usando a chave gravada no painel.")
            md = _cfg_get(cur, "GEMINI_MODEL", "")
            if md:
                ai_gemini.definir_modelo(md, "painel")
                print(" IA: usando o modelo gravado no painel:", md)
    except Exception as e:
        print(" (aviso) aquecimento do pool:", str(e)[:120])
    finally:
        _fechar(cur, con)
    # SEM pre-aquecimento pesado: com a base grande sem indice, aquecer varias
    # consultas ao mesmo tempo no boot competia e deixava tudo lento. Cada tela
    # carrega sob demanda e fica em cache; o single-flight evita duplicar.
    print(" Pool pronto. (aquecimento pesado desligado)")


# --------------------------------------------------------------------------- #
# FICHA DA NOTA: o que o sistema fez nesta nota, e por que
# --------------------------------------------------------------------------- #
# POR QUE ESTA TELA EXISTE. O sistema ja registrava as coisas, mas espalhadas:
# a AF ia para a trilha, o De/Para para a PRODFORNXML, a decisao humana para a
# fila de revisao, e a importacao so aparecia no proprio Teknisa. Para responder
# "por que esta nota foi dada como certa?" era preciso garimpar quatro lugares,
# e duas telas nossas ja discordaram sobre a mesma nota por olharem fontes
# diferentes (achado 90). Aqui a pergunta e feita uma vez so.
#
# A REGRA DA TELA: ela NAO recalcula nada. Mostra o que ficou REGISTRADO, e
# quando algo nao foi registrado ela DIZ que nao foi, em vez de reconstruir e
# apresentar como se fosse o que aconteceu. Reconstruir e util - o robo pode
# decidir diferente hoje, porque a pessoa ensinou regra no meio do caminho -
# mas e outra pergunta, e quem responde e a tela "Como ele pensa".
_FICHA_PASSO = {
    "DEPARA_ROBO":          ("robo",   "Reconheceu o produto e criou o De/Para"),
    "AF_VINCULO_ROBO":      ("robo",   "Escolheu a AF e gravou na nota"),
    "IMPORTADA":            ("robo",   "Importou a nota"),
    "CONFIRMADA":           ("robo",   "Confirmou o recebimento"),
    "PENDENTE_HUMANO":      ("robo",   "Parou e mandou para a fila de revisão"),
    "AF_VENCIDA":           ("robo",   "Recusou importar: a AF já tinha sido baixada"),
    "MODO_CONSULTA":        ("robo",   "Recusou gravar: sistema em modo consulta"),
    "SEM_ITENS":            ("robo",   "Nota sem itens"),
    "ERRO":                 ("robo",   "Erro no processamento"),
    "ERRO_CONFIRMA":        ("robo",   "Erro ao confirmar"),
    "AUDIT_VINCULO":        ("pessoa", "Uma pessoa escolheu o produto na fila de revisão"),
    "AUDIT_AF_VINCULO":     ("pessoa", "Uma pessoa escolheu a AF no painel"),
    "AUDIT_REPROCESSA":     ("pessoa", "Uma pessoa mandou processar a nota"),
    "AUDIT_ARQUIVA":        ("pessoa", "Uma pessoa arquivou a nota"),
    "AUDIT_DESARQUIVA":     ("pessoa", "Uma pessoa devolveu a nota para a lista"),
    "AUDIT_DESFAZ_VINCULO": ("pessoa", "Uma pessoa desfez o vínculo"),
    "AUDIT_REABRE":         ("pessoa", "Uma pessoa devolveu o item para a fila"),
    "AUDIT_OCORRENCIA":     ("pessoa", "Uma pessoa tratou a ocorrência"),
}


def _ficha_autor(op, desconhecido="teknisa"):
    """Quem fez, em portugues, a partir do operador gravado.

    O que e NOSSO se reconhece igual nas duas tabelas, e por isso a regra fica
    num lugar so. O que NAO e nosso muda de significado conforme a tabela, e
    por isso quem chama informa:

      PRODFORNXML  -> operador estranho e cadastro feito na tela do Teknisa
                      (vem como matricula curta, '1')
      HISTXMLNFE   -> operador estranho e a MATRICULA DE UMA PESSOA; so
                      'AUTOMATICO' e evento de maquina (achado 3)

    Forcar uma leitura so ja errou nos dois sentidos: primeiro o robo apareceu
    como "uma pessoa" (porque AIHUB_ROBO caiu no ramo generico), depois as
    matriculas apareceram como "cadastro do Teknisa"."""
    o = (op or "").strip().upper()
    if not o:
        return ("teknisa", "")
    if o == "AUTOMATICO":
        return ("teknisa", "")
    if o == "AIHUB_ROBO":
        return ("robo", "pelo robô deste sistema")
    if o.startswith("AIHUB_PAINEL") or o.startswith("PAINEL"):
        return ("pessoa", "por uma pessoa, no painel deste sistema")
    if desconhecido == "pessoa":
        return ("pessoa", "matrícula %s" % op)
    return ("teknisa", "cadastro do Teknisa (operador %s)" % op)


def _cols_presentes(cur, tabela, nomes, cache_key):
    """Quais das colunas pedidas existem nesta instalacao. UMA consulta.

    O `_col_disponivel` responde "qual destes nomes existe" e le a tabela
    inteira a cada chave nova; para conferir seis colunas de uma vez seriam
    seis leituras do ALL_TAB_COLUMNS. Aqui e uma so, e o resultado fica no
    mesmo cache."""
    if cache_key in _col_cache:
        return _col_cache[cache_key]
    achou = set()
    try:
        cur.execute("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS "
                    "WHERE OWNER='TEKNISA' AND TABLE_NAME=:t", t=tabela)
        cols = {r[0] for r in cur.fetchall()}
        achou = {n for n in nomes if n in cols}
    except Exception:
        pass                            # fail-safe: sem coluna, sem o campo
    _col_cache[cache_key] = achou
    return achou


# Os parametros do Teknisa que governam a decisao, com o que cada valor
# significa em portugues. Os nomes e os valores foram conferidos no banco.
_PARAM_FICHA = {
    "IDBLOQCMPSAF": (
        "Bloquear nota de compra sem AF",
        {"S": "sim, o Teknisa barra a nota que não tem AF",
         "N": "não barra: nota sem AF entra assim mesmo"}),
    "IDUTCFOPXMLLANNF": (
        "Usar o de/para de CFOP do XML",
        {"S": "sim, o CFOP do XML é convertido pela tabela do Teknisa",
         "N": "desligado (a tabela CFOPXMLTPOPER está vazia neste banco)"}),
    "IDGERNFIGUALXML": (
        "Gerar a nota igual ao XML",
        {"S": "sim, a nota nasce idêntica ao arquivo",
         "N": "não: o Teknisa aplica a parametrização dele"}),
    # SO VALE NO REGIME NATIVO. Sem esta ressalva a flag parece contradizer a
    # janela mostrada logo abaixo, que costuma estar ancorada na emissao.
    "IDUTILDTPROC": (
        "Ancorar a janela na data de processamento da filial",
        {"S": "sim — mas só quando a filial configurou limite de dias",
         "N": "não, a âncora seria a data de hoje"}),
}


def _ficha_criterios(cur, cd_filial, cd_tipo, dt_emissao, cfops):
    """OS CRITERIOS E PARAMETROS QUE VALERAM PARA ESTA NOTA.

    O QUE ESTA SECAO E, E O QUE ELA NAO E. Este sistema NAO parametriza
    imposto: quem calcula tributo e a parametrizacao do Teknisa, e quem decide
    regra fiscal e o Fiscal. O robo vincula produto e AF. O que cabe mostrar
    aqui, entao, e o que GOVERNOU a decisao - as tolerancias, a janela, as
    travas do ERP e a classificacao fiscal com que a nota chegou. Apresentar
    isso como "o robo avaliou o imposto" seria mentira, e a tela diz o
    contrario, com todas as letras.

    OS NUMEROS SAO LIDOS AO VIVO, e pelas MESMAS funcoes que o motor usa
    (ler_tolerancias, janela_da_filial). Uma copia da leitura aqui mostraria um
    numero e o motor usaria outro no dia em que alguem mexesse numa delas - e
    uma tela de auditoria que diverge do motor e pior que nenhuma tela.

    Fail-safe: cada bloco e independente e engolido em separado. Falta de GRANT
    em PARAMGERAL nao pode derrubar a ficha inteira."""
    out = {}

    # 1) tipo de operacao, com o nome que o Teknisa da a ele. A chave e
    #    (IDENTRSAIDOP, CDTIPOOPER): o codigo sozinho repete entre entrada e
    #    saida com sentidos opostos (achado 14).
    if cd_tipo:
        # O CODIGO ENTRA PRIMEIRO, e o nome e um extra. Antes o dicionario so
        # nascia se a consulta desse certo, e em producao o AIHUB_LEITURA nao
        # tem GRANT em TIPOOPERAC: o fail-safe funcionou (nao quebrou a tela)
        # mas ESCONDEU o tipo '01', que ja estava em maos. Fail-safe deve
        # fechar a porta, nao jogar fora o que ja se sabe.
        out["tipo"] = {"codigo": _valor_json(cd_tipo), "nome": None}
        if _tem_tabela(cur, "TEKNISA.TIPOOPERAC", "tipooperac"):
            try:
                cur.execute("""SELECT NMTIPOOPER FROM TEKNISA.TIPOOPERAC
                               WHERE IDENTRSAIDOP='E' AND CDTIPOOPER=:t""",
                            t=cd_tipo)
                r = cur.fetchone()
                if r:
                    out["tipo"]["nome"] = _fix(_valor_json(r[0]))
            except Exception as e:
                print(" aviso ficha/tipooperac:", str(e)[:120], flush=True)
        if not out["tipo"]["nome"]:
            out["tipo"]["obs"] = ("O nome do tipo não está disponível neste "
                                  "banco (falta acesso a TIPOOPERAC).")
    else:
        # sem tipo o robo nem alcanca a nota (achado 101): quem preenche essa
        # coluna e a tela "Relacionar AF", ou seja, uma pessoa
        out["tipo"] = {"codigo": None, "nome": None,
                       "aviso": "Esta nota não tem tipo de operação. O robô só "
                                "trabalha nota que já tem, porque sem ele não "
                                "dá para saber se é compra."}

    # 2) o CFOP e o unico criterio FISCAL que o robo de fato usa para decidir
    try:
        from core.cfop_natureza import natureza
        vistos, veredito = [], None
        for c in (cfops or []):
            nat, motivo = natureza(c)
            vistos.append({"cfop": c, "natureza": nat, "motivo": motivo})
            # a nota inteira segue o CFOP mais restritivo que apareceu
            if veredito is None or (nat != "COMPRA" and veredito == "COMPRA"):
                veredito = nat
        out["cfop"] = {"veredito": veredito, "por_codigo": vistos}
    except Exception as e:
        print(" aviso ficha/cfop:", str(e)[:120], flush=True)

    # 3) tolerancias: as MESMAS que o motor usa
    try:
        from core.af_matcher import ler_tolerancias
        qt, vr = ler_tolerancias(cur)
        out["tolerancias"] = {
            "quantidade": qt, "valor": vr,
            "onde": "PARAMGERAL.VRPETOLEQTAF e VRPETOLEVRAF",
            "para_que": ("A de quantidade decide se a AF cobre a nota. A de "
                         "valor é do fechamento, e quem aplica é a "
                         "VERIFICA_INCONS_NF do Teknisa, não este sistema.")}
    except Exception as e:
        print(" aviso ficha/tolerancias:", str(e)[:120], flush=True)

    # 4) a janela de entrega considerada
    try:
        from core.af_matcher import janela_da_filial
        anc, antes, depois = janela_da_filial(cur, cd_filial, dt_emissao)
        # QUAL DOS DOIS REGIMES ESTA VALENDO (achado 58). Sem dizer isto a tela
        # se contradiz: a flag IDUTILDTPROC anuncia "a ancora e a DTPROCESSA" e
        # logo abaixo a janela mostra a emissao como ancora. Sao regimes
        # diferentes, e o da filial so entra quando ela configurou os limites.
        propria = (dt_emissao is not None and anc == dt_emissao)
        out["janela"] = {
            "ancora": _valor_json(anc), "antes": antes, "depois": depois,
            "regime": ("propria" if propria else "nativa"),
            "onde": "PARAMFILIAL.NRMAXDIAANTDTAF e NRMAXDIAPOSDTAF",
            "para_que": ("Só entram AFs com entrega programada dentro dessa "
                         "faixa."),
            "explica": ("Esta filial NÃO tem limite de dias configurado, então "
                        "o Teknisa não aplicaria janela nenhuma e a busca "
                        "pegaria AF de anos atrás. Para o automático isso é "
                        "perigoso, e por isso o sistema usa uma janela própria "
                        "de ±%s dias ancorada na EMISSÃO da nota."
                        % (antes if antes is not None else "?")) if propria
                       else ("A filial configurou limite de dias, então vale a "
                             "regra nativa do Teknisa: a âncora é a data de "
                             "processamento da filial.")}
    except Exception as e:
        print(" aviso ficha/janela:", str(e)[:120], flush=True)

    # 5) as travas do proprio Teknisa que valem nesta nota
    try:
        cur.execute("""SELECT IDBLOQCMPSAF, IDUTCFOPXMLLANNF,
                              IDGERNFIGUALXML, IDUTILDTPROC
                       FROM TEKNISA.PARAMGERAL""")
        r = cur.fetchone()
        if r:
            flags = []
            for i, nome in enumerate(("IDBLOQCMPSAF", "IDUTCFOPXMLLANNF",
                                      "IDGERNFIGUALXML", "IDUTILDTPROC")):
                v = (_valor_json(r[i]) or "").strip().upper()
                rot, sig = _PARAM_FICHA[nome]
                flags.append({"campo": nome, "rotulo": rot, "valor": v,
                              "significa": sig.get(v, "valor %s" % v)})
            out["flags"] = flags
    except Exception as e:
        print(" aviso ficha/paramgeral:", str(e)[:120], flush=True)
    return out


def _ficha_seq(v):
    """A sequencia do item, numa forma so.

    A MESMA sequencia e gravada de dois jeitos dentro do sistema, e isto esta
    medido: TEKNISA.INTG_ITEMPRODUTO.NRSEQITPROD guarda '1','2','3' e a nossa
    AIHUB_PENDENCIA guarda '0001','0002'. Comparar o texto cru faria o item da
    fila nunca casar com o item da nota - e sem erro nenhum, so faltando dado
    na tela. Normalizar nas duas pontas resolve, e de quebra a tela para de
    dizer "Item 0001" num lugar e "#1" no outro."""
    t = str(v if v is not None else "").strip()
    return (t.lstrip("0") or "0") if t.isdigit() else t


def _ficha_passo(status):
    """Traduz o status gravado para linguagem de gente.

    Status desconhecido NAO some da linha do tempo: aparece com o codigo cru.
    Trilha que esconde o que nao entende e pior que trilha crua - bastaria um
    passo novo do robo para ele ficar invisivel so por ter nome que esta tela
    ainda nao conhece."""
    st = (status or "").strip()
    if st in _FICHA_PASSO:
        return _FICHA_PASSO[st]
    if st.startswith("AUDIT_"):
        return ("pessoa", st[6:].replace("_", " ").capitalize())
    return ("robo", st or "(sem status)")


@app.route("/api/ficha-nota")
def ficha_nota():
    """A historia completa de UMA nota: cada passo, quem deu, e a logica.

    Junta fontes NOSSAS e do TEKNISA de proposito. As nossas dizem o que o
    sistema DECIDIU; as do Teknisa dizem o que de fato ACONTECEU. Conferir so o
    nosso lado da falso verde (achado 90), entao a prova de que a nota entrou
    vem da NOTAFISCAL e a de que a AF foi baixada vem da NFRELACAF."""
    chave = (request.args.get("chave") or "").strip()
    if not chave:
        return jsonify({"achou": False,
                        "msg": "Informe o número ou a chave da nota."}), 400
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        out = {"achou": False, "passos": [], "itens": [], "apontamentos": []}

        # ---- 1) a nota na integracao -------------------------------------- #
        # aceita chave (44) ou numero da nota. _cond_chave usa igualdade quando
        # a chave vem inteira: o LIKE com curinga inicial anula o indice e a
        # busca vai de 3s para 95s (achado 93)
        binds = {}
        cond = _cond_chave("G.NRACESSONFE", chave, binds)
        if chave.isdigit() and len(chave) < 44:
            cond = "(%s OR G.NRNOTAFISC = :nrnf)" % cond
            binds["nrnf"] = chave
        cur.execute("""
            SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDSERIEENTR, G.CDFORNECED,
                   G.CDFILIRECE, G.DTEMISSAO, G.VRNOTAFISC, G.DSTAGORIGFILI,
                   G.DTINCLUSAO, G.DTUTILIZAXML, G.DTLANCAMENNF, G.CDTIPOOPER,
                   (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
                     WHERE F.CDFORNECED = G.CDFORNECED)
            FROM   TEKNISA.INTG_NOTAFISCAL G
            WHERE  %s
            ORDER  BY G.DTEMISSAO DESC NULLS LAST
            FETCH  FIRST 1 ROWS ONLY
        """ % cond, **binds)
        r = cur.fetchone()
        if not r:
            return jsonify({"achou": False,
                            "msg": "Não achei essa nota. Confira o número ou "
                                   "cole a chave de 44 dígitos."})
        v = [_valor_json(x) for x in r]
        chave = v[0]
        forn = r[3]
        out["achou"] = True
        out["nota"] = {
            "chave": v[0], "numero": v[1], "serie": v[2], "cd_fornecedor": v[3],
            "filial": v[4], "emissao": v[5], "valor": v[6], "origem": v[7],
            "chegada": v[8], "tipo": v[11], "fornecedor": _fix(v[12]),
        }

        # ---- 2) entrou ou nao (prova no proprio Teknisa) ------------------- #
        b2 = {}
        c2 = _cond_chave("N.NRACESSONFE", chave, b2)
        cur.execute("""
            SELECT N.CDFILIAL, N.NRLANCTONF, N.IDSTATUS, N.DTLANCAMENNF,
                   N.DTAPROVACAO
            FROM   TEKNISA.NOTAFISCAL N
            WHERE  %s AND N.IDENTRSAIDOP = 'E'
            FETCH  FIRST 1 ROWS ONLY
        """ % c2, **b2)
        nf = cur.fetchone()
        _ST = {"L": "lançada, aguardando aprovação", "A": "aprovada",
               "I": "importada", "P": "processada", "C": "cancelada"}
        if nf:
            out["situacao"] = {
                "entrou": True, "filial": _valor_json(nf[0]),
                "lancamento": _valor_json(nf[1]), "status": _valor_json(nf[2]),
                "status_texto": _ST.get((nf[2] or "").strip(), _valor_json(nf[2])),
                "lancada_em": _valor_json(nf[3]),
                "aprovada_em": _valor_json(nf[4]),
            }
        else:
            out["situacao"] = {
                "entrou": False,
                "status_texto": ("ainda não entrou" if not v[9]
                                 else "marcada como utilizada, sem nota criada")}

        # ---- 3) itens, produto interno e AF ------------------------------- #
        # A CLASSIFICACAO FISCAL VEM NA MESMA CONSULTA. Sao colunas da linha
        # que ja estamos lendo, entao o custo e zero; buscar depois seria uma
        # ida ao banco por item numa tela que ja abre varias.
        # Nomes conferidos no banco: CDFISOPEITEM e o CFOP (100% preenchido),
        # CDSITUTRIBPR o CST (100%), CDCLASFISC o NCM (98%). Ficaram DE FORA
        # VRFRETEITEMXML e CDIMPADIC, que estao 0% preenchidos - mostrar campo
        # sempre vazio faz a tela parecer quebrada.
        _FISC = ("CDFISOPEITEM", "CDSITUTRIBPR", "CDCLASFISC",
                 "IDORIGEMPROD", "SGUNIDTRIB")
        tem_f = _cols_presentes(cur, "INTG_ITEMPRODUTO", _FISC, "fisc_item")
        sel_f = "".join(", I.%s" % c if c in tem_f else ", NULL" for c in _FISC)
        cur.execute("""
            SELECT I.NRSEQITPROD, I.CDPRODUTO, I.NMPRODUTO, I.SGUNIDADE,
                   NVL(I.QTITEMPROD,0), I.NRAUTOFORN%s
            FROM   TEKNISA.INTG_ITEMPRODUTO I
            WHERE  I.NRACESSONFE = :c
            ORDER  BY I.NRSEQITPROD
        """ % sel_f, c=chave)
        itens = []
        for it in cur.fetchall():
            itens.append({
                "seq": _ficha_seq(_valor_json(it[0])),
                "cod_forn": _valor_json(it[1]),
                "desc_xml": _fix(_valor_json(it[2])), "un": _valor_json(it[3]),
                "qt": _valor_json(it[4]), "af": _valor_json(it[5]),
                "cd_produto": None, "nm_produto": None, "vinculo_quem": None,
                "vinculo_quando": None, "logica": None, "score": None,
                "autor": None, "autor_texto": None,
                # como a nota veio classificada, do jeito que o fornecedor
                # mandou no XML: a tela mostra, nao interpreta
                "cfop": _valor_json(it[6]), "cst": _valor_json(it[7]),
                "ncm": _valor_json(it[8]), "origem": _valor_json(it[9]),
                "un_trib": _valor_json(it[10]),
            })

        # o De/Para de TODOS os itens numa consulta so; um SELECT por item faria
        # N idas ao banco numa tela que ja abre varias consultas
        codigos = [i["cod_forn"] for i in itens if i["cod_forn"]]
        if codigos:
            bb = {"f": forn}
            marc = []
            for k, cx in enumerate(codigos[:200]):
                bb["c%d" % k] = cx
                marc.append(":c%d" % k)
            cur.execute("""
                SELECT D.CDPRODUTOXML, D.CDPRODUTO, D.CDOPERINCLUSAO,
                       D.DTINCLUSAO,
                       (SELECT MAX(P.NMPRODUTO) FROM TEKNISA.PRODUTO P
                         WHERE P.CDPRODUTO = D.CDPRODUTO)
                FROM   TEKNISA.PRODFORNXML D
                WHERE  D.CDFORNECED = :f AND D.IDATIVO = 'S'
                  AND  D.CDPRODUTOXML IN (%s)
            """ % ",".join(marc), **bb)
            dp = {}
            for cx, cd, op, dt, nm in cur.fetchall():
                dp[_valor_json(cx)] = (_valor_json(cd), _valor_json(op),
                                       _valor_json(dt), _fix(_valor_json(nm)))
            for i in itens:
                d = dp.get(i["cod_forn"])
                if d:
                    i["cd_produto"], i["vinculo_quem"] = d[0], d[1]
                    i["vinculo_quando"], i["nm_produto"] = d[2], d[3]
                    i["autor"], i["autor_texto"] = _ficha_autor(d[1])
                    if not i["autor_texto"]:
                        i["autor_texto"] = "cadastro antigo, sem operador registrado"
        out["itens"] = itens
        # os criterios e parametros que governaram a decisao nesta nota
        out["criterios"] = _ficha_criterios(
            cur, v[4], v[11], r[5], sorted({i["cfop"] for i in itens
                                            if i.get("cfop")}))

        # ---- 4) a trilha: o que o sistema fez, com a logica --------------- #
        # PROTEGIDA COMO AS OUTRAS. A trilha e nossa, nao do Teknisa, e por
        # isso parecia garantida - mas em PRODUCAO nenhuma tabela AIHUB_*
        # existe ainda, e a ficha respondia HTTP 500 (ORA-00942) em vez de
        # mostrar o lado do Teknisa, que ela tem em maos. Descoberto rodando a
        # propria rota contra producao. A regra do projeto ja dizia isto:
        # falta de tabela nao pode derrubar tela.
        out["tem_trilha"] = _tem_tabela(cur, "AIHUB.AIHUB_PROC_LOG", "trilha")
        if out["tem_trilha"]:
            cur.execute("""
                SELECT L.DT_PROC, L.STATUS,
                       TO_CHAR(SUBSTR(L.DETALHE,1,1300)), L.SCORE_MATCH
                FROM   AIHUB.AIHUB_PROC_LOG L
                WHERE  L.CHAVE_NF = :c
                ORDER  BY L.DT_PROC, L.ID
            """, c=chave)
            linhas_trilha = cur.fetchall()
        else:
            linhas_trilha = []
        for dt, st, det, sc in linhas_trilha:
            quem, rotulo = _ficha_passo(st)
            texto = _fix(_valor_json(det)) or ""
            pontos = (round(float(sc) * 100) if sc is not None else None)
            out["passos"].append({
                "quando": _valor_json(dt), "quem": quem, "o_que": rotulo,
                "detalhe": texto, "status": _valor_json(st), "score": pontos,
                "fonte": "sistema",
            })
            # a logica gravada volta para o ITEM a que pertence, senao a pessoa
            # teria de casar na mao o passo com a linha do item
            if (st or "") == "DEPARA_ROBO":
                m = re.search(r'item\s+(\S+)\s+"', texto)
                alvo = _ficha_seq(m.group(1)) if m else None
                for i in itens:
                    if alvo is not None and _ficha_seq(i["seq"]) == alvo:
                        i["logica"], i["score"] = texto, pontos
                        break

        # ---- 5) a fila de revisao (a decisao humana, item a item) --------- #
        if _tem_tabela(cur, "AIHUB.AIHUB_PENDENCIA", "pendencia"):
            cur.execute("""
                SELECT P.NRSEQITPROD, P.DESC_XML, P.NM_SUGERIDO, P.SCORE,
                       P.STATUS, P.DT_CRIACAO, P.DT_RESOLUCAO, P.CD_ESCOLHIDO
                FROM   AIHUB.AIHUB_PENDENCIA P
                WHERE  P.CHAVE_NF = :c
                ORDER  BY P.DT_CRIACAO
            """, c=chave)
            for p in cur.fetchall():
                q = [_valor_json(x) for x in p]
                feito = (q[4] or "") == "RESOLVIDO"
                sug = (" | o robô sugeria %s com %s%%" % (_fix(q[2]), q[3])
                       if q[2] else "")
                out["passos"].append({
                    "quando": (q[6] or q[5]),
                    "quem": ("pessoa" if feito else "robo"),
                    "o_que": (("Uma pessoa resolveu o item %s na fila"
                               % _ficha_seq(q[0])) if feito else
                              ("Item %s ficou na fila de revisão"
                               % _ficha_seq(q[0]))),
                    "detalhe": ('"%s" -> %s%s'
                                % (_fix(q[1]) or "",
                                   q[7] or "(ainda não escolhido)", sug)),
                    "status": "FILA", "score": q[3], "fonte": "sistema",
                })

        # ---- 6) o que o proprio Teknisa registrou ------------------------- #
        if _tem_tabela(cur, "TEKNISA.HISTXMLNFE", "histxml"):
            cur.execute("""
                SELECT H.DTHISTORICO, SUBSTR(H.DSOPERACAO,1,120), H.CDOPERADOR
                FROM   TEKNISA.HISTXMLNFE H
                WHERE  H.NRACESSONFE = :c
                ORDER  BY H.DTHISTORICO
            """, c=chave)
            for dt, ds, op in cur.fetchall():
                oper = (_valor_json(op) or "").strip()
                # QUEM E O AUTOR SAI DO _ficha_autor, e nao de um teste local.
                # A regra "AUTOMATICO ou vazio = Teknisa, o resto = pessoa"
                # parecia bastar e errava justamente o nosso caso: a procedure
                # nativa grava aqui com CDOPERADOR='AIHUB_ROBO', ou seja, e o
                # robo DESTE sistema aparecendo no historico do Teknisa - e a
                # tela dizia "uma pessoa". Dois lugares decidindo a mesma coisa
                # divergem; e a terceira vez que isso aparece no projeto.
                quem, autor = _ficha_autor(oper, desconhecido="pessoa")
                out["passos"].append({
                    "quando": _valor_json(dt),
                    "quem": quem,
                    "o_que": _fix(_valor_json(ds)) or "(evento do Teknisa)",
                    "detalhe": ("registrado no histórico do Teknisa"
                                + (" · %s" % autor if autor else "")),
                    "status": "TEKNISA", "score": None, "fonte": "teknisa",
                })

        # ---- 7) apontamentos do Teknisa ----------------------------------- #
        # pelo achado 1 essa tabela e LOG: a linha nunca e limpa. Em nota nao
        # importada ela e o motivo da ULTIMA recusa, e nao prova de que ainda
        # vale - por isso a tela escreve "ultima tentativa", nunca "vai barrar"
        try:
            b3 = {}
            c3 = _cond_chave("C.NRACESSONFE", chave, b3)
            cur.execute("""
                SELECT DISTINCT C.CDCONSISTXML
                FROM   TEKNISA.INTG_INCONSISTENCIA C
                WHERE  %s AND C.IDATIVO = 'S'
            """ % c3, **b3)
            for (cod,) in cur.fetchall():
                cd = _valor_json(cod)
                # CONSIST guarda uma TUPLA (quem, titulo, acao), e todo o resto
                # do painel a desempacota. So aqui ela ia inteira para a tela,
                # que imprimiria "COMPRAS,Nota sem AF,A nota nao tem AF..."
                # colado por virgula.
                info = CONSIST_N.get(_norm_cod(cd))
                out["apontamentos"].append({
                    "codigo": cd,
                    "quem": (info[0] if info else "OUTRO"),
                    "titulo": (info[1] if info else "Apontamento do Teknisa"),
                    "acao": (info[2] if info and len(info) > 2 else "")})
        except Exception as e:
            print(" aviso ficha/apontamentos:", str(e)[:120], flush=True)

        # ---- 8) prova: a AF foi mesmo baixada? ---------------------------- #
        out["prova"] = {"baixa_af": []}
        if nf:
            try:
                cur.execute("""
                    SELECT R.NRAUTOFORN, SUM(NVL(R.QTNFRELACAF,0))
                    FROM   TEKNISA.NFRELACAF R
                    WHERE  R.CDFILIAL = :f AND R.NRLANCTONF = :n
                    GROUP  BY R.NRAUTOFORN
                """, f=nf[0], n=nf[1])
                out["prova"]["baixa_af"] = [
                    {"af": _valor_json(a), "qt": _valor_json(q)}
                    for a, q in cur.fetchall()]
            except Exception as e:
                print(" aviso ficha/NFRELACAF:", str(e)[:120], flush=True)

        # ---- 9) o placar, em uma linha ------------------------------------ #
        rb = sum(1 for i in itens if i["autor"] == "robo")
        pn = sum(1 for i in itens if i["autor"] == "pessoa")
        sem = sum(1 for i in itens if not i["cd_produto"])
        # "este sistema encostou nesta nota?" e a primeira pergunta da tela.
        # Sem responder isso, linha do tempo vazia parece defeito da tela e nao
        # o que de fato e: nota que o robo nunca tocou.
        out["tocada"] = bool(rb or pn or
                             any(p["fonte"] == "sistema" for p in out["passos"]))
        out["resumo"] = {
            "itens": len(itens), "pelo_robo": rb, "pelo_painel": pn,
            "ja_existia": len(itens) - rb - pn - sem, "sem_vinculo": sem,
            "afs": sorted({i["af"] for i in itens if i["af"]}),
            "com_logica": sum(1 for i in itens if i["logica"]),
        }
        # ordena pelo instante real; passo sem data vai para o fim, e nao some
        out["passos"].sort(key=lambda p: (p["quando"] is None,
                                          _ficha_ordem(p["quando"])))
        return jsonify(out)
    except Exception as e:
        return jsonify({"achou": False, "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


def _ficha_ordem(txt):
    """Chave de ordenacao para a data ja formatada como dd/mm/aaaa hh:mm.

    Ordenar a string crua colocaria 02/01 antes de 31/12 do ano anterior, e a
    linha do tempo contaria a historia fora de ordem - defeito que so aparece
    quando a nota atravessa a virada do ano, ou seja, tarde."""
    s = (txt or "").strip()
    try:
        d, h = (s.split(" ") + [""])[:2]
        dd, mm, aa = d.split("/")
        return "%s%s%s %s" % (aa, mm, dd, h)
    except Exception:
        return s


# Status que significam "o sistema MEXEU na nota", em oposicao a "olhou e
# desistiu". A ficha lista os primeiros; os outros so aparecem quando a pessoa
# pede para ver tudo, senao a lista do que foi resolvido vem cheia de erro e de
# recusa, que e o oposto do que ela quer conferir.
_FICHA_AGIU = ("DEPARA_ROBO", "AF_VINCULO_ROBO", "IMPORTADA", "CONFIRMADA",
               "AUDIT_VINCULO", "AUDIT_AF_VINCULO")


@app.route("/api/ficha-recentes")
def ficha_recentes():
    """As ultimas notas em que ESTE sistema mexeu, para a pessoa clicar.

    Sem isto a ficha so serviria para quem ja soubesse a chave de cor - e a
    pergunta que a tela responde ("o que o robo andou fazendo?") e justamente a
    de quem NAO sabe qual nota olhar."""
    dias = max(1, min(365, request.args.get("dias", default=30, type=int)))
    tudo = (request.args.get("tudo") or "") == "1"
    con = cur = None
    try:
        con = _conn()
        cur = con.cursor()
        # sem a trilha instalada nao ha "notas em que o sistema mexeu": devolve
        # lista vazia com explicacao, e nao um erro (mesmo motivo da ficha)
        if not _tem_tabela(cur, "AIHUB.AIHUB_PROC_LOG", "trilha"):
            return jsonify({"linhas": [], "sem_trilha": True,
                            "msg": "A trilha do sistema ainda não está "
                                   "instalada neste banco."})
        cond = ""
        if not tudo:
            cond = " AND L.STATUS IN (%s)" % ",".join(
                "'%s'" % x for x in _FICHA_AGIU)      # lista fixa do codigo,
        cur.execute("""                               -- nunca entrada externa
            SELECT L.CHAVE_NF, MAX(L.DT_PROC), COUNT(*),
                   LISTAGG(DISTINCT L.STATUS, ',') WITHIN GROUP (ORDER BY L.STATUS)
            FROM   AIHUB.AIHUB_PROC_LOG L
            WHERE  L.CHAVE_NF IS NOT NULL AND LENGTH(L.CHAVE_NF) = 44
              AND  L.DT_PROC >= SYSDATE - :d %s
            GROUP  BY L.CHAVE_NF
            ORDER  BY MAX(L.DT_PROC) DESC
            FETCH  FIRST 60 ROWS ONLY
        """ % cond, d=dias)
        linhas = []
        for ch, dt, n, sts in cur.fetchall():
            linhas.append({"chave": _valor_json(ch), "quando": _valor_json(dt),
                           "passos": int(n or 0),
                           "status": (_valor_json(sts) or "").split(","),
                           "numero": None, "fornecedor": None})
        # o numero e o fornecedor numa consulta so: 60 SELECTs individuais numa
        # tela de listagem e o tipo de coisa que so aparece lenta em producao
        if linhas:
            bb, marc = {}, []
            for k, l in enumerate(linhas):
                bb["k%d" % k] = l["chave"]
                marc.append(":k%d" % k)
            cur.execute("""
                SELECT G.NRACESSONFE, G.NRNOTAFISC, G.DTEMISSAO,
                       (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
                         WHERE F.CDFORNECED = G.CDFORNECED)
                FROM   TEKNISA.INTG_NOTAFISCAL G
                WHERE  G.NRACESSONFE IN (%s)
            """ % ",".join(marc), **bb)
            info = {}
            for ch, nr, dtem, nmf in cur.fetchall():
                info[_valor_json(ch)] = (_valor_json(nr), _valor_json(dtem),
                                         _fix(_valor_json(nmf)))
            for l in linhas:
                i = info.get(l["chave"])
                if i:
                    l["numero"], l["emissao"], l["fornecedor"] = i[0], i[1], i[2]
        return jsonify({"linhas": linhas, "dias": dias, "tudo": tudo})
    except Exception as e:
        return jsonify({"linhas": [], "msg": _erro_amigavel(e)}), 500
    finally:
        _fechar(cur, con)


if __name__ == "__main__":
    print("=" * 60)
    print(" Gestao de Recebimento de NF-e - Painel operacional")
    print(" http://localhost:5000")
    print(" Modo escrita:", "LIGADO" if MODO_ESCRITA else "desligado (so leitura)")
    print(" Motor do robo: embutido (liga/desliga e tempo pelo painel)")
    print(" Conexoes: pool reaproveitavel (carregamento rapido)")
    print("=" * 60)
    threading.Thread(target=_robo_loop, daemon=True).start()
    threading.Thread(target=_prewarm, daemon=True).start()
    threading.Thread(target=_gemini_loop, daemon=True).start()
    # Servidor: usa waitress (producao, multithread) se disponivel; senao Flask
    # com threads ligadas. Isso evita o servidor atender 1 pedido por vez.
    try:
        from waitress import serve
        print(" Servidor: waitress (producao, 12 threads)")
        print("=" * 60)
        serve(app, host="0.0.0.0", port=5000, threads=12, channel_timeout=120)
    except ImportError:
        print(" Servidor: Flask embutido (com threads). Para mais performance:")
        print("   py -m pip install waitress   e reinicie o painel.")
        print("=" * 60)
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
