"""
investigacao/backtest_matcher.py
Roda o matcher sobre a base INTEIRA de De/Para (exportada pelo bloco D1) e
gera um relatorio, para investigar antes de o sistema rodar:
  - distribuicao real (AUTO / SUGESTAO / HUMANO);
  - lista do que o AUTO pegaria (para conferir que nenhum esta errado);
  - lista dos suspeitos (score baixo = possivel lixo na base);
  - conflitos (mesmo item do fornecedor apontando para produtos diferentes).

Nao altera nada. So le o CSV e escreve um relatorio.

Como usar:
  1) exporte o bloco D1 como CSV chamado 'pares_depara.csv' (separador ; ou ,)
  2) py backtest_matcher.py pares_depara.csv
  3) veja o resumo na tela e o arquivo 'relatorio_matcher.csv'

Colunas esperadas no CSV (nesta ordem):
  CDFORNECED ; CDPRODUTOXML ; NMPRODUTOXML ; CDPRODUTO ; NMPRODUTO
"""

from __future__ import annotations
import sys
import csv
import os

# usa o matcher do proprio sistema
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.ai_matcher import _sim, LIMIAR_AUTO, LIMIAR_SUGESTAO


def faixa(score: float) -> str:
    if score >= LIMIAR_AUTO:
        return "AUTO"
    if score >= LIMIAR_SUGESTAO:
        return "SUGESTAO"
    return "HUMANO"


def detectar_sep(caminho: str) -> str:
    with open(caminho, encoding="utf-8-sig") as f:
        cab = f.readline()
    return ";" if cab.count(";") >= cab.count(",") else ","


def main(caminho: str):
    sep = detectar_sep(caminho)
    linhas = []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f, delimiter=sep)
        cab = next(rd, None)  # pula cabecalho
        for row in rd:
            if len(row) < 5:
                continue
            forn, cpx, nmx, cdp, nmp = row[0], row[1], row[2], row[3], row[4]
            s = _sim(nmx, nmp)
            linhas.append((forn, cpx, nmx, cdp, nmp, round(s, 3), faixa(s)))

    total = len(linhas)
    if total == 0:
        print("Nenhuma linha lida. Confira o CSV e o separador.")
        return

    cont = {"AUTO": 0, "SUGESTAO": 0, "HUMANO": 0}
    for l in linhas:
        cont[l[6]] += 1

    # conflitos: mesmo (fornecedor, item XML) com produtos internos diferentes
    mapa = {}
    for forn, cpx, nmx, cdp, nmp, s, fx in linhas:
        mapa.setdefault((forn, cpx), set()).add(cdp)
    conflitos = {k: v for k, v in mapa.items() if len(v) > 1}

    print("=" * 60)
    print("BACK-TEST DO MATCHER NA BASE INTEIRA")
    print("=" * 60)
    print("Total de pares:        %d" % total)
    for k in ("AUTO", "SUGESTAO", "HUMANO"):
        print("  %-9s %6d  (%.1f%%)" % (k, cont[k], 100 * cont[k] / total))
    print("Conflitos (item->varios produtos): %d" % len(conflitos))
    print()
    print("Legenda: AUTO grava sozinho (>= %.0f) | SUGESTAO mostra ao humano "
          "(>= %.0f) | HUMANO procura do zero" % (LIMIAR_AUTO * 100, LIMIAR_SUGESTAO * 100))

    # relatorio completo, ordenado por score (os suspeitos primeiro)
    saida = os.path.join(os.path.dirname(os.path.abspath(caminho)),
                         "relatorio_matcher.csv")
    with open(saida, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["SCORE", "FAIXA", "CDFORNECED", "NMPRODUTOXML",
                    "CDPRODUTO", "NMPRODUTO", "CONFLITO"])
        for forn, cpx, nmx, cdp, nmp, s, fx in sorted(linhas, key=lambda x: x[5]):
            conf = "SIM" if (forn, cpx) in conflitos else ""
            w.writerow([s, fx, forn, nmx, cdp, nmp, conf])
    print("\nRelatorio completo salvo em: %s" % saida)
    print("Abra no Excel: os de score baixo (topo) sao os candidatos a lixo;")
    print("os marcados CONFLITO precisam de decisao humana.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: py backtest_matcher.py pares_depara.csv")
        sys.exit(1)
    main(sys.argv[1])
