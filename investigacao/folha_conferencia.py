# -*- coding: utf-8 -*-
r"""folha_conferencia.py - O QUE O ROBO TERIA FEITO, PARA UMA PESSOA CONFERIR.

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario AIHUB_LEITURA, que o banco
recusa para escrita) e chama as funcoes do robo em modo simulacao.

PARA QUE SERVE: levar para o setor validar. Cada linha traz a nota, a AF que o
robo escolheria e - quando alguem ja resolveu - a AF que a PESSOA escolheu, com
o veredito lado a lado. Quem confere nao precisa entender o sistema: olha as
duas colunas.

COMO A DECISAO E TOMADA: o motor decide COM A ESCOLHA HUMANA APAGADA
(af_atual=None). Sem isso ele veria a resposta e a conferencia nao valeria
nada. E o gabarito so e honesto na nota que ainda NAO foi importada: depois da
importacao a AF foi consumida, o predicado nativo deixa de enxerga-la e o motor
escolheria a vizinha - a precisao despencaria por artefato (achado 60). Por
isso a nota ja importada aparece separada, sem veredito.

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\folha_conferencia.py
    ... py investigacao\folha_conferencia.py 3      (ultimos 3 dias)
Saida: folha_conferencia.html na raiz (abre no navegador) e resumo na tela.
"""
import io
import os
import sys
import time

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RAIZ)
import oracledb

from core.cfop_natureza import natureza, COMPRA
from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, AUTOMATICA, AMBIGUA)
from robo.servico_robo import nota_bloqueada, resolver_depara_do_xml

DIAS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

for _v in ("AIHUB_PROD_USER", "AIHUB_PROD_PASS", "AIHUB_PROD_DSN"):
    if not os.getenv(_v):
        print("Falta %s. Rode: call CONFIG_PRODUCAO_LEITURA.bat" % _v)
        sys.exit(1)

def _abrir():
    return oracledb.connect(user=os.environ["AIHUB_PROD_USER"],
                            password=os.environ["AIHUB_PROD_PASS"],
                            dsn=os.environ["AIHUB_PROD_DSN"])


# RECONEXAO. Uma rodada sobre a quinzena leva mais de uma hora, e a conexao cai
# nesse tempo: medido em 25/08, 31 de 599 notas voltaram "DPY-1001: not
# connected to database" e ficaram sem avaliar. O erro nao e do sistema, mas o
# efeito e o mesmo - buraco no relatorio que vai para o setor.
_CAIU = ("DPY-1001", "DPY-4011", "ORA-03113", "ORA-03114", "ORA-02396",
         "ORA-01012",
         # rede/DNS: a reconexao pode cair no mesmo buraco que derrubou a
         # conexao. Sem isto, um piscar de rede vira ERRO definitivo.
         "getaddrinfo", "DPY-6005", "Name or service not known",
         "Temporary failure in name resolution", "ORA-12545", "ORA-12541")


def _caiu(e):
    t = str(e)
    return any(c in t for c in _CAIU)


con = _abrir()
cur = con.cursor()
cur.execute("SELECT USER, TO_CHAR(SYSDATE,'DD/MM/YYYY HH24:MI') FROM DUAL")
usr, agora = cur.fetchone()
tol = ler_tolerancias(cur)[0]

print("=" * 78)
print(" FOLHA DE CONFERENCIA - %s  (%s, somente leitura)" % (agora, usr))
print(" Notas que chegaram nos ultimos %s dias" % DIAS)
print("=" * 78)

cur.execute("""
    SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE, G.DTEMISSAO,
           TO_CHAR(G.DTINCLUSAO,'DD/MM HH24:MI'), G.CDTIPOOPER,
           NVL(G.DSTAGORIGFILI,'(automatica)'),
           CASE WHEN G.DTLANCAMENNF IS NOT NULL OR G.DTUTILIZAXML IS NOT NULL
                THEN 'S' ELSE 'N' END,
           (SELECT MIN(I.CDFISOPEITEM) FROM TEKNISA.INTG_ITEMPRODUTO I
             WHERE I.NRACESSONFE = G.NRACESSONFE),
           (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
             WHERE F.CDFORNECED = G.CDFORNECED)
    FROM   TEKNISA.INTG_NOTAFISCAL G
    WHERE  G.DTINCLUSAO >= TRUNC(SYSDATE) - :d + 1
      AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
    ORDER  BY G.DTINCLUSAO, G.NRNOTAFISC
""", d=DIAS)
notas = cur.fetchall()
print("\n %s nota(s) nao manuais no periodo. Avaliando..." % len(notas))

linhas = []
t0 = time.time()
for k, (ch, nr, forn, fil, dtem, chegou, tipo, orig, entrou, cfop, nmf) in \
        enumerate(notas, 1):
    if k % 25 == 0:
        print("   ... %s de %s (%.0f s)" % (k, len(notas), time.time() - t0))
    L = {"nf": nr, "forn": (nmf or forn or "")[:40], "chegou": chegou,
         "filial": fil, "entrou": entrou == "S", "af_pessoa": "", "af_robo": "",
         "situacao": "", "detalhe": ""}
    try:
        itens = itens_do_xml(cur, ch)
        traduzir_para_interno(cur, forn, itens)
        L["af_pessoa"] = ", ".join(sorted({i["af_atual"] for i in itens
                                           if i.get("af_atual")}))
    except Exception as e:
        if not _caiu(e):
            L["situacao"] = "ERRO"
            L["detalhe"] = str(e)[:70]
            linhas.append(L)
            continue
        print("   (conexao caiu; reconectando e refazendo a NF %s)" % nr)
        erro_final = None
        for _tent in range(3):
            try:
                if _tent:
                    time.sleep(5 * _tent)   # 5s, depois 10s
                con = _abrir()
                cur = con.cursor()
                itens = itens_do_xml(cur, ch)
                traduzir_para_interno(cur, forn, itens)
                L["af_pessoa"] = ", ".join(sorted({i["af_atual"] for i in itens
                                                   if i.get("af_atual")}))
                erro_final = None
                break
            except Exception as e2:
                erro_final = e2
                print("   (tentativa %s falhou: %s)" % (_tent + 1, str(e2)[:50]))
        if erro_final is not None:
            L["situacao"] = "ERRO"
            L["detalhe"] = str(erro_final)[:70]
            linhas.append(L)
            continue

    morta, porque = nota_bloqueada(cur, ch)
    if morta:
        L["situacao"] = "RECUSADA"
        L["detalhe"] = porque[:80]
        linhas.append(L)
        continue
    if natureza(cfop)[0] != COMPRA:
        L["situacao"] = "NAO E COMPRA"
        L["detalhe"] = "CFOP %s: %s" % (cfop, natureza(cfop)[1][:60])
        linhas.append(L)
        continue
    if not fil:
        L["situacao"] = "SEM FILIAL"
        L["detalhe"] = "a nota nao diz qual unidade recebeu"
        linhas.append(L)
        continue

    # o De/Para que o robo criaria antes de procurar a AF
    falt = [i for i in itens if not i.get("cd_interno")]
    if falt:
        det = []
        try:
            resolver_depara_do_xml(con, ch, True, det)
            esc = {d.get("cod_fornecedor"): d.get("escolhido")
                   for d in det if d.get("auto") and d.get("escolhido")}
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
    ainda = [i for i in itens if not i.get("cd_interno")]
    if ainda:
        L["situacao"] = "FALTA PRODUTO"
        L["detalhe"] = ("%s item(ns) sem produto interno: %s"
                        % (len(ainda),
                           ", ".join((i["desc_xml"] or "")[:24] for i in ainda[:2])))
        linhas.append(L)
        continue

    # DECIDE SEM VER A ESCOLHA DA PESSOA
    cegos = [dict(i, af_atual=None) for i in itens]
    try:
        d = escolher_af(cur, {"CDFORNECED": forn, "CDFILIRECE": fil,
                              "DTEMISSAO": dtem, "NRNOTAFISC": nr}, cegos, tol)
    except Exception as e:
        L["situacao"] = "ERRO"
        L["detalhe"] = str(e)[:70]
        linhas.append(L)
        continue
    dec = d.get("decisao")
    if dec == AUTOMATICA:
        af = (d.get("af") or {}).get("af") or ", ".join(
            sorted({p["af"] for p in (d.get("plano") or []) if p.get("af")}))
        L["af_robo"] = af
        L["situacao"] = "SOZINHO"
        L["detalhe"] = (d.get("motivo") or "")[:90]
    elif dec == AMBIGUA:
        n_pl = len(d.get("plano") or [])
        L["situacao"] = "LEVA PRONTO"
        L["detalhe"] = ("distribuicao pronta de %s item(ns)" % n_pl if n_pl
                        else "%s AF(s) na tela para escolher"
                             % len(d.get("opcoes") or []))
    else:
        L["situacao"] = "NAO ACHOU AF"
        L["detalhe"] = (d.get("motivo") or "")[:90]
    linhas.append(L)

# ------------------------------------------------------------- veredito ---
# So vale conferir na nota NAO importada: depois da importacao a AF foi
# consumida e o predicado nativo deixa de enxerga-la (achado 60).
for L in linhas:
    if L["situacao"] != "SOZINHO" or not L["af_pessoa"]:
        L["veredito"] = ""
    elif L["entrou"]:
        L["veredito"] = "nao da para conferir"
    else:
        L["veredito"] = ("IGUAL" if L["af_robo"] in
                         [x.strip() for x in L["af_pessoa"].split(",")]
                         else "DIFERENTE")

from collections import Counter
c = Counter(L["situacao"] for L in linhas)
v = Counter(L["veredito"] for L in linhas if L["veredito"])
print("\n" + "=" * 78)
print(" O QUE O ROBO TERIA FEITO COM AS %s NOTAS" % len(linhas))
print("=" * 78)
for k, n in c.most_common():
    print("   %-16s %4s  (%.0f%%)" % (k, n, 100.0 * n / max(len(linhas), 1)))
print("\n CONFERENCIA (so onde da para comparar):")
for k, n in v.most_common():
    print("   %-22s %4s" % (k, n))

dif = [L for L in linhas if L.get("veredito") == "DIFERENTE"]
if dif:
    print("\n AS QUE DIVERGIRAM (para o setor olhar primeiro):")
    for L in dif:
        print("   NF %-12s robo=%s  pessoa=%s  %s"
              % (L["nf"], L["af_robo"], L["af_pessoa"], L["forn"][:28]))

# ---------------------------------------------------------------- saida ---
_COR = {"SOZINHO": "ok", "LEVA PRONTO": "info", "RECUSADA": "",
        "NAO E COMPRA": "", "SEM FILIAL": "warn", "FALTA PRODUTO": "warn",
        "NAO ACHOU AF": "warn", "ERRO": "err"}


def esc(x):
    return (str(x if x is not None else "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


linhas.sort(key=lambda L: ({"SOZINHO": 0, "LEVA PRONTO": 1, "NAO ACHOU AF": 2,
                            "FALTA PRODUTO": 3, "SEM FILIAL": 4,
                            "NAO E COMPRA": 5, "RECUSADA": 6,
                            "ERRO": 7}.get(L["situacao"], 9), L["nf"]))
html = u"""<!doctype html><meta charset="utf-8">
<title>Folha de conferencia</title>
<style>
 body{font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;margin:26px;
   color:#16202e;font-size:14px}
 h1{font-size:21px;margin:0 0 4px} .sub{color:#4a5a70;margin-bottom:20px}
 .kpis{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
 .k{border:1px solid #dde4ed;border-radius:10px;padding:11px 16px;min-width:120px}
 .k b{display:block;font-size:22px;letter-spacing:-.02em}
 .k span{font-size:12px;color:#4a5a70}
 table{border-collapse:collapse;width:100%%;font-size:13px}
 th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
   color:#7d8da0;padding:8px 10px;border-bottom:1px solid #dde4ed}
 td{padding:8px 10px;border-bottom:1px solid #eef2f7;vertical-align:top}
 .t{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;
   border-radius:999px;background:#eef2f7;color:#4a5a70;white-space:nowrap}
 .t.ok{background:#e3f4ea;color:#147a4a} .t.info{background:#e7effa;color:#1f5f9e}
 .t.warn{background:#fbeedb;color:#a8630a} .t.err{background:#fbe6e3;color:#bc3b2d}
 .ig{color:#147a4a;font-weight:700} .di{color:#bc3b2d;font-weight:700}
 .mut{color:#7d8da0;font-size:12px}
 .nota{background:#fbeedb;border-radius:9px;padding:11px 14px;margin-bottom:18px;
   font-size:13px;color:#6b4a0f}
</style>
<h1>O que o rob&ocirc; teria feito</h1>
<div class="sub">Notas dos &uacute;ltimos %(dias)s dias &middot; lidas de <b>produ&ccedil;&atilde;o</b>,
em consulta, em %(agora)s &middot; %(n)s notas</div>
<div class="nota"><b>Como ler.</b> O rob&ocirc; decidiu <b>sem ver</b> a escolha da pessoa.
A coluna <i>Confer&ecirc;ncia</i> s&oacute; aparece quando d&aacute; para comparar: na nota que
<b>j&aacute; entrou</b> no Teknisa a AF foi consumida, e o sistema deixaria de enxerg&aacute;-la
&mdash; comparar ali daria um resultado falso.</div>
<div class="kpis">%(kpis)s</div>
<table><tr><th>NF</th><th>Fornecedor</th><th>Chegou</th><th>Filial</th>
<th>O rob&ocirc; faria</th><th>AF do rob&ocirc;</th><th>AF da pessoa</th>
<th>Confer&ecirc;ncia</th><th>Por qu&ecirc;</th></tr>
%(linhas)s</table>""" % {
    "dias": DIAS, "agora": esc(agora), "n": len(linhas),
    "kpis": "".join(
        '<div class="k"><b>%s</b><span>%s</span></div>' % (n, esc(k.lower()))
        for k, n in c.most_common()),
    "linhas": "\n".join(
        "<tr><td><b>%s</b></td><td>%s</td><td class='mut'>%s</td><td>%s</td>"
        "<td><span class='t %s'>%s</span></td><td><b>%s</b></td><td>%s</td>"
        "<td class='%s'>%s</td><td class='mut'>%s</td></tr>"
        % (esc(L["nf"]), esc(L["forn"]), esc(L["chegou"]), esc(L["filial"] or "-"),
           _COR.get(L["situacao"], ""), esc(L["situacao"]), esc(L["af_robo"] or "-"),
           esc(L["af_pessoa"] or "-"),
           "ig" if L.get("veredito") == "IGUAL" else
           ("di" if L.get("veredito") == "DIFERENTE" else "mut"),
           esc(L.get("veredito") or ""), esc(L["detalhe"]))
        for L in linhas)}

destino = os.getenv("FC_SAIDA") or os.path.join(RAIZ, "folha_conferencia.html")
io.open(destino, "w", encoding="utf-8").write(html)
print("\n folha para o setor: %s" % destino)
con.close()
