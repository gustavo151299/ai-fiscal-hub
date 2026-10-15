"""
core/af_matcher.py - Escolhe SOZINHO a AF que corresponde a uma nota do XML.

E a funcao que originou o projeto. Hoje, no Teknisa, esse passo e MANUAL: o
manual "Rotina da Unidade - Importacao de AF" diz, textualmente, que o sistema
"abrira uma tela com todas as AFs do fornecedor selecionado que estiverem em
aberto" e que a pessoa deve "marcar o checkbox ao lado da AF desejada". Nao ha
escolha automatica, e nao existe parametro para liga-la.

ONDE A AF MORA (medido em producao, 04/08/2026):
  A AF fica em INTG_ITEMPRODUTO.NRAUTOFORN, gravada ANTES da importacao.
  - notas ja importadas: 37.504 de 37.526 itens com AF preenchida (100%)
  - notas paradas:          972 de  1.453 itens (67%)
  O numero gravado na integracao e IDENTICO ao que aparece depois na
  ITEMPRODUTO. Ou seja: quem preenche esse campo determina a AF da nota.

A REGRA DE CASAMENTO NAO FOI INVENTADA. Ela vem dos proprios parametros do
Teknisa (tela "Parametros de Documentos Fiscais de Entrada", TEKNISA.PARAMGERAL):
  VRPETOLEQTAF = 15,0  -> tolerancia de 15% na QUANTIDADE
  VRPETOLEVRAF =  0,5  -> tolerancia de 0,5% no VALOR
  IDAGRUPPRODAFNF = S  -> agrupa produto AF x NF
Lidos de producao; o codigo le de novo em tempo de execucao (ver ler_tolerancias).

O PRODUTO CASA PELA ARVORE, NAO PELO CODIGO. Medido: a AF pode estar no produto
de COMPRA (embalagem, sufixo 01/02/03) enquanto a nota entra no de ESTOQUE
(granel, sufixo 00), e vice-versa. Sao o mesmo item, ligados pelos 8 primeiros
digitos (o nivel 4 da arvore), com fator de conversao entre eles. Exigir codigo
identico faz a busca pular a AF certa e pegar uma de dois anos atras (caso real
observado). Por isso o casamento e por SUBSTR(CDPRODUTO,1,8).

CUIDADO COM A CHAVE DA AF: a PK e (CDFILDEST, CDFILLANC, NRAUTOFORN). O numero
sozinho NAO identifica a AF.

FILOSOFIA DE SEGURANCA: melhor nao escolher do que escolher errado. Amarrar a
nota na AF errada baixa o pedido de outra compra. Por isso:
  - so decide sozinho quando ha UMA candidata (medido: 55% dos casos)
  - havendo mais de uma, devolve a lista ranqueada para a pessoa clicar
  - qualquer duvida (produto sem De/Para, filial ausente) para e devolve NENHUMA
"""

from __future__ import annotations

# Tolerancias padrao. Sao apenas a RESERVA para quando nao der para ler o
# PARAMGERAL: os valores reais vem do banco em ler_tolerancias().
TOL_QT_PADRAO = 15.0     # %
TOL_VR_PADRAO = 0.5      # %

# Janela de dias entre a entrega programada na AF e a emissao da nota.
# Medido: com +-20 dias a regra encontra candidata em 97% das notas; abaixo
# disso comeca a perder AF legitima (caso real: entrega 24/07, nota 04/08).
JANELA_DIAS = 20

# --------------------------------------------------------------------------- #
# O PREDICADO DE AF CANDIDATA - COPIADO DO CODIGO NATIVO, NAO INVENTADO.
#
# Fonte: PL/SQL das procedures do proprio Teknisa (VERIFICA_INCONS_NF,
# GERA_NOTAFISCAL_CORPO_NFE, NF_BAIXA_AF), achado na base de documentacao.
# Antes disto o motor inferia a regra por estatistica e errava em 3 pontos:
#   1. casava produto pelos 8 primeiros digitos da arvore -> o certo e
#      PRODUTO.CDPRODESTO com IGUALDADE (o prefixo era so uma proxy que
#      funcionava por acaso nesta base)
#   2. usava a tolerancia de 15% como filtro de selecao -> ela e consistencia
#      de FECHAMENTO (gera aviso), nunca escolhe AF
#   3. media a janela de dias contra a emissao da nota -> o nativo mede contra
#      a DATA DE PROCESSAMENTO da filial
# e nao aplicava nenhum dos filtros de situacao da AF abaixo.
#
# Filtros do nativo (todos obrigatorios):
_SQL_AF_CANDIDATA = """
          AND  (A.IDBAIXAAF = '1' OR A.IDBAIXAAF IS NULL)
          AND  (I.IDBAIXITMAF NOT IN ('2','3') OR I.IDBAIXITMAF IS NULL)
          AND  (NVL(I.QTPROGENTAF,0) - NVL(I.QTEFETENTRAF,0)) > 0
          AND  NVL(A.IDAFFORALIM,'N') = 'N'
          AND  A.IDSITUATUAAF IS NULL
          AND  NVL(A.IDAFOPERLOG,'1') = '1'
          AND  NVL(A.IDAFEXPIRADA,'N') = 'N'
"""
# Ordem de consumo do nativo: entrega programada mais ANTIGA primeiro.
# (NF_BAIXA_AF: ORDER BY CDFILDEST, CDFILLANC, DTPROGENTAF, NRAUTOFORN)
_SQL_ORDEM_NATIVA = "MIN(I.DTPROGENTAF), A.NRAUTOFORN"

# Decisoes possiveis
AUTOMATICA = "automatica"   # 1 candidata: pode gravar sozinho
AMBIGUA    = "ambigua"      # 2+ candidatas: a pessoa escolhe (lista ranqueada)
NENHUMA    = "nenhuma"      # nao ha AF que sirva


def ler_tolerancias(cur):
    """Le as tolerancias do proprio Teknisa. Nao inventa regra: usa a que o
    cliente configurou na tela de parametros.

    Se nao conseguir ler (falta de GRANT em PARAMGERAL, coluna ausente numa
    versao diferente), cai no padrao medido. Fail-safe no sentido seguro: o
    padrao e o valor real da producao, entao nao afrouxa nada."""
    try:
        cur.execute("SELECT VRPETOLEQTAF, VRPETOLEVRAF FROM TEKNISA.PARAMGERAL")
        r = cur.fetchone()
        if r:
            qt = float(r[0]) if r[0] is not None else TOL_QT_PADRAO
            vr = float(r[1]) if r[1] is not None else TOL_VR_PADRAO
            return qt, vr
    except Exception:
        pass
    return TOL_QT_PADRAO, TOL_VR_PADRAO


# POR QUE A TOLERANCIA DE VALOR (VRPETOLEVRAF) NAO VIRA FILTRO DE AF.
#
# A ideia e tentadora: em 900 vinculos verdadeiros de producao, 98,1% tem o
# preco da nota a menos de 0,5% do preco da AF, e NENHUM passa de 25%. Parece
# um veto barato contra AF errada. Medido nas notas paradas (1.596 itens), nao
# e - dos 61 itens com diferenca acima de 25%:
#     31 (51%) sao produto da nota != produto da AF (embalagem x granel)
#     11 (18%) tem razao inteira exata (2x, 3x, 5x, 20x): unidade, nao preco
#     22       sao diferenca de preco de verdade, mesmo produto e mesma unidade
# Ou seja, o veto barraria sobretudo conversao de unidade, que e normal. E os
# 22 legitimos nao sao trabalho nosso: divergencia de preco e consistencia de
# FECHAMENTO, tratada pela VERIFICA_INCONS_NF na confirmacao, que e onde o
# Teknisa usa esse parametro. Duplicar aqui so tiraria nota boa da automacao.
# ler_tolerancias devolve o valor mesmo assim, para quem quiser exibir.


def janela_da_filial(cur, cd_filial, dt_emissao=None):
    """Janela de datas para procurar AF.

    DUAS SITUACOES, e elas se comportam de forma bem diferente:

    1) O CLIENTE CONFIGUROU limite de dias (PARAMFILIAL.NRMAXDIAANTDTAF e
       NRMAXDIAPOSDTAF). Ai seguimos o nativo ao pe da letra: a ancora e a
       DATA DE PROCESSAMENTO da filial (PARAMFILIAL.DTPROCESSA quando
       PARAMGERAL.IDUTILDTPROC='S', senao SYSDATE), e os limites sao os dele.

    2) O CLIENTE NAO CONFIGUROU (o caso real: 0 de 819 filiais tem limite).
       O nativo entao NAO aplica janela nenhuma - ele conta com uma PESSOA
       escolhendo na lista. Para automacao isso e perigoso: sem janela, a busca
       ja pegou AF de dois anos atras. Entao aplicamos uma janela de seguranca
       propria, e ela e ancorada na EMISSAO DA NOTA, nao na DTPROCESSA.

    POR QUE NAO ANCORAR NA DTPROCESSA NO CASO 2 (erro que eu cometi):
    medido em producao, 105 das 210 filiais que recebem nota estao com
    DTPROCESSA NULA ou atrasada mais de 20 dias - a maior delas (9102, com 980
    notas) esta nula. Ancorar ali jogaria a janela para o passado e o motor nao
    acharia AF nenhuma para metade das filiais. A DTPROCESSA so e confiavel
    quando o cliente de fato usa esse controle, que e o caso 1.

    Devolve (ancora, dias_antes, dias_depois). None em dias = sem limite."""
    try:
        cur.execute("""
            SELECT PF.NRMAXDIAANTDTAF, PF.NRMAXDIAPOSDTAF,
                   CASE WHEN NVL(PG.IDUTILDTPROC,'N')='S'
                        THEN NVL(PF.DTPROCESSA, TRUNC(SYSDATE))
                        ELSE TRUNC(SYSDATE) END
            FROM   TEKNISA.PARAMGERAL PG, TEKNISA.PARAMFILIAL PF
            WHERE  PF.CDFILIAL = :f
            FETCH FIRST 1 ROWS ONLY
        """, f=cd_filial)
        r = cur.fetchone()
        if r and (r[0] is not None or r[1] is not None):
            # caso 1: o cliente configurou -> obedece o nativo
            return r[2], r[0], r[1]
    except Exception:
        pass
    # caso 2 (e tambem o fail-safe): janela de seguranca nossa, ancorada na
    # emissao da nota. Nunca abre geral.
    return (dt_emissao, JANELA_DIAS, JANELA_DIAS)


def _texto(v):
    """CLOB vira str. Sem isto o codigo do produto viraria '<LOB>'."""
    if v is not None and hasattr(v, "read"):
        try:
            return v.read()
        except Exception:
            return None
    return v


def itens_do_xml(cur, chave):
    """Itens da nota como estao na INTEGRACAO (antes de virar nota).

    ATENCAO: INTG_ITEMPRODUTO.CDPRODUTO e o codigo DO FORNECEDOR (vem '30252-0',
    '2', '4'), nao o interno de 10 digitos. Quem traduz e o De/Para. Cruzar essa
    coluna direto com ITEMAUTF nao acha nada - erro que ja custou uma rodada."""
    cur.execute("""
        SELECT NRSEQITPROD, CDPRODUTO, NMPRODUTO, SGUNIDADE,
               NVL(QTITEMPROD,0), NRAUTOFORN
        FROM   TEKNISA.INTG_ITEMPRODUTO
        WHERE  NRACESSONFE = :c
        ORDER  BY NRSEQITPROD
    """, c=chave)
    saida = []
    for seq, cprod, nome, un, qt, af in cur.fetchall():
        saida.append({"seq": seq, "cprod_xml": _texto(cprod),
                      "desc_xml": _texto(nome), "un": un,
                      "qt": float(qt or 0), "af_atual": af})
    return saida


def traduzir_para_interno(cur, cd_forn, itens):
    """cProd do fornecedor -> produto interno -> produto de ESTOQUE.

    Sem De/Para nao da para achar a AF: o produto interno e a unica ponte entre
    a nota e a AF. Item sem traducao volta com 'cd_interno' None, e isso derruba
    a decisao automatica (ver escolher_af).

    Traz tambem CDPRODESTO e VRFATOCONV: e por CDPRODESTO (igualdade) que o
    nativo casa item de NF com item de AF - a AF fica no produto de COMPRA
    (embalagem) e a nota pode vir no de ESTOQUE, ou em outra embalagem; o que
    os une e o produto de estoque comum, e a quantidade se converte pelo fator.
    """
    for it in itens:
        it["cd_interno"] = None
        it["cd_estoque"] = None
        it["fator"] = 1.0
        if not it["cprod_xml"]:
            continue
        cur.execute("""
            SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML
            WHERE  CDFORNECED=:f AND CDPRODUTOXML=:c AND IDATIVO='S'
            ORDER  BY DTINCLUSAO DESC NULLS LAST, CDPRODUTO
            FETCH FIRST 1 ROWS ONLY
        """, f=cd_forn, c=it["cprod_xml"])
        r = cur.fetchone()
        if not r:
            continue
        it["cd_interno"] = r[0]
        try:
            cur.execute("""SELECT CDPRODESTO, NVL(VRFATOCONV,1)
                           FROM TEKNISA.PRODUTO WHERE CDPRODUTO=:p""", p=r[0])
            p = cur.fetchone()
            if p:
                # produto de estoque: quando a coluna vem vazia, o proprio
                # produto ja e o de estoque (fator 1)
                it["cd_estoque"] = p[0] or r[0]
                it["fator"] = float(p[1] or 1)
        except Exception:
            it["cd_estoque"] = r[0]
    return itens


def _estoques(itens):
    """Produtos de ESTOQUE dos itens. E a chave de casamento com a AF,
    conforme o nativo: PRODUTO.CDPRODESTO com igualdade."""
    return {it["cd_estoque"] for it in itens if it.get("cd_estoque")}


def candidatas(cur, cd_forn, cd_filial, itens, dt_emissao, tol_qt=None):
    """AFs que podem ser desta nota, ranqueadas.

    Filtros (todos obrigatorios):
      - mesmo fornecedor
      - CDFILDEST da AF = filial que RECEBE a nota
      - a AF cobre TODOS os galhos de produto da nota
      - entrega programada dentro da janela em torno da emissao

    Ranking: primeiro a que tem a quantidade mais proxima (dentro da tolerancia
    do Teknisa), depois a entrega mais proxima da emissao.

    AS DUAS QUANTIDADES PRECISAM ESTAR NA MESMA UNIDADE, e nao estavam.
    A nota ja vinha convertida (qt do XML x fator do produto da nota), mas o
    lado da AF era lido cru, direto de QTPROGENTAF. So que a AF costuma estar
    no produto de COMPRA: caso real, AF 0001464044 programa "2" de
    "BOV CONG CARNE MOIDA - CX 5" (CX, fator 5) = 10 kg, e o codigo comparava
    "2" com os 15 kg da nota. Por isso a multiplicacao por PA.VRFATOCONV.
    Medido em producao (06/08/2026), 700 itens de vinculos VERDADEIROS cujo
    produto de AF tem fator != 1: comparando cru, a nota "cabia" na AF que o
    proprio Teknisa usou em apenas 61% dos casos; convertendo, 100%. Ou seja,
    a leitura crua descartava 39% de AF legitima - e no sentido contrario
    (fator 0,05, 3,3% dos itens) fazia o saldo parecer 20x maior do que e.

    Devolve lista de dicts, a melhor primeiro."""
    if tol_qt is None:
        tol_qt = TOL_QT_PADRAO
    estoques = _estoques(itens)
    if not estoques or not cd_filial or not dt_emissao:
        return []
    # quantidade na unidade de ESTOQUE (o nativo converte pelo fator antes de
    # comparar com a AF): qt do XML x fator do produto do XML
    qt_nota = sum(it["qt"] * it.get("fator", 1.0) for it in itens)
    dt_proc, d_ant, d_pos = janela_da_filial(cur, cd_filial, dt_emissao)
    binds = {"f": cd_forn, "fl": cd_filial, "dt": dt_emissao,
             "ne": len(estoques),
             "dtp": dt_proc, "dant": d_ant, "dpos": d_pos}
    for i, g in enumerate(sorted(estoques)):
        binds["g%d" % i] = g
    lista_g = ",".join(":g%d" % i for i in range(len(estoques)))
    cur.execute("""
        SELECT A.NRAUTOFORN, A.CDFILDEST, A.CDFILLANC,
               COUNT(DISTINCT PA.CDPRODESTO)                      AS COBRE,
               ROUND(SUM(NVL(I.QTPROGENTAF,0)
                         * NVL(NULLIF(PA.VRFATOCONV,0),1)),4)     AS QT_AF,
               ROUND(SUM(NVL(I.QTEFETENTRAF,0)
                         * NVL(NULLIF(PA.VRFATOCONV,0),1)),4)     AS QT_ENTROU,
               TO_CHAR(MIN(I.DTPROGENTAF),'DD/MM/YYYY')           AS ENTREGA,
               ABS(MIN(I.DTPROGENTAF) - :dt)                      AS DIST_DIAS,
               TO_CHAR(MAX(A.DTAUTOFORN),'DD/MM/YYYY')            AS DT_AF
        FROM   TEKNISA.AUTOFORNE A
        JOIN   TEKNISA.ITEMAUTF  I
               ON  I.CDFILDEST = A.CDFILDEST
               AND I.CDFILLANC = A.CDFILLANC
               AND I.NRAUTOFORN = A.NRAUTOFORN
        JOIN   TEKNISA.PRODUTO PA ON PA.CDPRODUTO = I.CDPRODUTO
        WHERE  A.CDFORNECED = :f
          AND  A.CDFILDEST  = :fl
          AND  NVL(A.IDATIVO,'S') <> 'N'
          AND  NVL(PA.CDPRODESTO, PA.CDPRODUTO) IN (""" + lista_g + """)
          AND  (:dant IS NULL OR I.DTPROGENTAF >= NVL(:dtp,TRUNC(SYSDATE)) - :dant)
          AND  (:dpos IS NULL OR I.DTPROGENTAF <= NVL(:dtp,TRUNC(SYSDATE)) + :dpos)
        """ + _SQL_AF_CANDIDATA + """
        GROUP  BY A.NRAUTOFORN, A.CDFILDEST, A.CDFILLANC
        HAVING COUNT(DISTINCT NVL(PA.CDPRODESTO, PA.CDPRODUTO)) = :ne
        ORDER  BY """ + _SQL_ORDEM_NATIVA + """
    """, binds)
    saida = []
    for (nraf, fildest, fillanc, cobre, qt_af, qt_ent, entrega, dist, dt_af) in cur.fetchall():
        qt_af = float(qt_af or 0)
        qt_ent = float(qt_ent or 0)
        saldo = qt_af - qt_ent
        # A QUANTIDADE DA NOTA NAO PRECISA SER IGUAL A DA AF.
        #
        # Erro que a simulacao em producao pegou: eu comparava a nota com o
        # TOTAL programado da AF e acusava "8650% de diferenca" numa nota de 16
        # contra AF de 1400. Nao era divergencia: era ENTREGA PARCIAL, que e
        # normal. O nativo (NF_BAIXA_AF) consome min(quantidade da nota, saldo
        # da AF) e leva o resto para a proxima AF - entrega parcial e o caso
        # comum, nao a excecao.
        #
        # O que importa entao e se a nota CABE no saldo. So passa do saldo (mais
        # a tolerancia) e' que vira divergencia de verdade.
        cabe = qt_nota <= saldo * (1 + tol_qt / 100.0) if saldo > 0 else False
        excedente = max(0.0, qt_nota - saldo)
        saida.append({
            "af": nraf, "cd_fildest": fildest, "cd_fillanc": fillanc,
            "qt_af": qt_af, "qt_nota": qt_nota, "qt_entrou": qt_ent,
            "saldo": round(saldo, 4),
            "excedente": round(excedente, 4),
            "consome_pct": round(100.0 * qt_nota / saldo, 1) if saldo else 0.0,
            "dentro_tolerancia": cabe,
            "entrega": entrega, "dias_da_emissao": int(dist or 0),
            "dt_af": dt_af, "cobre_itens": int(cobre or 0),
        })
    return saida


def candidatas_item(cur, cd_forn, cd_filial, cd_estoque, qt_item, dt_emissao, tol_qt):
    """AFs que atendem UM item (um produto de estoque), ranqueadas.

    Usada quando nenhuma AF sozinha cobre a nota inteira. Nao e excecao: 24.125
    notas em producao tem AFs DIFERENTES entre os itens - uma entrega pode
    cumprir dois pedidos. A amarracao nativa (NFRELACAF) tambem e item a item.

    Ranking: quantidade programada mais proxima da do item, depois entrega mais
    proxima da emissao.

    A MESMA GUARDA DA candidatas(), QUE FALTAVA AQUI. Sem data de emissao nao
    ha janela, e o bind nulo entra em 'MIN(DTPROGENTAF) - :dt': o Oracle nao
    tem como saber o tipo do NULL, assume NUMBER e estoura
    ORA-00932 'expected NUMBER got DATE'. Medido em homologacao: 87 de 150
    notas paradas quebravam assim, todas com DTEMISSAO nula. As duas funcoes
    decidem sobre a mesma coisa e tem que se proteger igual - foi so a
    candidatas() ter a guarda e a candidatas_item() nao que escondeu isto."""
    if not cd_estoque or not cd_filial or not dt_emissao:
        return []
    dt_proc, d_ant, d_pos = janela_da_filial(cur, cd_filial, dt_emissao)
    cur.execute("""
        SELECT A.NRAUTOFORN, A.CDFILDEST, A.CDFILLANC,
               ROUND(SUM(NVL(I.QTPROGENTAF,0)
                         * NVL(NULLIF(PA.VRFATOCONV,0),1)),4)  AS QT_AF,
               ROUND(SUM(NVL(I.QTEFETENTRAF,0)
                         * NVL(NULLIF(PA.VRFATOCONV,0),1)),4)  AS QT_ENTROU,
               TO_CHAR(MIN(I.DTPROGENTAF),'DD/MM/YYYY') AS ENTREGA,
               ABS(MIN(I.DTPROGENTAF) - :dt)            AS DIST
        FROM   TEKNISA.AUTOFORNE A
        JOIN   TEKNISA.ITEMAUTF  I
               ON  I.CDFILDEST = A.CDFILDEST
               AND I.CDFILLANC = A.CDFILLANC
               AND I.NRAUTOFORN = A.NRAUTOFORN
        JOIN   TEKNISA.PRODUTO PA ON PA.CDPRODUTO = I.CDPRODUTO
        WHERE  A.CDFORNECED = :f
          AND  A.CDFILDEST  = :fl
          AND  NVL(A.IDATIVO,'S') <> 'N'
          AND  NVL(PA.CDPRODESTO, PA.CDPRODUTO) = :g
          AND  (:dant IS NULL OR I.DTPROGENTAF >= NVL(:dtp,TRUNC(SYSDATE)) - :dant)
          AND  (:dpos IS NULL OR I.DTPROGENTAF <= NVL(:dtp,TRUNC(SYSDATE)) + :dpos)
        """ + _SQL_AF_CANDIDATA + """
        GROUP  BY A.NRAUTOFORN, A.CDFILDEST, A.CDFILLANC
        ORDER  BY """ + _SQL_ORDEM_NATIVA + """
    """, dt=dt_emissao, f=cd_forn, fl=cd_filial, g=cd_estoque,
         dtp=dt_proc, dant=d_ant, dpos=d_pos)
    saida = []
    for (nraf, fildest, fillanc, qt_af, qt_ent, entrega, dist) in cur.fetchall():
        qt_af = float(qt_af or 0)
        qt_ent = float(qt_ent or 0)
        saldo = qt_af - qt_ent
        # MESMA semantica de candidatas(): o que importa e o item CABER no
        # saldo, nao ser igual a AF. Estava divergente aqui, e foi so o erro de
        # chave ausente ('saldo') que expos - as duas funcoes decidem sobre a
        # mesma coisa e precisam medir igual.
        cabe = qt_item <= saldo * (1 + tol_qt / 100.0) if saldo > 0 else False
        saida.append({"af": nraf, "cd_fildest": fildest, "cd_fillanc": fillanc,
                      "qt_af": qt_af, "qt_item": qt_item,
                      "saldo": round(saldo, 4),
                      "excedente": round(max(0.0, qt_item - saldo), 4),
                      "consome_pct": round(100.0 * qt_item / saldo, 1) if saldo else 0.0,
                      "dentro_tolerancia": cabe,
                      "entrega": entrega, "dias_da_emissao": int(dist or 0)})
    return saida


def resolver_por_item(cur, nota, itens, tol_qt):
    """Distribui os itens entre as AFs, quando nenhuma AF cobre a nota toda.

    So devolve 'automatica' se TODO item achou AF sem ambiguidade. Se um item
    ficar sem AF, ou tiver empate de verdade, a nota vai para a pessoa - mas
    ja com a distribuicao proposta na tela, para ela so conferir."""
    filial = nota.get("CDFILIRECE")
    forn = nota.get("CDFORNECED")
    dt = nota.get("DTEMISSAO")
    plano = []
    todos_ok = True
    for it in itens:
        est = it.get("cd_estoque")
        # quantidade convertida para a unidade de estoque, como o nativo faz
        qt_est = it["qt"] * it.get("fator", 1.0)
        cands = candidatas_item(cur, forn, filial, est, qt_est, dt, tol_qt) if est else []
        # A PRIMEIRA candidata E a escolha do nativo: candidatas_item ordena por
        # DTPROGENTAF e depois NRAUTOFORN, que e a ordem em que a NF_BAIXA_AF
        # consome (entrega mais antiga primeiro). Nao inventamos criterio aqui;
        # so exigimos que a quantidade CAIBA no saldo.
        escolhida = cands[0] if (cands and cands[0]["dentro_tolerancia"]) else None
        # A PRIMEIRA nao coube, mas alguma OUTRA cabe. Isso e comum: o item
        # 15 kg contra uma AF de 10 kg e outra de 15 kg.
        #
        # NAO promovemos a outra a escolha automatica, de proposito. O nativo
        # consome na ordem de entrega e LEVA O RESTO para a AF seguinte - no
        # caso real ele usaria as duas. INTG_ITEMPRODUTO guarda UM numero por
        # item, entao isso nem da para representar; e pular a primeira por
        # conta propria seria inventar um criterio que o Teknisa nao tem.
        #
        # Mas a pessoa precisa VER que existe uma que cabe. Antes o motor
        # respondia "nenhuma AF atende os produtos da nota" - falso, e mandava
        # ela procurar do zero justamente quando a resposta estava na mao.
        sugerida = None
        if not escolhida:
            todos_ok = False
            sugerida = next((c for c in cands if c["dentro_tolerancia"]), None)
        plano.append({"seq": it["seq"], "produto": it.get("cd_interno"),
                      "desc": it.get("desc_xml"), "qt": it["qt"],
                      "af": (escolhida or {}).get("af"),
                      "af_sugerida": (sugerida or {}).get("af"),
                      "opcoes": cands[:4]})
    return plano, todos_ok


def af_inequivoca(cur, nr_af, cd_filial):
    """O NUMERO da AF identifica UMA unica AF nessa filial?

    POR QUE ISTO EXISTE. A gravacao vai para INTG_ITEMPRODUTO.NRAUTOFORN, que
    guarda SO O NUMERO - a tabela nao tem CDFILDEST nem CDFILLANC. Mas a PK da
    AF e (CDFILDEST, CDFILLANC, NRAUTOFORN). Ou seja: se o mesmo numero existir
    duas vezes, o que gravamos aponta para duas AFs, e o Teknisa escolhe uma
    delas - podendo baixar o pedido errado.

    Medido em producao (06/08/2026): 831.840 AFs, apenas 1 numero repetido
    (0001063951, nas filiais 1046 e 1117), e nenhuma nota o usa. E zero casos
    de mesmo numero com a MESMA filial destino. Ou seja, hoje nao ha problema.
    Mas isso e seguranca por DADO, nao por CONSTRUCAO: basta uma AF nova
    repetir o numero para o vinculo virar loteria.

    Entao conferimos na hora. Custa uma consulta por AF e fecha o buraco.
    Fail-safe: se nao der para conferir, responde NAO (nao vincula)."""
    if not nr_af:
        return False
    try:
        cur.execute("""SELECT COUNT(*) FROM TEKNISA.AUTOFORNE
                       WHERE NRAUTOFORN = :a AND CDFILDEST = :f""",
                    a=nr_af, f=cd_filial)
        r = cur.fetchone()
        return bool(r) and int(r[0] or 0) == 1
    except Exception:
        return False


def escolher_af(cur, nota, itens, tol_qt=None):
    """Decide a AF desta nota.

    'nota' precisa de: CDFORNECED, CDFILIRECE (filial que recebe), DTEMISSAO.

    Devolve dict com:
      decisao : automatica | ambigua | nenhuma
      af      : a AF escolhida (so em 'automatica')
      opcoes  : candidatas ranqueadas (em 'ambigua', para a pessoa clicar)
      motivo  : texto em portugues explicando a decisao

    So devolve 'automatica' quando NAO HA DUVIDA:
      - todos os itens tem De/Para (senao nao da para saber o produto)
      - existe EXATAMENTE uma AF candidata
      - a quantidade dela esta dentro da tolerancia do Teknisa
    Medido em producao: isso cobre 55% das notas. O resto vai para a pessoa,
    com as opcoes prontas - que ja e muito melhor que procurar do zero."""
    if tol_qt is None:
        tol_qt, _ = ler_tolerancias(cur)

    if not itens:
        return {"decisao": NENHUMA, "af": None, "opcoes": [],
                "motivo": "A nota não tem itens."}

    sem_depara = [it for it in itens if not it.get("cd_interno")]
    if sem_depara:
        return {"decisao": NENHUMA, "af": None, "opcoes": [],
                "motivo": ("%d item(ns) do fornecedor ainda não têm produto "
                           "vinculado (De/Para). Sem saber o produto interno não "
                           "dá para achar a AF." % len(sem_depara))}

    filial = nota.get("CDFILIRECE")
    if not filial:
        return {"decisao": NENHUMA, "af": None, "opcoes": [],
                "motivo": "A nota não informa a filial que recebe."}

    # SEM DATA DE EMISSAO NAO HA JANELA, e sem janela a busca pega AF de anos
    # atras. Recusa aqui, com motivo em portugues, em vez de deixar o erro
    # aparecer la embaixo como ORA-00932 (que foi o sintoma em homologacao).
    if not nota.get("DTEMISSAO"):
        return {"decisao": NENHUMA, "af": None, "opcoes": [],
                "motivo": ("A nota não tem data de emissão na integração. Sem "
                           "ela não dá para limitar a busca da AF a um período, "
                           "e o sistema não escolhe AF sem esse limite.")}

    cands = candidatas(cur, nota.get("CDFORNECED"), filial, itens,
                       nota.get("DTEMISSAO"), tol_qt)

    # CAMINHO 2: nenhuma AF cobre a nota inteira -> distribui item a item.
    # Nao e excecao: uma entrega pode cumprir dois pedidos, e o Teknisa amarra
    # item a item (NFRELACAF). Sem isto, toda nota multi-AF cairia no colo da
    # pessoa - o oposto do objetivo do sistema.
    if not cands:
        plano, todos_ok = resolver_por_item(cur, nota, itens, tol_qt)
        achou = [p for p in plano if p["af"]]
        if not achou:
            # DUAS SITUACOES BEM DIFERENTES, e dizer "nao ha AF" nas duas era
            # mentira em uma delas:
            #   a) nao existe AF nenhuma para estes produtos -> NENHUMA, certo
            #   b) existem AFs, mas a nota traz mais do que o saldo de cada uma
            #      -> o nativo dividiria a quantidade entre elas. Nao ha o que
            #      automatizar (um item guarda um numero de AF so), mas dizer
            #      "nenhuma AF atende" manda a pessoa procurar o que ja esta na
            #      tela. Caso real: NF 51836, item de 15 kg com AF de 10 e de 15.
            com_opcao = [p for p in plano if p.get("opcoes")]
            if com_opcao:
                cabem = [p for p in com_opcao if p.get("af_sugerida")]
                extra = ("" if not cabem else
                         " Em %d item(ns) há uma AF com saldo suficiente, só que "
                         "não é a primeira da fila de entrega." % len(cabem))
                # 'opcoes' fica VAZIO de proposito. Ele alimenta o fluxo "a
                # pessoa escolhe UMA AF para a nota inteira", e aqui as opcoes
                # sao de UM item: aplicar a nota toda poria uma AF que nao
                # cobre os outros produtos. As opcoes por item vao no 'plano',
                # que e o que a tela deve mostrar.
                return {"decisao": AMBIGUA, "af": None,
                        "opcoes": [], "plano": plano,
                        "por_item": True,
                        "motivo": ("Existem AFs em aberto para os produtos desta "
                                   "nota, mas a quantidade da nota passa do saldo "
                                   "de cada uma: o Teknisa dividiria a entrega "
                                   "entre elas, e essa divisão é sua.%s" % extra)}
            return {"decisao": NENHUMA, "af": None, "opcoes": [], "plano": plano,
                    "motivo": ("Nenhuma AF em aberto deste fornecedor, na filial "
                               "%s, atende os produtos da nota no período." % filial)}
        # NAO GRAVA SOZINHO QUANDO OS ITENS VEM DE AFs DIFERENTES.
        #
        # Medido em producao, 506 itens de notas multi-AF, comparando com a
        # distribuicao real. NENHUM criterio chega a 100%:
        #    melhor dentro da tolerancia .... 86,4% (61 itens errados)
        #    so quando ha 1 candidata ....... 96,7% (cobre so 18%)
        #    quantidade exata e unica ....... 96,6%
        #    valor unitario a 0,5% .......... 95,7% (cobre 24%)
        #
        # A causa e de negocio, nao de codigo: quando o fornecedor entrega
        # contra DOIS pedidos em aberto do MESMO produto, o dado nao registra
        # de qual pedido veio cada unidade. Quem decide isso e uma pessoa.
        # Amarrar no pedido errado baixa a compra de outra area.
        #
        # Entao: a proposta vem PRONTA na tela e a pessoa confirma em 1 clique.
        # Nao e o mesmo que "trabalho manual": hoje ela procura entre todas as
        # AFs em aberto do fornecedor; aqui ela so confere o que ja veio pronto.
        afs = sorted({p["af"] for p in achou})
        # mesma trava do caminho de AF unica: nenhum numero pode ser ambiguo
        ambiguas = [a for a in afs if not af_inequivoca(cur, a, filial)]
        if ambiguas:
            return {"decisao": AMBIGUA, "af": None, "opcoes": [], "plano": plano,
                    "por_item": True,
                    "motivo": ("O número da AF %s não identifica uma única AF na "
                               "filial %s. Confira no Teknisa antes de vincular."
                               % (", ".join(ambiguas), filial))}
        if todos_ok:
            return {"decisao": AMBIGUA, "af": None, "opcoes": [], "plano": plano,
                    "por_item": True, "pronta_para_confirmar": True,
                    "motivo": ("Os itens desta nota vêm de %d AFs diferentes (%s). "
                               "Já distribuí cada item na sua AF: confirme em um "
                               "clique. Não vinculo sozinho neste caso porque, "
                               "quando o mesmo produto está em dois pedidos "
                               "abertos, só uma pessoa sabe de qual veio."
                               % (len(afs), ", ".join(afs)))}
        faltam = [p["seq"] for p in plano if not p["af"]]
        return {"decisao": AMBIGUA, "af": None, "opcoes": [], "plano": plano,
                "por_item": True,
                "motivo": ("Achei a AF de %d dos %d itens. Faltam os itens %s."
                           % (len(achou), len(plano), ", ".join(map(str, faltam))))}

    if len(cands) == 1:
        c = cands[0]
        # UMA candidata: vincula, mesmo com a quantidade fora da tolerancia.
        #
        # A tolerancia (VRPETOLEQTAF) NAO e criterio de escolha de AF. No codigo
        # nativo ela aparece em CONFERE_ERROS, no FECHAMENTO da nota, gerando
        # aviso em ITEMPRODUTO.IDAVISOSITEM - depois do vinculo, nunca antes.
        # Barrar aqui era invencao minha: mandava para a pessoa um caso em que
        # nao ha o que escolher (so existe uma AF que serve). A divergencia de
        # quantidade continua sendo tratada, mas por quem e' de direito: a
        # VERIFICA_INCONS_NF, na confirmacao.
        if not c["dentro_tolerancia"]:
            # a nota traz MAIS do que o saldo da AF: sobra quantidade sem pedido.
            # O nativo levaria o excedente para outra AF; aqui, com uma unica
            # candidata, nao ha para onde levar. E caso de gente olhar.
            return {"decisao": AMBIGUA, "af": None, "opcoes": cands,
                    "motivo": ("A nota traz %s e a AF %s só tem saldo de %s. "
                               "Sobrariam %s sem pedido. Confira antes de "
                               "vincular." % (c["qt_nota"], c["af"], c["saldo"],
                                              c["excedente"]))}
        # TRAVA FINAL: o numero tem que identificar UMA AF nessa filial, senao
        # a gravacao (que so leva o numero) fica ambigua. Ver af_inequivoca.
        if not af_inequivoca(cur, c["af"], filial):
            return {"decisao": AMBIGUA, "af": None, "opcoes": cands,
                    "motivo": ("O número da AF %s não identifica uma única AF na "
                               "filial %s. Não vou vincular pelo número, para não "
                               "correr o risco de baixar o pedido errado."
                               % (c["af"], filial))}
        return {"decisao": AUTOMATICA, "af": c, "opcoes": cands,
                "motivo": ("Só existe uma AF possível (%s): mesmo fornecedor, "
                           "filial %s, cobre todos os produtos, está em aberto e "
                           "tem saldo (a nota usa %s de %s)."
                           % (c["af"], filial, c["qt_nota"], c["saldo"]))}

    return {"decisao": AMBIGUA, "af": None, "opcoes": cands,
            "motivo": ("Existem %d AFs possíveis para esta nota. A escolha é sua: "
                       "a lista está ordenada pela que mais se aproxima da "
                       "quantidade da nota." % len(cands))}


def af_ainda_valida(cur, chave):
    """As AFs JA gravadas nesta nota continuam em aberto e com saldo?

    A AF GRAVADA E PERECIVEL. Entre gravar e importar, outra entrega pode
    consumir a mesma AF. Se a nota for importada depois disso, ela baixa um
    pedido que ja foi atendido - exatamente o erro que nao pode acontecer.

    Nao e hipotese: medido em producao (06/08/2026) ha 11.034 notas com AF
    gravada e nao importada, 10.430 delas ha mais de 90 dias. Numa amostra de
    120, 58 (quase metade) ja estavam com a AF fechada ou sem saldo.

    Devolve (ok, lista_de_afs_mortas). Fail-safe: erro ao conferir responde
    ok=False, porque importar sem saber e pior que nao importar."""
    try:
        cur.execute("""SELECT DISTINCT NRAUTOFORN FROM TEKNISA.INTG_ITEMPRODUTO
                       WHERE NRACESSONFE = :c AND NRAUTOFORN IS NOT NULL""", c=chave)
        afs = [r[0] for r in cur.fetchall()]
    except Exception:
        return False, []
    if not afs:
        return True, []          # sem AF gravada: nao ha o que envelhecer
    mortas = []
    for af in afs:
        try:
            cur.execute("""
                SELECT COUNT(*)
                FROM   TEKNISA.AUTOFORNE A
                JOIN   TEKNISA.ITEMAUTF  I
                       ON  I.CDFILDEST = A.CDFILDEST
                       AND I.CDFILLANC = A.CDFILLANC
                       AND I.NRAUTOFORN = A.NRAUTOFORN
                WHERE  A.NRAUTOFORN = :a
                  AND  (A.IDBAIXAAF = '1' OR A.IDBAIXAAF IS NULL)
                  AND  NVL(A.IDAFEXPIRADA,'N') = 'N'
                  AND  (NVL(I.QTPROGENTAF,0) - NVL(I.QTEFETENTRAF,0)) > 0
            """, a=af)
            r = cur.fetchone()
            if not r or int(r[0] or 0) == 0:
                mortas.append(af)
        except Exception:
            return False, []
    return (not mortas), mortas


def quem_baixou_af(cur, nr_af):
    """Qual NOTA consumiu esta AF. Devolve (numero_nf, data) ou (None, None).

    POR QUE ISTO EXISTE: "AF vencida" nao e resposta acionavel. Medido em
    producao (14/08/2026), sobre as notas paradas cuja AF esta sem saldo:
    em 100% dos casos OUTRA NOTA ja lancada consumiu o pedido, e quase sempre
    do mesmo fornecedor, com um ou dois dias de diferenca. Caso real: a AF
    0001477220 tem TRES notas esperando por ela (006148804, 006153054 e
    006153055) e quem levou foi a 006153086, lancada em 13/08.

    Ou seja: nao e a AF que envelheceu, sao varias notas apontando o mesmo
    pedido. Dizer QUEM levou transforma um bloqueio em um caso com dono.

    CUIDADO NO FILTRO: NFRELACAF.CDFILIAL e a filial do LANCAMENTO, que nao e
    a CDFILIRECE da nota. Filtrar por ela devolve vazio e da a impressao de
    que ninguem baixou - foi exatamente o erro que me fez concluir errado.

    Fail-safe: qualquer erro devolve (None, None). E informacao extra para a
    mensagem, nunca base de decisao.
    """
    try:
        cur.execute("""
            SELECT N.NRNOTAFISC, TO_CHAR(N.DTLANCAMENNF,'DD/MM/YYYY')
            FROM   TEKNISA.NFRELACAF R
            JOIN   TEKNISA.NOTAFISCAL N
                   ON N.CDFILIAL = R.CDFILIAL AND N.NRLANCTONF = R.NRLANCTONF
            WHERE  R.NRAUTOFORN = :a
            ORDER  BY N.DTLANCAMENNF DESC NULLS LAST
            FETCH  FIRST 1 ROWS ONLY
        """, a=nr_af)
        r = cur.fetchone()
        return (r[0], r[1]) if r else (None, None)
    except Exception:
        return None, None


def gravar_af(cur, chave, por_item):
    """Grava a AF nos itens da nota, de forma ATOMICA.

    A CORRIDA QUE ISTO FECHA. Entre o momento em que o motor le a nota (sem AF)
    e o momento em que grava, uma PESSOA pode relacionar outra AF pela tela do
    Teknisa. Como o UPDATE so preenche o que esta nulo, a nota terminaria com
    DUAS AFs - dois pedidos baixados pela mesma nota. A trava contra rodada
    dupla do robo (_tentar_lock) nao cobre isso, porque o concorrente aqui e
    uma pessoa em outro sistema.

    Solucao: SELECT ... FOR UPDATE antes de decidir. O banco segura as linhas,
    entao ou a pessoa espera a gente terminar, ou a gente le o que ela ja
    gravou e desiste. Nao existe janela entre conferir e escrever.

    'por_item' e a lista [{seq, af}]. Devolve (gravados, motivo_da_recusa).
    Nao faz commit: quem chama decide, e assim o rollback tambem e de quem chama.
    """
    alvo = {str(p["seq"]): p["af"] for p in por_item if p.get("af")}
    if not alvo:
        return 0, "nenhum item para vincular"

    # Trava as linhas desta nota ate o fim da transacao.
    #
    # WAIT 3 e ESSENCIAL, nao detalhe. Um FOR UPDATE puro espera INDEFINIDAMENTE
    # pela liberacao: se alguem estiver com a nota aberta no Teknisa, o robo
    # congelaria ali para sempre, e a rodada inteira morreria junto - inclusive
    # o teto diario e o contador de erros, que dependem dela terminar.
    #
    # Com WAIT 3, se em 3 segundos a linha nao liberar, o Oracle devolve
    # ORA-30006 e a gente entende o recado: TEM GENTE MEXENDO NESTA NOTA. Nao e
    # erro, e a resposta certa - a pessoa tem prioridade, e a nota volta na
    # proxima rodada.
    try:
        cur.execute("""SELECT NRSEQITPROD, NRAUTOFORN
                       FROM   TEKNISA.INTG_ITEMPRODUTO
                       WHERE  NRACESSONFE = :c
                       FOR UPDATE WAIT 3""", c=chave)
    except Exception as e:
        txt = str(e)
        if "ORA-30006" in txt or "ORA-00054" in txt:
            return 0, ("outra pessoa está com esta nota aberta no Teknisa neste "
                       "momento. Nada foi alterado; tente de novo em seguida.")
        raise
    atual = {str(s): a for (s, a) in cur.fetchall()}

    # confere DEPOIS de travar: alguem pode ter escrito no meio do caminho
    ja = {s: a for s, a in atual.items() if a}
    if ja:
        # so segue se o que ja esta gravado for exatamente o que iamos gravar
        divergentes = {s: a for s, a in ja.items()
                       if alvo.get(s) and alvo[s] != a}
        if divergentes:
            return 0, ("outra AF foi relacionada nesta nota enquanto o sistema "
                       "decidia (item %s está com a AF %s). Nada foi alterado."
                       % (list(divergentes)[0], list(divergentes.values())[0]))
        # itens que ja tem AF e nao estavam no plano: nota virou parcial no meio
        fora = [s for s in ja if s not in alvo]
        if fora:
            return 0, ("a nota foi parcialmente vinculada por outra pessoa "
                       "enquanto o sistema decidia. Nada foi alterado.")

    gravados = 0
    for seq, af in alvo.items():
        if atual.get(seq):        # ja esta com a AF certa: nao reescreve
            continue
        cur.execute("""UPDATE TEKNISA.INTG_ITEMPRODUTO
                          SET NRAUTOFORN = :af
                        WHERE NRACESSONFE = :c AND NRSEQITPROD = :s
                          AND NRAUTOFORN IS NULL""",
                    af=af, c=chave, s=seq)
        gravados += cur.rowcount
    return gravados, None


def resolver(cur, chave):
    """Ponta a ponta: da chave da NF-e ate a decisao da AF.
    Usada pelo painel e pelo robo, para os dois decidirem igual."""
    cur.execute("""
        SELECT CDFORNECED, CDFILIRECE, DTEMISSAO, NRNOTAFISC,
               NVL(DSTAGORIGFILI,'(auto)'), DTUTILIZAXML
        FROM   TEKNISA.INTG_NOTAFISCAL
        WHERE  NRACESSONFE = :c
        FETCH FIRST 1 ROWS ONLY
    """, c=chave)
    r = cur.fetchone()
    if not r:
        return {"decisao": NENHUMA, "af": None, "opcoes": [],
                "motivo": "Nota não encontrada na integração."}
    nota = {"CDFORNECED": r[0], "CDFILIRECE": r[1], "DTEMISSAO": r[2],
            "NRNOTAFISC": r[3], "ORIGEM": r[4], "DTUTILIZAXML": r[5]}

    if nota["DTUTILIZAXML"] is not None:
        return {"decisao": NENHUMA, "af": None, "opcoes": [], "nota": nota,
                "motivo": "Esta nota já foi importada."}

    itens = itens_do_xml(cur, chave)
    # NOTA JA (PARCIALMENTE) VINCULADA.
    #
    # Achado no teste controlado em homologacao (NF 43263): existe nota com
    # PARTE dos itens ja apontando uma AF e parte vazia. Como o UPDATE so
    # preenche o que esta nulo, escolher uma AF nova aqui deixaria a nota com
    # DUAS AFs diferentes - dois pedidos distintos baixados pela mesma nota.
    #
    # Por isso: se QUALQUER item ja tem AF, o motor nao escolhe. Nota completa
    # so precisa importar; nota parcial e anomalia e vai para uma pessoa olhar.
    ja = [i for i in itens if i.get("af_atual")]
    if ja:
        afs = {i["af_atual"] for i in ja}
        # NOTA COMPLETA: TODO item ja aponta uma AF. Nao ha o que decidir aqui -
        # a escolha ja foi feita, so falta importar.
        #
        # ATENCAO AO NUMERO DE AFs: pode ser mais de uma, e isso e NORMAL. Uma
        # entrega cumpre dois pedidos (24.125 notas em producao sao assim) e a
        # amarracao nativa, NFRELACAF, e item a item justamente por causa disso.
        #
        # O codigo exigia len(afs)==1 aqui. Consequencia medida em producao
        # (06/08/2026): 15 notas de 45 dias, com TODOS os itens vinculados,
        # caiam no ramo de baixo e recebiam a mensagem "esta nota esta
        # PARCIALMENTE vinculada: 2 de 2 itens..." - texto que se contradiz. O
        # robo lia isso como duvida e nao importava nota nenhuma delas, embora
        # nao houvesse duvida alguma: uma pessoa ja tinha escolhido cada AF.
        # A validade das AFs continua conferida depois, por af_ainda_valida,
        # que ja trata varias AFs.
        if len(ja) == len(itens):
            return {"decisao": NENHUMA, "af": None, "opcoes": [], "nota": nota,
                    "itens": itens,
                    "motivo": ("A AF %s já está relacionada nesta nota; falta "
                               "apenas importar." % ", ".join(sorted(afs)))}
        # NOTA PARCIALMENTE VINCULADA: completa com a MESMA AF, se ela servir.
        #
        # Medido em producao: 40% das notas paradas recentes estao assim (parte
        # dos itens com AF, parte sem). Recusar todas era jogar fora o maior
        # bloco de trabalho automatizavel.
        #
        # O que NAO se pode fazer e escolher uma AF DIFERENTE para o resto: a
        # nota ficaria com duas AFs, baixando dois pedidos (caso real: NF 43263).
        # Entao a regra e: havendo UMA unica AF ja presente, e ela cobrindo os
        # produtos que faltam, completa com ela. Qualquer outra situacao para.
        itens = traduzir_para_interno(cur, nota["CDFORNECED"], itens)
        faltam = [i for i in itens if not i.get("af_atual")]
        if len(afs) == 1 and faltam:
            af_unica = list(afs)[0]
            est_faltam = {i["cd_estoque"] for i in faltam if i.get("cd_estoque")}
            # so segue se TODOS os itens que faltam tem produto resolvido
            if est_faltam and all(i.get("cd_estoque") for i in faltam):
                # A CONFERENCIA PRECISA SER NA AF CERTA E VIVA.
                #
                # Faltavam duas coisas aqui. Primeira, a filial: NRAUTOFORN
                # sozinho nao identifica a AF (a PK e CDFILDEST + CDFILLANC +
                # numero), entao a contagem podia somar itens de uma AF homonima
                # de OUTRA filial e concluir que a cobertura existe. Segunda, a
                # situacao: a AF ja gravada nos outros itens pode ter sido
                # baixada ou expirado desde entao - completar a nota com ela
                # seria escrever um pedido morto, que e o erro que este projeto
                # existe para evitar. Agora vale o mesmo predicado nativo usado
                # para escolher AF nova.
                cur.execute("""
                    SELECT COUNT(DISTINCT NVL(P.CDPRODESTO, P.CDPRODUTO))
                    FROM   TEKNISA.AUTOFORNE A
                    JOIN   TEKNISA.ITEMAUTF I
                           ON  I.CDFILDEST  = A.CDFILDEST
                           AND I.CDFILLANC  = A.CDFILLANC
                           AND I.NRAUTOFORN = A.NRAUTOFORN
                    JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = I.CDPRODUTO
                    WHERE  A.NRAUTOFORN = :a
                      AND  A.CDFILDEST  = :fl
                      AND  NVL(A.IDATIVO,'S') <> 'N'
                      """ + _SQL_AF_CANDIDATA + """
                      AND  NVL(P.CDPRODESTO, P.CDPRODUTO) IN (
                           SELECT COLUMN_VALUE FROM TABLE(
                             SYS.ODCIVARCHAR2LIST(""" +
                    ",".join("'%s'" % e for e in sorted(est_faltam)) + """)))
                """, a=af_unica, fl=nota["CDFILIRECE"])
                r = cur.fetchone()
                if r and int(r[0] or 0) == len(est_faltam) \
                        and af_inequivoca(cur, af_unica, nota["CDFILIRECE"]):
                    plano = [{"seq": i["seq"], "produto": i.get("cd_interno"),
                              "desc": i.get("desc_xml"), "qt": i["qt"],
                              "af": af_unica, "opcoes": []} for i in faltam]
                    return {"decisao": AUTOMATICA, "af": None, "opcoes": [],
                            "plano": plano, "por_item": True, "nota": nota,
                            "itens": itens,
                            "motivo": ("A nota já tem a AF %s em %d item(ns) e "
                                       "essa mesma AF cobre os %d que faltam. "
                                       "Completo com ela, sem inventar outra."
                                       % (af_unica, len(ja), len(faltam)))}
        return {"decisao": NENHUMA, "af": None, "opcoes": [], "nota": nota,
                "itens": itens,
                "motivo": ("Esta nota está parcialmente vinculada: %d de %d "
                           "itens já apontam a AF %s, e essa AF não cobre os "
                           "itens que faltam. Não vou vincular outra AF, senão a "
                           "nota ficaria com duas. Confira no Teknisa."
                           % (len(ja), len(itens), ", ".join(sorted(afs))))}

    itens = traduzir_para_interno(cur, nota["CDFORNECED"], itens)
    out = escolher_af(cur, nota, itens)
    out["nota"] = nota
    out["itens"] = itens
    return out
