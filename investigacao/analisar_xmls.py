# -*- coding: utf-8 -*-
"""MEDE, SOBRE XML DE VERDADE, quanto o leitor de AF resolve.

Ate agora a medicao vinha de 18 arquivos baixados a mao, o que nao sustenta
promessa nenhuma (o achado 80 do projeto e sobre exatamente isso: 7 itens de
homologacao criaram uma regra que 85.205 vinculos de producao reprovaram).

Este script roda o core\\xml_af.py sobre uma PASTA de XMLs e responde, com
numero, tres perguntas:

  1. em quantas notas o fornecedor escreveu a AF no arquivo
  2. quando escreveu, o numero e mesmo uma AF daquele fornecedor e filial
  3. em quantas a nota esta parada SEM AF e o numero estava la o tempo todo

A terceira e a que importa: e trabalho que o sistema passa a fazer sozinho.

uso:
    python investigacao\\analisar_xmls.py <pasta>
    python investigacao\\analisar_xmls.py <pasta> --limite 200
    python investigacao\\analisar_xmls.py <pasta> --acompanhar   (fica olhando)
"""

import io
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.abspath("."))
import oracledb                                        # noqa: E402
from core.xml_af import candidatos_no_xml, conferir_af  # noqa: E402

RE_CHAVE = re.compile(r"<chNFe>(\d{44})</chNFe>")
RE_CPL = re.compile(r"<(?:infCpl|infAdFisco|obsCont|xPed)[^>]*>(.*?)</", re.S)


def ler(caminho):
    """XML pode vir em UTF-8 ou latin1; nunca deixar um arquivo torto parar a
    medicao inteira."""
    for cod in ("utf-8", "latin-1"):
        try:
            return io.open(caminho, encoding=cod, errors="ignore").read()
        except Exception:
            continue
    return ""


def analisar(pasta, limite=None, ja_vistos=None):
    con = oracledb.connect(user=os.environ["TEKNISA_DB_USER"],
                           password=os.environ["TEKNISA_DB_PASS"],
                           dsn=os.environ["TEKNISA_DB_DSN"])
    cur = con.cursor()
    vistos = ja_vistos if ja_vistos is not None else set()

    c = Counter()
    formatos = Counter()
    ouro = []          # nota parada, sem AF, e o XML tem uma AF valida
    divergentes = []   # o XML aponta AF diferente da que esta na nota

    arquivos = []
    for raiz, _dirs, nomes in os.walk(pasta):
        for n in nomes:
            if n.lower().endswith(".xml"):
                arquivos.append(os.path.join(raiz, n))
    novos = [a for a in arquivos if a not in vistos]
    if limite:
        novos = novos[:limite]

    for caminho in novos:
        vistos.add(caminho)
        c["arquivos"] += 1
        xml = ler(caminho)
        m = RE_CHAVE.search(xml)
        if not m:
            c["sem_chave"] += 1
            continue
        chave = m.group(1)

        cur.execute("""SELECT CDFORNECED, CDFILIRECE, DTUTILIZAXML, NRNOTAFISC
                       FROM   TEKNISA.INTG_NOTAFISCAL
                       WHERE  NRACESSONFE = :c""", c=chave)
        r = cur.fetchone()
        if not r:
            c["fora_da_base"] += 1
            continue
        forn, filial, utilizada, nrnf = r
        c["na_base"] += 1
        if utilizada:
            c["ja_utilizada"] += 1
        else:
            c["parada"] += 1

        cur.execute("""SELECT DISTINCT NRAUTOFORN FROM TEKNISA.INTG_ITEMPRODUTO
                       WHERE NRACESSONFE=:c AND NRAUTOFORN IS NOT NULL""",
                    c=chave)
        reais = {a for (a,) in cur.fetchall()}

        cands = candidatos_no_xml(xml)
        if not cands:
            c["xml_sem_numero"] += 1
            continue
        c["xml_com_numero"] += 1

        # de que jeito o fornecedor escreveu: guarda o rotulo, nao o numero
        for trecho in RE_CPL.findall(xml)[:4]:
            for mm in re.finditer(r"(A\.?F\.?|AUT\w*\s*FORN\w*|PEDIDO|OC)"
                                  r"[\s.:/#-]{0,4}\d{4,12}", trecho, re.I):
                formatos[re.sub(r"\d", "", mm.group(0)).strip()[:18]] += 1

        # o primeiro candidato que o banco reconhece
        valido = None
        for af, origem, trecho in cands:
            existe, usavel, _mot = conferir_af(cur, af, forn, filial)
            if existe:
                valido = (af, origem, usavel)
                break
        if not valido:
            c["numero_nao_e_af"] += 1
            continue
        af, origem, usavel = valido
        c["af_validada"] += 1
        if usavel:
            c["af_em_aberto"] += 1

        if reais:
            if af in reais:
                c["confirma_a_gravada"] += 1
            else:
                c["diverge_da_gravada"] += 1
                if len(divergentes) < 12:
                    divergentes.append((nrnf, af, sorted(reais)))
        elif not utilizada:
            c["OURO"] += 1
            if len(ouro) < 25:
                ouro.append((nrnf, chave, af, origem, usavel))

    cur.close()
    con.close()
    return c, formatos, ouro, divergentes, vistos


def relatorio(c, formatos, ouro, divergentes):
    def pct(a, b):
        return (a / b * 100) if b else 0.0

    print("=" * 74)
    print("XMLs lidos: %s" % c["arquivos"])
    print("=" * 74)
    print("   na base da integracao   %6s   (%s fora, %s sem chave)"
          % (c["na_base"], c["fora_da_base"], c["sem_chave"]))
    print("      paradas              %6s" % c["parada"])
    print("      ja utilizadas        %6s" % c["ja_utilizada"])
    base = c["na_base"]
    print("\n   XML traz numero de AF   %6s   (%.1f%%)"
          % (c["xml_com_numero"], pct(c["xml_com_numero"], base)))
    print("   e o numero E uma AF     %6s   (%.1f%% dos que tem numero)"
          % (c["af_validada"], pct(c["af_validada"], c["xml_com_numero"])))
    print("      dessas, em aberto    %6s" % c["af_em_aberto"])
    print("   numero que NAO e AF     %6s   (descartado pela validacao)"
          % c["numero_nao_e_af"])
    print("\n   confirma a AF gravada   %6s" % c["confirma_a_gravada"])
    print("   DIVERGE da gravada      %6s   <- olhar uma a uma"
          % c["diverge_da_gravada"])
    print("   >>> NOTA PARADA SEM AF, E O XML TINHA: %s   (%.1f%% das paradas)"
          % (c["OURO"], pct(c["OURO"], c["parada"])))

    if formatos:
        print("\n   COMO OS FORNECEDORES ESCREVEM:")
        for f, q in formatos.most_common(12):
            print("      %-20s %s" % (repr(f), q))
    if ouro:
        print("\n   AMOSTRA DO QUE O SISTEMA RESOLVERIA SOZINHO:")
        for nf, chave, af, origem, usavel in ouro[:12]:
            print("      NF %-10s AF %s  via %-22s %s"
                  % ((nf or "").lstrip("0"), af, origem,
                     "EM ABERTO" if usavel else "(ja fechada)"))
    if divergentes:
        print("\n   DIVERGENCIAS (o XML aponta outra AF):")
        for nf, af, reais in divergentes:
            print("      NF %-10s XML=%s   nota=%s"
                  % ((nf or "").lstrip("0"), af, ", ".join(reais)))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pasta = args[0] if args else r"C:\Users\gustavo.santos\Desktop\importados"
    limite = None
    if "--limite" in sys.argv:
        limite = int(sys.argv[sys.argv.index("--limite") + 1])
    acompanhar = "--acompanhar" in sys.argv

    vistos = set()
    while True:
        c, formatos, ouro, div, vistos = analisar(pasta, limite, vistos)
        if c["arquivos"]:
            relatorio(c, formatos, ouro, div)
        else:
            print("nenhum arquivo novo")
        if not acompanhar:
            break
        print("\n... aguardando novos arquivos (Ctrl+C para parar)\n")
        time.sleep(60)
