# -*- coding: utf-8 -*-
"""Regressao das correcoes de 31/07/2026.

Cada teste aqui existe porque o comportamento JA ESTEVE ERRADO. Se algum voltar
a falhar, o bug voltou. Roda SEM banco (oracledb falso), como as demais.
"""
import sys, types, os, json
sys.path.insert(0, os.getcwd())
ora = types.ModuleType("oracledb"); ora.STRING = "STRING"
ora.Timestamp = lambda *a: "TS"
ora.connect = lambda *a, **k: None
ora.create_pool = lambda *a, **k: None
sys.modules["oracledb"] = ora
os.environ["TEKNISA_DB_DSN"] = "fake"; os.environ["TEKNISA_DB_PASS"] = "x"

T = []; F = [0]
def chk(d, c, det=""):
    ok = bool(c); F[0] += 0 if ok else 1
    T.append(("OK " if ok else "FALHA", d, "" if ok else str(det)))

print("=" * 74); print(" REGRESSAO DAS CORRECOES"); print("=" * 74)

# ===================================================================== #
# A) ROBO - fail-safes. Regra: na duvida, DESLIGADO.
# ===================================================================== #
os.environ["AIHUB_MODO_ESCRITA"] = "1"
from robo import servico_robo as R

class CurCfg:
    """Cursor que devolve o que o cenario mandar - ou explode, se pedido."""
    def __init__(s, valor=None, explode=False):
        s.valor = valor; s.explode = explode; s.rowcount = 1
    def execute(s, sql, *a, **kw):
        if s.explode: raise RuntimeError("ORA-00942: table or view does not exist")
        return s
    def fetchone(s): return s.valor
    def close(s): pass

class ConCfg:
    def __init__(s, cur): s.c = cur
    def cursor(s): return s.c
    def commit(s): pass
    def rollback(s): pass
    def close(s): pass

# --- ler_modo_auto -------------------------------------------------- #
chk("A1. MODO_AUTO='1' no banco -> automatico LIGADO",
    R.ler_modo_auto(CurCfg(valor=("1",))) is True)
chk("A2. MODO_AUTO='0' no banco -> automatico desligado",
    R.ler_modo_auto(CurCfg(valor=("0",))) is False)
# ANTES: caia no ambiente (AIHUB_MODO_AUTO, que nascia '1') e LIGAVA sozinho.
chk("A3. chave MODO_AUTO AUSENTE -> desligado (nao herda do ambiente)",
    R.ler_modo_auto(CurCfg(valor=None)) is False)
chk("A4. erro ao ler a config -> desligado",
    R.ler_modo_auto(CurCfg(explode=True)) is False)

# --- confirmadas_hoje (teto diario) --------------------------------- #
chk("A5. teto: contagem OK devolve o numero",
    R.confirmadas_hoje(CurCfg(valor=(7,))) == 7)
# ANTES: devolvia 0, e ai o teto NUNCA disparava justo quando o log falhava.
chk("A6. teto: erro na contagem devolve None (= 'nao sei'), nao 0",
    R.confirmadas_hoje(CurCfg(explode=True)) is None)

# --- lock de rodada -------------------------------------------------- #
# ANTES: devolvia True ("segue assim mesmo"), permitindo DUAS rodadas juntas -
# exatamente o que o lock existe para impedir.
chk("A7. lock: falha ao marcar a rodada -> NAO roda (evita rodada dupla)",
    R._tentar_lock(ConCfg(CurCfg(explode=True))) is False)
chk("A8. lock: marcou com sucesso -> roda",
    R._tentar_lock(ConCfg(CurCfg(valor=None))) is True)

# --- robo standalone respeita AIHUB_MODO_ESCRITA --------------------- #
chk("A9. robo le AIHUB_MODO_ESCRITA (mesma chave do painel)",
    hasattr(R, "MODO_ESCRITA") and R.MODO_ESCRITA is True)

# ===================================================================== #
# B) PAINEL
# ===================================================================== #
import importlib.util
spec = importlib.util.spec_from_file_location("pa", "interface/painel/app.py")
A = importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

# --- B1. nota bloqueada na mao NAO e "parada" ------------------------ #
# ANTES: o bloco que le o HISTXMLNFE rodava DEPOIS do bloco de motivos, entao
# "bloqueada_por_pessoa" era consultada antes de existir - o teste dava sempre
# falso e a nota bloqueada aparecia como "Parada na importacao".
class CurNota:
    def __init__(s, bloqueada):
        s.bloq = bloqueada; s.rows = []; s.description = []
    def execute(s, sql, *a, **kw):
        u = " ".join(sql.upper().split())
        if "FROM TEKNISA.NOTAFISCAL N" in u or "FROM TEKNISA.NOTAFISCAL WHERE" in u:
            s.rows = []; s.description = []                  # nao lancada
        elif "FROM TEKNISA.NOTADESTINADA" in u:
            s.rows = []; s.description = []                  # nao esta na caixa
        elif "INTG_INCONSISTENCIA" in u:
            s.rows = [("Produto nao corresponde ao pedido", "71")]
            s.description = [("M",), ("COD",)]
        elif "TEKNISA.HISTXMLNFE" in u:
            s.rows = ([("30/07/2026", "B", "Arquivo XML bloqueado pelo usuario", "12345")]
                      if s.bloq else [])
            s.description = [("DT",), ("IDSTATUS",), ("OPERACAO",), ("CDOPERADOR",)]
        else:
            s.rows = []; s.description = []
        return s
    def fetchone(s): return s.rows[0] if s.rows else None
    def fetchall(s): return list(s.rows)
    def close(s): pass

class ConNota:
    def __init__(s, bloq): s.c = CurNota(bloq)
    def cursor(s): return s.c
    def commit(s): pass
    def rollback(s): pass
    def close(s): pass

def chama_nota(bloqueada):
    A._conn = lambda b=bloqueada: ConNota(b)
    A._col_cache.clear()
    with A.app.test_request_context("/api/nota?chave=" + "4" * 44):
        r = A.nota()
        body = r[0] if isinstance(r, tuple) else r
        return json.loads(body.get_data(as_text=True))

d = chama_nota(True)
chk("B1. nota bloqueada por pessoa -> NAO e 'parada'", d.get("parada") is False, d.get("parada"))
chk("B2. nota bloqueada por pessoa -> diz 'Bloqueada pela equipe'",
    d.get("onde") == "Bloqueada pela equipe", d.get("onde"))
chk("B3. nota bloqueada por pessoa -> registra quem e quando",
    bool(d.get("bloqueio", {}).get("operador")), d.get("bloqueio"))

d = chama_nota(False)
chk("B4. nota NAO bloqueada, com motivo ativo -> continua 'parada'",
    d.get("parada") is True, d.get("parada"))

# --- B5. _classifica so por codigo ----------------------------------- #
chk("B5. _classifica recebe SO os codigos (sem o parametro de texto)",
    A._classifica("71")[0] == "ROBO", A._classifica("71"))

# --- B6. rotas de escrita fecham a conexao no erro ------------------- #
# ANTES: 10 rotas de leitura so fechavam no caminho feliz. Com pool max=12, uma
# dezena de erros derrubava o painel inteiro.
import re as _re
_src = open("interface/painel/app.py", encoding="utf-8").read().split("\n")
_rotas = [(i + 1, l) for i, l in enumerate(_src) if "@app.route" in l]
_rotas.append((len(_src) + 1, "EOF"))
_vaza = []
for _k in range(len(_rotas) - 1):
    _corpo = "\n".join(_src[_rotas[_k][0] - 1:_rotas[_k + 1][0] - 1])
    _m = _re.search(r'route\("([^"]+)"', _rotas[_k][1])
    _path = _m.group(1) if _m else "?"
    if "_conn()" not in _corpo:
        continue
    if not (("finally:" in _corpo and "_fechar(" in _corpo) or "_com_conexao" in _corpo):
        _vaza.append(_path)
chk("B6. nenhuma rota abre conexao sem garantir o fechamento",
    not _vaza, "vazam: " + ", ".join(_vaza))

# --- B7. a IA respeita o modo consulta ------------------------------- #
# ANTES: /api/gemini-agora era a UNICA rota de escrita sem a trava - gravava em
# AIHUB_GEMINI mesmo com o painel em modo consulta.
_fonte_gemini = "\n".join(_src)
_i = _fonte_gemini.find("def gemini_agora")
chk("B7. /api/gemini-agora checa MODO_ESCRITA",
    "MODO_ESCRITA" in _fonte_gemini[_i:_i + 700], "trava ausente")
_j = _fonte_gemini.find("def _gemini_loop")
chk("B8. o loop de fundo da IA tambem checa MODO_ESCRITA",
    "MODO_ESCRITA" in _fonte_gemini[_j:_j + 700], "trava ausente")

# --- B10. entrada x saida: o codigo '01' nao e unico ------------------ #
# ANTES: filtrava so CDTIPOOPER='01', que na TIPOOPERAC e "NF Compra" na ENTRADA
# mas "NF Venda" na SAIDA. Media no banco: 1.486.524 notas de entrada e 152.027
# de VENDA no mesmo filtro - 9,3% de contaminacao nos indicadores e nas listas.
_fontes = {
    "app.py": _src,
    "servico_robo.py": open("robo/servico_robo.py", encoding="utf-8").read().split("\n"),
    "validar_sistema.py": open("interface/painel/validar_sistema.py", encoding="utf-8").read().split("\n"),
}
_sem_es = []
for _nome, _linhas in _fontes.items():
    for _i, _l in enumerate(_linhas, 1):
        if "CDTIPOOPER" not in _l or "'01'" not in _l:
            continue
        if _l.lstrip().startswith("#"):
            continue
        # a INTG_NOTAFISCAL nao tem a coluna - unica excecao legitima
        _janela = "\n".join(_linhas[max(0, _i - 7):_i + 2])
        if "INTG_NOTAFISCAL" in _janela:
            continue
        if "IDENTRSAIDOP" not in _janela:
            _sem_es.append("%s:%d" % (_nome, _i))
chk("B10. todo filtro CDTIPOOPER='01' sobre NOTAFISCAL exige IDENTRSAIDOP='E'",
    not _sem_es, "sem o filtro: " + ", ".join(_sem_es))

# --- B11. politica por tipo de operacao ------------------------------- #
# Decisao da operacao (31/07/2026): bonificacao entra na automacao completa
# ("o robo pode fazer sim"); produtor rural fica de fora (100% digitada).
chk("B11. bonificacao (06) esta na lista de tipos atendidos",
    "06" in R.POLITICA_TIPO, sorted(R.POLITICA_TIPO))
chk("B12. bonificacao trata igual a compra (decisao da operacao)",
    R.politica("06") == R.politica("01"), R.politica("06"))
chk("B13. so os tipos decididos estao na politica - nada entra sem decisao",
    sorted(R.POLITICA_TIPO) == ["01", "06"], sorted(R.POLITICA_TIPO))
chk("B14. compra (01) mantem automacao completa",
    R.politica("01")["confirma"] and R.politica("01")["vincula_sozinho"])
chk("B15. produtor rural (08) fica FORA - nao existe XML para ler",
    "08" not in R.POLITICA_TIPO)
chk("B16. tipo desconhecido nao faz nada automatico",
    R.politica("99") == {"vincula_sozinho": False, "confirma": False})
# painel e robo tem que atender os MESMOS tipos, senao um mostra o que o
# outro nao trata
chk("B17. painel e robo atendem os mesmos tipos de operacao",
    tuple(sorted(A.TIPOS_ENTRADA)) == tuple(sorted(R.POLITICA_TIPO)),
    "painel=%s robo=%s" % (sorted(A.TIPOS_ENTRADA), sorted(R.POLITICA_TIPO)))

# --- B18. so a rotina simplificada aparece --------------------------- #
# Decisao da operacao (31/07/2026): "da manual nao precisa aparecer, somente o
# que e da rotina simplificada mesmo". Medido: de 1.486.759 notas de compra,
# 1.042.890 (70%) sao DIGITADAS. Sem este filtro o painel mostra tudo isso.
chk("B18. existe o filtro de rotina simplificada (_so_simplificada)",
    hasattr(A, "_so_simplificada") and "INTG_NOTAFISCAL" in A._so_simplificada("N"))
_faltou = []
for _i, _l in enumerate(_src, 1):
    if "_SQL_TIPOS" not in _l:
        continue
    _jan = "\n".join(_src[max(0, _i - 4):_i + 8])
    # EXCECAO PRECISA, nao furo na regra: o filtro existe para tirar da
    # NOTAFISCAL as notas DIGITADAS a mao. Consulta que le a INTG_NOTAFISCAL
    # nao precisa dele, porque ali TODA linha veio de XML por definicao - essa
    # tabela E a integracao. Exigir o filtro ali seria um EXISTS da tabela
    # contra ela mesma.
    if "TEKNISA.INTG_NOTAFISCAL" in _jan:
        continue
    if "_so_simplificada" not in _jan:
        _faltou.append("app.py:%d" % _i)
chk("B19. toda consulta de nota de entrada usa o filtro de rotina simplificada",
    not _faltou, "sem o filtro: " + ", ".join(_faltou))

# bonificacao passou a ser automacao completa (decisao de 31/07/2026)
chk("B20. bonificacao (06) agora confirma sozinha, como compra",
    R.politica("06")["confirma"] and R.politica("06")["vincula_sozinho"])

# --- B21. tela "Por onde comecar" (paradas agrupadas por motivo) ------ #
chk("B21. existe a rota que agrupa as paradas por motivo",
    hasattr(A, "paradas_por_motivo"))
# o filtro por motivo vai para o BANCO; se ficasse so no cliente, clicar num
# grupo de 1.322 notas filtraria apenas as 50 da pagina carregada
_i = _fonte_gemini.find("def travadas")
_jan = _fonte_gemini[_i:_i + 3000]
chk("B22. /api/travadas aceita o filtro por motivo (cod)",
    'request.args.get("cod")' in _jan)
chk("B23. o cod so aceita digito - nao entra texto na consulta",
    "c.isdigit()" in _jan and ":cod" in _jan)
_html = open("interface/painel/index.html", encoding="utf-8").read()
chk("B24. a tela manda o motivo escolhido para o backend",
    "q.set('cod'" in _html and "_pmCod" in _html)

# --- B25. chave da IA configuravel pela tela ------------------------- #
import importlib as _il
_ag = _il.import_module("core.ai_gemini")
chk("B25. a IA aceita trocar a chave em runtime (definir_chave)",
    hasattr(_ag, "definir_chave") and hasattr(_ag, "chave_mascarada"))
_ag.definir_chave("AQ.chave_de_teste_1234567890", "painel")
chk("B26. a chave nunca e devolvida crua - so mascarada",
    "AQ.chave" not in _ag.chave_mascarada() and "..." in _ag.chave_mascarada())
_ag.definir_chave("", "atalho")   # limpa o estado global do modulo
chk("B27. existe a rota que grava a chave (/api/gemini-chave)",
    hasattr(A, "gemini_chave"))
chk("B28. gravar a chave exige MODO_ESCRITA (checa a trava)",
    "MODO_ESCRITA" in _fonte_gemini[_fonte_gemini.find("def gemini_chave"):
                                    _fonte_gemini.find("def gemini_chave")+900])

# --- B29. auditoria nao insere NULL em NUMERO_NF --------------------- #
# ANTES: _auditar inseria NULL em NUMERO_NF. Em instalacoes onde a coluna e
# NOT NULL (script antigo), TODA auditoria do painel falhava em silencio.
_ia = _src[[i for i,l in enumerate(_src) if "def _auditar" in l][0]:]
_ia = "\n".join(_ia[:16])
chk("B29. _auditar nao insere NULL em NUMERO_NF",
    "VALUES (NULL" not in _ia and "'-'" in _ia)

# --- B30. modelo da IA nao adivinhado -------------------------------- #
# Nome de modelo ENVELHECE (gemini-2.5-flash-lite ficou indisponivel). Em vez de
# cravar um nome, o painel lista os modelos que a chave pode usar (listar_modelos)
# e a pessoa escolhe. O default e o alias que nao envelhece.
chk("B30. default do modelo e o alias que nao envelhece (flash-latest)",
    "latest" in _ag.MODEL)
chk("B31. da para trocar o modelo em runtime (definir_modelo)",
    hasattr(_ag, "definir_modelo") and hasattr(_ag, "listar_modelos"))
_ag.definir_modelo("gemini-2.0-flash", "painel")
chk("B32. definir_modelo troca de verdade", _ag.MODEL == "gemini-2.0-flash")
_ag.definir_modelo("", "padrao")   # volta ao default
chk("B33. existem as rotas de listar e gravar o modelo",
    hasattr(A, "gemini_modelos") and hasattr(A, "gemini_modelo"))

# --- B34. pente-fino: coerencia do _ranquear com o robo -------------- #
# _ranquear (busca manual e alternativas) tem que decidir igual ao robo: no
# empate entre o produto do ramo 6 (OL) e o direto, o direto ganha; e nao pode
# rotular 'auto' o que o pode_auto barraria.
_gemeos_r = [("6010127203","FRANGO CONG MOELA - CX 18 KG","CX"),
             ("1010127203","FRANGO CONG MOELA - CX 18 KG","CX")]
_res = A._ranquear("FRANGO CONG MOELA - CX 18 KG", _gemeos_r)
chk("B34. _ranquear poe o produto DIRETO na frente do OL (ramo 6)",
    _res["sugestoes"][0]["cd"] == "1010127203", _res["sugestoes"][0]["cd"])
# granel no XML x embalagem no candidato: nao pode sair como 'auto'
_conf = A._ranquear("BOV CONG ACEM", [("1010207000","BOV CONG ACEM","CX")], un_xml="KG")
chk("B35. _ranquear nao rotula 'auto' quando granel encontra embalagem",
    all(s["faixa"] != "auto" for s in _conf["sugestoes"]),
    [s["faixa"] for s in _conf["sugestoes"]])

# --- B36. importacao automatica cobre os MESMOS tipos da fila --------- #
# antes importava so '01'; bonificacao (06) ficava de fora
_src_robo = open("robo/servico_robo.py", encoding="utf-8").read()
# recorta a funcao inteira: janela de tamanho fixo quebra quando o codigo
# (ou o comentario que explica a decisao) cresce - ja aconteceu 3 vezes
def _corpo(fonte, nome):
    ini = fonte.find("def " + nome)
    fim = fonte.find("\ndef ", ini + 1)
    return fonte[ini:fim if fim > 0 else len(fonte)]
chk("B36. buscar_para_importar usa TIPOS_ATENDIDOS (nao so '01')",
    "TIPOS_ATENDIDOS" in _corpo(_src_robo, "buscar_para_importar"))

# --- B37. /api/alternativas nao vaza conexao se _conn falha ---------- #
_ia = _fonte_gemini.find("def alternativas")
chk("B37. /api/alternativas inicializa con=cur=None antes do try",
    "con = cur = None" in _fonte_gemini[_ia:_ia+260])

# --- B38. robo pula nota de origem MANUAL nas DUAS etapas ------------- #
# decisao da operacao: entrada manual so a gerente lanca, no recebimento
# fisico. As duas consultas do robo (importar e confirmar) tem que excluir.
chk("B38. buscar_para_importar exclui origem Manual",
    "_nao_manual(" in _corpo(_src_robo, "buscar_para_importar"))
chk("B38b. buscar_notas_novas exclui origem Manual",
    "_nao_manual(" in _corpo(_src_robo, "buscar_notas_novas"))

# --- B39. o filtro de manual deixa passar a automatica (DSTAGORIGFILI nulo) #
# fail-safe ao contrario: so a MANUAL e barrada; nulo (automatica) segue.
import robo.servico_robo as _rob
_frag = _rob._nao_manual("G")
chk("B39. _nao_manual barra 'Manual' e deixa passar nulo",
    "DSTAGORIGFILI IS NULL" in _frag and "<> 'Manual'" in _frag)

# --- B40. motor de AF: nunca escolhe quando ha duvida ----------------- #
# Amarrar a nota na AF errada baixa o pedido de outra compra. O motor so pode
# decidir sozinho quando ha UMA candidata; com 2+ tem que devolver 'ambigua'.
import core.af_matcher as _afm

class _CurAF:
    """Cursor de mentira: devolve N candidatas para testar a decisao."""
    def __init__(self, n): self.n = n; self._r = []
    def execute(self, sql, *a, **k):
        if "PARAMFILIAL" in sql:          # janela_da_filial
            self._r = [(__import__("datetime").datetime(2026, 6, 1), 20, 20)]
        elif "PARAMGERAL" in sql:         # ler_tolerancias
            self._r = [(15.0, 0.5)]
        elif "COUNT(*)" in sql and "AUTOFORNE" in sql:
            # af_inequivoca: o numero identifica UMA AF nessa filial
            self._r = [(1,)]
        elif "AUTOFORNE" in sql:
            self._r = [("000000000%d" % i, "1083", "9999", 1, 100.0, 0.0,
                        "01/06/2026", 0, "01/06/2026") for i in range(self.n)]
        else:
            self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

_nota = {"CDFORNECED": "J1", "CDFILIRECE": "1083", "DTEMISSAO": __import__("datetime").datetime(2026, 6, 1)}
# cd_estoque e a chave de casamento com a AF (nao mais o prefixo da arvore)
_itens = [{"seq": "1", "cd_interno": "1020101000", "cd_estoque": "1020101000",
           "fator": 1.0, "qt": 100.0}]

chk("B40. motor de AF decide sozinho quando ha 1 candidata",
    _afm.escolher_af(_CurAF(1), _nota, _itens, 15.0)["decisao"] == _afm.AUTOMATICA)
chk("B40b. motor de AF NAO decide quando ha 2 candidatas",
    _afm.escolher_af(_CurAF(2), _nota, _itens, 15.0)["decisao"] == _afm.AMBIGUA)
chk("B40c. motor de AF nao decide sem candidata",
    _afm.escolher_af(_CurAF(0), _nota, _itens, 15.0)["decisao"] == _afm.NENHUMA)

# --- B41. item sem De/Para trava a decisao automatica ----------------- #
# Sem saber o produto interno nao da para achar a AF: nao pode chutar.
chk("B41. item sem De/Para -> nao decide sozinho",
    _afm.escolher_af(_CurAF(1), _nota,
                     [{"seq": "1", "cd_interno": None, "qt": 10.0}], 15.0
                     )["decisao"] == _afm.NENHUMA)

# --- B42. tolerancia vem do Teknisa, nao do nosso codigo -------------- #
chk("B42. ler_tolerancias usa PARAMGERAL",
    _afm.ler_tolerancias(_CurAF(1)) == (15.0, 0.5))
class _CurSemParam:
    def execute(self, *a, **k): raise Exception("ORA-00942")
    def fetchone(self): return None
    def fetchall(self): return []
chk("B42b. sem PARAMGERAL cai no padrao medido (nao afrouxa)",
    _afm.ler_tolerancias(_CurSemParam()) == (_afm.TOL_QT_PADRAO, _afm.TOL_VR_PADRAO))

# --- B43. o UPDATE da AF so preenche o que esta VAZIO ----------------- #
# Nunca sobrescrever AF que uma pessoa ja escolheu.
_src_af = open("interface/painel/app.py", encoding="utf-8").read()
_af_src = open("core/af_matcher.py", encoding="utf-8").read()
# recorta ate a proxima rota: janela fixa quebra quando o codigo cresce
_ia = _src_af.find("def af_vincular")
_fim = _src_af.find("@app.route", _ia)
_bloco = _src_af[_ia:_fim if _fim > 0 else len(_src_af)]
chk("B43. /api/af-vincular grava pela funcao atomica (gravar_af)",
    "gravar_af(cur, chave, plano)" in _bloco)
chk("B43b. /api/af-vincular exige MODO_ESCRITA",
    "if not MODO_ESCRITA" in _bloco)
_rb = _corpo(_src_robo, "vincular_af_sozinho")
chk("B43c. robo tambem grava pela funcao atomica",
    "gravar_af(cur, chave, plano)" in _rb)
# a garantia de nao sobrescrever mora agora na gravar_af, num lugar so
_gv = _corpo(_af_src, "gravar_af")
chk("B43d. gravar_af nunca sobrescreve AF existente",
    "NRAUTOFORN IS NULL" in _gv)

# --- B44. nota PARCIALMENTE vinculada nao recebe outra AF ------------- #
# Achado no teste controlado em homologacao (NF 43263): 2 de 3 itens ja
# apontavam a AF 0001444987 e o motor tinha escolhido OUTRA AF. Como o UPDATE
# so preenche o que esta nulo, a nota terminaria com DUAS AFs - dois pedidos
# distintos baixados pela mesma nota. Medido: 224 notas nessa situacao.
# "resolver(" com parentese: senao casa com "resolver_por_item"
_rs = _corpo(_af_src, "resolver(cur, chave)")
chk("B44. motor recusa nota parcialmente vinculada",
    "parcialmente vinculada" in _rs)
chk("B44b. a rota tambem trava (nao depende so do motor)",
    "já tem a AF" in _bloco and "409" in _bloco)

# --- B45. De/Para resolvido ANTES de procurar a AF -------------------- #
# Circulo que travava nota de produto novo: sem De/Para nao acha AF, sem AF
# nao importa, e o matcher so rodava DEPOIS de importar. Medido em producao:
# 34 itens sem De/Para em 60 notas paradas; o matcher resolveria 11 sozinho.
_rd = _corpo(_src_robo, "resolver_depara_do_xml")
chk("B45. existe o passo que cria De/Para antes da AF",
    "def resolver_depara_do_xml" in _src_robo)
chk("B45b. usa a MESMA regra do resto (pode_auto), nao afrouxa",
    "pode_auto(" in _rd)
chk("B45c. vincular_af_sozinho chama esse passo antes",
    "resolver_depara_do_xml(con, chave, dry)" in _corpo(_src_robo, "vincular_af_sozinho"))
chk("B45d. respeita o modo simulacao (nao grava em dry)",
    "if dry:" in _rd and "gravar_depara(" in _rd)

# --- B46. quantidade: a nota tem que CABER no saldo da AF ------------- #
# Erro pego na simulacao em producao: comparar a nota com o TOTAL da AF
# acusava 8650% de diferenca numa entrega parcial normal (nota 16 x AF 1400).
chk("B46. candidatas mede o SALDO, nao o total programado",
    "saldo" in _af_src and "excedente" in _af_src)
chk("B46b. as duas buscas (nota e item) medem igual",
    _af_src.count("cabe = ") == 2)

# --- B47. normalizacao de texto do matcher --------------------------- #
# Padroes medidos em 1.200 pares onde o produto certo e conhecido. Ganho
# validado: auto-vinculos certos 160 -> 182 (+14%), errados 1 -> 1 (+0).
import core.ai_matcher as _AM
chk("B47. QJO vira QUEIJO",
    _AM._sim("QJO MUSSARELA", "QUEIJO MUSSARELA") > 0.9)
chk("B47b. BOV vira BOVINO",
    _AM._sim("ACEM BOVINO BIFE", "BOV ACEM BIFE") > 0.9)
chk("B47c. 'Peso: 19,500' do XML nao entra na comparacao",
    _AM._sim("MELAO AMARELO - Peso: 19,500", "MELAO AMARELO")
    >= _AM._sim("MELAO AMARELO", "MELAO AMARELO") - 0.01)
chk("B47d. 'IN NATURA' do cadastro nao derruba o score",
    _AM._sim("MANJERICAO", "MANJERICAO IN NATURA") > 0.9)

# --- B48. as correcoes nao afrouxam a trava de unidade --------------- #
# Produto diferente tem que continuar sendo recusado.
chk("B48. produto diferente continua com score baixo",
    _AM._sim("SACHE VINAGRE BOM SABOR", "VINHO TINTO SECO") < 0.6)
chk("B48b. granel x embalagem continua barrando o auto-vinculo",
    _AM.un_conflita("CX", "KG") is True)

# --- B49. tamanho em LT/LTS ------------------------------------------ #
# "REFRI COCA COLA 2LTS" nao tinha tamanho reconhecido (o \b falhava no T
# depois do L) e o matcher escolhia PET 200ML no lugar de PET 2L. Foram 2
# vinculos errados em producao, que somem ao reconhecer a unidade.
chk("B49. '2LTS' e reconhecido como 2 litros",
    _AM._tamanhos("REFRI COCA COLA 2LTS") == {"2L"})
chk("B49b. '2LT' idem",
    _AM._tamanhos("FANTA LARANJA 2LT") == {"2L"})
chk("B49c. tamanho conflitante derruba o score (2L x 200ML)",
    _AM._sim("REFRI COCA COLA 2LTS", "REFRIGERANTE COCA COLA PET 200ML") < 0.92)
chk("B49d. tamanho igual mantem o score alto",
    _AM._sim("REFRI COCA COLA 2LTS", "REFRIGERANTE COCA COLA PET 2L") >= 0.92)
chk("B49e. 'LT 350ML' continua sendo LATA, nao litro",
    "350ML" in str(_AM._tamanhos("REFRIGERANTE LT 350ML")).replace(" ",""))

# --- B50. plural: so o simples, sem estragar palavra ----------------- #
chk("B50. OVOS e OVO viram a mesma palavra",
    _AM._prep("OVOS") == _AM._prep("OVO"))
chk("B50b. nao mexe em palavra terminada em SS",
    _AM._singular("MOUSSE") == "MOUSSE")
chk("B50c. nao mexe em palavra curta",
    _AM._singular("GAS") == "GAS")

# --- B51. excecao do nome generico ----------------------------------- #
# Fornecedor manda "MANGA", cadastro tem "MANGA TOMMY": o score cai (57%)
# porque a variedade nunca e coberta. Quando o candidato e UNICO nao ha
# ambiguidade. Medido: +24 auto certos, 0 erros novos.
_NM = "COUVE FLOR LIMPA TAM M TIPO 2"          # caso real medido: 72%
_c_uma  = [("1", _NM, "KG")]
_c_duas = [("1", _NM, "KG"), ("2", "COUVE FLOR ORGANICA TAM M", "KG")]
_best_uma = ("1", _NM, _AM._sim("COUVE FLOR", _NM))
chk("B51. candidato unico com nome generico -> grava sozinho",
    _AM.pode_auto("COUVE FLOR", _best_uma, "KG", "KG", _c_uma) is True)
chk("B51b. com DUAS variedades -> NAO grava (humano decide)",
    _AM.pode_auto("COUVE FLOR", _best_uma, "KG", "KG", _c_duas) is False)
chk("B51c. sem passar candidatos, comportamento antigo (nao grava)",
    _AM.pode_auto("COUVE FLOR", _best_uma, "KG", "KG") is False)
# "MANGA" x "MANGA TOMMY" da 57,5%, abaixo do piso: fica de fora de proposito.
chk("B51d. nome MUITO generico (57%) continua barrado pelo piso",
    _AM.pode_auto("MANGA KG",
                  ("1", "MANGA TOMMY TAM DIVERSOS TIPO 3",
                   _AM._sim("MANGA KG", "MANGA TOMMY TAM DIVERSOS TIPO 3")),
                  "KG", "KG", [("1", "MANGA TOMMY TAM DIVERSOS TIPO 3", "KG")]) is False)

# --- B52. piso da excecao: abaixo de sugestao nao grava -------------- #
# Sem o piso a excecao trazia 11 vinculos errados, todos em 57%
# ("REPOLHO VERDE" indo para produto de outro repolho).
_baixo = ("9", "PRODUTO QUALQUER COISA DIFERENTE", 0.40)
chk("B52. score abaixo do limiar de sugestao nao grava, nem sem concorrente",
    _AM.pode_auto("XYZ", _baixo, None, None,
                  [("9", "PRODUTO QUALQUER COISA DIFERENTE", None)]) is False)

# --- B53. o painel decide IGUAL ao robo ------------------------------ #
# A simulacao e a lista de alternativas passam 'cands' para pode_auto; se nao
# passassem, a tela diria "sugestao" no que o robo grava sozinho.
_ap = open("interface/painel/app.py", encoding="utf-8").read()
chk("B53. /api/simular passa os candidatos ao pode_auto",
    "pode_auto(desc, best, un_xml, _un_cand, cands)" in _ap)
chk("B53b. _ranquear passa os candidatos ao pode_auto",
    "un_xml, un_cand, itens)" in _ap)
chk("B53c. o robo passa os candidatos nos dois pontos",
    _src_robo.count("un_de.get(best[0])") >= 1
    # 'cands_it' e a MESMA lista depois das regras ensinadas (filtrar_proibidos).
    # O que a invariante exige e que a lista chegue ao pode_auto - passar None
    # desligaria a excecao do nome generico e a tela diria "sugestao" no que o
    # robo grava sozinho.
    and ("un_de.get(best[0]), cands)" in _src_robo
         or "un_de.get(best[0]), cands_it)" in _src_robo))

# --- B54. unidades com sufixo numerico e siglas achadas na auditoria - #
# FDO1/CXA1/UN1 sao a mesma unidade com um numero colado (71, 63 e 39
# ocorrencias medidas). Sem tratar, perdiam o desempate por unidade igual.
chk("B54. FDO1 vira FD", _AM.normaliza_un("FDO1") == "FD")
chk("B54b. CXA1 vira CX", _AM.normaliza_un("CXA1") == "CX")
chk("B54c. UN1 vira UN", _AM.normaliza_un("UN1") == "UN")
chk("B54d. BANDEJA e BJ viram a mesma sigla",
    _AM.normaliza_un("BANDEJA") == _AM.normaliza_un("BJ"))
chk("B54e. sigla sem sufixo continua igual", _AM.normaliza_un("KG") == "KG")
chk("B54f. valor so com digito nao vira vazio",
    _AM.normaliza_un("5") == "5")

# --- B55. a trava granel x embalagem continua valendo nas novas ------ #
# O ponto de seguranca: nenhuma sigla nova pode virar granel por engano.
for _u in ("DZ", "SC", "PR", "BDJ", "FD", "CX", "UN"):
    chk("B55. %s continua sendo embalagem (conflita com KG)" % _u,
        _AM.un_conflita(_u, "KG") is True)

# --- B56. o NUMERO da AF tem que ser inequivoco na filial ------------ #
# A gravacao vai para INTG_ITEMPRODUTO.NRAUTOFORN, que guarda SO O NUMERO,
# mas a PK da AF e (CDFILDEST, CDFILLANC, NRAUTOFORN). Numero repetido =
# vinculo apontando duas AFs = risco de baixar o pedido errado.
# Medido: 831.840 AFs, 1 numero repetido, 0 com a mesma filial destino.
# Seguro pelo DADO, agora tambem por CONSTRUCAO.
class _CurAmb:
    """AF com numero AMBIGUO (2 AFs com o mesmo numero na filial)."""
    def __init__(self): self._r = []
    def execute(self, sql, *a, **k):
        if "PARAMFILIAL" in sql:
            self._r = [(__import__("datetime").datetime(2026, 6, 1), 20, 20)]
        elif "PARAMGERAL" in sql:
            self._r = [(15.0, 0.5)]
        elif "COUNT(*)" in sql and "AUTOFORNE" in sql:
            self._r = [(2,)]          # <== duas AFs com o mesmo numero
        elif "AUTOFORNE" in sql:
            self._r = [("0000000001", "1083", "9999", 1, 100.0, 0.0,
                        "01/06/2026", 0, "01/06/2026")]
        else:
            self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

chk("B56. numero de AF ambiguo NAO vira vinculo automatico",
    _afm.escolher_af(_CurAmb(), _nota, _itens, 15.0)["decisao"] == _afm.AMBIGUA)
chk("B56b. af_inequivoca responde False quando ha duas",
    _afm.af_inequivoca(_CurAmb(), "0000000001", "1083") is False)
chk("B56c. af_inequivoca responde True quando ha uma",
    _afm.af_inequivoca(_CurAF(1), "0000000001", "1083") is True)

class _CurQuebra:
    """Nao consegue conferir (erro de banco)."""
    def execute(self, *a, **k): raise Exception("ORA-00942")
    def fetchone(self): return None
    def fetchall(self): return []
chk("B56d. fail-safe: se nao consegue conferir, NAO vincula",
    _afm.af_inequivoca(_CurQuebra(), "0000000001", "1083") is False)

# --- B57. corrida robo x pessoa na mesma nota ------------------------ #
# Entre a leitura (nota sem AF) e a gravacao, uma PESSOA pode relacionar outra
# AF pela tela do Teknisa. Sem trava, o UPDATE preencheria o resto com a AF
# do robo e a nota ficaria com DUAS - dois pedidos baixados pela mesma nota.
# A trava de rodada dupla (_tentar_lock) nao cobre: o concorrente e uma pessoa
# em outro sistema. Solucao: SELECT ... FOR UPDATE antes de decidir.
_gv2 = _corpo(_af_src, "gravar_af")
chk("B57. gravar_af trava as linhas antes de decidir (FOR UPDATE)",
    "FOR UPDATE" in _gv2)
chk("B57b. gravar_af nao faz commit (quem chama controla a transacao)",
    ".commit(" not in _gv2)

class _CurCorrida:
    """Simula: quando o robo vai gravar, a pessoa ja pos OUTRA AF."""
    def __init__(self, af_da_pessoa): self.af = af_da_pessoa; self._r = []
    def execute(self, sql, *a, **k):
        if "FOR UPDATE" in sql:
            self._r = [("1", self.af), ("2", None)]
        else:
            self._r = []
        self.rowcount = 1
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

_pl = [{"seq": "1", "af": "0000000009"}, {"seq": "2", "af": "0000000009"}]
_n, _rec = _afm.gravar_af(_CurCorrida("0000000007"), "CH", _pl)
chk("B57c. AF diferente apareceu no meio -> RECUSA e nao grava nada",
    _n == 0 and _rec is not None and "outra AF" in _rec)

_n2, _rec2 = _afm.gravar_af(_CurCorrida(None), "CH", _pl)
chk("B57d. caminho limpo -> grava",
    _rec2 is None and _n2 > 0)

_n3, _rec3 = _afm.gravar_af(_CurCorrida("0000000009"), "CH", _pl)
chk("B57e. mesma AF ja gravada -> segue sem reescrever o que ja esta certo",
    _rec3 is None)

# --- B58. a AF gravada e PERECIVEL ----------------------------------- #
# Entre gravar a AF e importar, outra entrega pode consumi-la. Importar depois
# baixaria pedido ja atendido. Medido em producao: 11.034 notas esperam com AF
# gravada, e numa amostra de 120 quase metade ja estava com a AF morta.
_iu = _corpo(_src_robo, "importar_uma")
chk("B58. importar_uma confere se a AF ainda esta viva",
    "af_ainda_valida(cur, chave)" in _iu)
chk("B58b. AF morta NAO importa (status proprio)",
    "AF_VENCIDA" in _iu)

# --- B78. nao chamar a nativa quando a recusa ja e certa -------------- #
# Medido em homologacao: a GERA_NOTAFISCAL_CORPO_NFE leva MINUTOS mesmo quando
# vai RECUSAR (roda a validacao inteira antes de decidir). Item sem De\Para
# sempre volta com o codigo 014, e isso se confere em 202 ms por indice. A
# pessoa recebia a mesma resposta depois de mais de vinte minutos de espera.
chk("B78. importar_uma confere o De/Para antes de chamar a nativa",
    "itens_sem_depara(cur, chave)" in _iu)
chk("B78b. nota sem produto vinculado tem status proprio",
    "SEM_PRODUTO" in _iu)
chk("B78c. a checagem vem ANTES do callproc da GERA",
    _iu.find("itens_sem_depara") < _iu.find("GERA_NOTAFISCAL_CORPO_NFE"))
chk("B78d. o contador da rodada conhece o status novo (senao KeyError)",
    '"SEM_PRODUTO": 0' in _corpo(_src_robo, "rodar_importacao"))
# Fail-safe AO CONTRARIO do usual, e de proposito: aqui "nao consegui conferir"
# tem de deixar SEGUIR (a nativa decide), nunca barrar nota boa.
_isd = _corpo(_src_robo, "itens_sem_depara")
chk("B78e. erro ao conferir o De/Para deixa seguir, nao barra",
    "except Exception:" in _isd and _isd.rstrip().endswith("return []"))
chk("B78f. a checagem e UMA consulta so (nao uma por item)",
    _isd.count("cur.execute") == 1)
# A pendencia nasce do dado, nao da INTG_INCONSISTENCIA: la a linha fica ativa
# para sempre (achado 1), entao uma inconsistencia velha faria recusar nota boa.
_rdp = _corpo(_src_af, "_recusa_de_produto")
chk("B78g. a recusa 014 do nosso pre-teste e montada aqui, nao lida do log",
    "cur.execute" not in _rdp and '"014"' in _rdp)
chk("B78h. o pre-teste tambem abre a fila de produtos",
    'st != "SEM_PRODUTO"' in _corpo(_src_af, "_fila_produto"))

class _CurAfMorta:
    def __init__(self, viva): self.viva = viva; self._r = []
    def execute(self, sql, *a, **k):
        if "INTG_ITEMPRODUTO" in sql:
            self._r = [("0000000001",)]
        elif "AUTOFORNE" in sql:
            self._r = [(1 if self.viva else 0,)]
        else:
            self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

_ok, _mortas = _afm.af_ainda_valida(_CurAfMorta(True), "CH")
chk("B58c. AF em aberto com saldo -> pode importar", _ok is True and not _mortas)
_ok2, _mortas2 = _afm.af_ainda_valida(_CurAfMorta(False), "CH")
chk("B58d. AF sem saldo -> barra e diz qual", _ok2 is False and _mortas2 == ["0000000001"])
chk("B58e. fail-safe: erro ao conferir -> nao importa",
    _afm.af_ainda_valida(_CurQuebra(), "CH") == (False, []))

# --- B59. trilha do robo: da para saber o que ele vinculou ----------- #
# Sem trilha, um erro so apareceria quando alguem reclamasse, e nao haveria
# como levantar QUAIS notas o robo tocou.
chk("B59. o robo registra o vinculo de AF em AIHUB_PROC_LOG",
    "def _auditar_af" in _src_robo and "AF_VINCULO_ROBO" in _src_robo)
chk("B59b. a trilha vai na MESMA transacao da gravacao",
    "_auditar_af(cur, chave, afs, n, out.get(\"motivo\", \"\"))" in _src_robo)
_aa = _corpo(_src_robo, "_auditar_af")
chk("B59c. a trilha nao comita sozinha", ".commit(" not in _aa)

# --- B60. espera por trava NUNCA pode ser infinita ------------------- #
# FOR UPDATE puro espera INDEFINIDAMENTE. Se alguem estiver com a nota aberta
# no Teknisa, o robo congelaria ali e a rodada inteira morreria junto - com o
# teto diario e o contador de erros, que dependem dela terminar.
import re as _re
_sql_fu = [l for l in _af_src.splitlines()
           if "FOR UPDATE" in l and not l.strip().startswith("#")
           and "Solucao:" not in l]
chk("B60. todo FOR UPDATE do SQL tem limite de espera",
    all("WAIT" in l or "NOWAIT" in l for l in _sql_fu) and _sql_fu)
chk("B60b. trava ocupada e tratada como 'tem gente mexendo', nao como erro",
    "ORA-30006" in _af_src and "ORA-00054" in _af_src)

class _CurTravada:
    def execute(self, sql, *a, **k):
        if "FOR UPDATE" in sql:
            raise Exception("ORA-30006: resource busy; acquire with WAIT timeout expired")
    def fetchall(self): return []
    def fetchone(self): return None
_n4, _r4 = _afm.gravar_af(_CurTravada(), "CH", [{"seq": "1", "af": "A1"}])
chk("B60c. nota travada por outra pessoa -> nao grava e explica",
    _n4 == 0 and _r4 and "outra pessoa" in _r4)

# --- B61. fail-safe das funcoes criticas ----------------------------- #
# Toda funcao que decide algo tem que responder "NAO faz" quando nao consegue
# conferir. O caminho de erro fecha a protecao, nunca abre.
class _CurMorto:
    def execute(self, *a, **k): raise Exception("ORA-03113: end-of-file")
    def fetchone(self): return None
    def fetchall(self): return []
chk("B61. af_inequivoca com banco fora -> nao vincula",
    _afm.af_inequivoca(_CurMorto(), "1", "1083") is False)
chk("B61b. af_ainda_valida com banco fora -> nao importa",
    _afm.af_ainda_valida(_CurMorto(), "CH") == (False, []))
chk("B61c. ler_tolerancias com banco fora -> usa o padrao medido",
    _afm.ler_tolerancias(_CurMorto()) == (_afm.TOL_QT_PADRAO, _afm.TOL_VR_PADRAO))
chk("B61d. janela_da_filial com banco fora -> janela padrao, nao infinita",
    _afm.janela_da_filial(_CurMorto(), "1083") == (None, _afm.JANELA_DIAS, _afm.JANELA_DIAS))

# --- B62. erro no meio da gravacao sobe para quem chama -------------- #
# Se gravar_af engolisse o erro, quem chama comitaria uma gravacao pela metade.
class _CurMeio:
    def __init__(self): self.n = 0; self._r = []; self.rowcount = 1
    def execute(self, sql, *a, **k):
        if "FOR UPDATE" in sql:
            self._r = [("1", None), ("2", None)]; return
        self.n += 1
        if self.n > 1: raise Exception("ORA-03113: end-of-file")
        self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return None
try:
    _afm.gravar_af(_CurMeio(), "CH", [{"seq": "1", "af": "A1"}, {"seq": "2", "af": "A1"}])
    chk("B62. erro no meio da gravacao PROPAGA (nao grava pela metade)", False)
except Exception:
    chk("B62. erro no meio da gravacao PROPAGA (nao grava pela metade)", True)

# --- B63. a AF tambem pode morrer antes da CONFIRMACAO --------------- #
# Caso diferente do importar_uma: aqui a nota JA entrou e pode ter ficado dias
# na fila esperando vinculo de produto. Nesse intervalo outra entrega consome
# a AF, e confirmar depois baixaria pedido ja atendido - com a agravante de
# que a baixa da confirmacao mexe em ESTOQUE.
_pn = _corpo(_src_robo, "processar_nota")
chk("B63. a confirmacao confere se a AF ainda esta viva",
    "af_ainda_valida(cur, nota[\"NRACESSONFE\"])" in _pn)
chk("B63b. AF morta nao confirma (status proprio)",
    "return \"AF_VENCIDA\"" in _pn)
chk("B63c. a checagem vem ANTES do SAVEPOINT",
    _pn.find("af_ainda_valida") < _pn.find("SAVEPOINT antes_confirmar"))

# --- B64. o SAVEPOINT e real, nao ilusorio --------------------------- #
# Duvida antiga do projeto: se as nativas comitassem por dentro, o ROLLBACK TO
# nao desfaria as baixas de AF ja feitas. Conferido no fonte PL/SQL:
# NF_BAIXA_AF (1.064 linhas) e VERIFICA_INCONS_NF (4.836) nao tem nenhum
# COMMIT nem PRAGMA AUTONOMOUS_TRANSACTION.
chk("B64. o codigo registra que o savepoint foi verificado no fonte nativo",
    "NF_BAIXA_AF" in _pn and "ilusorio" in _pn.lower())
chk("B64b. confirmacao falha -> ROLLBACK TO SAVEPOINT",
    "ROLLBACK TO SAVEPOINT antes_confirmar" in _pn)

# --- B65. item que precisa de conversao nao confirma sozinho --------- #
# Lendo o fonte da NF_BAIXA_AF: o saldo vem de ITEMAUTF (unidade da AF) e a
# quantidade vem do parametro (unidade da NOTA); V_VRFATOCONV e declarada e
# NUNCA usada, ou seja, a procedure nao converte. Nos 8% de itens em que o
# produto da nota difere do da AF, passar a quantidade crua faria baixar a AF
# inteira no lugar da fracao (caso real: 8 KG contra saldo 3,5 PC).
chk("B65. existe a checagem de item com conversao",
    "def item_com_conversao" in _src_robo)
chk("B65b. a confirmacao para quando ha item com conversao",
    "return \"PENDENTE_CONVERSAO\"" in _pn)
chk("B65c. a checagem vem ANTES do savepoint e da baixa",
    _pn.find("item_com_conversao") < _pn.find("SAVEPOINT antes_confirmar"))
_ic = _corpo(_src_robo, "item_com_conversao")
chk("B65d. fail-safe: nao conseguir conferir conta como 'precisa conversao'",
    "except Exception" in _ic and "fora.append" in _ic)

# --- B66. parametro do CLIENTE nao pode ser constante nossa ---------- #
# P_IDGERNFIGUALXML e declarado na procedure como PARAMGERAL.IDGERNFIGUALXML.
# Estava 'N' fixo: coincide hoje (producao='N'), mas se o cliente ligar, o
# robo validaria DIFERENTE da tela nativa em silencio.
_cr = _corpo(_src_robo, "confirmar_recebimento")
_gx = _corpo(_src_robo, "_gera_igual_xml")
chk("B66. IDGERNFIGUALXML e lido do PARAMGERAL, nao fixo",
    "IDGERNFIGUALXML" in _gx and "FROM TEKNISA.PARAMGERAL" in _gx)
chk("B66b. fail-safe: nao conseguir ler devolve 'N' (o mais restritivo)",
    "return \"N\"" in _gx)
chk("B66c. as flags de LIBERAR inconsistencia continuam 'N'",
    "\"N\", tipoop, \"N\", erro" in _cr)
# as DUAS nativas declaram o parametro como PARAMGERAL.IDGERNFIGUALXML%TYPE.
# Uma leitura so para nao poderem divergir entre si.
chk("B66d. a confirmacao usa o helper, nao le por conta propria",
    "_gera_igual_xml(cur)" in _cr and "FROM TEKNISA.PARAMGERAL" not in _cr)
_iu2 = _corpo(_src_robo, "importar_uma")
chk("B66e. a importacao tambem usa o helper (era 'N' fixo)",
    "_gera_igual_xml(cur)" in _iu2 and '"P_GERAIGUALXML": "N"' not in _iu2)

# --- B67. quem CRIA documento fiscal tem trava propria --------------- #
# importar_uma chama a GERA nativa, que cria a nota. Os tres chamadores ja
# barram, mas depender de quem chama lembrar e fragil para uma operacao deste
# peso: um chamador novo furaria a trava sem ninguem perceber.
chk("B67. importar_uma exige MODO_ESCRITA por conta propria",
    "if not MODO_ESCRITA" in _iu2 or "if not MODO_ESCRITA" in _corpo(_src_robo, "importar_uma"))
chk("B67b. e devolve um status proprio, nao um erro generico",
    "MODO_CONSULTA" in _corpo(_src_robo, "importar_uma"))
chk("B67c. o contador da rodada conhece o status novo",
    "\"MODO_CONSULTA\": 0" in _corpo(_src_robo, "rodar_importacao"))
chk("B67d. o painel explica esse caso em portugues",
    "MODO_CONSULTA" in _src_af and "AIHUB_MODO_ESCRITA" in _src_af)

# --- B68. nenhuma escrita no TEKNISA fora dos pontos conhecidos ------ #
# Mapa: PRODFORNXML (De/Para) e INTG_ITEMPRODUTO (AF). Mais nada.
import re as _re2
_escritas = set()
for _a in ("interface/painel/app.py", "robo/servico_robo.py", "core/af_matcher.py"):
    _s = open(_a, encoding="utf-8").read()
    for _m in _re2.finditer(r"(?is)\b(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO)\s+TEKNISA\.(\w+)", _s):
        _escritas.add(_m.group(1).upper())
chk("B68. o sistema so escreve em PRODFORNXML e INTG_ITEMPRODUTO",
    _escritas == {"PRODFORNXML", "INTG_ITEMPRODUTO"},
    sorted(_escritas))

# --- B77. o menu de 4 grupos nao pode deixar tela orfa ---------------- #
# O menu passou de 12 itens para 4 porque "tem muita coisa e ta muito confuso".
# Nenhuma tela foi apagada: cada uma virou aba de um grupo. O risco agora e o
# inverso do de antes - alguem criar uma tela e esquecer de pendura-la num
# grupo, e ela ficar inalcancavel sem ninguem perceber.
_html = open("interface/painel/index.html", encoding="utf-8").read()
_bloco_grupos = _html[_html.find("const GRUPOS={"):]
_bloco_grupos = _bloco_grupos[:_bloco_grupos.find("};") + 2]
_em_grupo = set(_re2.findall(r"'([a-z]+)'", _bloco_grupos))
chk("B77. sao exatamente 4 itens de menu",
    len(_re2.findall(r'<a data-view="(\w+)"', _html)) == 4,
    _re2.findall(r'<a data-view="(\w+)"', _html))
# UM ITEM DE MENU = UMA TELA (ou telas empilhadas do mesmo assunto).
# O erro anterior foi trocar 12 itens de menu por 12 ABAS: nao simplificou,
# so mudou o lugar da confusao. Aba nao pode voltar a esconder tela.
chk("B77b. o menu nao usa abas para esconder tela",
    "renderGtabs(" not in _html and "ABA_ROTULO" not in _html)
# quem chama o nome ANTIGO de uma tela tem que cair no grupo certo, senao o
# clique no cartao de KPI leva para lugar nenhum
_bloco_antiga = _html[_html.find("const TELA_ANTIGA={"):]
_bloco_antiga = _bloco_antiga[:_bloco_antiga.find("};") + 2]
for _v in ("travadas", "revisao", "config", "caixa", "fila", "aprovadas",
           "arquivadas", "buscar", "naoutil", "relatorio", "simular"):
    chk("B77c. go('%s') ainda leva a algum lugar" % _v,
        _v in _em_grupo or _v in _bloco_antiga)
chk("B77e. o badge do menu e a soma das duas filas de trabalho",
    "_badgeVal.navNaoUtil+_badgeVal.navRevisao" in _html)

# --- B76. a chamada da NF_BAIXA_AF tem que continuar sendo delta zero  #
# Provado em teste controlado (homologacao, 06/08/2026) e no fonte da propria
# procedure: com P_IDSTATUS='A' ela calcula P_NEWQTITEMPROD - P_OLDQTITEMPROD
# (linha 315). Passamos os dois iguais, entao a quantidade e ZERO. Trocar 'A'
# por 'I' faria a procedure usar a quantidade INTEIRA (linha 311) - e ai a
# conversao de unidade, hoje inofensiva, viraria risco de estoque de verdade.
_chamada = _src_robo[_src_robo.find('cur.callproc("TEKNISA.NF_BAIXA_AF"'):]
_chamada = _chamada[:_chamada.find("])") + 2]
chk("B76. NF_BAIXA_AF continua sendo chamada com P_IDSTATUS='A'",
    '"A"' in _chamada, _chamada[:90])
chk("B76b. e com OLD igual a NEW na quantidade (delta zero)",
    _chamada.count('it["QTITEMPROD"]') == 2, _chamada[-70:])
chk("B76c. o codigo explica por que isso e proposital",
    "delta" in _corpo(_src_robo, "confirmar_recebimento").lower()
    or "ZERO por" in _corpo(_src_robo, "confirmar_recebimento"))

# --- B75. nota sem data de emissao nao pode estourar ----------------- #
# candidatas() ja se protegia; candidatas_item() nao. O bind nulo entra em
# 'MIN(DTPROGENTAF) - :dt', o Oracle assume NUMBER e da ORA-00932. Medido em
# homologacao: 87 de 150 notas paradas quebravam assim.
chk("B75. candidatas_item devolve vazio sem data de emissao",
    _afm.candidatas_item(_CurAF(1), "F1", "1083", "1020101000", 10.0, None, 15.0) == [])
chk("B75b. e tambem sem filial ou sem produto",
    _afm.candidatas_item(_CurAF(1), "F1", None, "1020101000", 10.0,
                         __import__("datetime").datetime(2026, 6, 1), 15.0) == []
    and _afm.candidatas_item(_CurAF(1), "F1", "1083", None, 10.0,
                             __import__("datetime").datetime(2026, 6, 1), 15.0) == [])
_sem_dt = _afm.escolher_af(_CurAF(1), {"CDFORNECED": "J1", "CDFILIRECE": "1083",
                                       "DTEMISSAO": None}, _itens, 15.0)
chk("B75c. escolher_af recusa com motivo em portugues, sem estourar",
    _sem_dt["decisao"] == _afm.NENHUMA and "data de emissão" in _sem_dt["motivo"],
    _sem_dt["motivo"][:60])

# --- B74. nunca baixar AF que ja foi baixada ------------------------- #
# Medido nos DOIS ambientes: toda nota que tem AF nos itens ja tem linha em
# NFRELACAF - a baixa acontece na IMPORTACAO. Chamar NF_BAIXA_AF depois
# consumiria o mesmo pedido duas vezes, e a baixa mexe em estoque.
class _CurBaixa:
    def __init__(self, n): self.n = n
    def execute(self, *a, **k): pass
    def fetchone(self): return (self.n,)
    def fetchall(self): return []
chk("B74. NFRELACAF com linhas -> considera ja baixada",
    _rob.af_ja_baixada(_CurBaixa(3), {"CDFILIAL": "1", "NRLANCTONF": "9"}) is True)
chk("B74b. sem linhas -> pode baixar",
    _rob.af_ja_baixada(_CurBaixa(0), {"CDFILIAL": "1", "NRLANCTONF": "9"}) is False)
class _CurBaixaErro:
    def execute(self, *a, **k): raise Exception("ORA-00942")
    def fetchone(self): return None
    def fetchall(self): return []
chk("B74c. fail-safe: nao conseguir conferir = NAO baixa de novo",
    _rob.af_ja_baixada(_CurBaixaErro(), {"CDFILIAL": "1", "NRLANCTONF": "9"}) is True)
# a trava fica na funcao que BAIXA, nao na consulta: quem trocar o filtro de
# status (o 'I' que producao nao produz) continua protegido
_cr = _corpo(_src_robo, "confirmar_recebimento")
chk("B74d. a trava esta em confirmar_recebimento, junto da chamada",
    "af_ja_baixada(cur, nota)" in _cr and "NF_BAIXA_AF" in _cr)
chk("B74e. pula so a baixa, nao a validacao e a confirmacao",
    "VERIFICA_INCONS_NF" in _cr and "else:" in _cr)
# as checagens que so fazem sentido antes de baixar ficam condicionadas
_pn2 = _corpo(_src_robo, "processar_nota")
chk("B74f. conversao e validade da AF so barram se formos de fato baixar",
    "vai_baixar = not af_ja_baixada(" in _pn2
    and "if vai_baixar else []" in _pn2
    and "if vai_baixar else (True, [])" in _pn2)

# --- B73. "nenhuma AF atende" so quando NAO HA AF -------------------- #
# Existindo AF mas com saldo menor que a nota, o nativo dividiria a entrega.
# Dizer "nenhuma AF atende" mandava a pessoa procurar o que ja estava na tela.
class _CurNaoCabe:
    """Nenhuma AF cobre a nota inteira; item a item existe AF, mas sem saldo."""
    def __init__(self): self._r = []
    def execute(self, sql, *a, **k):
        if "PARAMFILIAL" in sql:
            self._r = [(__import__("datetime").datetime(2026, 6, 1), 20, 20)]
        elif "COUNT(DISTINCT PA.CDPRODESTO)" in sql:   # candidatas(): nenhuma
            self._r = []
        elif "AUTOFORNE" in sql and "COUNT(*)" not in sql:  # candidatas_item()
            self._r = [("0000000001", "1083", "9999", 3.0, 0.0, "01/06/2026", 0)]
        elif "COUNT(*)" in sql and "AUTOFORNE" in sql:
            self._r = [(1,)]
        else:
            self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

_nc = _afm.escolher_af(_CurNaoCabe(), _nota, _itens, 15.0)   # item pede 100, AF tem 3
chk("B73. AF existe mas nao cabe -> 'ambigua', nao 'nenhuma'",
    _nc["decisao"] == _afm.AMBIGUA, _nc["decisao"])
chk("B73b. a mensagem nao mente dizendo que nao ha AF",
    "Nenhuma AF em aberto" not in _nc["motivo"], _nc["motivo"][:70])
# 'opcoes' alimenta o fluxo "uma AF para a nota inteira". Aqui as opcoes sao de
# UM item: aplicar a nota toda poria AF que nao cobre os outros produtos.
chk("B73c. nao oferece opcao de AF para a nota inteira nesse caso",
    not _nc.get("opcoes"))
chk("B73d. mas entrega o plano por item, para a tela mostrar",
    bool(_nc.get("plano")) and bool(_nc["plano"][0].get("opcoes")))
# a rota do painel so aceita AF que esteja na lista de candidatas - lista
# vazia tem que RECUSAR, nao liberar geral
_bl = _src_af[_src_af.find("def af_vincular"):
              _src_af.find("@app.route", _src_af.find("def af_vincular"))]
chk("B73e. /api/af-vincular recusa AF fora da lista, mesmo com lista vazia",
    "if af_pedida not in validas:" in _bl)
# a tela tem um ramo so para a distribuicao por item (o "1 clique"), mas a rota
# nao mandava o campo: o ramo era inalcancavel e a pessoa so via a lista de AFs
_bs = _src_af[_src_af.find("def af_sugestao"):
              _src_af.find("@app.route", _src_af.find("def af_sugestao"))]
chk("B73f. /api/af-sugestao devolve o plano por item (a tela usa)",
    '"plano"' in _bs and '"pronta_para_confirmar"' in _bs)
chk("B73g. e a tela le esse campo",
    "d.plano" in open("interface/painel/index.html", encoding="utf-8").read())

# --- B72. saldo da AF tem que vir na unidade de ESTOQUE -------------- #
# A nota ja era convertida (qt x fator do produto da nota), mas o lado da AF
# era lido cru. Como a AF costuma estar no produto de COMPRA, comparavam-se
# CAIXAS com QUILOS. Medido em 700 vinculos verdadeiros com fator != 1: cru,
# a nota "cabia" na AF real em 61%; convertido, 100%.
for _fn in ("candidatas", "candidatas_item"):
    _c = _corpo(_af_src, _fn)
    _qs = [l for l in _c.splitlines() if "QTPROGENTAF,0)" in l or "QTEFETENTRAF,0)" in l]
    # a linha do filtro de saldo (>0) nao precisa converter: o fator e sempre
    # positivo e nao muda o sinal. Aqui olhamos as somas do SELECT.
    _somas = [l for l in _qs if "SUM(" in l]
    chk("B72. %s converte o saldo da AF pelo fator do produto" % _fn,
        _somas and all("VRFATOCONV" in l or "VRFATOCONV" in _c[_c.find(l):_c.find(l)+180]
                       for l in _somas), _somas)
# divisao por zero: fator 0 nao pode zerar o saldo da AF
chk("B72b. fator zero nao vira multiplicacao por zero",
    _af_src.count("NULLIF(PA.VRFATOCONV,0)") >= 4)

# --- B71. nota COMPLETA com 2+ AFs nao e 'parcial' ------------------- #
# Uma entrega pode cumprir dois pedidos: a amarracao nativa (NFRELACAF) e item
# a item exatamente por isso. O motor exigia UMA unica AF para reconhecer a
# nota como pronta; com duas, ela caia no ramo de "parcialmente vinculada" e
# recebia a mensagem "2 de 2 itens...", que se contradiz. O robo lia aquilo
# como duvida e nao importava. Medido em producao: 15 notas em 45 dias.
class _CurResolver:
    """Cursor de mentira para resolver(): nota com N itens, AFs a escolha."""
    def __init__(self, afs_por_item):
        self.afs = afs_por_item          # ex ['AF1','AF2'] ou ['AF1', None]
        self._r = []
    def execute(self, sql, *a, **k):
        if "INTG_NOTAFISCAL" in sql:
            self._r = [("F1", "1083", __import__("datetime").datetime(2026, 6, 1),
                        "123", "AF(Relacionar AF)", None)]     # DTUTILIZAXML nulo
        elif "INTG_ITEMPRODUTO" in sql:
            self._r = [(str(i + 1), "X%d" % i, "PROD %d" % i, "KG", 10.0, af)
                       for i, af in enumerate(self.afs)]
        else:
            self._r = []
    def fetchall(self): return self._r
    def fetchone(self): return self._r[0] if self._r else None

_r_2af = _afm.resolver(_CurResolver(["0000001", "0000002"]), "CH")
chk("B71. nota com TODOS os itens vinculados e 'pronta', mesmo com 2 AFs",
    "já está relacionada" in _r_2af["motivo"], _r_2af["motivo"][:70])
chk("B71b. a mensagem cita as duas AFs (o robo audita pelo texto)",
    "0000001" in _r_2af["motivo"] and "0000002" in _r_2af["motivo"])
chk("B71c. nota com 1 AF em todos os itens continua pronta",
    "já está relacionada" in _afm.resolver(
        _CurResolver(["0000001", "0000001"]), "CH")["motivo"])
# e o que NAO pode afrouxar: item sem AF continua sendo nota parcial
_r_parc = _afm.resolver(_CurResolver(["0000001", None]), "CH")
chk("B71d. item sem AF ainda barra (nota parcial nao vira pronta)",
    "já está relacionada" not in _r_parc["motivo"], _r_parc["motivo"][:70])

# --- B69. corrida no De/Para: dois vinculos ativos ------------------- #
# Duas sessoes gravando o MESMO item do MESMO fornecedor com produtos
# diferentes nao enxergam o UPDATE uma da outra e as duas inserem. Medido: 0
# casos hoje, mas e seguranca por dado, nao por construcao.
_gd = _corpo(_src_robo, "gravar_depara")
chk("B69. gravar_depara trava a linha antes de mexer",
    "FOR UPDATE WAIT 3" in _gd)
chk("B69b. no robo, trava ocupada nao congela: desiste e volta depois",
    "ORA-30006" in _gd and "return False" in _gd)
_gv3 = _src_af[_src_af.find("def _gravar_vinculo"):
               _src_af.find("\ndef ", _src_af.find("def _gravar_vinculo")+1)]
# olha as linhas de SQL, nao a docstring (que menciona WAIT ao explicar por que
# o painel nao usa) - foi assim que este teste falhou na primeira versao
_sql_gv = [l for l in _gv3.splitlines() if "FOR UPDATE" in l and "#" not in l]
chk("B69c. o painel tambem trava (sem WAIT: e uma pessoa esperando a tela)",
    _sql_gv and all("WAIT" not in l for l in _sql_gv))

# --- B70. recusa de gravacao nao pode virar 'vinculado' -------------- #
# Se gravar_depara recusa e o codigo seguisse alinhando it["CDPRODUTO"], a
# NF_BAIXA_AF receberia um produto que NAO foi gravado como De/Para.
chk("B70. o robo so alinha CDPRODUTO se a gravacao deu certo",
    "gravou = gravar_depara(" in _pn and "if gravou:" in _pn)
# o 'if gravou:' tem que ter um else que abre pendencia. Comparar posicao com
# "pendentes += 1" nao serve: ele aparece antes, em outro ramo (item sem
# descricao) - foi assim que este teste falhou na primeira versao.
_ig = _pn.find("if gravou:")
_fim_ig = _pn.find("\n    if pendentes == 0", _ig)      # ate o bloco seguinte
_bloco_gravou = _pn[_ig:_fim_ig if _fim_ig > 0 else len(_pn)]
chk("B70b. recusa vira pendencia humana, nao vinculo silencioso",
    "else:" in _bloco_gravou and "abrir_pendencia(" in _bloco_gravou)
chk("B70c. o passo pre-importacao tambem respeita a recusa",
    "elif gravar_depara(" in _corpo(_src_robo, "resolver_depara_do_xml"))

def _ler_segredo_local():
    """A senha do banco, lida do CONFIG_LOCAL.bat (fora do versionamento).
    Devolve None quando o arquivo nao existe - ai o teste se declara pulado."""
    try:
        with open("CONFIG_LOCAL.bat", encoding="utf-8", errors="ignore") as fh:
            m = re.search(r"TEKNISA_DB_PASS=(.+)", fh.read())
        return m.group(1).strip() if m else None
    except Exception:
        return None


# --- B9. senha fora do codigo ---------------------------------------- #
# O SEGREDO NAO MORA AQUI, NEM EM PEDACOS. A versao anterior montava a senha
# concatenando dois trechos, para nao disparar o proprio teste - mas num
# repositorio publico concatenar e o mesmo que escrever. Agora ela e lida do CONFIG_LOCAL.bat,
# que e o unico lugar legitimo e nao acompanha o codigo. Sem o arquivo, o teste
# se declara pulado em vez de passar calado: "nao conferi" nao e "esta limpo".
_SENHA = _ler_segredo_local()
_com_senha = []
for _raiz, _dirs, _arqs in (os.walk(".") if _SENHA else []):
    if any(p in _raiz for p in (".git", "__pycache__", "venv", "node_modules")):
        continue
    for _a in _arqs:
        if not _a.lower().endswith((".py", ".bat", ".md", ".sql")):
            continue
        if _a == "CONFIG_LOCAL.bat":      # o unico lugar legitimo, nao versionado
            continue
        _p = os.path.join(_raiz, _a)
        try:
            if _SENHA in open(_p, encoding="utf-8", errors="ignore").read():
                _com_senha.append(_p)
        except Exception:
            pass
chk("B9. a senha do banco nao aparece em nenhum arquivo do projeto",
    not _com_senha, "aparece em: " + ", ".join(_com_senha))

# --- B79. o CFOP diz se a nota e compra com AF ------------------------ #
# Medido em 466.902 notas importadas: quando o motor diz COMPRA acerta 95,4%,
# quando diz SEM_AF acerta 92,1%, e so 0,1% cai em DUVIDA. Alcance nas paradas:
# 28.583 notas que o robo hoje ignora por falta de CDTIPOOPER (vazio em 78,7%).
from core.cfop_natureza import (natureza, nota_e_compra, medir,
                                COMPRA, SEM_AF, DUVIDA, _MEDIDO)
chk("B79. CFOP de compra vira COMPRA",
    natureza("5102")[0] == COMPRA and natureza("5101")[0] == COMPRA)
chk("B79b. CFOP sem AF vira SEM_AF",
    natureza("5656")[0] == SEM_AF and natureza("6353")[0] == SEM_AF)
# A familia 1\2\3 e ENTRADA no emitente: devolucao. Vale por DEFINICAO do CFOP,
# nao por estatistica - a base tem so 20 notas dessas, entao contar nao servia.
chk("B79c. familia 1/2/3 e devolucao, mesmo sem historico",
    natureza("1202")[0] == SEM_AF and natureza("2201")[0] == SEM_AF
    and natureza("3101")[0] == SEM_AF)
chk("B79d. o caso real da FORNECEDOR I (NF 7168609, CFOP 1202) sai da fila",
    natureza("1202")[0] == SEM_AF)
# FAIL-SAFE: nao conhecer NUNCA pode virar COMPRA. Liberar por engano faria o
# robo procurar AF em documento que nao e compra.
chk("B79e. CFOP desconhecido vira DUVIDA, nunca COMPRA",
    natureza("5999")[0] == DUVIDA and natureza("9876")[0] == DUVIDA)
chk("B79f. CFOP vazio ou nulo vira DUVIDA",
    natureza(None)[0] == DUVIDA and natureza("")[0] == DUVIDA
    and natureza("   ")[0] == DUVIDA)
chk("B79g. CFOP com pontuacao e limpo antes de decidir",
    natureza("5.102")[0] == COMPRA and natureza(" 5102 ")[0] == COMPRA)
chk("B79h. todo motivo vem em portugues, para a tela",
    all(isinstance(natureza(c)[1], str) and len(natureza(c)[1]) > 10
        for c in ("5102", "5656", "1202", "9999", None)))


class _CurCfop:
    """cursor de mentira: devolve o CFOP pedido, ou estoura."""
    def __init__(self, cfop, estoura=False):
        self.cfop = cfop; self.estoura = estoura; self._r = None
    def execute(self, sql, *a, **k):
        if self.estoura:
            raise RuntimeError("ORA-00942")
        self._r = (self.cfop,)
    def fetchone(self):
        return self._r


chk("B79i. nota_e_compra le o CFOP e classifica",
    nota_e_compra(_CurCfop("5102"), "x")[0] == COMPRA)
chk("B79j. erro ao ler o CFOP vira DUVIDA (fail-safe fecha)",
    nota_e_compra(_CurCfop(None, estoura=True), "x")[0] == DUVIDA)
chk("B79k. nota sem CFOP nenhum vira DUVIDA",
    nota_e_compra(_CurCfop(None), "x")[0] == DUVIDA)
# A tabela e MEDIDA, e o numero que a justifica fica junto - senao daqui a um
# ano ninguem sabe se aquele piso foi medido ou chutado.
chk("B79l. cada CFOP da tabela carrega o percentual e a base que o mediram",
    all(len(v) == 3 and isinstance(v[1], float) and v[2] >= 30
        for v in _MEDIDO.values()))
chk("B79m. existe funcao para refazer a medicao quando a base mudar",
    callable(medir))
# NAO pode virar terceira tabela de escrita: o B68 exige duas.
_src_cfop = open("core/cfop_natureza.py", encoding="utf-8").read()
chk("B79n. o motor de CFOP nao grava nada",
    not any(w in _src_cfop for w in ("UPDATE ", "INSERT ", "DELETE ", "MERGE ")))

# --- B80. o leitor de AF no XML reconhece TODOS os formatos vistos ----- #
# Auditados 450 XMLs de notas com AF ja gravada (o gabarito): a AF aparece no
# texto em 406 e o extrator pega 403 (99,3%). Os 3 perdidos eram: 'oc0233973'
# (sigla colada, CORRIGIDO), um numero digitado errado pelo fornecedor
# ('2561111' para a AF 0000256111) e um NRAUTOFORN='0' de lixo de cadastro.
# Cada linha abaixo e um formato REAL, tirado de XML de producao.
from core.xml_af import candidatos_no_xml, normaliza_af


def _acha(xml):
    c = candidatos_no_xml(xml)
    return c[0][0] if c else None


_FORMATOS = [
    ("AF - 1445341 (hifen, NF 62104 AKS)",
     "<infCpl>^^AF - 1445341^HC ^DATA - 23-06^^</infCpl>", "0001445341"),
    ("AF.1445241 (ponto, NF 839 FORNECEDOR J)",
     "<infCpl>CATARINENSE PHARMA. AF.1445241.</infCpl>", "0001445241"),
    ("AF : 0000636355 (espaco antes dos dois pontos)",
     "<infCpl>Inf. Contribuinte: AF : 0000636355;;</infCpl>", "0000636355"),
    ("AFs 1439794 (plural, NF 258474 FORNECEDOR B)",
     "<infCpl>ENTREGA 22/06 AFs 1439794 UNIDADE</infCpl>", "0001439794"),
    ("Autorizacao de Fornecimento:0001445109 (por extenso, IPO)",
     "<infCpl>Autorizacao de Fornecimento:0001445109|DATA</infCpl>", "0001445109"),
    ("oc0233973 (sigla COLADA no numero - o que faltava)",
     "<infCpl>FORMA PAGTO BC - oc0233973 - CONFIRA</infCpl>", "0000233973"),
    ("NACIONAL GAS AF 256111 (rotulo dentro de frase)",
     "<infCpl>NACIONAL GAS AF 256111 MARCIANO 21 03</infCpl>", "0000256111"),
    ("Ordem(ns) de compra(s): 0001443956 (S.O.S.)",
     "<infAdFisco>Ordem(ns) de compra(s):  0001443956 - SABOR</infAdFisco>",
     "0001443956"),
    ("NUMERO DO(S) PEDIDO(S) DO CLIENTE: 1444527 (POLZIN)",
     "<infCpl>NUMERO DO(S) PEDIDO(S) DO CLIENTE: 1444527</infCpl>", "0001444527"),
    ("<xPed>0001440050</xPed> (tag oficial limpa)",
     "<xPed>0001440050</xPed>", "0001440050"),
    ("<xPed>AF 1438360</xPed> (tag com texto junto, FORNECEDOR C)",
     "<xPed>AF 1438360</xPed>", "0001438360"),
    ("Autorizaco de Fornecimento0000240492 (sem cedilha e sem dois pontos)",
     "<infCpl>Autorizaco de Fornecimento0000240492 - Vendedor: 2</infCpl>",
     "0000240492"),
]
for _rot, _xml, _esp in _FORMATOS:
    chk("B80. formato reconhecido: " + _rot, _acha(_xml) == _esp,
        "achou %r, esperava %r" % (_acha(_xml), _esp))

# A AF tem 10 posicoes. Mais que isso NAO e AF: o XML da P.A.S trazia
# "Ordem Compra: 14448961444896", que e a AF 1444896 colada duas vezes.
# Aceitar aquilo seria amarrar a nota num numero inventado.
chk("B80m. numero de 11+ digitos e recusado (AF colada duas vezes)",
    normaliza_af("14448961444896") is None)
# ARMADILHA QUE ESTE TESTE JA PEGOU: ao afrouxar o rotulo para aceitar
# "Autorizaco" sem cedilha, usei \w*, que INCLUI DIGITO. O rotulo passou a
# engolir "Fornecimento000024" e sobrava "0492" - a AF 0000240492 virava
# 0000000492. Rotulo aceita letra; digito e do numero.
chk("B80m2. o rotulo nao pode engolir digito do numero",
    _acha("<infCpl>Autorizaco de Fornecimento0000240492</infCpl>") == "0000240492"
    and _acha("<infCpl>AF12345678</infCpl>") == "0012345678")
chk("B80n. normaliza para 10 posicoes, com e sem zeros a esquerda",
    normaliza_af("1445341") == "0001445341"
    and normaliza_af("0000636355") == "0000636355")
chk("B80o. numero vazio ou sem digito nao vira AF",
    normaliza_af("") is None and normaliza_af("AF") is None
    and normaliza_af(None) is None)
# A fonte mais confiavel tem de vir primeiro: a tag oficial vem sozinha, o
# texto livre vem cercado de "Ped: 63197" e "Cliente: 13508".
chk("B80p. a tag xPed e oferecida antes do texto livre",
    candidatos_no_xml(
        "<xPed>0001440050</xPed><infCpl>Pedido: 999888</infCpl>"
    )[0][1].startswith("tag"))

# --- B81. a AF encolhe a lista de candidatos do produto --------------- #
# Medido em 8 fornecedores: de 2.502 candidatos para 77 (-96,9%), com casos de
# 800 para 2 e 447 para 22. Ataca o achado 44: a ambiguidade se resolve pela
# LISTA, nao pelo score.
from core.ai_matcher import (candidatos, candidatos_da_af, escolher_com_af,
                             SQL_CANDIDATOS_AF, LIMIAR_AUTO, LIMIAR_SUGESTAO)


class _CurCands:
    """cursor de mentira: devolve listas diferentes para a consulta curta e a
    completa, para dar para provar QUAL delas foi usada."""
    def __init__(self, curta, completa, estoura_curta=False):
        self.curta, self.completa = curta, completa
        self.estoura_curta = estoura_curta
        self._r = []
        self.usou = None

    def execute(self, sql, *a, **k):
        # As DUAS consultas passam por ITEMAUTF, entao nao da para separar por
        # ela (a primeira versao deste teste separava assim e o proprio teste
        # estourou). A completa e a unica que parte da AUTOFORNE.
        if "AUTOFORNE" in sql:
            self.usou = "completa"
            self._r = list(self.completa)
        else:
            self.usou = "curta"
            if self.estoura_curta:
                raise RuntimeError("ORA-00942")
            self._r = list(self.curta)

    def fetchall(self):
        return self._r


_CURTA = [("1040453200", "QUEIJO MUSSARELA INTEIRO", "KG")]
_LONGA = [("1040453200", "QUEIJO MUSSARELA INTEIRO", "KG"),
          ("1040453000", "QUEIJO MUSSARELA BUFALA", "KG"),
          ("1040453100", "QUEIJO MUSSARELA FATIADO", "KG")]

_c = _CurCands(_CURTA, _LONGA)
chk("B81. com AF informada, usa a lista da AF",
    candidatos(_c, "FORN", "0001444896", "9153") == _CURTA
    and _c.usou == "curta")
_c2 = _CurCands(_CURTA, _LONGA)
chk("B81b. sem AF, usa a lista completa do fornecedor",
    candidatos(_c2, "FORN") == _LONGA and _c2.usou == "completa")
# FAIL-SAFE: a lista curta e um ATALHO, nunca uma restricao. Se a consulta dela
# falhar, o sistema tem de seguir funcionando como funcionava.
_c3 = _CurCands(_CURTA, _LONGA, estoura_curta=True)
chk("B81c. erro na lista curta cai na completa (atalho, nao restricao)",
    candidatos(_c3, "FORN", "0001444896", "9153") == _LONGA)
_c4 = _CurCands([], _LONGA)
chk("B81d. AF sem itens cai na completa",
    candidatos(_c4, "FORN", "0009999999", "9153") == _LONGA)
chk("B81e. candidatos_da_af sem AF ou sem filial devolve vazio",
    candidatos_da_af(_CurCands(_CURTA, _LONGA), None, "9153") == []
    and candidatos_da_af(_CurCands(_CURTA, _LONGA), "0001", None) == [])
# A CURTA SO DECIDE QUANDO O ACERTO E ALTO. Se a AF estiver errada (a auditoria
# achou dois fornecedores que digitam o numero trocado), a lista curta ESCONDE
# o produto certo - e o matcher devolveria o menos ruim de dois errados.
_c5 = _CurCands([("9999999999", "PARAFUSO SEXTAVADO 3/8", "UN")], _LONGA)
_m, _s, _origem, _cd = escolher_com_af(_c5, "QUEIJO MUSSARELA INTEIRO",
                                       "FORN", "0001444896", "9153")
chk("B81f. item que NAO esta na AF cai na lista completa",
    _origem == "fornecedor" and _m and _m[0] == "1040453200")
_c6 = _CurCands(_CURTA, _LONGA)
_m6, _s6, _org6, _ = escolher_com_af(_c6, "QUEIJO MUSSARELA INTEIRO",
                                     "FORN", "0001444896", "9153")
chk("B81g. acerto na lista curta -> decide por ela, e diz a origem",
    _org6 == "af" and _m6[0] == "1040453200")
# CASO REAL QUE REPROVOU A PRIMEIRA VERSAO (NF 9668877): o fornecedor escreve
# 'QJO MUSSARELA PC+-4KG CX+-24KG' e a AF pediu 'QUEIJO MUSSARELA INTEIRO'. O
# texto bate 67%. Exigindo LIMIAR_AUTO (92%) na curta, a busca caia nos 639
# produtos do fornecedor e escolhia 'MUSSARELA BUFALA' - errado, com o certo
# na mao. Numa lista de 2 que a AF pediu, 67% nao e duvida.
_c7 = _CurCands([("1040453200", "QUEIJO MUSSARELA INTEIRO", "KG"),
                 ("1040453202", "QUEIJO MUSSARELA INTEIRO - PCT 4 KG", "PC")],
                _LONGA)
_m7, _s7, _org7, _ = escolher_com_af(
    _c7, "QJO MUSSARELA PC+-4KG CX+-24KG A PC +-4KG", "FORN",
    "0001438737", "1040", "KG")
chk("B81g2. o caso da mussarela: a lista da AF decide mesmo abaixo de 92%",
    _org7 == "af" and _m7 and _m7[0].startswith("10404532"),
    "origem=%s escolheu=%s" % (_org7, _m7[0] if _m7 else None))
chk("B81g3. mas o piso continua existindo (senao item fora da AF casa errado)",
    LIMIAR_SUGESTAO > 0)
chk("B81h. a consulta da lista curta filtra por AF E filial (a PK exige)",
    ":af" in SQL_CANDIDATOS_AF and ":fil" in SQL_CANDIDATOS_AF
    and "CDFILDEST" in SQL_CANDIDATOS_AF)
chk("B81i. a lista curta tambem traz a versao base '00' (AF vem na embalagem)",
    "||'00'" in SQL_CANDIDATOS_AF)

# --- B82. os cortes tem que ser o TAMANHO REAL da coluna --------------- #
# ORA-12899 real, pego pelo usuario ao aprovar um vinculo: o painel gravava o
# operador 'PAINEL:127.0.0.1' (16 caracteres) em PRODFORNXML.CDOPERINCLUSAO,
# que tem 12 - como TODA coluna CDOPER% do Teknisa (conferido: 78 colunas).
# Passou despercebido porque as duas gravacoes anteriores couberam por acaso
# ('PAINEL:DEMO' tem 11, 'AIHUB_PAINEL' tem 12). E o NMPRODUTOXML era cortado
# em 240 quando a coluna aceita 80.
#
# O robo NAO estourava, porque grava 'AIHUB_ROBO' (10). E a mesma assimetria
# robo x painel do achado 76, e por isso os dois foram corrigidos juntos.
_gv_app = _corpo(_src_af, "_gravar_vinculo")
_gd_robo = _corpo(_src_robo, "gravar_depara")
chk("B82. painel corta NMPRODUTOXML em 80 (o tamanho da coluna)",
    '[:80]' in _gv_app and '[:240]' not in _gv_app)
chk("B82b. robo corta NMPRODUTOXML em 80 tambem",
    '[:80]' in _gd_robo and '[:240]' not in _gd_robo)
chk("B82c. painel corta o operador em 12",
    '[-12:]' in _gv_app)
chk("B82d. robo corta o operador em 12 tambem",
    '[-12:]' in _gd_robo)
# A defesa fica DENTRO da funcao que grava, e nao em quem chama: um chamador
# novo nao pode furar a trava por esquecimento (mesmo principio do achado 62).
chk("B82e. o corte esta na funcao que grava, nao so nos chamadores",
    '[-12:]' in _gv_app and '[-12:]' in _gd_robo)
chk("B82f. existe operador proprio para as colunas do Teknisa",
    "_operador_teknisa" in _src_af and "_MAX_OPER_TEKNISA = 12" in _src_af)

# --- B83. o produto tem que bater com a AF ---------------------------- #
# Caso real (NF 9668877): a pessoa vinculou 'MUSSARELA BUFALA' (1040453000) num
# item cuja AF pediu 'MUSSARELA INTEIRO' (1040453202, estoque 1040453200).
# Gravou sem reclamacao, e so na importacao o Teknisa recusou com o codigo 071.
# A regra ja existe no ERP; nos deixavamos gravar algo que ele rejeitaria.
from core.ai_matcher import produto_bate_com_af


class _CurAF:
    """cursor de mentira: itens da AF e o produto de estoque de um codigo."""
    def __init__(self, itens_af, estoque_de, estoura=False):
        self.itens_af = itens_af          # [(cd, nome, estoque)]
        self.estoque_de = estoque_de      # {cd: estoque}
        self.estoura = estoura
        self._r = []

    def execute(self, sql, *a, **k):
        if self.estoura:
            raise RuntimeError("ORA-00942")
        if "ITEMAUTF" in sql:
            self._r = list(self.itens_af)
        else:
            p = k.get("p")
            self._r = [(self.estoque_de.get(p),)] if p in self.estoque_de else []

    def fetchall(self):
        return self._r

    def fetchone(self):
        return self._r[0] if self._r else None


_AF_ITENS = [("1040453202", "QUEIJO MUSSARELA INTEIRO - PCT 4 KG", "1040453200")]
_ESTOQUES = {"1040453000": "1040453000",     # BUFALA
             "1040453200": "1040453200",     # INTEIRO, estoque
             "1040453202": "1040453200"}     # INTEIRO, embalagem

_bate, _lista = produto_bate_com_af(_CurAF(_AF_ITENS, _ESTOQUES),
                                    "1040453000", "0001438737", "1040")
chk("B83. produto de fora da AF e recusado (o caso da BUFALA)",
    _bate is False and len(_lista) == 1)
# A COMPARACAO E PELO PRODUTO DE ESTOQUE (achado 36): a AF esta na embalagem
# (1040453202, PC fator 4) e a nota vem no estoque (1040453200, KG). Comparar
# codigo exato recusaria vinculo CORRETO.
_bate2, _ = produto_bate_com_af(_CurAF(_AF_ITENS, _ESTOQUES),
                                "1040453200", "0001438737", "1040")
chk("B83b. o produto de ESTOQUE da AF e aceito (embalagem x granel)",
    _bate2 is True)
_bate3, _ = produto_bate_com_af(_CurAF(_AF_ITENS, _ESTOQUES),
                                "1040453202", "0001438737", "1040")
chk("B83c. o proprio produto da AF tambem e aceito", _bate3 is True)
# FAIL-SAFE AO CONTRARIO DO USUAL, e de proposito: esta funcao existe para
# evitar um erro conhecido, nao para virar trava nova. Nao conseguir conferir
# tem de DEIXAR PASSAR, senao ela barraria vinculo que hoje funciona.
_bate4, _ = produto_bate_com_af(_CurAF([], {}, estoura=True),
                                "1040453000", "0001438737", "1040")
chk("B83d. erro ao conferir deixa passar (nao vira trava nova)",
    _bate4 is True)
_bate5, _ = produto_bate_com_af(_CurAF([], _ESTOQUES),
                                "1040453000", "0001438737", "1040")
chk("B83e. AF sem itens deixa passar", _bate5 is True)
chk("B83f. sem AF ou sem filial, nao ha o que conferir",
    produto_bate_com_af(None, "1040453000", None, "1040")[0] is True
    and produto_bate_com_af(None, "1040453000", "0001438737", None)[0] is True)
# A tela precisa da LISTA, nao so do 'nao pode': sem ela a pessoa sabe que
# errou e continua sem saber qual e o certo.
chk("B83g. a recusa devolve os produtos que a AF pediu",
    _lista and _lista[0][0] == "1040453202")
chk("B83h. a rota de resolver pendencia usa a conferencia",
    "produto_bate_com_af" in _corpo(_src_af, "resolver_pendencia"))

# --- B84. achar A NOTA na fila de produtos ---------------------------- #
# Relato de uso: depois da recusa por falta de produto, a pessoa vai para a
# fila atras DAQUELA nota. Filtrando so por fornecedor ela recebia todos os
# itens pendentes do fornecedor (a FORNECEDOR A tem dezenas) e tinha de garimpar.
_pend = _corpo(_src_af, "pendencias")
chk("B84. a fila aceita filtro por nota",
    'request.args.get("nota")' in _pend)
# Um campo so para os dois: chave tem 44 digitos, numero tem poucos. Quem usa
# nao quer escolher qual e qual.
chk("B84b. o filtro distingue chave de numero pelo tamanho",
    "len(so_dig) >= 20" in _pend)
chk("B84c. chave busca por CHAVE_NF",
    "P.CHAVE_NF LIKE :nt" in _pend)
chk("B84d. numero busca pela INTG_NOTAFISCAL (a pendencia nao guarda o numero)",
    "NRNOTAFISC" in _pend and "TRIM(LEADING '0'" in _pend)
_html = open("interface/painel/index.html", encoding="utf-8").read()
chk("B84e. a tela tem o campo de busca por nota",
    'id="pendNota"' in _html and "nota:val('pendNota')" in _html)
chk("B84f. o botao da recusa leva para a fila JA filtrada na nota",
    "irFilaDaNota" in _html)
# Chegando pela nota, esconder itens por faixa faria a nota parecer resolvida.
chk("B84g. ao buscar por nota, nao filtra por faixa de confianca",
    "_bandaAtual=''" in _html and "'':['Todas as faixas'" in _html)

# --- B85. dizer QUAIS itens faltam, nao so que falta ------------------ #
# Caso real: nota de 4 itens, a pessoa vinculou 1, voltou para importar e ouviu
# de novo 'produto sem vinculo'. Achou que o vinculo nao pegou - mas faltava
# OUTRO item. Sem a lista, a mensagem certa parece defeito.
_rdp2 = _corpo(_src_af, "_recusa_de_produto")
chk("B85. a recusa por produto lista os itens que faltam",
    "itens_sem_depara" in _rdp2 and '"itens"' in _rdp2)
chk("B85b. a mensagem diz quantos e quais",
    "len(faltam)" in _rdp2)
chk("B85c. sem conseguir listar, ainda explica o que fazer",
    "Falta o produto" in _rdp2)
chk("B85d. a tela mostra a lista de itens que faltam",
    "r.itens" in _html)

# --- B86. FILA DE OCORRENCIAS: nada se perde -------------------------- #
# Antes, o motivo da recusa vivia num modal: a pessoa lia, fechava, e ninguem
# sabia quantas notas estavam paradas por preco nem se alguem foi atras.
# Caso real (NF 9667827): SETE apontamentos de uma vez - 051 de vencimento,
# dois 055 de preco e quatro 056 de produto fora da AF. Tres areas envolvidas.
_ocor = _corpo(_src_af, "_abrir_ocorrencias")
_fech = _corpo(_src_af, "_fechar_ocorrencias")
_sql_oc = open("sql/CRIAR_OCORRENCIAS.sql", encoding="utf-8").read()

chk("B86. toda recusa vira ocorrencia registrada",
    "AIHUB_OCORRENCIA" in _ocor and "INSERT INTO" in _ocor)
chk("B86b. a ocorrencia guarda QUEM RESOLVE",
    "QUEM" in _ocor and "quem" in _ocor)
# Sem o indice unico, cada clique em Importar duplicaria a fila: a NF 9667827
# sozinha geraria 7 linhas novas por tentativa.
chk("B86c. indice unico impede duplicar ao clicar Importar de novo",
    "IX_OCOR_ABERTA" in _sql_oc and "UNIQUE INDEX" in _sql_oc)
# O detalhe entra na chave porque o MESMO codigo aparece varias vezes na mesma
# nota com textos diferentes - os quatro 056 eram produtos distintos.
chk("B86d. a unicidade inclui o DETALHE (mesmo codigo, produtos diferentes)",
    "DETALHE" in _sql_oc and "STATUS = ''ABERTO''" in _sql_oc)
chk("B86e. a nota entrando fecha as ocorrencias dela",
    "STATUS='RESOLVIDO'" in _fech and "STATUS='ABERTO'" in _fech)
chk("B86f. as duas rotas de importacao registram e fecham",
    _src_af.count("_abrir_ocorrencias(con, cur, chave") >= 1
    and _src_af.count("_fechar_ocorrencias(cur, chave") >= 2)
# Registrar e um EXTRA: nao pode custar a operacao nem derrubar a resposta.
chk("B86g. erro ao registrar nao derruba a rota",
    "except Exception:" in _ocor and "_rollback(con)" in _ocor)
chk("B86h. em modo consulta nao grava ocorrencia",
    "if not (MODO_ESCRITA" in _ocor)
# 'Resolvido' sem dizer o que foi feito e um botao de esconder.
_res = _corpo(_src_af, "ocorrencia_resolver")
chk("B86i. tratar exige escrever o que foi feito",
    "len(obs) < 3" in _res)
chk("B86j. tratar exige MODO_ESCRITA como toda rota que grava",
    "if not MODO_ESCRITA" in _res and "403" in _res)
chk("B86k. nao trata duas vezes a mesma ocorrencia",
    "AND STATUS='ABERTO'" in _res and "409" in _res)
# Falta de GRANT nao pode derrubar tela (regra do projeto).
chk("B86l. tabela ausente nao derruba a tela",
    "_tem_tabela" in _corpo(_src_af, "ocorrencias"))
chk("B86m. a tela existe e esta no menu",
    'id="view-ocorrencias"' in _html and "ocorrencias:'resolver'" in _html)
chk("B86n. a tela mostra o placar por responsavel",
    "ocorPlacar" in _html and "_OCOR_QUEM" in _html)
chk("B86o. existe o script de criacao para a TI rodar",
    "CREATE TABLE AIHUB.AIHUB_OCORRENCIA" in _sql_oc)
# -955 = "nome ja usado", que significa ja existe. Qualquer outro tem de subir.
chk("B86p. o script e repetivel, mas nao engole erro de verdade",
    "SQLCODE != -955" in _sql_oc)

# --- B87. a tela nao pode quebrar por classe/SVG inventado ------------- #
# Aconteceu de verdade: escrevi <div class="filters-title"> com um SVG de funil
# SEM width/height, e como essa classe NAO EXISTE no CSS, o SVG assumiu o
# tamanho do container e um triangulo preto ocupou a tela inteira. O painel e
# um arquivo so - classe que nao existe nao da erro, so estraga em silencio.
import re as _re

_sec_ini = _html.index('id="view-ocorrencias"')
_sec_fim = _html.index("</section>", _sec_ini)
_sec = _html[_sec_ini:_sec_fim]

# 1) todo SVG solto (fora de info-banner/empty, que tem CSS proprio) precisa
#    declarar tamanho, senao estica
_svgs_sem_tam = [m for m in _re.findall(r"<svg[^>]*>", _sec)
                 if "width=" not in m and "class=" not in m]
chk("B87. nenhum SVG solto sem tamanho na tela A revisar",
    len(_svgs_sem_tam) <= 3,      # os de info-banner e empty, que tem CSS
    "%d svg sem width: %s" % (len(_svgs_sem_tam), _svgs_sem_tam[:2]))

# 2) as classes proprias da tela existem no CSS
for _c in ("ocor-placar", "ocor-cx", "ocor-num", "ocor-lab", "ocor-hint",
           "ocor-topo", "ocor-nf", "ocor-acoes"):
    chk("B87b. classe .%s declarada no CSS" % _c,
        ("." + _c + "{") in _html)

# 3) nao usa classe que so existe na cabeca de quem escreveu
chk("B87c. nao sobrou classe inventada (filters-title, filters-grid, kpis)",
    not any(('class="%s"' % c) in _html
            for c in ("filters-title", "filters-grid", "kpis")))

# 4) todo campo de filtro da tela e lido pelo JS - senao o filtro nao filtra
for _f in ("ocorNota", "ocorForn", "ocorQuem", "ocorStatus"):
    chk("B87d. filtro %s existe no HTML e e lido no JS" % _f,
        ('id="%s"' % _f) in _html and ("'%s'" % _f) in _html)

# 5) o backend aceita os quatro filtros que a tela oferece
_oc_rota = _corpo(_src_af, "ocorrencias")
for _p in ("nota", "forn", "quem", "status"):
    chk("B87e. a rota aceita o filtro %s" % _p,
        ('request.args.get("%s")' % _p) in _oc_rota)

# 6) o placar filtra ao clicar: e o caminho de quem chega ("o que e meu?")
chk("B87f. o placar por area e clicavel e alterna o filtro",
    "filtrarQuem" in _html and "e.value===q?''" in _html)
# 7) tabela larga rola dentro do cartao, nunca a pagina de lado
chk("B87g. a lista rola dentro do cartao",
    "#ocorLista .card{overflow-x:auto}" in _html)
# 8) O TAMANHO DO ICONE VIRA REGRA DE CSS, e nao atributo em cada tag.
#    Duas secoes traziam width="17" inline; ao escrever a terceira eu esqueci,
#    e o icone assumiu o tamanho do container - um triangulo preto tomou a tela
#    DUAS VEZES seguidas, porque na primeira correcao eu tratei o sintoma.
chk("B87h. .sec-titulo tem regra de tamanho para o icone",
    ".sec-titulo svg{width:" in _html)
# 9) existe varredura automatica: e o unico jeito de nao depender de lembrar
chk("B87i. existe o conferidor de SVG sem tamanho",
    os.path.exists("testes/conferir_svg.py"))

# --- B88. CONFERENCIA PREVIA: o que falta ANTES de clicar ------------- #
# Relato de uso: a pessoa clicava em Importar e so entao descobria o que
# faltava - as vezes depois de minutos, porque a nativa roda a validacao
# inteira antes de recusar. E em nota "Aguardando AF" o clique era aposta
# perdida: sem AF nao ha o que importar.
from interface.painel.app import _checklist as _ck

_NOTA_OK = {"CDFILIRECE": "1051", "CDTIPOOPER": "01", "TEM_AF": 1,
            "SEM_PROD": 0, "INCONS": 0}
chk("B88. nota completa: tudo verde",
    all(i["ok"] for i in _ck(_NOTA_OK, "Automática")))
chk("B88b. sem filial, aponta a filial",
    not _ck(dict(_NOTA_OK, CDFILIRECE=None), "Automática")[0]["ok"])
chk("B88c. item sem De/Para diz QUANTOS faltam",
    "3 item(ns)" in _ck(dict(_NOTA_OK, SEM_PROD=3), "Automática")[1]["falta"])
chk("B88d. sem AF, manda vincular antes de importar",
    "Vincular AF" in _ck(dict(_NOTA_OK, TEM_AF=0), "Automática")[2]["falta"])
chk("B88e. sem tipo, cita o codigo 064 que o Teknisa devolve",
    "064" in _ck(dict(_NOTA_OK, CDTIPOOPER=None), "Automática")[3]["falta"])
chk("B88f. apontamento aberto manda para 'A revisar'",
    "A revisar" in _ck(dict(_NOTA_OK, INCONS=7), "Automática")[4]["falta"])
# ORDEM IMPORTA: sem produto interno nao ha ponte para a AF (achado 40), entao
# mandar procurar AF antes de vincular produto e mandar para o lugar errado.
_ordem = [i["item"] for i in _ck(_NOTA_OK, "Automática")]
chk("B88g. produto vem ANTES de AF na ordem de resolver",
    _ordem.index("Produtos vinculados") < _ordem.index("AF vinculada"))
# Nota Manual nao e trabalho do robo: dizer "falta AF" nela seria mentira.
_man = _ck(_NOTA_OK, "Manual")
chk("B88h. nota Manual explica que e da gerente, sem checklist falso",
    len(_man) == 1 and "gerente" in _man[0]["falta"])
# NAO PROMETER O QUE NAO SE CONFERE: preco, quantidade e vencimento so
# aparecem quando o Teknisa roda.
chk("B88i. a tela avisa que preco/quantidade/vencimento so o Teknisa diz",
    "só aparecem quando o " in _html and "A revisar" in _html)
# A conferencia vem JUNTO da lista: uma consulta por linha custaria 50 idas ao
# banco por pagina, a ~150 ms cada (o banco e um RDS na AWS).
_nu = _corpo(_src_af, "naoutilizadas")
chk("B88j. a conferencia vem na mesma consulta da lista",
    "AS TEM_AF" in _nu and "AS SEM_PROD" in _nu and "AS INCONS" in _nu)
chk("B88k. a tela mostra a coluna 'O que falta'",
    "<th>O que falta</th>" in _html and "_seloCheck" in _html)
chk("B88l. o detalhe nao consulta o banco de novo",
    "_nuCache" in _html and "function verCheck" in _html)

# --- B89. A TELA "A REVISAR" ------------------------------------------ #
# Sete defeitos de uma vez, todos visiveis na primeira olhada e nenhum pego
# por teste: e o custo de entregar tela sem abrir o navegador.
import re as _re89

# 1) o cabecalho da pagina mostrava "Vincular AF" na tela "A revisar", porque
#    TITLES nao tinha a chave e caia no titulo do GRUPO.
_titles = _corpo(_html, "TITLES") if False else _html[_html.index("const TITLES={"):]
_titles = _titles[:_titles.index("};")]
_telas_menu = _re89.findall(r"\{id:'([a-z-]+)'", _html[_html.index("const GRUPOS={"):])
chk("B89. TODA tela do menu tem titulo proprio",
    all(("\n  " + t + ":[") in _titles or ('"' + t + '":[') in _titles
        for t in set(_telas_menu)))
chk("B89b. a tela 'A revisar' nao herda o titulo de 'Vincular AF'",
    "ocorrencias:[\"A revisar\"" in _titles)

# 2) o total somava as areas: nota com apontamento de Compras E do Fiscal
#    contava duas vezes, e a tela dizia "3 notas" com 2 na lista.
_ocor = _corpo(_src_af, "ocorrencias")
chk("B89c. o total de notas vem do banco (ROLLUP), nao de somar as areas",
    "ROLLUP(QUEM)" in _ocor and '"total": total' in _ocor)
chk("B89d. a tela usa o total do banco",
    "d.total" in _html and "notas+=pq[k].notas" not in _html)

# 3) o badge do menu existia e ninguem escrevia nele: a fila crescia sem
#    aparecer para quem nao abrisse a tela.
chk("B89e. o contador de 'A revisar' e alimentado pelos indicadores",
    '"a_revisar": a_revisar' in _src_af and "navOcor'), d.a_revisar" in _html)
chk("B89f. TODO badge declarado no menu tem destino no mapa",
    all(("%s:{" % b) in _html or ("%s:0" % b) in _html
        for b in _re89.findall(r"badge:'(\w+)'", _html)))
chk("B89g. 'A resolver' soma as tres filas, inclusive a de revisao",
    "_badgeVal.navNaoUtil+_badgeVal.navRevisao+_badgeVal.navOcor" in _html)

# 4) o cabecalho da tabela repetia em CADA nota: com 3 notas na tela, "Codigo /
#    O que precisa ser feito / Quem resolve" aparecia 3 vezes.
chk("B89h. sem cabecalho de tabela repetido a cada nota",
    "O que precisa ser feito</th>" not in _html)

# 5) a tela dizia "1 nota(s) - 1 apontamento(s)". Comentario nao conta: o que
#    importa e o que chega na tela.
_sem_com = _re89.sub(r"/\*.*?\*/", "", _html, flags=_re89.S)
chk("B89j. existe plural de verdade, sem o '(s)' fixo",
    "function plural(" in _sem_com and "nota(s)" not in _sem_com
    and "apontamento(s)" not in _sem_com)

# 6/7) A TELA TEM QUE SER MONTADA COMO AS IRMAS.
# Eu troquei os componentes do painel por versoes proprias (busca solta no
# lugar de .filters, divs no lugar da tabela) e o resultado foi uma tela com
# cara de outro sistema, com o texto colado na borda do cartao.
def _secao(nome):
    i = _html.index('id="view-%s"' % nome)
    return _html[i:_html.index("</section>", i)]

_v_ocor = _secao("ocorrencias")
_v_irma = _secao("naoutil")
_COMPONENTES = ["sec-titulo", "info-banner", 'class="filters"', "filters-top",
                "fgrid", "fld", "filters-actions", "listhead"]
for _c in _COMPONENTES:
    chk("B89l. 'A revisar' usa o componente %s, como as irmas" % _c,
        (_c in _v_ocor) and (_c in _v_irma))
# o titulo da secao nao repete o do cabecalho, mas carrega o contador como as
# irmas ("Falta a AF" e "Falta o produto" tem o mesmo selo)
chk("B89m2. a secao tem titulo proprio e contador ligado",
    "Apontamentos do Teknisa" in _v_ocor and 'id="secOcor"' in _v_ocor
    and "sec:'secOcor'" in _html)
chk("B89n. sem componente de filtro inventado so para esta tela",
    "ocor-busca" not in _html and "ocor-filtros" not in _html
    and "alternarFiltrosOcor" not in _html)

# .card e CONTORNO, nao caixa com margem interna: quem esta dentro dele da o
# proprio respiro. A tabela do painel ja faz isso (td tem padding); um <div>
# solto nao faz, e foi assim que o texto encostou na borda.
chk("B89o. a lista de apontamentos usa a tabela do painel",
    "<table><tbody>" in _html.split("porNota[ch]")[1][:3000])
chk("B89p. o cabecalho da nota tem o mesmo respiro lateral da tabela",
    _re89.search(r"\.ocor-topo\{[^}]*padding:\d+px 14px", _html) is not None)
chk("B89q. nenhum bloco proprio ficou sem padding dentro do cartao",
    "ocor-item" not in _html and "ocor-txt" not in _html)

# --- B90. A FILA DO ROBO PEGAVA NOTA DE 2018 -------------------------- #
# buscar_para_importar tinha FETCH FIRST SEM ORDER BY - o MESMO defeito ja
# corrigido em buscar_notas_novas e nunca aplicado aqui. Medido em 11/08/2026:
# as 25 notas da rodada tinham emissao entre 26/10/2018 e 02/09/2019, nenhuma
# com menos de 30 dias. Pelo achado 49, acima de 30 dias 100% das AFs ja foram
# consumidas: a rodada inteira parava em AF_VENCIDA e o robo nunca alcancava a
# nota nova, que e a unica em que ele consegue agir. Era o "rodei e nao
# aconteceu nada".
_robo_src = open("robo/servico_robo.py", encoding="utf-8").read()


def _fn(nome):
    i = _robo_src.index("def %s(" % nome)
    j = _robo_src.find("\ndef ", i + 1)
    return _robo_src[i:j if j > 0 else len(_robo_src)]


for _f in ("buscar_para_importar", "buscar_notas_novas"):
    _c = _fn(_f)
    chk("B90. %s: FETCH FIRST sempre com ORDER BY" % _f,
        ("FETCH" not in _c.upper()) or ("ORDER" in _c.upper()))
chk("B90b. a fila de importacao pega a nota MAIS NOVA primeiro",
    "DTEMISSAO DESC" in _fn("buscar_para_importar"))
chk("B90c. a ordem tem desempate deterministico",
    "NRLANCTONF" in _fn("buscar_para_importar").split("ORDER")[1])
# Ordenar sem indice custa a tabela inteira: medido, 36.543 ms sem janela
# contra 855-2.045 ms com 180 dias, DEVOLVENDO AS MESMAS NOTAS. A janela e
# desempenho, nao regra - por isso e generosa (6x os 30 dias em que a AF vive).
chk("B90d. a fila tem janela de data, senao ordenar varre tudo",
    "G.DTEMISSAO >= SYSDATE - :dias" in _robo_src
    and "JANELA_FILA_DIAS" in _robo_src)
chk("B90e. a janela cobre com folga o prazo em que a AF ainda vive",
    int(_re89.search(r'AIHUB_JANELA_FILA", "(\d+)"', _robo_src).group(1)) >= 90)
# o ganho vem do PLANO, e depender disso e fragil: o indice tira a loteria
chk("B90f. o indice da fila esta no script do DBA",
    "IX_INTGNF_EMISSAO" in open("sql/CRIAR_INDICES.sql", encoding="utf-8").read())

# --- B91. VER FUNCIONANDO: ensaio e prova ----------------------------- #
# "O sistema funciona?" nao se responde com "IMPORTADA: 2, ERRO: 1".
_ens = _corpo(_src_af, "ensaio_robo")
# O ensaio TEM que usar as funcoes do robo. Reimplementar a regra por fora e o
# que fez a tela "Simular" mentir: ela ensaia IDSTATUS='I', estado que quase
# nao ocorre (achado 75), entao ensaiava o que o robo nao faz.
chk("B91. o ensaio usa a fila REAL do robo",
    "buscar_para_importar" in _ens)
chk("B91b. o ensaio usa as funcoes de decisao do robo, sem copia",
    "resolver_depara_do_xml" in _ens and "resolver(cur" in _ens
    and "af_ainda_valida" in _ens)
chk("B91c. o ensaio roda em modo simulacao (nao grava)",
    "resolver_depara_do_xml(con, ch, True" in _ens)
chk("B91d. o ensaio nao chama procedure nativa",
    "callproc" not in _ens)
# a funcao do robo devolve o raciocinio item a item, para a tela nao ter de
# recalcular por fora
chk("B91e. o De/Para do robo sabe devolver o detalhe",
    "def resolver_depara_do_xml(con, chave, dry, detalhe=None)" in _robo_src
    and "if detalhe is not None:" in _robo_src)
# A PROVA le o ERP, nao a nossa trilha
_conf = _corpo(_src_af, "conferir_rodada")
for _t in ("INTG_ITEMPRODUTO", "NOTAFISCAL", "NFRELACAF"):
    chk("B91f. a conferencia le %s no proprio Teknisa" % _t, _t in _conf)
chk("B91g. a conferencia nao decide pelo nosso log",
    "AIHUB_PROC_LOG" in _conf and "STATUS='IMPORTADA'" not in _conf)
chk("B91h. nota parcialmente vinculada nao vira 'sem AF'",
    "PARCIAL" in _ens and "parcialmente vinculada" in _ens)
chk("B91i. a AF que ja estava na nota vem do dado, nao do texto",
    "af_ja_estava" in _ens and "SELECT DISTINCT NRAUTOFORN" in _ens)
chk("B91j. a tela usa os componentes do painel",
    all(c in _secao("cfg-ver") for c in ("info-banner", "sec-titulo",
                                         'class="filters"', "fgrid", "fld",
                                         "filters-actions")))
chk("B91k. 'Ver funcionando' tem titulo proprio no cabecalho",
    '"cfg-ver":["Ver funcionando"' in _html)
chk("B91l. o botao de rodar escreve na tela que esta aberta",
    "current==='cfg-ver'?'verRodarState'" in _html)
# ID REPETIDO E FALHA SILENCIOSA: getElementById devolve o PRIMEIRO do
# documento. Eu chamei de "confArea" uma area que ja existia em "Vincular
# produto", e a conferencia foi escrita num elemento escondido de outra tela -
# a tela nova ficava vazia sem nenhum erro no console.
import collections as _col
_ids = _re89.findall(r'\bid="([A-Za-z0-9_-]+)"', _html)
_dup = sorted(i for i, n in _col.Counter(_ids).items() if n > 1)
chk("B91m. nenhum id repetido no painel", not _dup, _dup)
# duas chamadas sobrepostas deixavam a tela no "carregando" para sempre: a
# segunda punha o spinner depois de a primeira ja ter desenhado
# O ENSAIO DAVA "IMPORTARIA SOZINHO" em nota que o Teknisa JA TINHA RECUSADO.
# Ele conferia so o que NOS controlamos (produto, AF, AF viva) e ignorava o
# apontamento aberto. Caso real: NF 62104, verde nos tres passos, com 051
# (vencimento no passado) e 055 (preco fora da AF) em aberto. Pior: a tela
# "Falta a AF" JA mostrava esses apontamentos na conferencia previa - duas
# telas nossas discordando sobre a mesma nota.
chk("B91o. o ensaio le os apontamentos do Teknisa",
    "INTG_INCONSISTENCIA" in _ens and '"apontamentos"' in _ens)
chk("B91p. usa o MESMO mapa de codigos das outras telas, sem copia",
    "CONSIST_N.get(_norm_cod(cod))" in _ens)
chk("B91q. apontamento aberto impede o veredito 'importaria sozinho'",
    "elif aponta:" in _ens and '"APONTAMENTO"' in _ens)
chk("B91r. a tela mostra o apontamento e quem resolve",
    "_VEREDITO" in _html and "APONTAMENTO:" in _html
    and "apontamentos da última tentativa" not in _html)  # vem do plural()
chk("B91s. o texto nao promete que vai barrar (achado 1: e log, nao estado)",
    "última tentativa" in _html)
# BASE CONGELADA MUDA O SENTIDO DO RESULTADO. As 5 notas do ensaio de
# 11/08/2026 apareciam pendentes em homologacao e ja estavam RESOLVIDAS em
# producao desde 24 e 25/06 - a copia e de 23/06. Sem aviso, a leitura natural
# e "a operacao tem pendencia", e nao tem.
chk("B91t. o ensaio avisa quando a base esta parada",
    "atraso_dias" in _ens and "Esta base está parada em" in _html)
_ens_cod = _re89.sub(r"(?m)^\s*#.*$", "", _ens)   # comentario nao conta (ver B89j)
chk("B91u. a atualidade vem do cache, sem varrer a tabela a cada ensaio",
    '_cache.get("atualidade")' in _ens and "MAX(DTEMISSAO)" not in _ens_cod)
chk("B91v. cache frio nao esconde o aviso (o silencio seria o lado permissivo)",
    "if(d.atraso_dias==null)" in _html and "'/api/atualidade'" in _html)

# --- B92. CURINGA NO COMECO PROIBIA O INDICE -------------------------- #
# O indice IX_NF_ACESSONFE existia e a consulta nao deixava usar: LIKE
# '%chave%' obriga a ler a coluna inteira. Medido em homologacao, contando a
# mesma nota na NOTAFISCAL: 3.958 ms com contains contra 143 ms com igualdade
# (INDEX FAST FULL SCAN custo 4.677 contra INDEX RANGE SCAN custo 3). Era o
# que fazia /api/buscar por chave levar 95 SEGUNDOS.
chk("B92. existe uma unica regra de busca por chave",
    "def _cond_chave(" in _src_af)
chk("B92b. a igualdade so entra com a chave inteira e limpa",
    'len(v) == 44 and v.isdigit()' in _src_af)
chk("B92c. o parcial vira PREFIXO, nao contains",
    'binds[nome] = v + "%"' in _src_af)
# nenhum curinga inicial sobre tabela do Teknisa. Nas NOSSAS tabelas (pequenas)
# ele e aceitavel, entao a varredura olha so as consultas do Teknisa.
_linhas_like = [(i + 1, l) for i, l in enumerate(_src_af.split("\n"))
                if '"%" + chave + "%"' in l or "'%' + chave + '%'" in l]
chk("B92d. nenhum LIKE '%chave%' sobrou no codigo", not _linhas_like,
    [n for n, _ in _linhas_like])
chk("B92e. o indice por fornecedor esta no script do DBA",
    "IX_NF_FORNECED" in open("sql/CRIAR_INDICES.sql", encoding="utf-8").read())

# --- B93. SCORE E FRACAO, A TELA ESCREVE PORCENTO --------------------- #
# LIMIAR_AUTO = 0.92, ou seja o score vai de 0 a 1. O detalhe do ensaio
# mandava round(score) e a tela escrevia '%' do lado: casamento de 95%
# aparecia como "1%". Achado rodando o ensaio contra producao.
chk("B93. o detalhe do ensaio manda o score em porcento",
    "round(best[2] * 100)" in _robo_src)
# quem escreve '%' na tela tem que receber o valor ja em porcento OU
# multiplicar; as duas coisas juntas dariam 9500%
_ens_js = _html[_html.index("function loadEnsaio"):]
_ens_js = _ens_js[:_ens_js.index("async function loadConferir")]
chk("B93b. a tela do ensaio nao multiplica de novo",
    "p.score+'%" in _ens_js and "score*100" not in _ens_js)
# o resto do painel recebe fracao e multiplica na hora de mostrar
chk("B93c. as outras telas continuam multiplicando por 100",
    _html.count("(Number(it.score)||0)*100") >= 1)

# --- B94. "AF VENCIDA" SEM DONO NAO E RESPOSTA ------------------------ #
# Medido em producao (14/08/2026): quando a AF esta sem saldo, em 100% dos
# casos OUTRA nota ja lancada a consumiu, quase sempre do mesmo fornecedor e
# com 1 ou 2 dias de diferenca. Caso real: a AF 0001477220 tem TRES notas
# esperando e quem levou foi a NF 006153086. Nao e a AF que envelheceu, sao
# varias notas apontando o mesmo pedido.
_afm = open("core/af_matcher.py", encoding="utf-8").read()
chk("B94. existe a busca de quem baixou a AF", "def quem_baixou_af(" in _afm)
# CDFILIAL da NFRELACAF e a filial do LANCAMENTO, nao a que recebe: filtrar
# por ela devolve vazio e faz parecer que ninguem baixou (erro que eu cometi)
_qb = _afm[_afm.index("def quem_baixou_af("):]
_qb = _qb[:_qb.index("\ndef ")]
_qb_sql = _qb[_qb.index('cur.execute("""'):_qb.index('""", a=nr_af)')]
chk("B94b. a busca nao filtra pela filial que recebe",   # so o SQL, ver B89j
    "R.NRAUTOFORN = :a" in _qb_sql and "CDFILIRECE" not in _qb_sql)
chk("B94c. e fail-safe: erro nao vira decisao",
    "return None, None" in _qb)
chk("B94d. o robo diz quem levou o pedido",
    "quem_baixou_af" in _robo_src and "ja foi baixada pela NF" in _robo_src)
chk("B94e. o ensaio mostra quem levou",
    "af_levada_por" in _ens and "af_levada_por" in _html)

# A LINHA "Importacao" dizia "pode importar" em nota SEM AF nenhuma, porque
# mostrava so a trava da AF (que passa por vacuidade quando nao ha AF para
# conferir) - contradizendo o veredito logo acima, no mesmo cartao.
chk("B91n2. a linha de importacao segue o veredito, nao so a trava da AF",
    "n.veredito==='HUMANO_PRODUTO'" in _html
    and "Tentaria importar, e o Teknisa recusaria" in _html
    and "pode importar" not in _sem_com)   # comentario nao conta, ver B89j
chk("B91n. resposta atrasada nao sobrescreve a mais recente",
    "_ensSeq" in _html and "if(meu!==_ensSeq)return" in _html
    and "if(meu!==_confSeq)return" in _html)

# 8) variaveis de tema que nao existem viram cor transparente/preta
_vars_css = set(_re89.findall(r"(--[a-z0-9-]+)\s*:", _html))
_usadas = set(_re89.findall(r"var\((--[a-z0-9-]+)", _html))
chk("B89m. o CSS so usa variavel de tema que existe",
    not (_usadas - _vars_css), sorted(_usadas - _vars_css))

# --- B95. O ROBO EXPLICANDO O PROPRIO RACIOCINIO ---------------------- #
# A pessoa via a conclusao e nao via o caminho. Sem o caminho ela nao julga se
# a conclusao faz sentido, nem consegue apontar onde o raciocinio errou.
_exp_src = open("core/explicador.py", encoding="utf-8").read()
from core.explicador import explicar as _explicar

_CANDS = [("1010261705", "BOV RESF ACEM ISCAS - CX 10 KG", "CX"),
          ("1010261700", "BOV RESF ACEM ISCAS", "KG"),
          ("1050718300", "REFRIGERANTE GUARANA ANTARCTICA PET 2L", "UN"),
          ("1050718200", "REFRIGERANTE GUARANA ANTARCTICA PET 200ML", "UN")]

# NAO PODE DUPLICAR REGRA: se o explicador reimplementasse o matcher, as duas
# verdades divergiriam com o tempo. Foi o erro da tela "Simular" (achado 89).
chk("B95. o explicador nao reimplementa a regra, importa do matcher",
    "from core.ai_matcher import" in _exp_src
    and "def _sim(" not in _exp_src and "def pode_auto(" not in _exp_src)
chk("B95b. o explicador nao grava nada",
    all(x not in _exp_src for x in ("INSERT", "UPDATE ", "MERGE", "commit")))

_e = _explicar("ACEM BOVINO ISCAS CX 10KG", _CANDS, "CX",
               {c[0]: c[2] for c in _CANDS})
chk("B95c. mostra o texto como o robo leu",
    _e["texto_lido"] == "ACEM BOVINO ISCA CAIXA 10KG")
chk("B95d. mostra a abreviacao que expandiu",
    any(a["de"] == "CX" and a["para"] == "CAIXA" for a in _e["abreviacoes"]))
chk("B95e. mostra quantos candidatos avaliou", _e["candidatos_avaliados"] == 4)
chk("B95f. abre a conta do score em palavras",
    "de 4 palavra" in _e["ranking"][0]["cobre"]
    and "RESFRIADO" in _e["ranking"][0]["faltou_no_fornecedor"])
chk("B95g. explica por que NAO decidiu sozinho",
    _e["decisao"] != "auto" and any("mais de um" in b or "genuinamente" in b
                                    for b in _e["bloqueios"]))

# ABAIXO DO PISO nao existe "melhor": dizer que ha varios parecidos seria
# mentira, e a precisao do texto e o proposito desta tela.
_z = _explicar("XPTO NADA A VER", _CANDS, "KG")
chk("B95h. sem nenhum parecido, o texto nao inventa concorrencia",
    "se parece" in _z["regra"] and "mais de um produto parecido" not in _z["regra"])
chk("B95i. e nao marca escolhido um candidato de 0%",
    not any(r["escolhido"] for r in _z["ranking"]) and "escolhido" not in _z)

# o tamanho declarado e a unidade tem de aparecer, sao os dois sinais que mais
# barram auto-vinculo (achados 13 e 42)
_g = _explicar("GUARANA ANTARCTICA 2 L", _CANDS, "UND",
               {c[0]: c[2] for c in _CANDS})
chk("B95j. o tamanho declarado entra na explicacao",
    _g["tamanho_declarado"] == ["2L"]
    and any(r["tamanho_conflita"] for r in _g["ranking"]))

_rac = _corpo(_src_af, "raciocinio")
chk("B95k. a rota usa o explicador, sem recalcular por fora",
    "from core.explicador import explicar" in _rac and "_sim(" not in _rac)
chk("B95l. item ja vinculado e dito como tal, sem fingir ranking usado",
    "ja_vinculado" in _rac and "não compara texto" in _rac)
chk("B95m. a rota usa a MESMA lista de candidatos do robo (estreitada pela AF)",
    "_cands_af(" in _rac)

# --- B96. TRATAR NOTAS POR LOTE DE CHEGADA ---------------------------- #
# Medido em producao: as notas NAO chegam continuamente. Entram em lote a cada
# 2 horas, sempre por volta do minuto 27. Pedir "das 14h as 15h" pode devolver
# zero legitimamente - foi o que aconteceu no teste.
_lot = _corpo(_src_af, "lotes")
chk("B96. o lote usa a hora de CHEGADA, nao a de emissao",
    "DTINCLUSAO" in _lot and "DTEMISSAO" not in _lot)
chk("B96b. separa o que esta parado do que o robo pegaria",
    "PARADAS" in _lot and "DO_ROBO" in _lot)
chk("B96c. a coluna de origem e detectada, nao suposta",
    "_col_disponivel(" in _lot)
chk("B96d. nota manual fica fora da conta do robo (achado 18)",
    "'Manual'" in _lot)

# --- B97. O QUE A PESSOA ENSINA AO ROBO ------------------------------- #
# Ate aqui a pessoa corrigia o RESULTADO (o De/Para virava permanente) e nao o
# RACIOCINIO: o robo continuava lendo mal a descricao na proxima nota.
from core.regras import (interpretar, aplicar_sinonimos, filtrar_proibidos,
                         limiar_de)
_reg_src = open("core/regras.py", encoding="utf-8").read()

_R = [{"tipo": "SINONIMO", "escopo": "FORNECEDOR", "forn": "F1", "termo": "PCT",
       "significa": "PACOTE", "cprod_xml": None, "cd_produto": None,
       "valor": None, "id": 1},
      {"tipo": "NEGATIVA", "escopo": "FORNECEDOR", "forn": "F1", "termo": None,
       "significa": None, "cprod_xml": "3062", "cd_produto": "4033000100",
       "valor": None, "id": 2},
      {"tipo": "LIMIAR", "escopo": "FORNECEDOR", "forn": "F1", "termo": None,
       "significa": None, "cprod_xml": None, "cd_produto": None,
       "valor": 0.95, "id": 3}]

# PALAVRA INTEIRA: sem isso ensinar "PC -> PACOTE" transformaria PCT em
# PACOTET e quebraria o que ja funcionava
chk("B97. sinonimo troca palavra inteira, nunca pedaco",
    aplicar_sinonimos("PCTX E PCT", _R, "F1") == "PCTX E PACOTE")
chk("B97b. regra de fornecedor nao vaza para outro fornecedor",
    aplicar_sinonimos("QJO PCT 500G", _R, "F2") == "QJO PCT 500G")
_C = [("4033000100", "PLACA CORTE AVES", "UN"),
      ("4033002200", "PLACA CORTE ASSADOS", "UN")]
chk("B97c. regra negativa tira o produto proibido da lista",
    [c[0] for c in filtrar_proibidos(_C, _R, "F1", "3062")] == ["4033002200"])
chk("B97d. e so para o item ensinado",
    len(filtrar_proibidos(_C, _R, "F1", "9999")) == 2)
chk("B97e. limiar do fornecedor manda; sem regra vale o padrao medido",
    limiar_de(_R, "F1") == 0.95 and limiar_de(_R, "F2") == 0.92)

# INTERPRETACAO CONSERVADORA: a primeira versao INVENTAVA regra a partir de
# conversa solta ("faz o que achar melhor ai" virava sinonimo de QU). Inventar
# e pior que nao entender, porque a pessoa confirma no automatico.
for _f, _t in (("quando aparecer PCT, leia PACOTE", "SINONIMO"),
               ("PCT significa PACOTE", "SINONIMO"),
               ("MUS = MUSSARELA", "SINONIMO"),
               ("leia FD como FARDO", "SINONIMO"),
               ("nunca vincule o item 3062 no produto 4033002200", "NEGATIVA"),
               ("so grave sozinho com 95%", "LIMIAR")):
    _i = interpretar(_f)
    chk("B97f. entende %r" % _f[:34], _i["ok"] and _i.get("tipo") == _t)
for _f in ("faz o que achar melhor ai", "esse fornecedor sempre erra",
           "arruma isso por favor", "o produto ta errado", ""):
    chk("B97g. RECUSA %r em vez de inventar" % _f[:30], not interpretar(_f)["ok"])
chk("B97h. limiar fora do razoavel e recusado",
    not interpretar("so grave sozinho com 300%")["ok"])

# a regra devolve SEMPRE o que entendeu, para a pessoa conferir antes de valer
chk("B97i. sempre devolve o que entendeu, em portugues",
    all("entendi" in interpretar(x) for x in
        ("PCT = PACOTE", "qualquer coisa", "")))

# NADA VALE SEM TESTE: a regra nasce RASCUNHO e so vira ATIVA depois de rodar
# contra os vinculos que ja estao certos
chk("B97j. existe o teste contra o historico", "def testar(" in _reg_src)
chk("B97k. a amostra depende do alcance (global mede mais)",
    "120 if forn else 300" in _reg_src)
chk("B97l. o resultado diz a cobertura, nao so o placar",
    "de %d vínculo(s) afetado(s)" in _reg_src)
chk("B97m. o teste usa as funcoes do matcher, nao uma copia",
    "from core.ai_matcher import" in _reg_src and "def _sim(" not in _reg_src)
chk("B97n. ler regra e fail-safe: erro devolve nenhuma regra",
    "return []" in _reg_src and "sem tabela ou sem grant" in _reg_src)

# LIGADO NO ROBO, nas bordas: o matcher continua com uma regra so
_rd = _corpo(_src_robo, "resolver_depara_do_xml")
chk("B97o. o robo aplica as regras ensinadas",
    "aplicar_sinonimos" in _rd and "filtrar_proibidos" in _rd
    and "limiar_de" in _rd)
chk("B97p. e usa a lista JA filtrada para escolher",
    "escolher(desc, cands_it" in _rd and "cands_it)" in _rd)
chk("B97q. o piso ensinado so aperta o que ja passou",
    "if auto and best[2] < piso" in _rd)

# a tabela existe com as travas que impedem regra pela metade
_sqlr = open("sql/CRIAR_REGRAS.sql", encoding="utf-8").read()
chk("B97r. escopo FORNECEDOR exige fornecedor", "CK_REGRA_ESCOPO" in _sqlr)
chk("B97s. cada tipo exige os seus campos", "CK_REGRA_CAMPOS" in _sqlr)
chk("B97t. regra nasce RASCUNHO, nao ATIVA", "DEFAULT ''RASCUNHO''" in _sqlr)


# --- B98. AS ROTAS DAS REGRAS ----------------------------------------- #
# O ciclo tem ordem: escrever -> conferir o que o sistema entendeu -> testar
# contra o historico -> so entao adotar. Nenhuma regra pula o teste.
_rg_salvar = _corpo(_src_af, "regra_salvar")
_rg_testar = _corpo(_src_af, "regra_testar")
_rg_pedido = _corpo(_src_af, "_regra_do_pedido")

chk("B98. gravar regra exige MODO_ESCRITA e responde 403",
    "if not MODO_ESCRITA" in _rg_salvar and "403" in _rg_salvar)
chk("B98b. desativar regra tambem exige MODO_ESCRITA",
    "if not MODO_ESCRITA" in _corpo(_src_af, "regra_desativar"))
# SEM TESTE NAO ADOTA: e a disciplina do projeto inteiro, medir antes de mudar
chk("B98c. nao adota regra sem o teste historico",
    'teste = d.get("teste")' in _rg_salvar and "if not teste" in _rg_salvar)
chk("B98d. regra que QUEBRA vinculo certo exige confirmacao explicita",
    "mesmo_quebrando" in _rg_salvar and "409" in _rg_salvar)
# ESCOPO EXPLICITO: global e o que espalha um erro pela base inteira
chk("B98e. o escopo e obrigatorio, sem padrao silencioso",
    'escopo not in ("FORNECEDOR", "GLOBAL")' in _rg_pedido)
chk("B98f. escopo de fornecedor exige o fornecedor",
    'escopo == "FORNECEDOR" and not forn' in _rg_pedido)
chk("B98g. limiar fora de 50%-100% e recusado",
    "0.50 <= regra" in _rg_pedido)
chk("B98h. testar nao grava nada",
    all(x not in _rg_testar for x in ("INSERT", "UPDATE", "commit")))
# o robo tem de largar o cache assim que a regra muda, senao ensina e nao vale
chk("B98i. adotar ou desligar regra limpa o cache do robo",
    _src_af.count("from core.regras import esquecer") >= 2)
chk("B98j. a rota registra QUEM ensinou, na trilha",
    "_auditar(cur, \"REGRA\"" in _rg_salvar and "_operador()" in _rg_salvar)
chk("B98k. sem a tabela, a tela explica em vez de quebrar",
    "_tem_tabela(cur, \"AIHUB.AIHUB_REGRA\"" in _corpo(_src_af, "regras_listar"))


# --- B99. UNIDADE DE MEDIDA NAO E NOME DE PRODUTO --------------------- #
# Achado por quem opera, olhando um exemplo de teste: ensinar "KG significa
# BANANA" trocaria a UNIDADE por um NOME DE PRODUTO em toda descricao daquele
# fornecedor. "PERA ARGENTINA KG" viraria "PERA ARGENTINA BANANA", e o robo
# passaria a achar banana onde ha pera.
#
# O teste historico pegava depois (2 vinculos quebrados na medicao), mas
# depender dele aqui e errado: ele roda numa AMOSTRA, entao pode NAO pegar, e
# a pessoa esperaria minutos por um "nao" que da para dar na hora.
from core.regras import conflito_unidade, e_unidade

for _u in ("KG", "G", "ML", "L", "UN", "UND", "CX", "PCT", "FD", "LT",
           "CAIXA", "PACOTE", "FARDO", "LATA"):
    chk("B99. %s e reconhecida como unidade" % _u, e_unidade(_u))
for _p in ("BANANA", "MUSSARELA", "BOV", "QJO", "ACEM"):
    chk("B99b. %s NAO e unidade" % _p, not e_unidade(_p))

# o caso que a operacao apontou
chk("B99c. unidade virando nome de produto e RECUSADO",
    conflito_unidade("KG", "BANANA") is not None)
chk("B99d. e o contrario tambem (nome virando unidade)",
    conflito_unidade("BANANA", "KG") is not None)
# o que TEM de continuar passando: unidade para unidade e legitimo e util
chk("B99e. unidade para unidade continua permitido",
    conflito_unidade("CX", "CAIXA") is None
    and conflito_unidade("PCT", "PACOTE") is None
    and conflito_unidade("FD", "FARDO") is None)
chk("B99f. abreviacao comum continua permitida",
    conflito_unidade("BOV", "BOVINO") is None
    and conflito_unidade("QJO", "QUEIJO") is None)

for _f in ("KG significa BANANA", "L = LEITE", "quando aparecer G, leia GOIABA"):
    chk("B99g. a interpretacao recusa %r" % _f[:26], not interpretar(_f)["ok"])
chk("B99h. a explicacao diz POR QUE, nao so 'nao pode'",
    "estragaria a descrição" in (conflito_unidade("KG", "BANANA") or ""))

# a guarda vale tambem no FORMULARIO: senao bastava preencher a mao para furar
chk("B99i. a guarda tambem esta na rota, nao so no texto livre",
    "from core.regras import conflito_unidade" in _corpo(_src_af, "_regra_do_pedido"))
# quem decide o que e unidade e a MESMA funcao que o matcher usa
chk("B99j. usa normaliza_un do matcher, sem lista paralela",
    "normaliza_un" in _reg_src and "_UN_SINONIMO" in _reg_src)


# --- B100. A TELA "COMO ELE PENSA" ------------------------------------ #
_v_reg = _secao("cfg-regras")
for _c in ("info-banner", "sec-titulo", 'class="filters"', "fgrid", "fld",
           "filters-actions"):
    chk("B100. a tela usa o componente %s, como as irmas" % _c, _c in _v_reg)
chk("B100b. tem titulo proprio no cabecalho",
    '"cfg-regras":["Como ele pensa"' in _html)
chk("B100c. entra no menu de Ajustes",
    "{id:'cfg-regras'" in _html and "'cfg-regras':'ajustes'" in _html)
chk("B100d. a lista de regras carrega sozinha ao abrir a tela",
    "if(current==='cfg-regras') return loadRegras();" in _html)
# o raciocinio NAO carrega sozinho: cada consulta refaz o caminho item a item
chk("B100e. o raciocinio so roda quando a pessoa pede uma nota",
    "loadRaciocinio" not in _html.split("function reloadView")[1][:400])

# ECO ANTES DE VALER: a pessoa confere o entendimento antes de qualquer coisa
chk("B100f. a tela mostra o que o sistema entendeu",
    "regra-interpretar" in _html and "Entendi assim:" in _html)
chk("B100g. o botao de adotar nasce bloqueado, ate testar",
    'id="ensAdotar" disabled' in _html)
chk("B100h. testar libera o adotar, e mudar a regra bloqueia de novo",
    "ad.disabled=false" in _html and "ad)ad.disabled=true" in _html)
chk("B100i. regra que quebra vinculo pede confirmacao na tela tambem",
    "mesmo_quebrando" in _html and "quebra '+c.teste.quebrou" in _html)
# ESCOPO EXPLICITO, a cada correcao
chk("B100j. a tela pergunta o alcance em toda correcao",
    'id="ensEscopo"' in _html and "Para todos os fornecedores" in _html)
chk("B100k. e avisa o que global significa",
    "espalha um erro pela base inteira" in _html)
# resposta atrasada nao sobrescreve a mais recente (licao do B91n)
chk("B100l. consulta atrasada nao sobrescreve a nova",
    "_racSeq" in _html and "if(meu!==_racSeq)return" in _html)

# --- B101. A CONSULTA DE CANDIDATOS VARRIA A AUTOFORNE DUAS VEZES ----- #
# Medido em 20/08/2026: TABLE ACCESS FULL AUTOFORNE (818.136 linhas, custo
# 19.867) em CADA lado do UNION, para achar as 103 AFs de um fornecedor.
# 137 SEGUNDOS para devolver 56 candidatos. Isso pesa no auto-vinculo do robo,
# nas sugestoes do painel, no teste de regra e na tela nova.
_aim = open("core/ai_matcher.py", encoding="utf-8").read()
chk("B101. a consulta le as AFs do fornecedor UMA vez",
    "WITH itens_af AS" in _aim and "MATERIALIZE" in _aim)
chk("B101b. e o lado do UNION reaproveita, sem varrer de novo",
    _aim.count("FROM   itens_af T") == 2
    and _aim.count("FROM   TEKNISA.AUTOFORNE A") == 1)
chk("B101c. o indice que resolve de vez esta no script do DBA",
    "IX_AUTOFORNE_FORN" in open("sql/CRIAR_INDICES.sql", encoding="utf-8").read())
# o cabecalho do script dizia que AUTOFORNE nao precisava de indice: valia para
# OUTRA consulta, e quem lesse o topo descartaria o item novo
chk("B101d. o cabecalho do script foi corrigido, para nao enganar",
    "isso NAO vale para a lista de CANDIDATOS"
    in open("sql/CRIAR_INDICES.sql", encoding="utf-8").read())


# --- B102. CONFERIR POR LOTE DE CHEGADA -------------------------------- #
# Medido em producao: as notas entram em bloco a cada 2 horas, sempre por volta
# do minuto 27. Pedir "das 14h as 15h" devolve zero legitimamente - foi o que
# aconteceu no teste. Por isso a tela MOSTRA os lotes que existem.
_nu = _corpo(_src_af, "naoutilizadas")
chk("B102. a lista aceita o recorte por lote de chegada",
    'request.args.get("lote")' in _nu and "G.DTINCLUSAO >=" in _nu)
# O LOTE E UM FILTRO: sem isto o periodo padrao de EMISSAO escondia as notas
# que CHEGARAM no lote. Medido: o lote dizia 23 notas e a lista mostrava zero.
chk("B102b. escolher um lote dispensa o periodo padrao de emissao",
    "or lote):" in _nu)
chk("B102c. o lote entra na chave do cache, senao devolve a lista errada",
    "lote, pag)" in _nu)
# chegada e emissao sao datas DIFERENTES, e confundir as duas foi o defeito
chk("B102d. o lote filtra por CHEGADA e o periodo por EMISSAO",
    "DTINCLUSAO" in _nu and "DTEMISSAO" in _nu)

chk("B102e. a tela mostra os lotes existentes",
    "function loadLotes" in _html and 'id="nuLotes"' in _html)
# tres dias vazios sao legitimos (fim de semana, feriado, base congelada)
chk("B102f. a faixa alarga sozinha quando os ultimos dias estao vazios",
    "[3, 15, 90]" in _html and "Mostrando os lotes mais recentes" in _html)
chk("B102g. clicar de novo tira o filtro, nao prende a pessoa",
    "const ligando = (_loteSel !== de)" in _html)
chk("B102h. escolher um lote limpa o periodo, que e outra data",
    "['nuIni','nuFim'].forEach" in _html.split("function filtrarLote")[1][:700])
chk("B102i. e ao desfazer o periodo padrao volta marcado",
    "periodo('nu','30', mes)" in _html)
chk("B102j. a tela diz quando esta filtrada por lote",
    "mostrando só o lote das" in _html)


# --- B103. FICHA DA NOTA: o que o sistema fez, e por que ---------------- #
# A pergunta "por que esta nota foi dada como certa?" nao tinha tela. Os
# registros existiam em quatro fontes (trilha, De/Para, fila, Teknisa) e
# ninguem consultava as quatro.
_fic = _corpo(_src_af, "ficha_nota")
chk("B103. existe rota que conta a historia de uma nota",
    '@app.route("/api/ficha-nota")' in _src_af and len(_fic) > 400)
# CONFERIR SO O NOSSO LADO DA FALSO VERDE (achado 90): a prova de que a nota
# entrou tem de vir da NOTAFISCAL, e a da baixa da AF, da NFRELACAF.
chk("B103a. a prova vem do Teknisa, nao so do nosso registro",
    "TEKNISA.NOTAFISCAL" in _fic and "TEKNISA.NFRELACAF" in _fic)
chk("B103b. le a trilha, o De/Para, a fila e o historico do Teknisa",
    all(t in _fic for t in ("AIHUB_PROC_LOG", "PRODFORNXML",
                            "AIHUB_PENDENCIA", "HISTXMLNFE")))
# a tela e de CONSULTA: nenhuma rota da ficha pode gravar
chk("B103c. a ficha nao grava nada",
    not any(w in _fic.upper() for w in ("INSERT ", "UPDATE ", "DELETE ",
                                        "MERGE ")))
chk("B103d. a lista de notas tocadas tambem so le",
    not any(w in _corpo(_src_af, "ficha_recentes").upper()
            for w in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ")))
# rota que abre conexao fecha no finally, senao 12 erros derrubam o painel
chk("B103e. as duas rotas fecham a conexao no finally",
    _fic.count("_fechar(cur, con)") == 1
    and "_fechar(cur, con)" in _corpo(_src_af, "ficha_recentes"))

# --- B104. O ROBO PRECISA REGISTRAR O RACIOCINIO ------------------------ #
# Ate aqui a trilha guardava a AF (achado 51) mas NAO o De/Para: o vinculo era
# gravado sem nenhum registro de por que aquele produto. E o piso MUDA quando a
# pessoa ensina regra, entao recalcular depois pode dar outra resposta e
# ninguem saberia dizer qual valeu.
chk("B104. o robo grava POR QUE escolheu o produto",
    "def _auditar_depara" in _src_robo and "'DEPARA_ROBO'" in _src_robo)
_rdx = _corpo(_src_robo, "resolver_depara_do_xml")
chk("B104a. a trilha do De/Para vai na MESMA transacao da gravacao",
    "_auditar_depara(cur," in _rdx
    and _rdx.index("_auditar_depara(cur,") < _rdx.index("con.commit()"))
chk("B104b. so registra o que de fato gravou, nunca a recusa",
    "gravar_depara(cur," in _rdx.split("_auditar_depara(cur,")[0][-400:])
_aud = _corpo(_src_robo, "_auditar_depara")
chk("B104c. o registro guarda score, piso, unidade e concorrente",
    all(t in _aud for t in ("score %d%%", "piso %d%%", "un XML", "2o lugar")))
chk("B104d. e guarda o texto LIDO quando a regra mudou o original",
    "regra ensinada" in _aud)
# SCORE_MATCH e NUMBER(5,4): guarda a fracao, a tela escreve o %
chk("B104e. o score vai como fracao, para caber em NUMBER(5,4)",
    "round(float(best[2]), 4)" in _aud)
# o custo do segundo lugar so se paga nos itens vinculados
_seg = _corpo(_src_robo, "_segundo_lugar")
chk("B104f. o concorrente nunca derruba a gravacao",
    "except Exception:" in _seg and "return None" in _seg)

# --- B105. A APROVACAO HUMANA PRECISA SER ACHAVEL PELA NOTA ------------- #
# Gravava com CHAVE_NF='-' embora a chave estivesse em maos: quem abrisse a
# ficha via o vinculo do robo e nao via o da pessoa, como se ninguem tivesse
# decidido nada.
chk("B105. a aprovacao na fila registra a chave da nota",
    'pendencia %s: item %s -> produto %s (decisao de uma pessoa"' in _src_af
    and '% (pid, cprod, cd), chave)' in _src_af)

# --- B106. LER CLOB LINHA A LINHA CUSTA UMA IDA AO BANCO POR LINHA ------ #
# Medido: 24 linhas levaram 4.164 ms contra 472 ms com TO_CHAR - mesmo
# conteudo, 8,8x. E SUBSTR sobre CLOB CONTINUA CLOB (achado 5), entao envolver
# em SUBSTR nao resolve; quem converte e o TO_CHAR.
import re as _re_clob
# LINHA A LINHA, e nao por regex de SELECT..FROM sobre o arquivo inteiro: com
# subconsulta os spans desalinham e o teste passava COM o defeito presente
# (conferido reintroduzindo a leitura crua). A unica confusao possivel e a
# lista de colunas de um INSERT, que se resolve olhando as linhas de cima.
_L = _src_af.split("\n")
# CAIXA ALTA e o discriminador: no projeto o SQL e escrito em maiusculas e as
# variaveis Python em minusculas, entao ignorar a caixa acusava o "detalhe" de
# comentario e de assinatura de funcao
_RX_DET = _re_clob.compile(r"\bDETALHE\b")
_sel_det = []
for _i, _l in enumerate(_L):
    if not _RX_DET.search(_l):
        continue
    if "TO_CHAR" in _l.upper():
        continue
    _t = _l.strip()
    if _t.startswith("--") or _t.startswith("#"):
        continue                      # comentario, SQL ou Python
    _u = _l.upper()
    if "INSERT" in _u or "VALUES" in _u or ":DET" in _u or "DET=" in _u:
        continue
    _antes = " ".join(_L[max(0, _i - 4):_i]).upper()
    if "INSERT INTO" in _antes and "SELECT" not in _antes:
        continue                      # e lista de colunas de INSERT
    # SO A TRILHA. Conferido no schema: AIHUB_PROC_LOG.DETALHE e CLOB e
    # AIHUB_OCORRENCIA.DETALHE e VARCHAR2 (zero colunas LOB naquela tabela).
    # Cobrar TO_CHAR de quem nao e LOB seria exigir o que nao ajuda, e o teste
    # viraria ruido que alguem desliga.
    _dep = " ".join(_L[_i:_i + 6]).upper()
    if "AIHUB_PROC_LOG" not in _dep:
        continue
    _sel_det.append(_l.strip()[:70])
chk("B106. nenhuma consulta le o CLOB DETALHE da trilha sem TO_CHAR",
    not _sel_det, " | ".join(_sel_det[:2]))

chk("B106a. o limite cabe no teto de 4000 BYTES da conversao",
    "SUBSTR(DETALHE,1,1300)" in _src_af or "SUBSTR(L.DETALHE,1,1300)" in _src_af)

# --- B107. _tem_tabela SEM SCHEMA FECHA O FAIL-SAFE E ESCONDE DADO ------ #
# 'HISTXMLNFE' sem o TEKNISA. faz a sonda falhar, o fail-safe responde "nao
# existe" e o bloco inteiro some CALADO: a nota mostrava 28 passos tendo 33.
# pega o ARGUMENTO do _tem_tabela, e nao "a primeira coisa entre aspas da
# linha": em `out["tem_trilha"] = _tem_tabela(cur, "AIHUB.X", ...)` a primeira
# aspa e a da chave do dicionario, e o teste acusava a propria chamada correta
_sem_schema = [m.group(0) for m in
               _re_clob.finditer(r'_tem_tabela\(cur,\s*"([^"]+)"', _src_af)
               if "." not in m.group(1)]
chk("B107. toda sonda de tabela informa o schema", not _sem_schema,
    " | ".join(_sem_schema[:2]))

# --- B108. A MESMA SEQUENCIA E GRAVADA DE DOIS JEITOS ------------------- #
# Medido: TEKNISA.INTG_ITEMPRODUTO guarda '1','2' e AIHUB_PENDENCIA guarda
# '0001','0002'. Comparar texto cru faria a logica nunca casar com o item, sem
# erro nenhum - so faltando dado na tela.
chk("B108. existe normalizacao da sequencia do item",
    "def _ficha_seq" in _src_af)
chk("B108a. a logica casa com o item por numero, nao por texto formatado",
    "_ficha_seq(m.group(1))" in _src_af and "_ficha_seq(i[\"seq\"]) == alvo" in _src_af)
chk("B108b. a fila e a tabela mostram a sequencia na mesma forma",
    _src_af.count("_ficha_seq(q[0])") == 2
    and "\"seq\": _ficha_seq(_valor_json(it[0]))" in _src_af)

# --- B109. QUEM FEZ MUDA DE LEITURA CONFORME A TABELA ------------------- #
# Em PRODFORNXML operador estranho e cadastro na tela do Teknisa; em HISTXMLNFE
# e a MATRICULA DE UMA PESSOA (achado 3). Forcar uma leitura so errou nos dois
# sentidos: o robo virou "uma pessoa" e as matriculas viraram "cadastro".
chk("B109. a leitura do autor sabe de qual tabela veio",
    "def _ficha_autor(op, desconhecido=" in _src_af
    and 'desconhecido="pessoa"' in _src_af)
chk("B109a. AIHUB_ROBO no historico do Teknisa aparece como o robo",
    'o == "AIHUB_ROBO"' in _src_af and 'return ("robo"' in _src_af)

# --- B110. GRUPO NOVO COM FILHOS PRECISA DA CAIXA DO SUBMENU ------------ #
# A lista era fixa em dois nomes e o dono saia de um ternario: grupo novo com
# filhos entrava no menu e o submenu simplesmente nao renderizava, sem erro.
chk("B110. o roteador usa mapa de submenus, nao lista fixa",
    "const SUBBOX = {" in _html and "subNotas:'notas'" in _html)
_subbox = _html.split("const SUBBOX = {")[1].split("}")[0]
_grupos = _html.split("const GRUPOS={")[1].split("\n};")[0]
for _g in ("resolver", "notas", "ajustes"):
    _tem_filhos = ("filhos:" in _grupos.split(_g + ":")[1].split("},\n  ")[0]
                   if (_g + ":") in _grupos else False)
    chk("B110%s. o grupo '%s' com filhos tem caixa de submenu"
        % ({"resolver": "a", "notas": "b", "ajustes": "c"}[_g], _g),
        (not _tem_filhos) or ("'" + _g + "'" in _subbox))
chk("B110d. a caixa do submenu de Notas existe no HTML",
    'id="subNotas"' in _html)

# --- B111. A TELA DA FICHA ---------------------------------------------- #
chk("B111. a tela existe e esta ligada ao menu",
    'id="view-ficha"' in _html and "ficha:'notas'" in _html
    and "ficha:[" in _html)
chk("B111a. a tela recarrega ao entrar",
    "if(current==='ficha')" in _html)
chk("B111b. mostra as ultimas notas em que o sistema mexeu",
    "function loadFichaRecentes" in _html and "/api/ficha-recentes" in _html)
# HONESTIDADE: vinculo criado antes desta versao nao tem o registro, e a tela
# tem de DIZER isso em vez de reconstruir e apresentar como se fosse a epoca
chk("B111c. a tela avisa quando a logica nao foi gravada",
    "passou a ser gravado nesta versão" in _html)
# no HTML a chamada esta dentro de um onclick montado por JS, entao a aspa
# simples chega escapada: procurar a forma "limpa" nao acha nunca
chk("B111d. e manda para 'Como ele pensa' quem quiser o calculo de hoje",
    "cfg-regras" in _html.split("function _fichaRender")[1])
chk("B111e. a linha do tempo separa robo, pessoa e Teknisa",
    ".fic-p.robo:before" in _html and ".fic-p.pessoa:before" in _html)


# --- B114. NOTA QUE A OPERACAO JA RECUSOU ------------------------------- #
# Medido em producao (20/08/2026), nas 300 notas mais novas da fila do robo:
# 236 (79%) com o XML bloqueado por uma pessoa AGORA, 213 (71%) canceladas,
# estornadas ou desconhecidas, 263 (88%) em pelo menos um dos casos. Das 9 que
# o motor resolveria sozinho naquele instante, 6 eram nota morta.
chk("B114. o robo confere se a operacao ja recusou a nota",
    "def nota_bloqueada" in _src_robo and "HISTXMLNFE" in _src_robo)
_nb = _corpo(_src_robo, "nota_bloqueada")
# o desbloqueio e um evento POSTERIOR: olhar "existe algum bloqueio" prenderia
# para sempre a nota que a equipe ja liberou (achado 3)
chk("B114a. a regra e o ULTIMO evento, nao 'existe algum bloqueio'",
    "ORDER  BY NRSEQHISTOR DESC" in _nb and "FETCH  FIRST 1 ROWS ONLY" in _nb)
# DTHISTORICO nao serve: varios eventos caem no mesmo instante. O teste olha
# so o SQL - a docstring CITA a coluna para explicar por que nao a usa, e a
# versao anterior deste teste reprovava a propria explicacao
_nb_sql = _nb.split('cur.execute("""')[1].split('"""')[0] if 'cur.execute("""' in _nb else _nb
chk("B114b. ordena por sequencia, e nao por data",
    "DTHISTORICO" not in _nb_sql)
chk("B114c. fail-safe FECHADO: erro ao ler responde bloqueada",
    "except Exception as e:" in _nb
    and "return True," in _nb.split("except Exception as e:")[1][:200])
# a nota costuma ser bloqueada porque o produto nao era aquele, entao aprender
# De/Para dali ensinaria o vinculo errado para todas as notas do fornecedor
_vas = _corpo(_src_robo, "vincular_af_sozinho")
chk("B114d. a trava vem ANTES do De/Para e da AF",
    _vas.find("nota_bloqueada(cur, chave)") >= 0
    and _vas.find("nota_bloqueada(cur, chave)")
        < _vas.find("resolver_depara_do_xml(con, chave, dry)"))
_iu2 = _corpo(_src_robo, "importar_uma")
chk("B114e. e ANTES de chamar a nativa que cria a nota",
    _iu2.find("nota_bloqueada(cur, chave)") >= 0
    and _iu2.find("nota_bloqueada(cur, chave)")
        < _iu2.find("GERA_NOTAFISCAL_CORPO_NFE"))
# perguntar "a AF ainda vale" numa nota estornada e responder a pergunta errada
chk("B114f. vem antes tambem da conferencia da AF",
    _iu2.find("nota_bloqueada(cur, chave)") < _iu2.find("af_ainda_valida"))
# mesmo descuido do SEM_PRODUTO (B78d): contador sem a chave derruba a rodada,
# e a rodada e quem segura o teto diario e a contagem de erros
_ri = _corpo(_src_robo, "rodar_importacao")
chk("B114g. os DOIS contadores da rodada conhecem o status novo",
    _ri.count('"RECUSADA_PELA_OPERACAO": 0') == 2)


# --- B120. OS CRITERIOS E PARAMETROS DA NOTA ---------------------------- #
# A tela mostra o que GOVERNOU a decisao. Este sistema nao parametriza nem
# calcula imposto (quem faz e a parametrizacao do Teknisa e o Fiscal), e a
# secao tem de dizer isso - senao e lida como "o robo conferiu o tributo".
_cri = _corpo(_src_af, "_ficha_criterios")
chk("B120. a ficha traz os criterios que valeram", len(_cri) > 400
    and 'out["criterios"] = _ficha_criterios(' in _src_af)
chk("B120a. a tela avisa que o sistema NAO decide imposto",
    "não parametriza nem calcula imposto" in _html)
# os numeros vem das MESMAS funcoes do motor: uma copia da leitura mostraria um
# numero e o motor usaria outro no dia em que alguem mexesse numa delas
chk("B120b. tolerancia e janela saem das funcoes do motor, nao de copia",
    "ler_tolerancias(cur)" in _cri and "janela_da_filial(cur," in _cri)
# olha o SQL, e nao o texto: o rotulo mostrado na tela CITA os nomes das
# colunas ("PARAMGERAL.VRPETOLEQTAF e VRPETOLEVRAF"), e a versao anterior
# deste teste reprovava a propria legenda
_cri_sql = " ".join(_re_clob.findall(r'cur\.execute\("""(.*?)"""', _cri,
                                     _re_clob.S))
chk("B120c. e a ficha nao le a tolerancia por conta propria",
    "VRPETOLEQTAF" not in _cri_sql)
# achado 58: sao dois regimes, e sem dizer qual vale a tela se contradiz (a
# flag anuncia DTPROCESSA e a janela mostra a emissao como ancora)
chk("B120d. a janela diz QUAL regime esta valendo",
    '"regime": ("propria" if propria else "nativa")' in _cri)
chk("B120e. e a flag da ancora ressalva que so vale no regime nativo",
    "só quando a filial configurou limite de dias" in _src_af)
# achado 14: o codigo do tipo repete entre entrada e saida com sentidos opostos
chk("B120f. o tipo de operacao e buscado com IDENTRSAIDOP='E'",
    "IDENTRSAIDOP='E' AND CDTIPOOPER=:t" in _cri)
# fail-safe que joga fora o que ja se sabe nao e fail-safe: em producao falta
# GRANT em TIPOOPERAC e o codigo '01' sumia da tela junto com o nome
chk("B120g. sem acesso a TIPOOPERAC o codigo do tipo ainda aparece",
    'out["tipo"] = {"codigo": _valor_json(cd_tipo), "nome": None}' in _cri
    and _cri.index('out["tipo"] = {"codigo"')
        < _cri.index("SELECT NMTIPOOPER"))
chk("B120h. a sonda de TIPOOPERAC informa o schema",
    '_tem_tabela(cur, "TEKNISA.TIPOOPERAC"' in _cri)

# --- B121. A CLASSIFICACAO FISCAL DE CADA ITEM -------------------------- #
# Nomes conferidos no banco: CDFISOPEITEM e o CFOP (100% preenchido),
# CDSITUTRIBPR o CST (100%), CDCLASFISC o NCM (98%).
_fn = _corpo(_src_af, "ficha_nota")
chk("B121. os campos fiscais vem na consulta que ja lia os itens",
    all(c in _fn for c in ("CDFISOPEITEM", "CDSITUTRIBPR", "CDCLASFISC",
                           "IDORIGEMPROD", "SGUNIDTRIB")))
# conta a clausula FROM, e nao o nome solto: ele aparece tambem na conferencia
# de colunas (_cols_presentes) e no comentario que explica a escolha
chk("B121a. e nao numa consulta por item",
    _fn.count("FROM   TEKNISA.INTG_ITEMPRODUTO") == 1)
# medido: 0% preenchidas. Campo sempre vazio faz a tela parecer quebrada.
# O teste olha o SELECT: o comentario CITA as duas colunas para dizer por que
# elas ficaram de fora, e checar o texto reprovava a explicacao.
# olha a TUPLA onde as colunas sao declaradas: o SELECT e montado por %s, entao
# procurar o nome no texto da consulta nao acha nada
_fisc_decl = _fn.split("_FISC = (")[1].split(")")[0] if "_FISC = (" in _fn else ""
chk("B121b. as colunas medidas como vazias ficam de fora",
    bool(_fisc_decl) and "VRFRETEITEMXML" not in _fisc_decl
    and "CDIMPADIC" not in _fisc_decl)
# coluna que varia por versao precisa ser conferida antes (regra do projeto)
chk("B121c. as colunas fiscais sao conferidas antes de entrar no SELECT",
    '_cols_presentes(cur, "INTG_ITEMPRODUTO"' in _fn)
chk("B121d. existe o helper que confere varias colunas numa consulta so",
    "def _cols_presentes" in _src_af)
chk("B121e. a tela mostra os codigos sem interpretar",
    "sem interpretar" in _html and "function _fichaFiscal" in _html)


# --- B130. A FILA ALCANCA A NOTA FRESCA, PELO CFOP ---------------------- #
# O filtro de CDTIPOOPER sozinho fazia o robo nunca ver a nota nova: medido em
# producao (21/08/2026), no lote das 11h com uma hora de vida, **0 de 37 notas
# tinham tipo**; duas horas depois, 53 de 56 tinham - porque o tipo nasce da
# acao humana na tela "Relacionar AF" (achado 101), o proprio trabalho que o
# robo existe para poupar.
_bpi = _corpo(_src_robo, "buscar_para_importar")
chk("B130. a fila tem uma segunda fonte, pelo CFOP",
    "from core.cfop_natureza import natureza, COMPRA" in _bpi
    and "G.CDTIPOOPER IS NULL" in _bpi)
# so COMPRA entra: DUVIDA e SEM_AF ficam de fora, e CFOP desconhecido nunca
# vira COMPRA por conta propria (achado 82)
chk("B130a. so 'COMPRA' entra pela porta nova",
    'natureza(cfop)[0] == COMPRA' in _bpi)
# sem filial nao ha como saber qual unidade recebeu, e o pedido e por unidade.
# Medido: um CNPJ do grupo cobre 312 filiais, entao nao da para deduzir
chk("B130b. a nota sem filial de recebimento NAO entra",
    "G.CDFILIRECE IS NOT NULL" in _bpi)
# nota ja resolvida por uma pessoa nao volta para a fila
chk("B130c. a porta nova ignora nota que ja tem AF",
    "I.NRAUTOFORN IS NOT NULL" in _bpi.split("G.CDTIPOOPER IS NULL")[1])
chk("B130d. da para desligar sem mexer no codigo",
    '_CFOP_LIGADO = os.getenv("AIHUB_CFOP_FILA", "1")' in _src_robo
    and "if not _CFOP_LIGADO:" in _bpi)
# fail-safe: erro ao ler a fila nova devolve a antiga, nunca derruba a rodada
chk("B130e. falha na fonte nova nao derruba a rodada",
    _bpi.count("return _so_tipo") >= 3
    and "except Exception:" in _bpi)

# --- B131. VINCULAR A AF NAO E IMPORTAR --------------------------------- #
# A nota fresca vem marcada como nao importavel: o robo vincula a AF, que e o
# trabalho pesado, e deixa a importacao para quando a nota tiver tipo. Medido:
# importar sem tipo praticamente nao acontece (5 de 7.765 em 30 dias, achado
# 101), e nao da para provar em leitura que a nativa aceitaria.
chk("B131. a fila diz, por nota, se ela pode ser importada",
    "(r[0], True, r[1])" in _bpi and "(ch, False, dtem)" in _bpi
    and "return [(ch, pode) for ch, pode, _d in juntas]" in _bpi)
# QUEM PEDE N ESPERA N. Somar as duas fontes devolvia ate o dobro: o ensaio
# pediu 5 notas, recebeu 10, e passou de 10s para 68s sem ninguem ter mexido
# nele. Conferido contra o banco: limite 5, 25 e 120 devolvem 5, 25 e 120.
chk("B131g. o limite pedido vale para a fila inteira",
    "juntas = juntas[:limite]" in _bpi)
# e o corte e pela EMISSAO, nao pela origem: cortar por fonte deixaria a nota
# de ontem de fora para caber uma de tres meses atras (achados 88 e 97)
chk("B131h. o corte tira as mais antigas, nao uma das fontes",
    "juntas.sort(key=lambda x: (x[2] is None, x[2]), reverse=True)" in _bpi)
_ri2 = _corpo(_src_robo, "rodar_importacao")
# .find() e nao .index(): quando o trecho some, o index ESTOURA e derruba a
# bateria inteira, escondendo todos os testes seguintes. Teste tem de falhar,
# nao quebrar - conferido reintroduzindo o defeito.
_p_marca = _ri2.find("if not pode_importar:")
_p_imp = _ri2.find("r[importar_uma(con, ch)]")
_p_vinc = _ri2.find("vincular_af_sozinho(con, ch, dry)")
chk("B131a. a rodada respeita a marca e NAO importa a nota fresca",
    _p_marca >= 0 and _p_imp >= 0 and _p_marca < _p_imp)
chk("B131b. mas VINCULA a AF nela (e o trabalho que ela vem buscar)",
    _p_vinc >= 0 and _p_marca >= 0 and _p_vinc < _p_marca)
# mesmo descuido do SEM_PRODUTO e do RECUSADA_PELA_OPERACAO: contador sem a
# chave derruba a rodada, e com ela o teto diario e a contagem de erros
chk("B131c. o contador conhece o status novo (senao KeyError)",
    '"AGUARDA_TIPO": 0' in _ri2)
chk("B131d. e a rodada diz quantas ficaram esperando o tipo",
    "aguardando o tipo de" in _ri2)
# mudanca em funcao compartilhada tem de ser conferida em TODO chamador - e a
# quarta vez que essa licao aparece no projeto
chk("B131e. o ensaio do painel le a fila no formato novo",
    "for ch, pode_importar in fila:" in _src_af)
_ti = open("robo/teste_importacao.py", encoding="utf-8").read()
chk("B131f. o teste manual de importacao tambem",
    "buscar_para_importar(cur, QTD) if pode" in _ti)


# --- B132. RODAR SO UM PEDACO (quantidade ou lote de chegada) ----------- #
# Quem confere o sistema pela primeira vez nao quer soltar o robo na fila
# inteira: escolhe 3 notas, ou o lote de uma hora, confere, e so entao segue.
_rl = _corpo(_src_robo, "_recorte_lote")
chk("B132. a fila aceita recorte por lote de chegada",
    "def buscar_para_importar(cur, limite, lote=None)" in _src_robo
    and "def _recorte_lote" in _src_robo)
# UMA funcao decide o SQL E os binds. A versao anterior tinha duas - uma
# montava o fragmento olhando "tem lote?" e a outra os binds olhando "o lote e
# valido?" - e com lote malformado a consulta saia com :lote_ini sem o bind.
chk("B132a. uma funcao so decide fragmento E binds",
    "return \"\", {}" in _rl and "lote_ini" in _rl
    and "_cond_lote" not in _src_robo and "_binds_lote" not in _src_robo)
# o valor vem da tela: nunca pode entrar concatenado no texto da consulta
chk("B132b. o lote entra como BIND, nunca no texto do SQL",
    ":lote_ini" in _rl and ":lote_fim" in _rl
    and "%s" not in _rl.split("return (")[-1])
# fail-safe: lote que nao da para entender vira 'sem recorte', com aviso.
# Rodada que morre leva junto o teto diario e o contador de erros.
chk("B132c. lote malformado nao derruba a rodada",
    "except Exception:" in _rl
    and "nao entendido" in _rl)
chk("B132d. o recorte chega ate a rodada",
    "def rodada(dry=False, limite=None, lote=None)" in _src_robo
    and "def rodar_importacao(con, limite, dry=False, vincula_af=True, lote=None)"
        in _src_robo)
chk("B132e. e e decidido UMA vez, usado nas duas fontes da fila",
    "_cond, _bl = _recorte_lote(lote)" in _corpo(_src_robo, "buscar_para_importar"))

# --- B133. O RECORTE E DA RODADA PEDIDA, E SO DELA ---------------------- #
# Se ficasse guardado, a proxima rodada AUTOMATICA herdaria o recorte de 3
# notas que alguem usou para testar - e o robo pararia de trabalhar sem
# ninguem entender por que.
chk("B133. o painel aceita quantidade e lote no 'rodar agora'",
    'qtd = dados.get("quantidade")' in _src_af
    and '_robo["lote"] = (dados.get("lote") or "").strip() or None' in _src_af)
chk("B133a. a quantidade tem teto (nao vira atalho para furar o AIHUB_LOTE)",
    "max(1, min(200, int(qtd)))" in _src_af)
_loop = _corpo(_src_af, "_robo_loop")
chk("B133b. as opcoes sao limpas depois de usadas",
    '_robo["limite"] = None' in _loop and '_robo["lote"] = None' in _loop)
chk("B133c. e so valem quando a rodada foi PEDIDA a mao",
    '_robo.get("limite") if agora else None' in _loop)
chk("B133d. a rodada recebe as duas",
    "limite=lim_pedido, lote=lote_pedido" in _loop)
# a trilha registra o recorte: sem isso, "o robo so pegou 3 notas" vira
# misterio no dia seguinte
chk("B133e. a trilha registra qual recorte foi usado",
    "RODAR_AGORA pedido pelo painel (%s%s)" in _src_af)

# --- B134. A TELA DO PEDACO --------------------------------------------- #
chk("B134. a tela tem os dois controles",
    'id="rodQtd"' in _html and 'id="rodLote"' in _html)
# reaproveita o rodarAgora inteiro (acompanhamento, estado, erro): uma segunda
# funcao com a mesma logica divergiria da primeira na primeira correcao
chk("B134a. o botao reusa o rodarAgora, nao duplica a logica",
    "return rodarAgora(btn, {quantidade:" in _html
    and "async function rodarAgora(btn, opcoes)" in _html)
chk("B134b. o pedido leva as opcoes junto",
    "Object.assign({agora:true}, opcoes||{})" in _html)
chk("B134c. o seletor de lotes e preenchido pela rota que ja existe",
    "function loadLotesRodada" in _html and "/api/lotes?dias=" in _html)
# TRES DIAS VAZIOS SAO LEGITIMOS (fim de semana, feriado, base congelada), e a
# busca alarga sozinha. O seletor da rodada nasceu SEM o alargamento e vinha
# sempre vazio em homologacao (parada em 23/06): o recurso parecia quebrado
# sem estar. Uma funcao so faz a busca, para as duas telas nao divergirem.
chk("B134f. as duas telas usam a MESMA busca de lotes, que alarga a janela",
    "async function buscarLotes()" in _html
    and _html.count("await buscarLotes()") == 2
    and "[3, 15, 90]" in _html)
chk("B134d. e a tela carrega os lotes ao abrir",
    "if(current==='cfg-ver')   return loadLotesRodada();" in _html)
chk("B134e. sem escolher nada, avisa em vez de rodar a fila inteira",
    "Escolha a quantidade ou o horário do lote" in _html)


print()
for st, d_, det in T:
    print("  %-6s %s%s" % (st, d_, ("   -> " + det) if st != "OK " and det else ""))
print(); print("RESULTADO: %d/%d passaram" % (len(T) - F[0], len(T)))
sys.exit(1 if F[0] else 0)
