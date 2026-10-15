# -*- coding: utf-8 -*-
r"""vitrine_producao.py - O ROBO TRABALHANDO, AO VIVO, CONTRA PRODUCAO.

Roda o motor REAL sobre a fila REAL de producao e desenha o resultado nota a
nota numa pagina que se atualiza sozinha. Serve para MOSTRAR o sistema
decidindo, sem depender de apontar o painel para producao.

POR QUE NAO APONTAR O PAINEL PARA PRODUCAO:
  1. O painel le TEKNISA_DB_*. A credencial de producao mora em AIHUB_PROD_*
     de proposito, para que nenhum caminho leve o painel a gravar la.
  2. As tabelas do sistema (AIHUB_CONFIG, AIHUB_PROC_LOG, AIHUB_PENDENCIA,
     AIHUB_OCORRENCIA) NAO existem em producao, entao varias telas quebrariam.

100% SOMENTE LEITURA: usa AIHUB_LEITURA, que o banco recusa para escrita, e o
codigo chamado aqui roda todo em modo simulacao.

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\vitrine_producao.py
Variaveis: VIT_LIMITE (padrao 60), VIT_DIAS (padrao 7), VIT_SAIDA (arquivo).
"""
import io
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oracledb

from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, af_ainda_valida, quem_baixou_af,
                             AUTOMATICA, AMBIGUA)

LIMITE = int(os.getenv("VIT_LIMITE", "60"))
DIAS = int(os.getenv("VIT_DIAS", "7"))
SAIDA = os.getenv("VIT_SAIDA", "vitrine.html")

CSS = """
:root{--bg:#eef2f7;--card:#fff;--line:#dde4ed;--text:#16202e;--muted:#4a5a70;
 --muted2:#7d8da0;--ok:#147a4a;--okbg:#e3f4ea;--warn:#a8630a;--warnbg:#fbeedb;
 --err:#bc3b2d;--errbg:#fbe6e3;--info:#1f5f9e;--infobg:#e7effa;--brand:#1f5f9e}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,
 "Segoe UI",Roboto,sans-serif;padding:22px}
.topo{display:flex;justify-content:space-between;align-items:flex-start;
 gap:18px;flex-wrap:wrap;margin-bottom:18px}
h1{font-size:21px;font-weight:700;letter-spacing:-.01em}
.sub{font-size:13px;color:var(--muted);margin-top:3px}
.selo{font-size:11.5px;font-weight:700;padding:5px 12px;border-radius:999px;
 background:var(--infobg);color:var(--info)}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:13px 18px;min-width:168px}
.kpi .n{font-size:27px;font-weight:600;letter-spacing:-.02em;line-height:1.1}
.kpi .l{font-size:12px;color:var(--muted);margin-top:2px}
.kpi.ok .n{color:var(--ok)} .kpi.warn .n{color:var(--warn)}
.kpi.err .n{color:var(--err)} .kpi.info .n{color:var(--info)}
.barra{height:7px;background:var(--card);border:1px solid var(--line);
 border-radius:99px;overflow:hidden;margin-bottom:16px}
.barra i{display:block;height:100%;background:var(--brand);transition:.4s}
table{width:100%;border-collapse:collapse;background:var(--card);
 border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
 color:var(--muted2);font-weight:700;padding:11px 14px;border-bottom:1px solid var(--line)}
td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}
tr:last-child td{border-bottom:0}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 9px;
 border-radius:999px;white-space:nowrap}
.t-ok{background:var(--okbg);color:var(--ok)}
.t-warn{background:var(--warnbg);color:var(--warn)}
.t-err{background:var(--errbg);color:var(--err)}
.t-info{background:var(--infobg);color:var(--info)}
.nf{font-weight:700}
.mini{font-size:11.5px;color:var(--muted2);margin-top:2px}
.rodape{margin-top:16px;font-size:12px;color:var(--muted2)}
"""

TOM = {"resolve": ("t-ok", "resolveria sozinho"),
       "importa": ("t-ok", "importaria sozinho"),
       "clique": ("t-info", "um clique"),
       "barra": ("t-err", "barraria"),
       "humano": ("t-warn", "para uma pessoa")}


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def desenha(linhas, placar, feito, total, seg, fim=False):
    pct = int(100.0 * feito / max(1, total))
    resolve = placar["resolve"] + placar["importa"]
    corpo = "\n".join(
        "<tr><td><span class='nf'>NF %s</span><div class='mini'>%s</div></td>"
        "<td>%s</td><td>%s<div class='mini'>%s</div></td></tr>"
        % (esc(l["nf"]), esc(l["forn"])[:38],
           "<span class='tag %s'>%s</span>" % (TOM[l["cat"]][0], TOM[l["cat"]][1]),
           esc(l["titulo"]), esc(l["detalhe"])[:150])
        for l in linhas)
    refresh = "" if fim else '<meta http-equiv="refresh" content="3">'
    html = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Robo trabalhando - producao</title>%s<style>%s</style></head><body>
<div class="topo">
  <div><h1>O rob&ocirc; trabalhando</h1>
  <div class="sub">Motor real, fila real de produ&ccedil;&atilde;o, %s.
  Nada &eacute; gravado.</div></div>
  <span class="selo">%s</span>
</div>
<div class="kpis">
  <div class="kpi ok"><div class="n">%d</div><div class="l">resolveria sozinho</div></div>
  <div class="kpi info"><div class="n">%d</div><div class="l">entrega em um clique</div></div>
  <div class="kpi err"><div class="n">%d</div><div class="l">impediria erro</div></div>
  <div class="kpi warn"><div class="n">%d</div><div class="l">deixa para a pessoa</div></div>
</div>
<div class="barra"><i style="width:%d%%"></i></div>
<table><thead><tr><th style="width:230px">Nota</th><th style="width:150px">Decis&atilde;o</th>
<th>Por qu&ecirc;</th></tr></thead><tbody>
%s
</tbody></table>
<div class="rodape">%d de %d nota(s) processadas em %.0f s &middot;
somente leitura (usu&aacute;rio AIHUB_LEITURA) &middot; nenhuma grava&ccedil;&atilde;o,
nenhuma procedure nativa chamada</div>
</body></html>""" % (refresh, CSS, esc(time.strftime("%d/%m/%Y %H:%M")),
                     "CONCLUIDO" if fim else "PROCESSANDO...",
                     resolve, placar["clique"], placar["barra"],
                     placar["humano"], pct, corpo, feito, total, seg)
    tmp = SAIDA + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(html)
    os.replace(tmp, SAIDA)


for _v in ("AIHUB_PROD_USER", "AIHUB_PROD_PASS", "AIHUB_PROD_DSN"):
    if not os.getenv(_v):
        print(" Falta %s. Rode com CONFIG_PRODUCAO_LEITURA.bat." % _v)
        raise SystemExit(1)

con = oracledb.connect(user=os.environ["AIHUB_PROD_USER"],
                       password=os.environ["AIHUB_PROD_PASS"],
                       dsn=os.environ["AIHUB_PROD_DSN"])
cur = con.cursor()
TOL = ler_tolerancias(cur)
cur.execute("""
    SELECT * FROM (
      SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE,
             G.DTEMISSAO, NVL(F.NMRAZSOCFORN, G.CDFORNECED) NM
      FROM   TEKNISA.INTG_NOTAFISCAL G
      LEFT   JOIN TEKNISA.FORNECEDOR F ON F.CDFORNECED = G.CDFORNECED
      WHERE  G.CDTIPOOPER IN ('01','06')
        AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
        AND  G.DTEMISSAO >= TRUNC(SYSDATE) - :d
        AND  NOT EXISTS (SELECT 1 FROM TEKNISA.NOTAFISCAL N
                         WHERE N.CDFILIAL=G.CDFILIAL AND N.NRLANCTONF=G.NRLANCTONF)
      ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIAL, G.NRLANCTONF)
    WHERE ROWNUM <= :l""", d=DIAS, l=LIMITE)
fila = cur.fetchall()
print("fila real: %d nota(s). Escrevendo em %s" % (len(fila), SAIDA))

linhas, placar, t0 = [], Counter(), time.time()
desenha(linhas, placar, 0, len(fila), 0)

for i, (chave, nf, forn, fil, dtem, nm) in enumerate(fila, 1):
    nota = {"CDFORNECED": forn, "CDFILIRECE": fil, "DTEMISSAO": dtem,
            "NRNOTAFISC": nf, "ORIGEM": "?", "DTUTILIZAXML": None}
    cat, titulo, detalhe = "humano", "", ""
    try:
        itens = itens_do_xml(cur, chave)
        traduzir_para_interno(cur, forn, itens)
        ja = [x for x in itens if x.get("af_atual")]
        if ja and len(ja) == len(itens):
            viva, mortas = af_ainda_valida(cur, chave)
            if viva:
                cat, titulo = "importa", "AF ja escolhida e viva"
                detalhe = "Chamaria a importacao do Teknisa."
            else:
                cat, titulo = "barra", "A AF ja foi consumida"
                q_nf, q_dt = quem_baixou_af(cur, (mortas or [None])[0])
                if q_nf:
                    detalhe = ("A AF %s foi baixada pela NF %s em %s. Importar "
                               "agora baixaria pedido ja atendido."
                               % ((mortas or ["?"])[0], str(q_nf).lstrip("0"), q_dt))
                else:
                    detalhe = ("A AF %s nao esta mais em aberto com saldo."
                               % (mortas or ["?"])[0])
        elif ja:
            cat, titulo = "humano", "Nota parcialmente vinculada"
            detalhe = "Parte dos itens ja aponta uma AF. Nao vincula outra."
        else:
            d = escolher_af(cur, nota, itens, TOL[0])
            dec = d.get("decisao")
            if dec == AUTOMATICA:
                af = (d.get("af") or {}).get("af") or ",".join(sorted(
                    {p["af"] for p in (d.get("plano") or []) if p.get("af")}))
                cat, titulo = "resolve", "Escolheria a AF %s" % af
                detalhe = d.get("motivo") or ""
            elif dec == AMBIGUA:
                cat, titulo = "clique", "Entrega as opcoes"
                detalhe = d.get("motivo") or ""
            else:
                cat = "humano"
                mot = d.get("motivo") or ""
                titulo = ("Falta vincular produto" if "produto vinculado" in mot
                          else "Nenhuma AF atende")
                detalhe = mot
    except Exception as e:
        cat, titulo = "humano", "Nao consegui avaliar"
        detalhe = str(e).split("\n")[0][:110]
    placar[cat] += 1
    linhas.insert(0, {"nf": str(nf).lstrip("0"), "forn": nm,
                      "cat": cat, "titulo": titulo, "detalhe": detalhe})
    desenha(linhas, placar, i, len(fila), time.time() - t0)

desenha(linhas, placar, len(fila), len(fila), time.time() - t0, fim=True)
print("pronto: %d resolveria, %d um clique, %d barraria, %d humano"
      % (placar["resolve"] + placar["importa"], placar["clique"],
         placar["barra"], placar["humano"]))
con.close()
