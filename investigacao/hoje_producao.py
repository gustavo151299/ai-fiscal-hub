# -*- coding: utf-8 -*-
r"""hoje_producao.py - QUANTAS NOTAS O ROBO RESOLVERIA HOJE, EM PRODUCAO.

100% SOMENTE LEITURA. Usa AIHUB_PROD_* (usuario AIHUB_LEITURA), nunca
TEKNISA_DB_*. Nao existe INSERT/UPDATE/DELETE neste arquivo, e as funcoes
importadas do motor tambem so consultam - gravar_af() nao entra aqui.

O QUE ELE RESPONDE, que a simulacao por amostra nao responde: a simulacao mede
PERCENTUAL sobre uma amostra; isto conta a FILA DE VERDADE de hoje e diz o
numero absoluto. Sao perguntas diferentes e as duas importam - a primeira diz
se o motor acerta, esta diz quanto trabalho ele tira da mesa.

A fila e montada com o MESMO criterio do robo (buscar_para_importar): nota de
compra, nao manual, sem lancamento, dentro da janela. Reimplementar o criterio
aqui daria um numero que nao corresponde ao que o robo faria - foi o defeito da
tela "Simular" antiga (achado 89).

    call CONFIG_PRODUCAO_LEITURA.bat && py investigacao\hoje_producao.py
Variaveis: HJ_LIMITE (padrao 120), HJ_CHEGADA (dias de chegada; vazio = todas).
"""
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import oracledb

from core.af_matcher import (itens_do_xml, traduzir_para_interno, escolher_af,
                             ler_tolerancias, AUTOMATICA, AMBIGUA)
# a MESMA trava que o robo usa, e nao uma copia da regra aqui: medir com uma
# regra propria e o caminho para o numero da simulacao nao ser o do robo
from robo.servico_robo import nota_bloqueada, resolver_depara_do_xml


# --------------------------------------------------------------------- #
# ULTIMA ETAPA DO FUNIL: o Teknisa deixaria essa nota entrar?
# --------------------------------------------------------------------- #
# Conferir so o NOSSO lado da falso verde (achado 90). O motor pode escolher a
# AF certa e a nota ainda assim ser recusada por preco, vencimento ou item fora
# da AF - coisas que nao sao trabalho deste sistema, mas que impedem a
# importacao. Sem esta etapa, "resolveria sozinho" seria uma promessa que a
# importacao nao cumpre.
#
# A CLASSIFICACAO E IMPORTADA do painel, nunca copiada: CONSIST diz de quem e
# cada apontamento (ROBO, COMPRAS, FISCAL, SISTEMA), e uma copia aqui
# divergiria da tela no dia em que alguem mexesse na tabela.
#
# CUIDADO DE LEITURA (achado 1): a INTG_INCONSISTENCIA e LOG e a linha nunca e
# limpa. Em nota nao importada ela e o motivo da ULTIMA tentativa, e nao prova
# de que o problema continua. Por isso o texto diz "ultima tentativa apontou".
try:
    os.environ.setdefault("TEKNISA_DB_DSN", "importacao-apenas")
    os.environ.setdefault("TEKNISA_DB_PASS", "importacao-apenas")
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..",
        "interface", "painel"))
    from app import CONSIST_N, _norm_cod
except Exception as _e:                 # sem Flask instalado, por exemplo
    CONSIST_N, _norm_cod = None, None
    print("  (aviso) sem a tabela de apontamentos: %s" % str(_e)[:70])


# A PERGUNTA CERTA E "a acao do robo conserta isto?", e NAO "de quem e o
# apontamento". O campo 'quem' do CONSIST diz quem trata o caso na fila do
# painel, que e outra coisa - e usar ele aqui deu resposta errada logo no
# primeiro teste: a NF 001915417 foi rebaixada pelo apontamento 031, "Nota sem
# AF", que e exatamente o que o robo resolve.
#
# Entram os codigos que a VINCULACAO resolve:
#   071, 032, 014  produto sem vinculo ou vinculado errado -> o De/Para conserta
#   031            nota sem AF                             -> o robo da uma AF
#   020            AF baixada ou inexistente               -> ele escolhe uma viva
#   017            AF fora do periodo                      -> ele aplica a janela
#   029            filial diferente da AF                  -> ele filtra por filial
#
# Ficam DE FORA, de proposito, os que comparam o CONTEUDO da nota com o pedido
# (054 quantidade, 055 preco, 056 item a mais, 050 vencimento) e todos os
# fiscais: o robo nao muda o que veio na nota, entao vincular nao resolve nada
# disso. Na duvida o codigo fica de fora - errar para o lado de "nao resolve"
# so subestima o numero; errar para o outro promete o que nao se cumpre.
_RESOLVE_VINCULANDO = {"71", "32", "14", "31", "20", "17", "29"}


def apontamentos_abertos(cur, chave):
    """Codigos ativos da nota, separados entre 'e nosso' e 'nao e nosso'."""
    try:
        cur.execute("""
            SELECT DISTINCT CDCONSISTXML FROM TEKNISA.INTG_INCONSISTENCIA
            WHERE  NRACESSONFE = :c AND IDATIVO = 'S'
        """, c=chave)
        cods = [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        return [], []                   # sem acesso: nao inventa apontamento
    if not CONSIST_N:
        return cods, []
    nosso, alheio = [], []
    for cd in cods:
        info = CONSIST_N.get(_norm_cod(cd))
        dono = info[0] if info else "OUTRO"
        titulo = info[1] if info else "apontamento %s" % cd
        alvo = nosso if _norm_cod(cd) in _RESOLVE_VINCULANDO else alheio
        alvo.append((cd, dono, titulo))
    return nosso, alheio


# Recorte por CHEGADA (DTINCLUSAO), opcional. Vazio = nao filtra, que e o
# padrao. Com HJ_CHEGADA=1 mede so o que entrou hoje - e outra pergunta, porque
# a fila carrega 9 mil notas antigas e elas afogam o numero do dia.

def aplicar_depara_simulado(con, cur, chave, itens, forn):
    """Preenche nos itens o De/Para que o robo CRIARIA nesta rodada.

    POR QUE ISTO PRECISA EXISTIR, e por que a medicao sem ele MENTE PARA BAIXO.
    O robo faz, nesta ordem: resolve o De/Para que falta (`resolver_depara_do_xml`)
    e SO ENTAO procura a AF. A medicao anterior pulava a primeira etapa e ia
    direto ao `escolher_af`, entao toda nota com item novo caia em "item sem
    De/Para" - exatamente as notas que o robo existe para destravar.

    E o modo simulacao do proprio robo tambem subestima, por construcao: em
    `dry` a primeira etapa nao grava, e a segunda le o De/Para DO BANCO, onde
    nada foi escrito. Ou seja, `vincular_af_sozinho(dry=True)` responde menos do
    que o robo faria de verdade. Aqui a ponte e feita na memoria.

    A DECISAO CONTINUA SENDO DO ROBO: quem escolhe o produto e
    `resolver_depara_do_xml`, atraves do parametro `detalhe`. O que este codigo
    faz e so traduzir o produto escolhido para os campos que o casamento de AF
    usa (`cd_estoque` e `fator`), com a MESMA consulta do
    `traduzir_para_interno`. Reimplementar a escolha aqui seria medir a
    simulacao, e nao o robo (achado 89).

    Devolve quantos itens foram preenchidos."""
    faltando = [i for i in itens if not i.get("cd_interno")]
    if not faltando:
        return 0
    detalhe = []
    try:
        resolver_depara_do_xml(con, chave, True, detalhe)
    except Exception:
        return 0                        # sem De/Para novo: segue como estava
    # o detalhe vem por sequencia do item; casa por codigo do fornecedor, que e
    # a chave do De/Para
    escolhas = {d.get("cod_fornecedor"): d.get("escolhido")
                for d in detalhe if d.get("auto") and d.get("escolhido")}
    if not escolhas:
        return 0
    n = 0
    for it in faltando:
        cd = escolhas.get(it.get("cprod_xml"))
        if not cd:
            continue
        it["cd_interno"] = cd
        it["cd_estoque"] = cd
        it["fator"] = 1.0
        try:
            cur.execute("""SELECT CDPRODESTO, NVL(VRFATOCONV,1)
                           FROM TEKNISA.PRODUTO WHERE CDPRODUTO=:p""", p=cd)
            p = cur.fetchone()
            if p:
                it["cd_estoque"] = p[0] or cd
                it["fator"] = float(p[1] or 1)
        except Exception:
            pass                        # fica com o proprio codigo e fator 1
        n += 1
    return n


CHEGADA = os.getenv("HJ_CHEGADA", "").strip()
LIMITE = int(os.getenv("HJ_LIMITE", "120"))

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

L = "=" * 78
print(L)
print(" A FILA DE HOJE EM PRODUCAO - %s   (%s, somente leitura)" % (agora, usr))
print(L)

# ---------------------------------------------------------------- panorama --
# UMA consulta agrupada, e nao uma por recorte: contar em varias passadas sobre
# a INTG_NOTAFISCAL custa minutos, e o numero e o mesmo.
cur.execute("""
    SELECT SUM(CASE WHEN G.DTINCLUSAO >= TRUNC(SYSDATE) THEN 1 ELSE 0 END),
           SUM(CASE WHEN G.DTINCLUSAO >= TRUNC(SYSDATE)-6 THEN 1 ELSE 0 END),
           COUNT(*)
    FROM   TEKNISA.INTG_NOTAFISCAL G
    WHERE  G.DTEMISSAO >= SYSDATE - 180
      AND  G.DTLANCAMENNF IS NULL
      AND  G.DTUTILIZAXML IS NULL
      AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
""")
hoje, semana, parada = cur.fetchone()
print("\n CHEGARAM E AINDA NAO ENTRARAM (nao manuais, 180 dias):")
print("   hoje              %6s" % (hoje or 0))
print("   nos ultimos 7 dias%6s" % (semana or 0))
print("   total da fila     %6s" % (parada or 0))

# ------------------------------------------------------- a fila que o robo ve -
# mesmo recorte de buscar_para_importar: precisa de CDTIPOOPER (achado 20/101)
cur.execute("""
    SELECT G.NRACESSONFE, G.NRNOTAFISC, G.CDFORNECED, G.CDFILIRECE,
           G.DTEMISSAO, G.CDTIPOOPER, G.DSTAGORIGFILI,
           (SELECT MAX(F.NMRAZSOCFORN) FROM TEKNISA.FORNECEDOR F
             WHERE F.CDFORNECED = G.CDFORNECED)
    FROM   TEKNISA.INTG_NOTAFISCAL G
    WHERE  G.DTEMISSAO >= SYSDATE - 180
      AND  G.DTLANCAMENNF IS NULL
      AND  G.DTUTILIZAXML IS NULL
      AND  G.CDTIPOOPER IS NOT NULL
      AND  NVL(G.DSTAGORIGFILI,'x') <> 'Manual'
      AND  NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                        WHERE I.NRACESSONFE = G.NRACESSONFE
                          AND I.NRAUTOFORN IS NOT NULL)
      %s
    ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIRECE, G.NRACESSONFE
    FETCH  FIRST :lim ROWS ONLY
""" % ("AND G.DTINCLUSAO >= TRUNC(SYSDATE) - :d + 1" if CHEGADA else ""),
            **({"lim": LIMITE, "d": int(CHEGADA)} if CHEGADA
               else {"lim": LIMITE}))
fila = cur.fetchall()
print("\n DESSAS, AS QUE O ROBO ALCANCA (tem tipo de operacao e nenhuma AF)%s:"
      % (", chegadas nos ultimos %s dia(s)" % CHEGADA if CHEGADA else ""))
print("   %s nota(s) na fila que ele trabalharia" % len(fila))

if not fila:
    print("\n Nada a decidir agora.")
    con.close()
    sys.exit(0)

# ------------------------------------------------------------- o veredito ---
# ler_tolerancias devolve (quantidade, valor); o motor quer so a de QUANTIDADE.
# Passar a tupla inteira estoura com "unsupported operand type(s) for /" - erro
# de Python, sem codigo ORA, que se disfarca de "o motor nao achou AF".
tol = ler_tolerancias(cur)[0]
t0 = time.time()
placar = Counter()
motivos = Counter()
resolvidas, humanas, recusadas, barradas = [], [], [], []
for ch, nr, forn, fil, dtem, tipo, orig, nmf in fila:
    # a nota vai como DICIONARIO, no formato que o motor espera; montar isso
    # "quase igual" e o caminho para medir uma coisa e o robo fazer outra
    nota = {"CDFORNECED": forn, "CDFILIRECE": fil, "DTEMISSAO": dtem,
            "NRNOTAFISC": nr}
    # PRIMEIRA PERGUNTA, como no robo: a operacao ja recusou esta nota?
    morta, porque = nota_bloqueada(cur, ch)
    if morta:
        placar["a operacao ja recusou (bloqueada/estornada)"] += 1
        recusadas.append((nr, nmf, porque))
        continue
    try:
        itens = itens_do_xml(cur, ch)
        traduzir_para_interno(cur, forn, itens)
        # a ETAPA QUE FALTAVA: o robo resolve o De/Para antes de procurar a AF
        novos = aplicar_depara_simulado(con, cur, ch, itens, forn)
        if novos:
            placar["  (o robo criaria De/Para de item novo)"] += novos
        out = escolher_af(cur, nota, itens, tol)
    except Exception as e:
        placar["erro ao avaliar"] += 1
        motivos["erro: %s" % str(e)[:50]] += 1
        continue
    dec = out.get("decisao")
    if dec == AUTOMATICA:
        af = (out.get("af") or {}).get("af")
        if not af:                      # nota que cumpre mais de um pedido
            af = ", ".join(sorted({p["af"] for p in (out.get("plano") or [])
                                   if p.get("af")}))
        _nosso, _alheio = apontamentos_abertos(cur, ch)
        if _alheio:
            # a AF esta resolvida, mas a nota nao entra por outro motivo
            placar["AF resolvida, mas o Teknisa barrou por outro motivo"] += 1
            barradas.append((nr, nmf, af, _alheio))
        else:
            placar["resolveria sozinho, de ponta a ponta"] += 1
            resolvidas.append((nr, nmf, af, out.get("motivo", "")))
    elif dec == AMBIGUA:
        # SAO DUAS TELAS DIFERENTES, e contar as duas juntas esconde a melhor.
        # 'opcoes' = lista de AFs para a pessoa escolher.
        # 'plano'  = distribuicao PRONTA item a item, que ela confirma em um
        #            clique (achado 69). Somar os dois como "manda para a
        #            pessoa" faz o trabalho de um clique parecer com o de
        #            procurar AF do zero.
        n_op = len(out.get("opcoes") or [])
        n_pl = len(out.get("plano") or [])
        if n_pl:
            placar["leva pronto para 1 clique (distribuicao por item)"] += 1
            humanas.append((nr, nmf, "distribuicao pronta de %s item(ns)" % n_pl))
        else:
            placar["manda escolher entre AFs (lista na tela)"] += 1
            humanas.append((nr, nmf, "%s AF(s) para escolher" % n_op))
    else:
        placar["nao achou AF"] += 1
        motivos[(out.get("motivo") or "sem motivo")[:74]] += 1
    if any(not i.get("cd_interno") for i in itens):
        placar["  (dessas, com item sem De/Para)"] += 1

print("   %.0f s para decidir as %s" % (time.time() - t0, len(fila)))
print("\n O QUE ELE FARIA AGORA:")
for k, v in placar.most_common():
    print("   %-44s %4s  (%s%%)"
          % (k, v, round(100.0 * v / len(fila))))

if resolvidas:
    print("\n RESOLVERIA SOZINHO (nota -> AF):")
    for nr, nmf, af, mot in resolvidas[:12]:
        print("   NF %-12s AF %-12s %s" % (nr, af or "?", (nmf or "")[:38]))
    if len(resolvidas) > 12:
        print("   ... e mais %s" % (len(resolvidas) - 12))

if humanas:
    print("\n LEVARIA PARA A PESSOA, COM O TRABALHO JA FEITO:")
    for nr, nmf, como in humanas[:8]:
        print("   NF %-12s %-38s %s" % (nr, (nmf or "")[:38], como))
    if len(humanas) > 6:
        print("   ... e mais %s" % (len(humanas) - 6))

if barradas:
    print("\n A AF ESTARIA RESOLVIDA, MAS A NOTA NAO ENTRA:")
    print("   (a ultima tentativa de importacao apontou motivo que nao e",
          "trabalho deste sistema)")
    for nr, nmf, af, alheio in barradas[:8]:
        print("   NF %-12s AF %-12s %s" % (nr, af or "?", (nmf or "")[:28]))
        for cd, dono, tit in alheio[:3]:
            print("      %s [%s] %s" % (cd, dono, tit))
    if len(barradas) > 8:
        print("   ... e mais %s" % (len(barradas) - 8))

if recusadas:
    print("\n A OPERACAO JA RECUSOU (o robo nao encosta):")
    for nr, nmf, pq in recusadas[:6]:
        print("   NF %-12s %-28s %s" % (nr, (nmf or "")[:28], pq[:58]))
    if len(recusadas) > 6:
        print("   ... e mais %s" % (len(recusadas) - 6))

if motivos:
    print("\n POR QUE NAO ACHOU AF:")
    for m, v in motivos.most_common(6):
        print("   %3sx %s" % (v, m))

print("\n" + L)
con.close()
