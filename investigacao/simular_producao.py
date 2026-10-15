# -*- coding: utf-8 -*-
r"""simular_producao.py - O MOTOR REAL CONTRA OS DADOS REAIS DE PRODUCAO.

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario de consulta), nunca
TEKNISA_DB_*, para nao existir caminho em que isto escreva em producao.

Sao duas medicoes diferentes, e a distincao importa:

  PARTE A - COM RESPOSTA CERTA (retrospectiva)
    Notas que AINDA NAO foram importadas mas em que uma PESSOA ja escolheu a
    AF. O motor decide do zero, sem ver a escolha dela, e comparamos.
    Isto so e possivel porque a nota nao foi importada: a AF ainda esta em
    aberto, entao o predicado nativo continua enxergando a resposta certa.
    (Medir contra nota JA IMPORTADA nao funciona - achado 60: a importacao
    consumiu a AF, e o filtro de saldo apaga justamente a resposta certa.)

  PARTE B - SEM RESPOSTA CERTA (prospectiva)
    A fila de verdade: notas sem AF nenhuma. Aqui nao ha gabarito, entao o que
    se mede e o COMPORTAMENTO: quantas ele resolveria sozinho, quantas
    mandaria para uma pessoa, e por que.

Uso:
    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\simular_producao.py
"""
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oracledb

from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, AUTOMATICA, AMBIGUA)

LIMITE_A = int(os.getenv("SIM_LIMITE_A", "40"))
LIMITE_B = int(os.getenv("SIM_LIMITE_B", "40"))
JANELA = int(os.getenv("SIM_JANELA", "180"))

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
print(" SIMULACAO CONTRA PRODUCAO - %s  (usuario %s, somente leitura)"
      % (agora, usuario))
print("=" * 78)
TOL = ler_tolerancias(cur)
print(" Tolerancias lidas do Teknisa: quantidade %s%%, valor %s%%" % TOL)


def monta(chave):
    """Le a nota e os itens como o motor le. Devolve (nota, itens)."""
    cur.execute("""SELECT CDFORNECED, CDFILIRECE, DTEMISSAO, NRNOTAFISC,
                          NVL(DSTAGORIGFILI,'(auto)'), DTUTILIZAXML
                   FROM   TEKNISA.INTG_NOTAFISCAL WHERE NRACESSONFE=:c""", c=chave)
    r = cur.fetchone()
    if not r:
        return None, None
    nota = {"CDFORNECED": r[0], "CDFILIRECE": r[1], "DTEMISSAO": r[2],
            "NRNOTAFISC": r[3], "ORIGEM": r[4], "DTUTILIZAXML": r[5]}
    itens = itens_do_xml(cur, chave)
    traduzir_para_interno(cur, nota["CDFORNECED"], itens)
    return nota, itens


# ===========================================================================
print("\n" + "-" * 78)
print(" PARTE A - O MOTOR CONTRA A ESCOLHA DE UMA PESSOA")
print(" (notas nao importadas em que alguem ja relacionou a AF)")
print("-" * 78)
t0 = time.time()
cur.execute("""
    SELECT * FROM (
      SELECT G.NRACESSONFE, G.DTEMISSAO
      FROM   TEKNISA.INTG_NOTAFISCAL G
      WHERE  G.CDTIPOOPER IN ('01','06')
        AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
        AND  G.DTEMISSAO >= SYSDATE - :dias
        AND  G.DTUTILIZAXML IS NULL
        AND  EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                     WHERE I.NRACESSONFE=G.NRACESSONFE AND I.NRAUTOFORN IS NOT NULL)
        AND  NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I2
                         WHERE I2.NRACESSONFE=G.NRACESSONFE AND I2.NRAUTOFORN IS NULL)
      ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIAL, G.NRLANCTONF)
    WHERE ROWNUM <= :lim""", dias=JANELA, lim=LIMITE_A)
alvos_a = [r[0] for r in cur.fetchall()]
print(" %d nota(s) com AF ja escolhida por gente (%.0f s para achar)"
      % (len(alvos_a), time.time() - t0))

acerto = erro = nao_decidiu = pulou = 0
exemplos_erro, exemplos_ok = [], []
for ch in alvos_a:
    nota, itens = monta(ch)
    if not itens:
        pulou += 1
        continue
    reais = sorted({i["af_atual"] for i in itens if i.get("af_atual")})
    # o motor decide SEM ver a escolha da pessoa
    cegos = [dict(i, af_atual=None) for i in itens]
    try:
        d = escolher_af(cur, nota, cegos, TOL[0])
    except Exception:
        pulou += 1
        continue
    if d.get("decisao") != AUTOMATICA:
        nao_decidiu += 1
        continue
    if d.get("af"):
        escolhida = [d["af"]["af"]]
    else:
        escolhida = sorted({p["af"] for p in (d.get("plano") or []) if p.get("af")})
    if set(escolhida) & set(reais):
        acerto += 1
        if len(exemplos_ok) < 3:
            exemplos_ok.append((nota["NRNOTAFISC"], reais, escolhida))
    else:
        erro += 1
        if len(exemplos_erro) < 5:
            exemplos_erro.append((nota["NRNOTAFISC"], reais, escolhida,
                                  (d.get("motivo") or "")[:90]))

decidiu = acerto + erro
print("\n  o motor DECIDIU sozinho em %d de %d nota(s)" % (decidiu, len(alvos_a)))
if decidiu:
    print("    acertou a AF da pessoa : %3d  (%.1f%%)" % (acerto, 100.0 * acerto / decidiu))
    print("    escolheu OUTRA AF      : %3d  (%.1f%%)" % (erro, 100.0 * erro / decidiu))
print("    deixou para a pessoa   : %3d" % nao_decidiu)
print("    nao deu para avaliar   : %3d" % pulou)
for nf, reais, esc in exemplos_ok:
    print("      OK   NF %-9s pessoa=%s  motor=%s" % (nf, ",".join(reais), ",".join(esc)))
for nf, reais, esc, mot in exemplos_erro:
    print("      ERRO NF %-9s pessoa=%s  motor=%s" % (nf, ",".join(reais), ",".join(esc)))
    print("           %s" % mot)

# ===========================================================================
print("\n" + "-" * 78)
print(" PARTE B - A FILA DE VERDADE (notas sem AF nenhuma)")
print("-" * 78)
t0 = time.time()
cur.execute("""
    SELECT * FROM (
      SELECT G.NRACESSONFE
      FROM   TEKNISA.INTG_NOTAFISCAL G
      WHERE  G.CDTIPOOPER IN ('01','06')
        AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
        AND  G.DTEMISSAO >= SYSDATE - :dias
        AND  NOT EXISTS (SELECT 1 FROM TEKNISA.NOTAFISCAL N
                         WHERE N.CDFILIAL=G.CDFILIAL AND N.NRLANCTONF=G.NRLANCTONF)
        AND  NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                         WHERE I.NRACESSONFE=G.NRACESSONFE AND I.NRAUTOFORN IS NOT NULL)
      ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIAL, G.NRLANCTONF)
    WHERE ROWNUM <= :lim""", dias=JANELA, lim=LIMITE_B)
alvos_b = [r[0] for r in cur.fetchall()]
print(" %d nota(s) na fila (%.0f s para achar)" % (len(alvos_b), time.time() - t0))

placar = Counter()
motivos = Counter()
sem_prod = 0
for ch in alvos_b:
    nota, itens = monta(ch)
    if not itens:
        placar["sem itens"] += 1
        continue
    faltam = [i for i in itens if not i.get("cd_interno")]
    if faltam:
        sem_prod += 1
    try:
        d = escolher_af(cur, nota, itens, TOL[0])
    except Exception as e:
        placar["erro no motor"] += 1
        motivos[str(e)[:70]] += 1
        continue
    dec = d.get("decisao")
    if dec == AUTOMATICA:
        placar["resolveria sozinho"] += 1
    elif dec == AMBIGUA:
        placar["manda para a pessoa (mais de uma opcao)"] += 1
    else:
        placar["nao achou AF"] += 1
    motivos[(d.get("motivo") or "")[:68]] += 1

print("\n  O QUE O ROBO FARIA COM ESSAS %d NOTAS:" % len(alvos_b))
for k, v in placar.most_common():
    print("    %-42s %3d  (%.0f%%)" % (k, v, 100.0 * v / max(1, len(alvos_b))))
print("    %-42s %3d" % ("(dessas, com item sem De/Para)", sem_prod))
print("\n  MOTIVOS MAIS COMUNS:")
for m, v in motivos.most_common(6):
    print("    %3dx %s" % (v, m))

con.close()
print("\n" + "=" * 78)
