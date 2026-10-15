# -*- coding: utf-8 -*-
r"""auditoria_geral.py - CONFERE O SISTEMA CONTRA O BANCO, nao contra si mesmo.

O que as outras baterias NAO cobrem:
  testes\*            rodam sem banco: provam a FORMA do codigo e a logica pura
  validar_sistema.py  prova que cada rota RESPONDE, nao que a resposta e certa

Aqui a pergunta e outra: os dados que o sistema gravou estao corretos? Os
parametros que ele supoe existem mesmo? Duas telas nossas concordam sobre a
mesma nota? O robo se recusa a agir quando deve?

100% SOMENTE LEITURA. Nao grava, nao chama procedure, nao altera nada.

    py investigacao\auditoria_geral.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oracledb

PASS = FAIL = WARN = 0


def ok(n, d=""):
    global PASS
    PASS += 1
    print("  [OK]   %-52s %s" % (n, d))


def falha(n, d=""):
    global FAIL
    FAIL += 1
    print("  [X ]   %-52s %s" % (n, d))


def aviso(n, d=""):
    global WARN
    WARN += 1
    print("  [! ]   %-52s %s" % (n, d))


def secao(t):
    print("\n" + t)
    print("-" * 78)


def um(cur, sql, **b):
    cur.execute(sql, b)
    r = cur.fetchone()
    return r[0] if r else None


con = oracledb.connect(user=os.environ.get("TEKNISA_DB_USER", "AIHUB"),
                       password=os.environ["TEKNISA_DB_PASS"],
                       dsn=os.environ["TEKNISA_DB_DSN"])
cur = con.cursor()
print("=" * 78)
print(" AUDITORIA GERAL - %s" % um(cur, "SELECT TO_CHAR(SYSDATE,'DD/MM/YYYY HH24:MI') FROM DUAL"))
print("=" * 78)

# ---------------------------------------------------------------------------
secao("1. O QUE O SISTEMA GRAVOU NO TEKNISA (as duas unicas tabelas)")
# ---------------------------------------------------------------------------
# 1a) De/Para: um unico vinculo ativo por (fornecedor, item). E a regra central
#     de _gravar_vinculo; dois vinculos ativos apontariam dois produtos.
n = um(cur, """SELECT COUNT(*) FROM (
                 SELECT CDFORNECED, CDPRODUTOXML FROM TEKNISA.PRODFORNXML
                 WHERE IDATIVO='S' GROUP BY CDFORNECED, CDPRODUTOXML
                 HAVING COUNT(*) > 1)""")
(ok if n == 0 else falha)("De/Para com dois vinculos ativos", "%s caso(s)" % n)

# 1b) De/Para apontando produto que nao existe ou esta inativo
n = um(cur, """SELECT COUNT(*) FROM TEKNISA.PRODFORNXML D
               WHERE D.IDATIVO='S'
                 AND NOT EXISTS (SELECT 1 FROM TEKNISA.PRODUTO P
                                 WHERE P.CDPRODUTO=D.CDPRODUTO
                                   AND NVL(P.IDATIVO,'S')<>'N')""")
(ok if n == 0 else falha)("De/Para apontando produto inexistente/inativo", "%s" % n)

# 1c) O QUE NOS gravamos, separado do que o Teknisa ja tinha
nosso = um(cur, """SELECT COUNT(*) FROM TEKNISA.PRODFORNXML
                   WHERE IDATIVO='S' AND (CDOPERINCLUSAO LIKE 'AIHUB%'
                                       OR CDOPERINCLUSAO LIKE 'PAINEL%')""")
tot = um(cur, "SELECT COUNT(*) FROM TEKNISA.PRODFORNXML WHERE IDATIVO='S'")
ok("De/Para ativos", "%s no total, %s gravados por este sistema" % (tot, nosso))

# 1d) AF gravada por nos: e do MESMO fornecedor da nota? (o erro mais grave)
n = um(cur, """SELECT COUNT(*) FROM TEKNISA.INTG_ITEMPRODUTO I
               JOIN   TEKNISA.INTG_NOTAFISCAL G ON G.NRACESSONFE=I.NRACESSONFE
               JOIN   AIHUB.AIHUB_PROC_LOG L
                      ON L.CHAVE_NF=I.NRACESSONFE AND L.STATUS='AF_VINCULO_ROBO'
               WHERE  I.NRAUTOFORN IS NOT NULL
                 AND  NOT EXISTS (SELECT 1 FROM TEKNISA.AUTOFORNE A
                                  WHERE A.NRAUTOFORN=I.NRAUTOFORN
                                    AND A.CDFORNECED=G.CDFORNECED)""")
(ok if n == 0 else falha)("AF do robo de fornecedor DIFERENTE da nota", "%s item(ns)" % n)

# 1e) AF gravada por nos: e da filial que recebe?
n = um(cur, """SELECT COUNT(*) FROM TEKNISA.INTG_ITEMPRODUTO I
               JOIN   TEKNISA.INTG_NOTAFISCAL G ON G.NRACESSONFE=I.NRACESSONFE
               JOIN   AIHUB.AIHUB_PROC_LOG L
                      ON L.CHAVE_NF=I.NRACESSONFE AND L.STATUS='AF_VINCULO_ROBO'
               WHERE  I.NRAUTOFORN IS NOT NULL AND G.CDFILIRECE IS NOT NULL
                 AND  NOT EXISTS (SELECT 1 FROM TEKNISA.AUTOFORNE A
                                  WHERE A.NRAUTOFORN=I.NRAUTOFORN
                                    AND A.CDFILDEST=G.CDFILIRECE)""")
(ok if n == 0 else falha)("AF do robo de OUTRA filial", "%s item(ns)" % n)

# 1f) nota com DUAS AFs vindas do robo (achado 28: nao pode)
n = um(cur, """SELECT COUNT(*) FROM (
                 SELECT I.NRACESSONFE FROM TEKNISA.INTG_ITEMPRODUTO I
                 JOIN AIHUB.AIHUB_PROC_LOG L
                      ON L.CHAVE_NF=I.NRACESSONFE AND L.STATUS='AF_VINCULO_ROBO'
                 WHERE I.NRAUTOFORN IS NOT NULL
                 GROUP BY I.NRACESSONFE HAVING COUNT(DISTINCT I.NRAUTOFORN) > 1)""")
(aviso if n else ok)("Nota do robo com mais de uma AF",
                     "%s (legitimo se a entrega cumpre 2 pedidos)" % n)

# ---------------------------------------------------------------------------
secao("2. PARAMETROS DO CLIENTE - o codigo le, nao chuta")
# ---------------------------------------------------------------------------
try:
    from core.af_matcher import ler_tolerancias, janela_da_filial
    t = ler_tolerancias(cur)
    ok("Tolerancias lidas do PARAMGERAL", str(t)[:60])
except Exception as e:
    falha("Tolerancias", str(e)[:60])

for col, esperado in (("IDGERNFIGUALXML", None), ("IDUTILDTPROC", None),
                      ("IDBLOQCMPSAF", None), ("IDUTCFOPXMLLANNF", None)):
    try:
        v = um(cur, "SELECT %s FROM TEKNISA.PARAMGERAL" % col)
        ok("PARAMGERAL.%s" % col, repr(v))
    except Exception as e:
        falha("PARAMGERAL.%s" % col, str(e)[:50])

n = um(cur, """SELECT COUNT(*) FROM TEKNISA.PARAMFILIAL
               WHERE NRMAXDIAANTDTAF IS NOT NULL OR NRMAXDIAPOSDTAF IS NOT NULL""")
tot = um(cur, "SELECT COUNT(*) FROM TEKNISA.PARAMFILIAL")
ok("Filiais com janela de AF configurada",
   "%s de %s (0 = usamos a janela propria, achado 58)" % (n, tot))

# ---------------------------------------------------------------------------
secao("3. AS DUAS TRAVAS DO ROBO")
# ---------------------------------------------------------------------------
for chave in ("MODO_AUTO", "ROBO_LIGADO", "MODO_IMPORTA"):
    v = um(cur, "SELECT VALOR FROM AIHUB.AIHUB_CONFIG WHERE CHAVE=:k", k=chave)
    ok("AIHUB_CONFIG.%s" % chave, repr(v) + ("  (desligado)" if v != "1" else "  (LIGADO)"))
print("     AIHUB_MODO_ESCRITA no ambiente: %r" % os.getenv("AIHUB_MODO_ESCRITA"))

# ---------------------------------------------------------------------------
secao("4. AS TABELAS DO PROPRIO SISTEMA")
# ---------------------------------------------------------------------------
for t in ("AIHUB_PROC_LOG", "AIHUB_PENDENCIA", "AIHUB_CONFIG",
          "AIHUB_CONFERIDO", "AIHUB_OCORRENCIA"):
    try:
        ok("AIHUB.%s" % t, "%s linha(s)" % um(cur, "SELECT COUNT(*) FROM AIHUB.%s" % t))
    except Exception as e:
        falha("AIHUB.%s" % t, str(e)[:60])

con.close()
print("\n" + "=" * 78)
print(" PARCIAL: %d ok, %d falha(s), %d aviso(s)" % (PASS, FAIL, WARN))
print("=" * 78)
sys.exit(1 if FAIL else 0)
