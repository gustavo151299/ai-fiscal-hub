# -*- coding: utf-8 -*-
r"""ensaio_producao.py - CACA A ERROS antes de ligar o sistema em producao.

Diferente do simular_producao.py, que mede ACERTO, este procura DEFEITO: roda a
cadeia de decisao inteira do robo sobre notas reais e registra toda excecao,
todo dado que quebra suposicao e tudo que falta para o sistema sequer subir la.

100% SOMENTE LEITURA, e por dois motivos independentes:
  1. o codigo chamado aqui nao grava (tudo roda em modo simulacao);
  2. o usuario AIHUB_LEITURA nao tem privilegio de escrita - a Parte 0 prova
     isso tentando escrever e conferindo que o banco recusa.

Uso:
    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\ensaio_producao.py
Variaveis: ENS_LIMITE (padrao 120), ENS_JANELA (padrao 180).
"""
import os
import sys
import time
import traceback
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oracledb

LIMITE = int(os.getenv("ENS_LIMITE", "120"))
JANELA = int(os.getenv("ENS_JANELA", "180"))

PROB = []          # problemas encontrados: (gravidade, titulo, detalhe)


def erro(t, d=""):
    PROB.append(("ERRO", t, d))
    print("  [ERRO] %-50s %s" % (t, d))


def alerta(t, d=""):
    PROB.append(("ALERTA", t, d))
    print("  [!]    %-50s %s" % (t, d))


def ok(t, d=""):
    print("  [ok]   %-50s %s" % (t, d))


def secao(t):
    print("\n" + "=" * 78)
    print(" " + t)
    print("=" * 78)


for v in ("AIHUB_PROD_USER", "AIHUB_PROD_PASS", "AIHUB_PROD_DSN"):
    if not os.getenv(v):
        print("\n Falta %s. Rode com CONFIG_PRODUCAO_LEITURA.bat.\n" % v)
        raise SystemExit(1)

con = oracledb.connect(user=os.environ["AIHUB_PROD_USER"],
                       password=os.environ["AIHUB_PROD_PASS"],
                       dsn=os.environ["AIHUB_PROD_DSN"])
cur = con.cursor()
cur.execute("SELECT USER, TO_CHAR(SYSDATE,'DD/MM/YYYY HH24:MI') FROM DUAL")
usuario, agora = cur.fetchone()
print("=" * 78)
print(" ENSAIO EM PRODUCAO - %s   usuario: %s" % (agora, usuario))
print("=" * 78)

# ===========================================================================
secao("PARTE 0 - O SISTEMA CONSEGUIRIA SUBIR EM PRODUCAO?")
# ===========================================================================
# 0a) a conexao e mesmo so de leitura?
try:
    cur.execute("UPDATE TEKNISA.PRODFORNXML SET IDATIVO=IDATIVO WHERE 1=0")
    erro("o usuario de consulta CONSEGUE escrever",
         "revise os grants antes de qualquer teste")
    con.rollback()
except Exception as e:
    cod = str(e).split(":")[0]
    ok("o banco recusa escrita deste usuario", cod)

# 0b) as tabelas do proprio sistema existem em producao?
FALTANDO = []
for t in ("AIHUB_CONFIG", "AIHUB_PROC_LOG", "AIHUB_PENDENCIA",
          "AIHUB_CONFERIDO", "AIHUB_OCORRENCIA"):
    try:
        cur.execute("SELECT COUNT(*) FROM AIHUB.%s" % t)
        ok("AIHUB.%s" % t, "%s linha(s)" % cur.fetchone()[0])
    except Exception as e:
        FALTANDO.append(t)
        erro("AIHUB.%s NAO existe/sem acesso" % t, str(e).split("\n")[0][:52])

# 0c) as tabelas do Teknisa que o sistema le
for t in ("INTG_NOTAFISCAL", "INTG_ITEMPRODUTO", "INTG_INCONSISTENCIA",
          "NOTAFISCAL", "ITEMPRODUTO", "AUTOFORNE", "ITEMAUTF", "NFRELACAF",
          "PRODUTO", "PRODFORNXML", "FORNECEDOR", "PARAMGERAL", "PARAMFILIAL",
          "HISTXMLNFE", "NOTADESTINADA"):
    try:
        cur.execute("SELECT COUNT(*) FROM TEKNISA.%s WHERE ROWNUM<=1" % t)
        cur.fetchone()
    except Exception as e:
        erro("TEKNISA.%s sem acesso" % t, str(e).split("\n")[0][:52])

# 0d) as procedures nativas que o robo chama
cur.execute("""SELECT OBJECT_NAME FROM ALL_OBJECTS
               WHERE OWNER='TEKNISA' AND OBJECT_NAME IN
                     ('GERA_NOTAFISCAL_CORPO_NFE','NF_BAIXA_AF',
                      'VERIFICA_INCONS_NF','CONFIRMACAO_AUTOMATICA_NF')""")
vistas = {r[0] for r in cur.fetchall()}
for p in ("GERA_NOTAFISCAL_CORPO_NFE", "NF_BAIXA_AF", "VERIFICA_INCONS_NF",
          "CONFIRMACAO_AUTOMATICA_NF"):
    (ok if p in vistas else alerta)("procedure %s" % p,
                                    "visivel" if p in vistas else "NAO visivel para este usuario")

# 0e) os indices que fazem o painel ser usavel
cur.execute("""SELECT INDEX_NAME FROM ALL_INDEXES
               WHERE TABLE_OWNER='TEKNISA' AND INDEX_NAME IN
                     ('IX_NF_ACESSONFE','IX_PFXML_FORN','IX_INTGINC_ACESSO',
                      'IX_INTGNF_EMISSAO','IX_NF_FORNECED','IX_NF_APROVACAO',
                      'IX_INTGNF_FIL_LANC')""")
tem = {r[0] for r in cur.fetchall()}
for i in ("IX_NF_ACESSONFE", "IX_PFXML_FORN", "IX_INTGINC_ACESSO",
          "IX_INTGNF_EMISSAO", "IX_NF_FORNECED", "IX_NF_APROVACAO",
          "IX_INTGNF_FIL_LANC"):
    (ok if i in tem else alerta)("indice %s" % i,
                                 "existe" if i in tem else "FALTA (tela lenta)")

# ===========================================================================
secao("PARTE 1 - DADOS QUE QUEBRAM SUPOSICAO DO CODIGO")
# ===========================================================================
def conta(rot, sql, limite_alerta=0, obs=""):
    try:
        cur.execute(sql)
        n = cur.fetchone()[0]
        (alerta if n > limite_alerta else ok)(rot, "%s  %s" % (n, obs))
        return n
    except Exception as e:
        erro(rot, str(e).split("\n")[0][:52])
        return None


BASE = ("FROM TEKNISA.INTG_NOTAFISCAL G WHERE G.DTUTILIZAXML IS NULL "
        "AND G.CDTIPOOPER IN ('01','06') "
        "AND NVL(G.DSTAGORIGFILI,'x') <> 'Manual' "
        "AND G.DTEMISSAO >= SYSDATE - %d" % JANELA)
conta("notas na janela sem DTEMISSAO",
      "SELECT COUNT(*) FROM TEKNISA.INTG_NOTAFISCAL G "
      "WHERE G.DTUTILIZAXML IS NULL AND G.CDTIPOOPER IN ('01','06') "
      "AND G.DTEMISSAO IS NULL", 0, "(achado 76: quebrava o motor)")
conta("notas sem filial de recebimento",
      "SELECT COUNT(*) " + BASE + " AND G.CDFILIRECE IS NULL", 0,
      "(o motor recusa, com motivo)")
conta("notas sem nenhum item", "SELECT COUNT(*) " + BASE +
      " AND NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I "
      "WHERE I.NRACESSONFE=G.NRACESSONFE)", 0)
conta("numero de AF repetido em filiais diferentes",
      """SELECT COUNT(*) FROM (SELECT NRAUTOFORN FROM TEKNISA.AUTOFORNE
         GROUP BY NRAUTOFORN HAVING COUNT(DISTINCT CDFILDEST) > 1)""", 0,
      "(achado 47: af_inequivoca protege)")
conta("De/Para com dois vinculos ativos",
      """SELECT COUNT(*) FROM (SELECT CDFORNECED, CDPRODUTOXML
         FROM TEKNISA.PRODFORNXML WHERE IDATIVO='S'
         GROUP BY CDFORNECED, CDPRODUTOXML HAVING COUNT(*)>1)""", 0)
conta("produto de AF com fator de conversao ZERO",
      """SELECT COUNT(*) FROM TEKNISA.PRODUTO
         WHERE NVL(VRFATOCONV,0)=0 AND NVL(IDATIVO,'S')<>'N'""", 0,
      "(NULLIF protege a divisao)")

# ===========================================================================
secao("PARTE 2 - A CADEIA DE DECISAO INTEIRA, CACANDO EXCECAO")
# ===========================================================================
from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, af_ainda_valida, af_inequivoca,
                             AUTOMATICA, AMBIGUA)
from core.ai_matcher import candidatos, escolher, pode_auto, normaliza_un
from core.cfop_natureza import nota_e_compra

TOL = ler_tolerancias(cur)
ok("tolerancias lidas do Teknisa", "quantidade %s%%, valor %s%%" % TOL)

t0 = time.time()
cur.execute("""
    SELECT * FROM (
      SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE, G.DTEMISSAO
      FROM   TEKNISA.INTG_NOTAFISCAL G
      WHERE  G.CDTIPOOPER IN ('01','06')
        AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
        AND  G.DTEMISSAO >= SYSDATE - :dias
        AND  NOT EXISTS (SELECT 1 FROM TEKNISA.NOTAFISCAL N
                         WHERE N.CDFILIAL=G.CDFILIAL AND N.NRLANCTONF=G.NRLANCTONF)
      ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIAL, G.NRLANCTONF)
    WHERE ROWNUM <= :lim""", dias=JANELA, lim=LIMITE)
notas = cur.fetchall()
print("\n  %d nota(s) da fila real (%.0f s para achar)" % (len(notas), time.time() - t0))

falhas = defaultdict(list)      # etapa -> [(nf, mensagem)]
placar = Counter()
depara_auto = []                # os pares que o robo criaria sozinho
t0 = time.time()
for chave, nf, forn, fil, dtem in notas:
    nota = {"CDFORNECED": forn, "CDFILIRECE": fil, "DTEMISSAO": dtem,
            "NRNOTAFISC": nf, "ORIGEM": "(auto)", "DTUTILIZAXML": None}

    def passo(etapa, fn, padrao=None):
        try:
            return fn()
        except Exception as e:
            falhas[etapa].append((nf, "%s: %s" % (type(e).__name__,
                                                  str(e).split("\n")[0][:90])))
            return padrao

    itens = passo("ler itens", lambda: itens_do_xml(cur, chave), [])
    if not itens:
        placar["sem itens"] += 1
        continue
    passo("traduzir De/Para", lambda: traduzir_para_interno(cur, forn, itens))
    passo("natureza pelo CFOP", lambda: nota_e_compra(cur, chave))

    # o matcher, no item que ainda nao tem produto: e onde nasce dado errado
    faltam = [i for i in itens if not i.get("cd_interno")]
    if faltam:
        cands = passo("candidatos do fornecedor",
                      lambda: candidatos(cur, forn), [])
        un_de = {c[0]: (c[2] if len(c) > 2 else None) for c in (cands or [])}
        for it in faltam:
            desc = (it.get("desc_xml") or "").strip()
            if not desc or not cands:
                continue
            best = passo("escolher produto",
                         lambda: escolher(desc, cands, it.get("un"))[0])
            if best and passo("pode_auto", lambda: pode_auto(
                    desc, best, it.get("un"), un_de.get(best[0]), cands), False):
                depara_auto.append((nf, desc, it.get("un"), best[0], best[1],
                                    round(best[2] * 100)))   # score e fracao

    d = passo("escolher AF", lambda: escolher_af(cur, nota, itens, TOL[0]),
              {"decisao": "erro"})
    dec = d.get("decisao")
    placar[{AUTOMATICA: "resolveria sozinho",
            AMBIGUA: "manda para a pessoa"}.get(dec, "nao achou AF")] += 1
    if dec == AUTOMATICA and d.get("af"):
        passo("af_inequivoca", lambda: af_inequivoca(cur, d["af"]["af"], fil))
    passo("af_ainda_valida", lambda: af_ainda_valida(cur, chave))

print("  %d nota(s) processadas em %.0f s (%.1f s por nota)"
      % (len(notas), time.time() - t0, (time.time() - t0) / max(1, len(notas))))

print("\n  O QUE ELE FARIA:")
for k, v in placar.most_common():
    print("     %-34s %4d  (%.0f%%)" % (k, v, 100.0 * v / max(1, len(notas))))

print("\n  EXCECOES POR ETAPA:")
if not falhas:
    ok("nenhuma excecao na cadeia inteira", "%d notas" % len(notas))
else:
    for etapa, lista in sorted(falhas.items(), key=lambda x: -len(x[1])):
        erro("etapa '%s'" % etapa, "%d nota(s)" % len(lista))
        vistos = set()
        for nf, msg in lista:
            if msg not in vistos:
                vistos.add(msg)
                print("           NF %-11s %s" % (nf, msg))
            if len(vistos) >= 3:
                break

# ===========================================================================
secao("PARTE 3 - OS DE/PARA QUE O ROBO CRIARIA SOZINHO")
# ===========================================================================
print(" Sao os vinculos de produto que ele gravaria sem perguntar. Cada linha")
print(" precisa fazer sentido para um humano: e aqui que nasceria dado errado.\n")
if not depara_auto:
    ok("nenhum vinculo automatico nesta amostra", "")
else:
    for nf, desc, un, cd, nome, score in depara_auto[:25]:
        marca = ""
        if un and normaliza_un(un) and nome:
            marca = ""
        print("   NF %-10s %-42s (%s)" % (nf, desc[:42], un or "-"))
        print("      -> %s  %-40s  %s%%" % (cd, (nome or "")[:40], score))
    print("\n   total: %d vinculo(s) automatico(s) em %d nota(s)"
          % (len(depara_auto), len(notas)))

# ===========================================================================
secao("RESUMO")
# ===========================================================================
erros = [p for p in PROB if p[0] == "ERRO"]
alertas = [p for p in PROB if p[0] == "ALERTA"]
print(" %d erro(s), %d alerta(s)" % (len(erros), len(alertas)))
for g, t, d in PROB:
    print("   %-7s %-50s %s" % (g, t, d))
con.close()
