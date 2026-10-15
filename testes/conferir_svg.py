# -*- coding: utf-8 -*-
"""SVG SEM TAMANHO ESTICA ATE OCUPAR A TELA - varre o painel atras deles.

O QUE ACONTECEU. Duas secoes traziam <svg width="17" height="17"> inline; ao
escrever a terceira eu esqueci o atributo. Como `.sec-titulo` nao tinha regra
de CSS para svg, o icone assumiu o tamanho do container e um triangulo preto
tomou a tela inteira. E o pior tipo de erro num arquivo unico: nao da erro
nenhum, so estraga.

A REGRA: todo <svg> precisa de tamanho por UM destes caminhos -
  (a) atributo width no proprio elemento, ou
  (b) uma regra CSS que alcance ele (`.classe svg{width:...}`).

Este script confere os dois e aponta quem nao tem nenhum.

uso:  py testes\\conferir_svg.py [arquivo.html]
"""

import io
import re
import sys

ARQ = sys.argv[1] if len(sys.argv) > 1 else "interface/painel/index.html"
html = io.open(ARQ, encoding="utf-8").read()

# 1) quais classes ja tem regra de tamanho para svg no CSS
#    pega ".alguma-classe svg{...width...}" e tambem ".a .b svg{...}"
regras = set()
for m in re.finditer(r"([.#][\w .#>-]*?)\s+svg\s*\{([^}]*)\}", html):
    if "width" in m.group(2):
        # a ultima classe do seletor e a que precisa estar no elemento pai
        for cls in re.findall(r"[.#]([\w-]+)", m.group(1)):
            regras.add(cls)

# 2) todo svg do arquivo, com a classe do elemento que o contem
#    (olhamos 260 caracteres para tras, que cobre o pai imediato)
problemas = []
for m in re.finditer(r"<svg\b[^>]*>", html):
    tag = m.group(0)
    if "width=" in tag or "class=" in tag:
        continue                       # tem tamanho proprio, ou classe propria
    antes = html[max(0, m.start() - 260):m.start()]
    classes = set()
    for c in re.findall(r'class="([^"]+)"', antes):
        classes.update(c.split())
    if classes & regras:
        continue                       # o CSS alcanca este svg
    linha = html[:m.start()].count("\n") + 1
    problemas.append((linha, sorted(classes)[:4], tag[:70]))

print("=" * 74)
print("SVG SEM TAMANHO - %s" % ARQ)
print("=" * 74)
print("   classes com regra de svg no CSS: %d" % len(regras))
print("   svg sem tamanho e sem regra    : %d" % len(problemas))
for ln, cls, tag in problemas:
    print("\n   linha %s   dentro de: %s" % (ln, ", ".join(cls) or "(sem classe)"))
    print("      %s" % tag)

if problemas:
    print("\n   CORRIJA de um dos dois jeitos:")
    print("      - width=\"17\" height=\"17\" no proprio <svg>, ou")
    print("      - regra .classe svg{width:17px;height:17px} no CSS")
sys.exit(1 if problemas else 0)
