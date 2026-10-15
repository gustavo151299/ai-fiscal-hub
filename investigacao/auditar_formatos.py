# -*- coding: utf-8 -*-
"""O QUE O LEITOR DEIXA PASSAR - auditoria de formatos.

Contar o que o extrator ACHA nao prova nada sobre o que ele PERDE. Este script
faz o teste ao contrario, que e o unico que responde "o sistema reconhece todas
as formas?":

  1. pega XML de nota que JA TEM AF gravada (entao a resposta certa e conhecida)
  2. procura esse numero no texto cru do XML, com e sem zeros a esquerda
  3. se o numero ESTA no texto e o extrator NAO devolveu -> FORMATO PERDIDO

Cada formato perdido vem com o trecho em volta, que e o que permite corrigir a
expressao. Um formato novo aqui vale mais que mil arquivos a mais: cobertura se
ganha por variedade de fornecedor, nao por volume.

uso:  python investigacao\\auditar_formatos.py <pasta> [--limite 800]
"""

import io
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath("."))
import oracledb                                # noqa: E402
from core.xml_af import candidatos_no_xml      # noqa: E402

RE_CHAVE = re.compile(r"<chNFe>(\d{44})</chNFe>")


def ler(caminho):
    for cod in ("utf-8", "latin-1"):
        try:
            return io.open(caminho, encoding=cod, errors="ignore").read()
        except Exception:
            continue
    return ""


def onde_aparece(xml, numero):
    """Todos os trechos do XML onde o numero aparece, com contexto.

    Procura tanto com zeros a esquerda quanto sem: o fornecedor escreve
    '0000636355' e tambem '1445341' para a mesma AF de 10 posicoes."""
    cru = numero.lstrip("0")
    achados = []
    for forma in (numero, cru):
        if not forma:
            continue
        for m in re.finditer(re.escape(forma), xml):
            ini = max(0, m.start() - 60)
            achados.append(xml[ini:m.end() + 20].replace("\n", " "))
    return achados


def auditar(pasta, limite=None):
    con = oracledb.connect(user=os.environ["TEKNISA_DB_USER"],
                           password=os.environ["TEKNISA_DB_PASS"],
                           dsn=os.environ["TEKNISA_DB_DSN"])
    cur = con.cursor()
    c = Counter()
    perdidos = []
    pegos = Counter()

    arquivos = []
    for raiz, _d, nomes in os.walk(pasta):
        for n in nomes:
            if n.lower().endswith(".xml"):
                arquivos.append(os.path.join(raiz, n))
    if limite:
        arquivos = arquivos[:limite]

    for caminho in arquivos:
        xml = ler(caminho)
        m = RE_CHAVE.search(xml)
        if not m:
            continue
        chave = m.group(1)
        cur.execute("""SELECT DISTINCT NRAUTOFORN FROM TEKNISA.INTG_ITEMPRODUTO
                       WHERE NRACESSONFE=:c AND NRAUTOFORN IS NOT NULL""",
                    c=chave)
        reais = {a for (a,) in cur.fetchall()}
        if not reais:
            continue                       # sem gabarito, nao serve de teste
        c["com_gabarito"] += 1

        achados = {a for a, _o, _t in candidatos_no_xml(xml)}
        for real in reais:
            trechos = onde_aparece(xml, real)
            if not trechos:
                c["af_nao_esta_no_xml"] += 1
                continue
            c["af_esta_no_xml"] += 1
            if real in achados:
                c["extrator_pegou"] += 1
                # guarda o rotulo que precedeu, para o inventario
                for t in trechos[:1]:
                    r = re.search(r"([A-Za-z .:/#-]{0,14})$",
                                  t[:t.rfind(real.lstrip("0")) or len(t)])
                    pegos[(r.group(1) if r else "").strip()[-12:] or "(sem rotulo)"] += 1
            else:
                c["EXTRATOR_PERDEU"] += 1
                if len(perdidos) < 30:
                    perdidos.append((os.path.basename(caminho), real, trechos[0]))
    cur.close()
    con.close()
    return c, perdidos, pegos


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else r"C:\Users\gustavo.santos\Desktop\importados"
    limite = None
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])

    c, perdidos, pegos = auditar(pasta, limite)
    print("=" * 74)
    print("AUDITORIA DE FORMATOS - o que o leitor deixa passar")
    print("=" * 74)
    print("   XMLs com AF conhecida (gabarito) : %s" % c["com_gabarito"])
    print("   a AF aparece no texto do XML     : %s" % c["af_esta_no_xml"])
    print("   a AF NAO esta no XML             : %s   (nada a achar)"
          % c["af_nao_esta_no_xml"])
    tot = c["af_esta_no_xml"]
    print("\n   >>> o extrator PEGOU  : %s   (%.1f%%)"
          % (c["extrator_pegou"], (c["extrator_pegou"] / tot * 100) if tot else 0))
    print("   >>> o extrator PERDEU : %s   (%.1f%%)"
          % (c["EXTRATOR_PERDEU"], (c["EXTRATOR_PERDEU"] / tot * 100) if tot else 0))

    if pegos:
        print("\n   ROTULOS QUE ELE JA RECONHECE:")
        for r, q in pegos.most_common(20):
            print("      %-26s %s" % (repr(r), q))
    if perdidos:
        print("\n   FORMATOS PERDIDOS (corrigir a expressao com estes):")
        for arq, af, trecho in perdidos:
            print("\n      AF %s  em %s" % (af, arq[:44]))
            print("         ...%s..." % trecho.strip()[:110])
    else:
        print("\n   NENHUM FORMATO PERDIDO nesta amostra.")
