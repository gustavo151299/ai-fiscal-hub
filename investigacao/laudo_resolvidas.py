# -*- coding: utf-8 -*-
r"""laudo_resolvidas.py - A LOGICA, NOTA A NOTA, DAS QUE O ROBO RESOLVEU.

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario AIHUB_LEITURA, que o banco
recusa para escrita) e chama as funcoes do robo em modo simulacao.

PARA QUE SERVE: a equipe conferir se o robo acertou. A folha de conferencia diz
QUANTAS; esta diz POR QUE cada uma. Para cada nota resolvida sozinho:

  - os itens da nota e o produto interno de cada um
  - a AF escolhida, com entrega, saldo por produto e situacao
  - QUANTAS AFs concorriam, e por que as outras sairam
  - o que a pessoa escolheu, quando ja escolheu

Recebe as NF pela linha de comando ou por arquivo (uma por linha), para nao ter
de reavaliar a fila inteira:

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\laudo_resolvidas.py nfs.txt
    ... py investigacao\laudo_resolvidas.py 000011355 000068778
"""
import io
import os
import sys
import time

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RAIZ)
import oracledb

from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, janela_da_filial,
                             _SQL_AF_CANDIDATA, AUTOMATICA)
from robo.servico_robo import resolver_depara_do_xml, LIMIAR_AUTO

alvo = sys.argv[1:]
if len(alvo) == 1 and os.path.exists(alvo[0]):
    alvo = [x.strip() for x in io.open(alvo[0], encoding="utf-8")
            .read().split("\n") if x.strip()]
if not alvo:
    print("Informe as NF, ou um arquivo com uma por linha.")
    sys.exit(1)

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
print("Avaliando %s nota(s)..." % len(alvo))

# Janela so para DESEMPATAR nota homonima (achado 118), nunca para filtrar:
# com 3 dias fixos o laudo descartava, CALADO, nota que a folha tinha achado.
DIAS = int(os.getenv("AIHUB_LAUDO_DIAS", "10"))
print("Janela de busca: %s dias (AIHUB_LAUDO_DIAS)" % DIAS)

nao_achadas = []
nao_automaticas = []
fichas = []
t0 = time.time()
for k, nf in enumerate(alvo, 1):
    if k % 10 == 0:
        print("   ... %s de %s (%.0f s)" % (k, len(alvo), time.time() - t0))
    cur.execute("""SELECT G.NRACESSONFE, G.CDFORNECED, G.CDFILIRECE, G.DTEMISSAO,
                          TO_CHAR(G.DTINCLUSAO,'DD/MM HH24:MI'),
                          (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
                            WHERE F.CDFORNECED = G.CDFORNECED),
                          CASE WHEN G.DTLANCAMENNF IS NOT NULL
                                 OR G.DTUTILIZAXML IS NOT NULL
                               THEN 'S' ELSE 'N' END
                   FROM TEKNISA.INTG_NOTAFISCAL G
                   WHERE G.NRNOTAFISC = :n
                     AND G.DTINCLUSAO >= TRUNC(SYSDATE) - :dias
                     AND NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
                   ORDER BY G.DTINCLUSAO DESC FETCH FIRST 1 ROWS ONLY""",
                n=nf, dias=DIAS)
    r = cur.fetchone()
    if not r:
        nao_achadas.append(nf)
        continue
    ch, forn, fil, dtem, cheg, nmf, entrou = r
    F = {"nf": nf, "forn": nmf or forn, "filial": fil, "chegou": cheg,
         "entrou": entrou == "S",
         "emissao": dtem.strftime("%d/%m/%Y") if dtem else "-",
         "itens": [], "af": "", "af_pessoa": "", "concorrentes": [],
         "entrega": "", "novos_depara": []}

    itens = itens_do_xml(cur, ch)
    traduzir_para_interno(cur, forn, itens)
    F["af_pessoa"] = ", ".join(sorted({i["af_atual"] for i in itens
                                       if i.get("af_atual")}))
    # o De/Para que o robo criaria antes de procurar a AF
    falt = [i for i in itens if not i.get("cd_interno")]
    if falt:
        det = []
        try:
            resolver_depara_do_xml(con, ch, True, det)
            esc = {d.get("cod_fornecedor"): d.get("escolhido")
                   for d in det if d.get("auto") and d.get("escolhido")}
            for d in det:
                if d.get("auto"):
                    F["novos_depara"].append(
                        "%s -> %s %s (%s%% de semelhanca)"
                        % ((d.get("desc_xml") or "")[:32], d.get("escolhido"),
                           (d.get("nome_escolhido") or "")[:26], d.get("score")))
            for it in falt:
                cd = esc.get(it.get("cprod_xml"))
                if not cd:
                    continue
                it["cd_interno"] = it["cd_estoque"] = cd
                it["fator"] = 1.0
                cur.execute("""SELECT CDPRODESTO, NVL(VRFATOCONV,1)
                               FROM TEKNISA.PRODUTO WHERE CDPRODUTO=:p""", p=cd)
                pr = cur.fetchone()
                if pr:
                    it["cd_estoque"] = pr[0] or cd
                    it["fator"] = float(pr[1] or 1)
        except Exception:
            pass
    for i in itens:
        cur.execute("""SELECT NMPRODUTO FROM TEKNISA.PRODUTO
                       WHERE CDPRODUTO = :p""", p=i.get("cd_interno"))
        p = cur.fetchone()
        F["itens"].append({
            "seq": i["seq"], "desc": i.get("desc_xml") or "",
            "qt": i.get("qt"), "un": i.get("un"),
            "cd": i.get("cd_interno") or "", "nm": (p[0] if p else "") or "",
            "estoque": i.get("cd_estoque") or "", "fator": i.get("fator")})

    cegos = [dict(i, af_atual=None) for i in itens]
    try:
        d = escolher_af(cur, {"CDFORNECED": forn, "CDFILIRECE": fil,
                              "DTEMISSAO": dtem, "NRNOTAFISC": nf}, cegos, tol)
    except Exception:
        continue
    if d.get("decisao") != AUTOMATICA:
        nao_automaticas.append((nf, d.get("decisao")))
        continue
    F["af"] = (d.get("af") or {}).get("af") or ", ".join(
        sorted({p["af"] for p in (d.get("plano") or []) if p.get("af")}))
    F["motivo"] = (d.get("motivo") or "")

    # QUANTAS AFs CONCORRIAM: as do fornecedor, na filial, com os produtos e
    # dentro da janela, ANTES do filtro de saldo/baixa. Mostra que a escolha
    # nao foi "a unica que existia", e sim a unica que sobrou depois das regras
    # do proprio Teknisa.
    est = sorted({i["cd_estoque"] for i in itens if i.get("cd_estoque")})
    anc, da, dp = janela_da_filial(cur, fil, dtem)
    lista = ",".join("'%s'" % e for e in est)
    try:
        cur.execute("""
          SELECT A.NRAUTOFORN, TO_CHAR(MIN(I.DTPROGENTAF),'DD/MM/YYYY'),
                 NVL(MAX(A.IDBAIXAAF),'-'), NVL(MAX(A.IDAFEXPIRADA),'-'),
                 ROUND(SUM((NVL(I.QTPROGENTAF,0)-NVL(I.QTEFETENTRAF,0))
                       * NVL(NULLIF(PA.VRFATOCONV,0),1)),2),
                 COUNT(DISTINCT NVL(PA.CDPRODESTO, PA.CDPRODUTO))
          FROM TEKNISA.AUTOFORNE A
          JOIN TEKNISA.ITEMAUTF I ON I.CDFILDEST=A.CDFILDEST
               AND I.CDFILLANC=A.CDFILLANC AND I.NRAUTOFORN=A.NRAUTOFORN
          JOIN TEKNISA.PRODUTO PA ON PA.CDPRODUTO=I.CDPRODUTO
          WHERE A.CDFORNECED=:f AND A.CDFILDEST=:fl AND NVL(A.IDATIVO,'S')<>'N'
            AND NVL(PA.CDPRODESTO,PA.CDPRODUTO) IN (%s)
            AND (:dant IS NULL OR I.DTPROGENTAF >= NVL(:dtp,TRUNC(SYSDATE)) - :dant)
            AND (:dpos IS NULL OR I.DTPROGENTAF <= NVL(:dtp,TRUNC(SYSDATE)) + :dpos)
          GROUP BY A.NRAUTOFORN
          ORDER BY MIN(I.DTPROGENTAF)
          FETCH FIRST 8 ROWS ONLY""" % lista,
                    {"f": forn, "fl": fil, "dant": da, "dpos": dp, "dtp": anc})
        for af, ent, bx, ex, saldo, ncob in cur.fetchall():
            if af == F["af"]:
                F["entrega"] = ent
            por = []
            if bx not in ("1", "-"):
                por.append("ja baixada")
            if ex != "N":
                por.append("expirada")
            if (saldo or 0) <= 0:
                por.append("sem saldo")
            if ncob < len(est):
                por.append("cobre so %s de %s produtos" % (ncob, len(est)))
            F["concorrentes"].append(
                {"af": af, "entrega": ent, "saldo": saldo,
                 "escolhida": af == F["af"],
                 "porque_nao": ", ".join(por) or ""})
    except Exception:
        pass
    fichas.append(F)

print("\n %s nota(s) com laudo montado (%.0f s)" % (len(fichas), time.time() - t0))
if nao_achadas:
    print(" %s nota(s) NAO achadas na janela de %s dias: %s"
          % (len(nao_achadas), DIAS, ", ".join(nao_achadas)))
    print("   aumente AIHUB_LAUDO_DIAS se a folha usou janela maior")
if nao_automaticas:
    print(" %s nota(s) que o motor NAO resolve mais sozinho agora:" % len(nao_automaticas))
    for _n, _dec in nao_automaticas:
        print("   %s -> %s" % (_n, _dec))


def e(x):
    return (str(x if x is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


html = [u"""<!doctype html><meta charset="utf-8"><title>Laudo das notas resolvidas</title>
<style>
 body{font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:26px;
   color:#16202e;font-size:14px;max-width:1060px}
 h1{font-size:21px;margin:0 0 4px}.sub{color:#4a5a70;margin-bottom:18px}
 .nota{border:1px solid #dde4ed;border-radius:12px;padding:16px 18px;margin-bottom:16px}
 .cab{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;
   align-items:baseline;margin-bottom:12px}
 .cab b{font-size:16px}.cab .m{color:#7d8da0;font-size:12.5px}
 h4{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#7d8da0;
   margin:14px 0 6px}
 table{border-collapse:collapse;width:100%%;font-size:12.5px}
 th{text-align:left;font-size:10.5px;text-transform:uppercase;color:#7d8da0;
   padding:5px 8px;border-bottom:1px solid #dde4ed}
 td{padding:5px 8px;border-bottom:1px solid #f2f5f9}
 .ok{background:#e3f4ea}.t{display:inline-block;font-size:11px;font-weight:700;
   padding:2px 8px;border-radius:999px;background:#e3f4ea;color:#147a4a}
 .t.w{background:#fbeedb;color:#a8630a}.t.n{background:#eef2f7;color:#4a5a70}
 .mut{color:#7d8da0}.novo{background:#e7effa;border-radius:8px;padding:8px 11px;
   font-size:12.5px;color:#1f5f9e;margin-top:8px}
 .aviso{background:#fbeedb;border-radius:9px;padding:11px 14px;margin-bottom:18px;
   font-size:13px;color:#6b4a0f}
 /* PARA IMPRIMIR / VIRAR PDF. Sem isto uma nota quebra no meio da pagina e a
    tabela de concorrentes fica separada dos itens a que se refere - quem
    confere perde a ligacao entre as duas. */
 @page{size:A4;margin:13mm 11mm}
 @media print{
   body{margin:0;max-width:none;font-size:10.5px}
   h1{font-size:16px}
   .sub{font-size:11px;margin-bottom:12px}
   .aviso{font-size:10.5px;padding:9px 11px;margin-bottom:14px}
   .nota{break-inside:avoid;page-break-inside:avoid;margin-bottom:10px;
     padding:11px 13px}
   .cab b{font-size:13px}
   table{font-size:9.5px}
   th{padding:3px 6px}td{padding:3px 6px}
   h4{margin:9px 0 4px}
   /* o fundo verde da AF escolhida tem de sair na impressao, senao a linha
      mais importante da tabela vira uma linha comum */
   .ok{background:#e3f4ea !important;-webkit-print-color-adjust:exact;
     print-color-adjust:exact}
   .t{-webkit-print-color-adjust:exact;print-color-adjust:exact}
 }
</style>
<h1>Laudo das notas que o rob&ocirc; resolveu sozinho</h1>
<div class="sub">Lido de <b>produ&ccedil;&atilde;o</b>, em consulta, em %s &middot; %s notas</div>
<div class="aviso"><b>Como conferir.</b> Em cada nota: os itens e o produto interno de
cada um, a AF escolhida, e <b>quais AFs concorriam</b> com o motivo de cada uma ter
sa&iacute;do. A linha verde &eacute; a escolhida. Onde algu&eacute;m j&aacute; tinha
resolvido, a AF da pessoa aparece ao lado &mdash; se s&atilde;o iguais, o crit&eacute;rio
do rob&ocirc; &eacute; o mesmo da equipe.<br><br>
Em algumas notas aparece <b>&quot;n&atilde;o d&aacute; para comparar&quot;</b>: elas
j&aacute; entraram no Teknisa, e ao entrar consumiram a AF. O sistema deixa de
enxerg&aacute;-la, e a compara&ccedil;&atilde;o acusaria uma diverg&ecirc;ncia que
n&atilde;o existe.</div>""" % (e(agora), len(fichas))]

for F in fichas:
    # SO COMPARA NA NOTA QUE AINDA NAO ENTROU. Depois da importacao a AF foi
    # consumida, o predicado nativo deixa de enxerga-la e o motor escolhe a
    # vizinha (achado 60): comparar ali acusa "DIFERENTE" que e artefato da
    # medicao. A primeira versao deste laudo mostrava 7 divergencias onde a
    # folha mostrava 1 - e mandaria a equipe investigar 7 casos inexistentes.
    comparavel = bool(F["af_pessoa"]) and not F["entrou"]
    igual = comparavel and F["af"] in [x.strip()
                                       for x in F["af_pessoa"].split(",")]
    html.append(u'<div class="nota"><div class="cab"><div><b>NF %s</b> '
                u'<span class="m">%s</span></div><div class="m">chegou %s '
                u'&middot; emiss&atilde;o %s &middot; filial %s</div></div>'
                % (e(F["nf"]), e(F["forn"]), e(F["chegou"]), e(F["emissao"]),
                   e(F["filial"])))
    html.append(u'<div><span class="t">AF %s</span> ' % e(F["af"]))
    if comparavel:
        html.append(u'<span class="t %s">a pessoa escolheu %s &mdash; %s</span>'
                    % ("" if igual else "w", e(F["af_pessoa"]),
                       "IGUAL" if igual else "DIFERENTE"))
    elif F["af_pessoa"]:
        html.append(u'<span class="t n">a pessoa escolheu %s &middot; a nota j&aacute; '
                    u'entrou, ent&atilde;o esta n&atilde;o d&aacute; para comparar</span>'
                    % e(F["af_pessoa"]))
    else:
        html.append(u'<span class="t n">ningu&eacute;m tinha mexido nesta nota</span>')
    html.append(u'</div><div class="mut" style="margin-top:6px">%s</div>'
                % e(F.get("motivo", ""))[:200])
    if F["novos_depara"]:
        html.append(u'<div class="novo"><b>O rob&ocirc; cadastrou o produto '
                    u'antes de procurar a AF:</b><br>%s</div>'
                    % "<br>".join(e(x) for x in F["novos_depara"]))
    html.append(u'<h4>Itens da nota</h4><table><tr><th>#</th><th>Como veio na nota</th>'
                u'<th>Qtd</th><th>Produto interno</th></tr>')
    for i in F["itens"]:
        html.append(u'<tr><td>%s</td><td>%s</td><td>%s %s</td>'
                    u'<td><b>%s</b> %s</td></tr>'
                    % (e(i["seq"]), e(i["desc"][:44]), e(i["qt"]), e(i["un"]),
                       e(i["cd"]), e(i["nm"][:34])))
    html.append(u'</table>')
    if F["concorrentes"]:
        html.append(u'<h4>AFs que concorriam (mesmo fornecedor, mesma filial, '
                    u'com os produtos, na janela)</h4>'
                    u'<table><tr><th>AF</th><th>Entrega</th><th>Saldo</th>'
                    u'<th>Situa&ccedil;&atilde;o</th></tr>')
        for c in F["concorrentes"]:
            html.append(u'<tr class="%s"><td><b>%s</b></td><td>%s</td><td>%s</td>'
                        u'<td>%s</td></tr>'
                        % ("ok" if c["escolhida"] else "", e(c["af"]),
                           e(c["entrega"]), e(c["saldo"]),
                           "<b>ESCOLHIDA</b>" if c["escolhida"]
                           else e(c["porque_nao"] or "nao atende")))
        html.append(u'</table>')
    html.append(u'</div>')

destino = os.getenv("LR_SAIDA") or os.path.join(RAIZ, "laudo_resolvidas.html")
io.open(destino, "w", encoding="utf-8").write("\n".join(html))
print(" laudo: %s" % destino)
con.close()
