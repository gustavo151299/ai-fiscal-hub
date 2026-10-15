# -*- coding: utf-8 -*-
r"""passo_a_passo.py - O ROBO DECIDINDO, COM A LOGICA INTEIRA A VISTA.

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario AIHUB_LEITURA, que o banco
recusa para escrita) e chama as funcoes do robo em modo simulacao.

A DIFERENCA PARA O hoje_producao.py: aquele conta quantas ele resolveria; este
MOSTRA COMO. Cada nota vira um relatorio com todos os passos, na ordem exata em
que o robo os executa, e com o numero que decidiu cada um:

  1. A operacao ja recusou esta nota?      (nota_bloqueada, HISTXMLNFE)
  2. Esta nota e compra?                   (cfop_natureza)
  3. Cada item tem produto interno?        (De/Para, e o matcher quando falta)
  4. Que AF atende?                        (predicado nativo, janela, cobertura)
  5. O Teknisa deixaria entrar?            (INTG_INCONSISTENCIA)

Nada e recalculado por fora: as decisoes saem das MESMAS funcoes que o robo
chama. Reimplementar a regra aqui daria um relatorio bonito que descreve outro
sistema - foi o defeito da tela "Simular" antiga (achado 89).

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\passo_a_passo.py
    ... py investigacao\passo_a_passo.py 5            (so 5 notas)
    ... py investigacao\passo_a_passo.py 5 "2026-08-24 08"   (so o lote das 8h)
"""
import os
import sys
import time

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, RAIZ)
import oracledb

from core.cfop_natureza import natureza
from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, janela_da_filial, AUTOMATICA,
                             AMBIGUA)
from robo.servico_robo import (buscar_para_importar, nota_bloqueada,
                               resolver_depara_do_xml, LIMIAR_AUTO)

LIMITE = int(sys.argv[1]) if len(sys.argv) > 1 else 8
LOTE = sys.argv[2] if len(sys.argv) > 2 else None

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
print(" O ROBO PASSO A PASSO - %s   (%s, somente leitura)" % (agora, usr))
if LOTE:
    print(" Recorte: notas que chegaram no lote de %s" % LOTE)
print(L)

fila = buscar_para_importar(cur, LIMITE, LOTE)
print("\n A FILA: %s nota(s)  (%s com tipo de operacao, %s alcancadas pelo CFOP)"
      % (len(fila), sum(1 for _c, p in fila if p),
         sum(1 for _c, p in fila if not p)))

placar = {"sozinho": 0, "humano": 0, "parado": 0, "recusada": 0}
for n, (ch, pode_importar) in enumerate(fila, 1):
    cur.execute("""SELECT G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE, G.DTEMISSAO,
                          G.CDTIPOOPER, NVL(G.DSTAGORIGFILI,'(automatica)'),
                          TO_CHAR(G.DTINCLUSAO,'DD/MM HH24:MI'),
                          (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
                            WHERE F.CDFORNECED = G.CDFORNECED)
                   FROM TEKNISA.INTG_NOTAFISCAL G
                   WHERE G.NRACESSONFE = :c""", c=ch)
    nr, forn, fil, dtem, tipo, orig, chegou, nmf = cur.fetchone()
    print("\n" + "-" * 78)
    print(" NOTA %d de %d   NF %s   %s" % (n, len(fila), nr, (nmf or "")[:40]))
    print("-" * 78)
    print("   chegou %s | emissao %s | filial %s | origem %s"
          % (chegou, dtem.strftime("%d/%m/%Y") if dtem else "?", fil or "-", orig))

    # ---- passo 1 -------------------------------------------------------- #
    morta, porque = nota_bloqueada(cur, ch)
    print("\n   PASSO 1  A operacao ja recusou esta nota?")
    print("            le TEKNISA.HISTXMLNFE, ULTIMO evento por NRSEQHISTOR")
    if morta:
        print("            -> SIM. %s" % porque)
        print("            *** PARA AQUI. Nao encosta na nota. ***")
        placar["recusada"] += 1
        continue
    print("            -> nao, a nota esta viva")

    # ---- passo 2 -------------------------------------------------------- #
    cur.execute("""SELECT MIN(CDFISOPEITEM) FROM TEKNISA.INTG_ITEMPRODUTO
                   WHERE NRACESSONFE = :c""", c=ch)
    cfop = (cur.fetchone() or [None])[0]
    nat, motivo_cfop = natureza(cfop)
    print("\n   PASSO 2  Esta nota e compra com pedido?")
    print("            CFOP %s -> %s" % (cfop, nat))
    print("            %s" % motivo_cfop)
    if tipo:
        print("            (esta nota ja tem CDTIPOOPER=%s, entao entrou pela"
              " fila antiga)" % tipo)
    else:
        print("            (sem CDTIPOOPER: foi o CFOP que a trouxe para a fila)")

    # ---- passo 3 -------------------------------------------------------- #
    itens = itens_do_xml(cur, ch)
    traduzir_para_interno(cur, forn, itens)
    faltando = [i for i in itens if not i.get("cd_interno")]
    print("\n   PASSO 3  Cada item tem produto interno? (De/Para)")
    print("            %s item(ns) na nota, %s ja com produto, %s sem"
          % (len(itens), len(itens) - len(faltando), len(faltando)))
    for i in itens[:6]:
        if i.get("cd_interno"):
            print("              item %-4s %-38s -> %s"
                  % (i["seq"], (i["desc_xml"] or "")[:38], i["cd_interno"]))
    if faltando:
        print("            os sem produto passam pelo matcher (piso %d%%):"
              % round(LIMIAR_AUTO * 100))
        det = []
        try:
            resolver_depara_do_xml(con, ch, True, det)
        except Exception as e:
            print("              (nao consegui rodar o matcher: %s)" % str(e)[:50])
        for d in det[:6]:
            print("              item %-4s %-34s"
                  % (d.get("seq"), (d.get("desc_xml") or "")[:34]))
            if d.get("escolhido"):
                print("                 melhor: %s %s  score %s%%  -> %s"
                      % (d.get("escolhido"), (d.get("nome_escolhido") or "")[:28],
                         d.get("score"),
                         "VINCULA" if d.get("auto") else "abaixo do piso, PARA"))
            else:
                print("                 nenhum candidato pontuou")
        # aplica na memoria o que ele criaria, para o passo 4 ser o real
        esc = {d.get("cod_fornecedor"): d.get("escolhido")
               for d in det if d.get("auto") and d.get("escolhido")}
        for it in faltando:
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
        ainda = [i for i in itens if not i.get("cd_interno")]
        if ainda:
            print("            -> %s item(ns) continuam sem produto: PARA AQUI"
                  % len(ainda))
            print("               (vai para a fila 'Falta o produto', no painel)")
            placar["parado"] += 1
            continue
        print("            -> todos resolvidos")

    # ---- passo 4 -------------------------------------------------------- #
    anc, dias_a, dias_d = janela_da_filial(cur, fil, dtem)
    est = sorted({i["cd_estoque"] for i in itens if i.get("cd_estoque")})
    print("\n   PASSO 4  Que AF atende esta nota?")
    print("            produtos de estoque: %s" % (est or "nenhum"))
    print("            janela: %s dias antes e %s depois de %s"
          % (dias_a, dias_d, anc.strftime("%d/%m/%Y") if anc else "?"))
    print("            tolerancia de quantidade: %s%%" % tol)
    print("            filtros do Teknisa: sem baixa, nao expirada, com saldo,")
    print("            filial de destino = %s, e a AF tem de cobrir os %s produto(s)"
          % (fil, len(est)))
    cegos = [dict(i, af_atual=None) for i in itens]   # decide sem ver a pessoa
    try:
        d = escolher_af(cur, {"CDFORNECED": forn, "CDFILIRECE": fil,
                              "DTEMISSAO": dtem, "NRNOTAFISC": nr}, cegos, tol)
    except Exception as e:
        print("            -> erro: %s" % str(e)[:60])
        placar["parado"] += 1
        continue
    dec = d.get("decisao")
    reais = sorted({i["af_atual"] for i in itens if i.get("af_atual")})
    if dec == AUTOMATICA:
        af = (d.get("af") or {}).get("af") or ",".join(
            sorted({p["af"] for p in (d.get("plano") or []) if p.get("af")}))
        print("            -> UMA candidata: AF %s" % af)
        print("               %s" % (d.get("motivo") or "")[:70])
        if reais:
            print("               conferencia: a pessoa escolheu %s -> %s"
                  % (reais, "IGUAL" if af in reais else "DIFERENTE"))
    elif dec == AMBIGUA:
        n_op, n_pl = len(d.get("opcoes") or []), len(d.get("plano") or [])
        print("            -> mais de uma possibilidade: %s"
              % ("distribuicao pronta de %s item(ns)" % n_pl if n_pl
                 else "%s AF(s) para escolher" % n_op))
        print("               %s" % (d.get("motivo") or "")[:70])
        print("            *** PARA AQUI, mas leva o trabalho pronto ***")
        placar["humano"] += 1
        continue
    else:
        print("            -> nenhuma AF atende")
        print("               %s" % (d.get("motivo") or "")[:70])
        placar["parado"] += 1
        continue

    # ---- passo 5 -------------------------------------------------------- #
    print("\n   PASSO 5  O Teknisa deixaria a nota entrar?")
    cur.execute("""SELECT TRIM(LEADING '0' FROM CDCONSISTXML), SUBSTR(DSINCONSIS,1,60)
                   FROM TEKNISA.INTG_INCONSISTENCIA
                   WHERE NRACESSONFE=:c AND IDATIVO='S'""", c=ch)
    ap = cur.fetchall()
    if ap:
        print("            a ultima tentativa apontou:")
        for cod, txt in ap[:4]:
            print("              %s  %s" % (cod, txt))
    else:
        print("            nenhum apontamento aberto")
    if pode_importar:
        print("\n   RESULTADO -> RESOLVE SOZINHO: vincula a AF %s e importa" % af)
    else:
        print("\n   RESULTADO -> vincula a AF %s. NAO importa: a nota ainda nao" % af)
        print("                tem tipo de operacao, e importar sem ele nao esta")
        print("                provado. Fica pronta para um clique.")
    placar["sozinho"] += 1

print("\n" + L)
print(" PLACAR: %s resolveria sozinho | %s leva pronto para a pessoa | "
      "%s para antes | %s ja recusadas"
      % (placar["sozinho"], placar["humano"], placar["parado"], placar["recusada"]))
print(L)
con.close()
