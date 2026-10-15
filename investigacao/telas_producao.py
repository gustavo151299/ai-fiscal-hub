# -*- coding: utf-8 -*-
r"""telas_producao.py - AS TELAS DO PAINEL, ALIMENTADAS POR PRODUCAO.

100% SOMENTE LEITURA. Nao ha INSERT/UPDATE/DELETE aqui, e a conexao usada e a
de consulta (AIHUB_PROD_*, usuario AIHUB_LEITURA), que o banco recusa para
escrita.

COMO ELE FAZ, e por que assim: chama A PROPRIA ROTA do painel, atraves do
cliente de teste do Flask, com a conexao trocada para producao. Nao reimplementa
nenhuma consulta.

  Reimplementar seria mais simples e estaria errado: a simulacao passaria a
  medir o codigo da simulacao, nao o do painel, e as duas divergiriam em
  silencio. Ja aconteceu neste projeto duas vezes (achados 89 e 90).

POR QUE NAO BASTA APONTAR O PAINEL PARA PRODUCAO: as tabelas AIHUB_* nao
existem la (conferido: zero). O painel inteiro quebraria em varias telas. Aqui
so as rotas escolhidas rodam, e o que falta aparece como falta - que e
justamente o que se quer enxergar antes de instalar.

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\telas_producao.py
Saida: scratchpad/telas_producao.json  (payloads reais, para a tela desenhar)
"""
import io
import json
import os
import re as _re
import sys
import time

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "interface", "painel"))
import oracledb

for _v in ("AIHUB_PROD_USER", "AIHUB_PROD_PASS", "AIHUB_PROD_DSN"):
    if not os.getenv(_v):
        print("Falta %s. Rode: call CONFIG_PRODUCAO_LEITURA.bat" % _v)
        sys.exit(1)

# O app.py se RECUSA a subir sem estas duas (protecao contra apontar para o
# banco errado). Damos valores de fachada so para o modulo importar: a conexao
# de verdade e trocada logo abaixo, e nenhuma consulta usa estes valores.
os.environ.setdefault("TEKNISA_DB_DSN", "importacao-apenas")
os.environ.setdefault("TEKNISA_DB_PASS", "importacao-apenas")
os.environ["AIHUB_MODO_ESCRITA"] = "0"      # segunda trava, alem da do banco

import app as painel                                          # noqa: E402


class _ConexaoProd:
    """Conexao de producao que IGNORA o close().

    As rotas fecham a conexao no finally, como devem. Se cada chamada fechasse
    a nossa, a segunda ja falharia. Aqui o close vira no-op e quem fecha de
    verdade e este script, no fim."""

    def __init__(self):
        self._c = oracledb.connect(user=os.environ["AIHUB_PROD_USER"],
                                   password=os.environ["AIHUB_PROD_PASS"],
                                   dsn=os.environ["AIHUB_PROD_DSN"])

    def __getattr__(self, nome):
        return getattr(self._c, nome)

    def close(self):
        pass

    def de_verdade(self):
        self._c.close()


_CON = _ConexaoProd()
painel._conn = lambda: _CON            # a troca: tudo passa a ler producao
painel.MODO_ESCRITA = False


# ---------------------------------------------------------------------------
# A PAGINA PARA OLHAR: as telas do painel, com dado de producao.
# ---------------------------------------------------------------------------
# Reusa o CSS e a funcao de desenho DO PROPRIO index.html, recortados do
# arquivo. Copiar o visual para ca daria uma tela parecida com a do painel, que
# e exatamente o que nao serve: a pessoa precisa conferir o que vai ver, nao
# uma reproducao.
def _bloco(txt, ini):
    """Recorta de 'ini' ate a chave que fecha, contando { e }."""
    i = txt.find(ini)
    if i < 0:
        raise SystemExit("nao achei no index.html: %s" % ini)
    j = txt.find("{", i)
    n = 0
    for k in range(j, len(txt)):
        if txt[k] == "{":
            n += 1
        elif txt[k] == "}":
            n -= 1
            if n == 0:
                return txt[i:k + 1]
    raise SystemExit("bloco sem fim: %s" % ini)


def gerar_html(saida, notas):
    idx = io.open(os.path.join(RAIZ, "interface", "painel", "index.html"),
                  encoding="utf-8").read()
    css = idx.split("<style>", 1)[1].split("</style>", 1)[0]
    # A LISTA PRECISA COBRIR TUDO QUE O _fichaRender CHAMA. Ela ja ficou curta
    # uma vez: entraram _fichaCriterios e _fichaFiscal no painel e a pagina
    # gerada quebrava com ReferenceError, sem nada indicar - por isso existe a
    # conferencia logo abaixo, que falha alto em vez de gerar pagina morta.
    _FUNCS = ("esc(", "escJs(", "num(", "plural(",
              "_fichaCriterios(", "_fichaFiscal(", "_fichaRender(")
    js = "\n".join(_bloco(idx, "function " + f) for f in _FUNCS)
    corpo = _bloco(idx, "function _fichaRender(")
    faltou = [c for c in _re.findall(r"\b(_[A-Za-z]\w*)\s*\(", corpo)
              if ("function " + c + "(") not in js]
    if faltou:
        raise SystemExit("o _fichaRender chama %s, que nao entrou na pagina. "
                         "Acrescente em _FUNCS." % ", ".join(sorted(set(faltou))))
    cartoes = []
    for n in notas:
        f = n.get("ficha") or {}
        if not f.get("achou"):
            continue
        nf = (f.get("nota") or {}).get("numero") or "?"
        cartoes.append({"nf": nf, "rot": n.get("rotulo", ""),
                        "ficha": f})
    html = u"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Telas com dados de producao</title><style>%s
 body{padding:26px;max-width:1100px;margin:0 auto}
 .vit-h{margin-bottom:8px;font-size:22px;font-weight:800;letter-spacing:-.02em}
 .vit-s{color:var(--muted);margin-bottom:26px;font-size:13.5px}
 .vit-n{margin:34px 0 14px;padding:11px 15px;border-radius:10px;
   background:var(--brand-100);color:var(--brand-700);font-weight:700}
 .vit-n small{display:block;font-weight:500;color:var(--muted);margin-top:3px}
</style></head><body>
<div class="vit-h">A tela &ldquo;O que foi feito na nota&rdquo;, com dados de produ&ccedil;&atilde;o</div>
<div class="vit-s">Mesmo CSS e mesma fun&ccedil;&atilde;o de desenho do painel. Os dados vieram do
banco de <b>produ&ccedil;&atilde;o</b>, em leitura, em %s.</div>
%s
<script>function go(){alert('No painel, este link abre Ajustes > Como ele pensa.');}
%s
document.querySelectorAll('[data-ficha]').forEach(function(el){
  el.innerHTML = _fichaRender(JSON.parse(el.getAttribute('data-ficha')));
});
</script></body></html>""" % (
        css,
        time.strftime("%d/%m/%Y %H:%M"),
        "\n".join(
            u'<div class="vit-n">NF %s<small>%s</small></div>'
            u'<div data-ficha="%s"></div>'
            % (c["nf"], c["rot"],
               json.dumps(c["ficha"], ensure_ascii=False).replace("&", "&amp;")
               .replace('"', "&quot;").replace("<", "&lt;"))
            for c in cartoes),
        js)
    io.open(saida, "w", encoding="utf-8").write(html)
    return saida


cli = painel.app.test_client()


def rota(caminho):
    r = cli.get(caminho)
    try:
        return r.status_code, json.loads(r.data.decode("utf-8"))
    except Exception:
        return r.status_code, {"erro": r.data.decode("utf-8", "replace")[:200]}


# ------------------------------------------------------------------ alvos --
# Notas concretas, escolhidas pela decisao que o motor toma nelas, para as telas
# mostrarem os TRES caminhos e nao so o caso feliz.
#   py investigacao\telas_producao.py <chave> <chave> ...
# Chave na linha de comando e mais simples que variavel de ambiente: JSON com
# aspas dentro de "set" do cmd nao sobrevive.
ALVOS = [a.strip() for a in sys.argv[1:] if a.strip()]
if not ALVOS:
    cur = _CON.cursor()
    cur.execute("""
        SELECT NRACESSONFE FROM TEKNISA.INTG_NOTAFISCAL
        WHERE  DTEMISSAO >= SYSDATE - 30 AND DTLANCAMENNF IS NULL
          AND  DTUTILIZAXML IS NULL AND CDTIPOOPER IS NOT NULL
          AND  NVL(DSTAGORIGFILI,'x') <> 'Manual'
        ORDER  BY DTEMISSAO DESC FETCH FIRST 3 ROWS ONLY""")
    ALVOS = [r[0] for r in cur.fetchall()]
    cur.close()

saida = {"gerado_contra": "producao (AIHUB_LEITURA, somente leitura)",
         "notas": []}

print("=" * 78)
print(" AS ROTAS DO PAINEL, RODANDO CONTRA PRODUCAO")
print("=" * 78)
for ch in ALVOS:
    print("\n  nota %s..." % ch[-12:])
    st_f, ficha = rota("/api/ficha-nota?chave=" + ch)
    st_r, rac = rota("/api/raciocinio?chave=" + ch)
    print("    /api/ficha-nota  HTTP %s  passos=%s itens=%s tocada=%s"
          % (st_f, len(ficha.get("passos") or []),
             len(ficha.get("itens") or []), ficha.get("tocada")))
    print("    /api/raciocinio  HTTP %s  %s"
          % (st_r, ("itens=%s" % len(rac.get("itens") or []))
             if st_r == 200 else str(rac)[:80]))
    saida["notas"].append({"chave": ch, "ficha": ficha, "raciocinio": rac})

destino = os.getenv("TP_SAIDA") or os.path.join(RAIZ, "scratchpad_telas.json")
io.open(destino, "w", encoding="utf-8").write(
    json.dumps(saida, ensure_ascii=False, indent=1))
print("\n  payloads gravados em: %s" % destino)
pag = os.getenv("TP_HTML") or os.path.join(RAIZ, "telas_producao.html")
gerar_html(pag, [dict(n, rotulo=((n.get("ficha") or {}).get("nota") or {})
                      .get("fornecedor") or "") for n in saida["notas"]])
print("  pagina para olhar  : %s" % pag)
print("=" * 78)
_CON.de_verdade()
