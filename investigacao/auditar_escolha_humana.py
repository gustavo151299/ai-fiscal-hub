# -*- coding: utf-8 -*-
r"""auditar_escolha_humana.py - A LOGICA DA PESSOA E A MESMA DO ROBO?

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario AIHUB_LEITURA, que o banco
recusa para escrita).

A PERGUNTA QUE ELE RESPONDE e diferente da folha de conferencia. Aquela compara
NUMERO de AF: bateu ou nao bateu. Esta pergunta e outra: **a AF que a pessoa
escolheu passaria nos filtros do robo?** Se passa, os dois usam o mesmo
criterio, e a diferenca de numero (quando houver) e so preferencia entre duas
opcoes validas. Se nao passa, o criterio e diferente - e vale entender qual dos
dois esta certo.

OS SEIS FILTROS conferidos, um a um, na AF DA PESSOA:
  1. mesmo fornecedor da nota
  2. filial de destino = filial que recebe a nota
  3. a AF tem os produtos da nota
  4. entrega dentro da janela
  5. AF viva pelo predicado nativo (sem baixa, nao expirada, com saldo > 0)
  6. cobre TODOS os produtos da nota

O QUE NAO E FILTRO, e por que: "o saldo cobre a quantidade da nota". A primeira
versao conferia isso e reprovou 28 escolhas humanas - inclusive notas em que o
proprio robo escolheu A MESMA AF, o que ja denunciava o erro. O motor NAO exige
saldo suficiente: `candidatas()` marca `dentro_tolerancia` e devolve a AF
mesmo assim, porque ENTREGA PARCIAL E O CASO NORMAL - a NF_BAIXA_AF consome
min(quantidade, saldo) e leva o resto para a proxima AF. Conferir por quantidade
seria auditar uma regra que o sistema nao tem.

SO VALE NA NOTA NAO LANCADA. Depois da importacao a AF foi consumida e os
filtro 5 acusaria falha que e consequencia da propria nota (achado 60).
Por isso a consulta exclui as ja lancadas.

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\auditar_escolha_humana.py
    ... py investigacao\auditar_escolha_humana.py 5     (ultimos 5 dias)
"""
import io
import os
import sys
import time
from collections import Counter

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RAIZ)
import oracledb

from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, janela_da_filial, AUTOMATICA,
                             AMBIGUA)

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

for _v in ("AIHUB_PROD_USER", "AIHUB_PROD_PASS", "AIHUB_PROD_DSN"):
    if not os.getenv(_v):
        print("Falta %s. Rode: call CONFIG_PRODUCAO_LEITURA.bat" % _v)
        sys.exit(1)

con = oracledb.connect(user=os.environ["AIHUB_PROD_USER"],
                       password=os.environ["AIHUB_PROD_PASS"],
                       dsn=os.environ["AIHUB_PROD_DSN"])
cur = con.cursor()
cur.execute("SELECT USER, TO_CHAR(SYSDATE,'DD/MM/YYYY HH24:MI') FROM DUAL")
usr, agora = cur.fetchone()
tol = ler_tolerancias(cur)[0]

L = "=" * 78
print(L)
print(" A ESCOLHA DA PESSOA PASSA NOS FILTROS DO ROBO? - %s" % agora)
print(" (%s, somente leitura)" % usr)
print(L)

cur.execute("""
    SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE, G.DTEMISSAO,
           (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
             WHERE F.CDFORNECED = G.CDFORNECED)
    FROM   TEKNISA.INTG_NOTAFISCAL G
    WHERE  G.DTINCLUSAO >= TRUNC(SYSDATE) - :d + 1
      AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
      AND  G.DTLANCAMENNF IS NULL
      AND  G.DTUTILIZAXML IS NULL
      AND  EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                    WHERE I.NRACESSONFE = G.NRACESSONFE
                      AND I.NRAUTOFORN IS NOT NULL)
    ORDER  BY G.DTINCLUSAO, G.NRNOTAFISC
""", d=DIAS)
notas = cur.fetchall()
print("\n %s nota(s) com AF escolhida por uma pessoa e ainda nao lancadas."
      % len(notas))
print(" Auditando a escolha delas...\n")

FILTROS = ["1 mesmo fornecedor", "2 filial de destino", "3 tem os produtos",
           "4 dentro da janela", "5 AF viva (sem baixa/expirada/com saldo)",
           "6 cobre TODOS os produtos"]
falhas = Counter()
placar = Counter()
casos = []
t0 = time.time()
for k, (ch, nr, forn, fil, dtem, nmf) in enumerate(notas, 1):
    if k % 20 == 0:
        print("   ... %s de %s (%.0f s)" % (k, len(notas), time.time() - t0))
    itens = itens_do_xml(cur, ch)
    traduzir_para_interno(cur, forn, itens)
    afs = sorted({i["af_atual"] for i in itens if i.get("af_atual")})
    if len(afs) != 1:
        placar["a pessoa usou mais de uma AF na nota"] += 1
        continue
    af_p = afs[0]
    est = sorted({i["cd_estoque"] for i in itens if i.get("cd_estoque")})
    if not est or any(not i.get("cd_interno") for i in itens):
        placar["item sem produto interno (nao da para auditar)"] += 1
        continue
    need = {}
    for i in itens:
        q = float(i.get("qt") or 0) * float(i.get("fator") or 1)
        need[i["cd_estoque"]] = need.get(i["cd_estoque"], 0) + q

    # --- a AF DA PESSOA, filtro por filtro -------------------------------- #
    ok = [True] * 6
    porque = []
    try:
        cur.execute("""
            SELECT A.CDFORNECED, A.CDFILDEST, NVL(A.IDBAIXAAF,'-'),
                   NVL(A.IDAFEXPIRADA,'-'), NVL(A.IDSITUATUAAF,'-'),
                   NVL(A.IDAFFORALIM,'N'), NVL(A.IDAFOPERLOG,'1'),
                   TO_CHAR(MIN(I.DTPROGENTAF),'DD/MM/YYYY'),
                   MIN(I.DTPROGENTAF)
            FROM   TEKNISA.AUTOFORNE A
            JOIN   TEKNISA.ITEMAUTF I ON I.CDFILDEST=A.CDFILDEST
                   AND I.CDFILLANC=A.CDFILLANC AND I.NRAUTOFORN=A.NRAUTOFORN
            WHERE  A.NRAUTOFORN = :a
            GROUP  BY A.CDFORNECED, A.CDFILDEST, A.IDBAIXAAF, A.IDAFEXPIRADA,
                      A.IDSITUATUAAF, A.IDAFFORALIM, A.IDAFOPERLOG
        """, a=af_p)
        r = cur.fetchone()
    except Exception:
        r = None
    if not r:
        placar["a AF da pessoa nao foi encontrada"] += 1
        continue
    (a_forn, a_fil, bx, ex, si, fa, ol, ent_txt, ent_dt) = r

    if a_forn != forn:
        ok[0] = False
        porque.append("a AF e do fornecedor %s, a nota e do %s" % (a_forn, forn))
    if a_fil != fil:
        ok[1] = False
        porque.append("a AF e da filial %s, a nota recebe na %s" % (a_fil, fil))
    # produtos da AF
    cur.execute("""SELECT DISTINCT NVL(PA.CDPRODESTO, PA.CDPRODUTO)
                   FROM TEKNISA.ITEMAUTF I
                   JOIN TEKNISA.PRODUTO PA ON PA.CDPRODUTO = I.CDPRODUTO
                   WHERE I.NRAUTOFORN = :a""", a=af_p)
    tem = {x[0] for x in cur.fetchall()}
    cobre = [e for e in est if e in tem]
    if not cobre:
        ok[2] = False
        porque.append("a AF nao tem nenhum dos produtos da nota")
    anc, da, dp = janela_da_filial(cur, fil, dtem)
    if ent_dt and anc:
        dif = (ent_dt.date() - anc.date()).days
        if (da is not None and dif < -da) or (dp is not None and dif > dp):
            ok[3] = False
            porque.append("entrega %s esta a %+d dias da nota (janela e -%s/+%s)"
                          % (ent_txt, dif, da, dp))
    viva = (bx in ("1", "-") and ex == "N" and si == "-"
            and fa == "N" and ol == "1")
    if not viva:
        ok[4] = False
        det = []
        if bx not in ("1", "-"):
            det.append("IDBAIXAAF=%s" % bx)
        if ex != "N":
            det.append("expirada")
        if si != "-":
            det.append("situacao=%s" % si)
        if fa != "N":
            det.append("fora da linha")
        if ol != "1":
            det.append("operador logistico")
        porque.append("a AF esta " + ", ".join(det))
    if len(cobre) < len(est):
        ok[5] = False
        porque.append("cobre %s de %s produtos (faltam %s)"
                      % (len(cobre), len(est),
                         ", ".join(e for e in est if e not in tem)[:40]))
    # O SALDO E INFORMACAO, NAO REPROVACAO. Entrega parcial e normal: a nativa
    # consome min(quantidade, saldo) e leva o resto para a proxima AF.
    cur.execute("""SELECT NVL(PA.CDPRODESTO,PA.CDPRODUTO),
                          ROUND(SUM((NVL(I.QTPROGENTAF,0)-NVL(I.QTEFETENTRAF,0))
                                * NVL(NULLIF(PA.VRFATOCONV,0),1)),4)
                   FROM TEKNISA.ITEMAUTF I
                   JOIN TEKNISA.PRODUTO PA ON PA.CDPRODUTO=I.CDPRODUTO
                   WHERE I.NRAUTOFORN=:a
                   GROUP BY NVL(PA.CDPRODESTO,PA.CDPRODUTO)""", a=af_p)
    saldos = {p: float(s or 0) for p, s in cur.fetchall()}
    parcial = sum(1 for p, q in need.items() if saldos.get(p, 0.0) < q)

    passou = all(ok)
    if passou and parcial:
        placar["   (dessas, com entrega parcial - e normal)"] += 1
    for i, v in enumerate(ok):
        if not v:
            falhas[FILTROS[i]] += 1

    # --- o que o robo escolheria ----------------------------------------- #
    cegos = [dict(i, af_atual=None) for i in itens]
    try:
        d = escolher_af(cur, {"CDFORNECED": forn, "CDFILIRECE": fil,
                              "DTEMISSAO": dtem, "NRNOTAFISC": nr}, cegos, tol)
    except Exception:
        d = {}
    dec = d.get("decisao")
    af_r = ""
    if dec == AUTOMATICA:
        af_r = (d.get("af") or {}).get("af") or ", ".join(
            sorted({p["af"] for p in (d.get("plano") or []) if p.get("af")}))

    if passou and af_r == af_p:
        placar["MESMA LOGICA: passa nos filtros e o robo escolheria a mesma"] += 1
    elif passou and dec == AMBIGUA:
        placar["passa nos filtros; o robo veria mais de uma e deixaria escolher"] += 1
    elif passou and af_r:
        placar["passa nos filtros, mas o robo preferiria outra"] += 1
        casos.append((nr, nmf, af_p, af_r, "as duas sao validas; o robo prefere "
                      "a de entrega mais antiga com saldo", ok))
    elif passou:
        placar["passa nos filtros; o robo nao decidiria sozinho"] += 1
    else:
        placar["A ESCOLHA DA PESSOA NAO PASSA nos filtros do robo"] += 1
        casos.append((nr, nmf, af_p, af_r or "(nao decidiria)",
                      "; ".join(porque)[:150], ok))

print("\n" + L)
print(" RESULTADO DA AUDITORIA")
print(L)
for k, v in placar.most_common():
    print("   %-58s %3s" % (k, v))
if falhas:
    print("\n EM QUE FILTRO A ESCOLHA HUMANA FALHOU:")
    for k, v in falhas.most_common():
        print("   %-46s %3s nota(s)" % (k, v))
if casos:
    print("\n OS CASOS, PARA CONFERIR:")
    for nr, nmf, ap, ar, pq, ok in casos[:20]:
        print("\n   NF %-12s %s" % (nr, (nmf or "")[:40]))
        print("     pessoa: AF %s   |   robo: AF %s" % (ap, ar))
        print("     %s" % pq)
    if len(casos) > 20:
        print("\n   ... e mais %s" % (len(casos) - 20))
print("\n" + L)
con.close()
