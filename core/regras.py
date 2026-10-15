# -*- coding: utf-8 -*-
r"""core/regras.py - O QUE A PESSOA ENSINOU AO ROBO.

Ate aqui a pessoa corrigia o RESULTADO (dizia qual era o produto certo, e o
De/Para virava permanente). Nao corrigia o RACIOCINIO: se o robo lia mal a
descricao do fornecedor, continuava lendo mal na proxima nota.

Aqui ela ensina a LEITURA, em tres pecas que da para TESTAR:

  SINONIMO   "quando aparecer PCT, leia PACOTE"
  NEGATIVA   "para este fornecedor, nunca ligue este item a este produto"
  LIMIAR     "para este fornecedor, exija mais para gravar sozinho"

REGRA LIVRE EM TEXTO nao e um quarto tipo. Ela e interpretada, vira uma das
tres, e a pessoa confirma o que o sistema entendeu antes de valer.

COMO ISTO ENTRA NO MATCHER, SEM MEXER NELE
------------------------------------------
Nao alteramos as funcoes de comparacao. As regras agem NAS BORDAS:
  1. o texto passa por `aplicar_sinonimos()` ANTES de comparar;
  2. a lista de candidatos passa por `filtrar_proibidos()` ANTES de comparar;
  3. o piso vai como parametro para `pode_auto()`.
Assim a regra do matcher continua uma so, e uma regra ensinada nunca pode
mudar o significado de outra coisa por acidente.

NADA VALE SEM TESTE
-------------------
Toda regra nasce RASCUNHO. `testar()` roda a regra contra os vinculos que JA
existem e estao certos e responde quantos acertos ela ganha e quantos quebra.
Regra que quebra vinculo certo nao deve ser adotada. E o mesmo metodo que o
projeto usa desde o inicio: medir antes de mudar (achado 41).

FAIL-SAFE: qualquer erro ao ler regra devolve "nenhuma regra". O robo volta ao
comportamento padrao, que e o testado. Nunca o contrario.
"""
import re
import time

from core.ai_matcher import (LIMIAR_AUTO, _UNIDADES, _UN_SINONIMO, _core,
                             escolher, normaliza_un, pode_auto)

_CACHE = {"quando": 0, "regras": []}
_TTL = 60          # segundos; a tela mexe pouco e o robo le a cada item

# A LISTA DE CANDIDATOS DE UM FORNECEDOR e cara: medido, 102 s para um
# fornecedor com 1.467 itens. Quem esta ajustando uma regra testa varias vezes
# seguidas, entao a segunda em diante sai na hora. Cache SO do teste - o
# caminho do robo continua lendo direto, sem cache, para nunca decidir com
# lista velha.
_CANDS = {}
_CANDS_TTL = 300


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def carregar(cur, forcar=False):
    """As regras ATIVAS, do banco. Cache curto porque o robo le a cada item.

    Fail-safe: erro devolve lista vazia, ou seja, comportamento padrao."""
    agora = time.time()
    if not forcar and (agora - _CACHE["quando"]) < _TTL:
        return _CACHE["regras"]
    try:
        cur.execute("""SELECT TIPO, ESCOPO, CDFORNECED, TERMO, SIGNIFICA,
                              CDPRODUTOXML, CDPRODUTO, VALOR, ID
                       FROM   AIHUB.AIHUB_REGRA WHERE STATUS = 'ATIVA'""")
        regras = [{"tipo": r[0], "escopo": r[1], "forn": r[2], "termo": r[3],
                   "significa": r[4], "cprod_xml": r[5], "cd_produto": r[6],
                   "valor": (float(r[7]) if r[7] is not None else None),
                   "id": r[8]} for r in cur.fetchall()]
    except Exception:
        return []          # sem tabela ou sem grant: o robo segue como sempre
    _CACHE["quando"] = agora
    _CACHE["regras"] = regras
    return regras


def esquecer():
    """Zera o cache. Chamado depois de gravar ou desativar uma regra."""
    _CACHE["quando"] = 0
    _CACHE["regras"] = []


def _valem(regras, cd_forn, tipo):
    """As regras daquele tipo que valem para este fornecedor: as globais mais
    as dele. Regra do fornecedor vem depois, entao ela ganha em caso de
    conflito - o especifico manda no geral."""
    globais = [r for r in regras if r["tipo"] == tipo and r["escopo"] == "GLOBAL"]
    do_forn = [r for r in regras if r["tipo"] == tipo
               and r["escopo"] == "FORNECEDOR" and r["forn"] == cd_forn]
    return globais + do_forn


# --------------------------------------------------------------------------- #
# Aplicacao - as tres bordas
# --------------------------------------------------------------------------- #
def aplicar_sinonimos(texto, regras, cd_forn):
    """Troca os termos ensinados ANTES da comparacao.

    Troca PALAVRA INTEIRA, nunca pedaco: sem isso ensinar 'PC -> PACOTE'
    transformaria 'PCT' em 'PACOTET' e quebraria o que ja funcionava."""
    t = (texto or "")
    for r in _valem(regras, cd_forn, "SINONIMO"):
        termo = (r["termo"] or "").strip()
        if not termo:
            continue
        t = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(termo),
                   r["significa"] or "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def filtrar_proibidos(cands, regras, cd_forn, cprod_xml):
    """Tira da lista os produtos proibidos para este item deste fornecedor."""
    proibidos = {r["cd_produto"] for r in _valem(regras, cd_forn, "NEGATIVA")
                 if str(r["cprod_xml"] or "") == str(cprod_xml or "")}
    if not proibidos:
        return cands
    return [c for c in cands if c[0] not in proibidos]


def limiar_de(regras, cd_forn):
    """O piso para gravar sozinho. Sem regra, o padrao medido do projeto."""
    v = [r["valor"] for r in _valem(regras, cd_forn, "LIMIAR")
         if r["valor"] is not None]
    return v[-1] if v else LIMIAR_AUTO


# --------------------------------------------------------------------------- #
# UNIDADE DE MEDIDA NAO E NOME DE PRODUTO
# --------------------------------------------------------------------------- #
# Achado por quem opera, olhando um exemplo de teste: ensinar "KG significa
# BANANA" trocaria a UNIDADE por um NOME DE PRODUTO em toda descricao daquele
# fornecedor. "PERA ARGENTINA KG" viraria "PERA ARGENTINA BANANA", e a
# comparacao passaria a achar banana onde ha pera.
#
# O teste historico pegava isso depois (media 2 vinculos quebrados), mas
# depender do teste aqui e errado por dois motivos: o teste roda numa AMOSTRA,
# entao pode nao pegar; e a pessoa perderia minutos para receber um "nao" que
# o sistema podia dar na hora.
#
# A regra: unidade so pode virar unidade. "CX = CAIXA" e legitimo e util (as
# duas sao a mesma unidade escrita de dois jeitos). "KG = BANANA" nao e.
# Quem decide o que e unidade e normaliza_un(), a mesma funcao que o matcher
# usa para comparar unidade - nao uma lista paralela que poderia divergir.
_CANONICAS = set(_UN_SINONIMO.values()) | set(_UN_SINONIMO) | set(_UNIDADES)


def e_unidade(palavra):
    """A palavra e uma unidade de medida ou embalagem?"""
    p = (palavra or "").strip().upper()
    return bool(p) and (p in _CANONICAS or normaliza_un(p) in _CANONICAS)


def conflito_unidade(termo, significa):
    """Devolve a explicacao do problema, ou None quando a regra e aceitavel."""
    t_un, s_un = e_unidade(termo), e_unidade(significa)
    if t_un and not s_un:
        return ("<b>%s</b> é uma unidade de medida, e <b>%s</b> é nome de "
                "produto. Trocar uma pela outra estragaria a descrição inteira: "
                "\"PERA ARGENTINA KG\" viraria \"PERA ARGENTINA %s\", e o robô "
                "passaria a achar %s onde há pera. Unidade só pode virar "
                "unidade (como CX para CAIXA)."
                % (termo.upper(), significa.upper(), significa.upper(),
                   significa.upper()))
    if s_un and not t_un:
        return ("<b>%s</b> é nome de produto e <b>%s</b> é uma unidade de "
                "medida. Isso transformaria o nome do produto em unidade e o "
                "robô perderia a palavra que identifica o item."
                % (termo.upper(), significa.upper()))
    return None


# --------------------------------------------------------------------------- #
# Interpretacao do texto livre
# --------------------------------------------------------------------------- #
# PADROES ANCORADOS E ESPECIFICOS, nesta ordem.
#
# A primeira versao era frouxa e INVENTAVA regra: "faz o que achar melhor ai"
# virava um sinonimo de "QU" para "ACHAR MELHOR AI". Texto que nao e um pedido
# claro tem de voltar como "nao entendi" - inventar regra a partir de conversa
# solta e pior que nao entender, porque a pessoa confirma no automatico.
#
# Por isso: cada forma e escrita inteira, com ^ e $, e as que tem palavra-chave
# propria (nunca / grave sozinho) vem ANTES da forma generica de sinonimo.
_TERMO = r"[A-Za-z0-9]{1,20}"
_SIGN = r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 ]{1,39}"

_PADROES = [
    # --- LIMIAR: tem palavra propria, entao vem primeiro ------------------
    (r"^(?:so|só|somente|apenas)\s+(?:grave|grava|vincule|vincula)\s+"
     r"(?:sozinho\s+)?(?:com|a partir de|acima de)?\s*(\d{2,3})\s*%?\.?$", "LIMIAR"),
    (r"^(?:exija|exigir|exige|peça|peca)\s+(\d{2,3})\s*%\s*"
     r"(?:para\s+(?:gravar|vincular)(?:\s+sozinho)?)?\.?$", "LIMIAR"),

    # --- NEGATIVA: idem ---------------------------------------------------
    (r"^(?:nunca|jamais|não|nao)\s+(?:vincule|vincular|ligue|ligar|use|usar)\s+"
     r"(?:o\s+)?(?:item|codigo|código|produto do fornecedor)\s+"
     r"[\"']?([A-Za-z0-9._/-]{1,40})[\"']?\s+"
     r"(?:no|ao|com|em|para|no produto|ao produto)\s+"
     r"(?:o\s+)?(?:produto\s+)?[\"']?(\d{6,10})[\"']?\.?$", "NEGATIVA"),

    # --- SINONIMO: as formas exatas, uma a uma ---------------------------
    (r"^(%s)\s*=\s*(%s)\.?$" % (_TERMO, _SIGN), "SINONIMO"),
    (r"^(%s)\s+(?:significa|quer dizer|é|eh|e)\s+(%s)\.?$" % (_TERMO, _SIGN), "SINONIMO"),
    (r"^(%s)\s+é\s+o\s+mesmo\s+que\s+(%s)\.?$" % (_TERMO, _SIGN), "SINONIMO"),
    (r"^(?:quando\s+)?(?:aparecer|vier|chegar|escrever)\s+(%s)\s*,?\s*"
     r"(?:leia|entenda|considere|use|trate)\s+(?:como\s+)?(%s)\.?$"
     % (_TERMO, _SIGN), "SINONIMO"),
    (r"^(?:leia|entenda|considere|trate)\s+(%s)\s+como\s+(%s)\.?$"
     % (_TERMO, _SIGN), "SINONIMO"),
]


def interpretar(texto):
    """Le o pedido em portugues e devolve a REGRA ESTRUTURADA que entendeu.

    Devolve sempre um dicionario com 'entendi' (frase em portugues, para a
    pessoa confirmar) e, quando conseguiu, os campos da regra. Quando NAO
    conseguiu, diz o que faltou em vez de inventar - texto que nao vira regra
    testavel nao entra no sistema.

    E deliberadamente conservador: prefere dizer "nao entendi" a criar uma
    regra que a pessoa nao pediu.
    """
    t = re.sub(r"\s+", " ", (texto or "").strip())
    if not t:
        return {"ok": False, "entendi": "Você não escreveu nada."}

    for padrao, tipo in _PADROES:
        m = re.search(padrao, t, flags=re.I)
        if not m:
            continue
        if tipo == "SINONIMO":
            de, para = m.group(1).upper(), m.group(2).upper().strip()
            if de == para:
                return {"ok": False,
                        "entendi": "O termo e o significado são iguais (%s)." % de}
            problema = conflito_unidade(de, para)
            if problema:
                return {"ok": False, "entendi": problema}
            return {"ok": True, "tipo": "SINONIMO", "termo": de, "significa": para,
                    "entendi": ("Sempre que aparecer <b>%s</b> na descrição do "
                                "fornecedor, o robô vai ler como <b>%s</b>."
                                % (de, para))}
        if tipo == "NEGATIVA":
            item, prod = m.group(1), m.group(2)
            return {"ok": True, "tipo": "NEGATIVA", "cprod_xml": item,
                    "cd_produto": prod,
                    "entendi": ("O robô nunca vai vincular o item <b>%s</b> do "
                                "fornecedor ao produto <b>%s</b>." % (item, prod))}
        if tipo == "LIMIAR":
            pct = int(m.group(1))
            if not 50 <= pct <= 100:
                return {"ok": False,
                        "entendi": ("%d%% está fora do razoável. Use algo entre "
                                    "50%% e 100%%." % pct)}
            return {"ok": True, "tipo": "LIMIAR", "valor": round(pct / 100.0, 4),
                    "entendi": ("O robô só vai gravar sozinho quando a descrição "
                                "bater em <b>%d%%</b> ou mais (o padrão é %d%%)."
                                % (pct, round(LIMIAR_AUTO * 100)))}

    return {"ok": False,
            "entendi": ("Não consegui transformar isso em uma regra que dê para "
                        "testar. Tente uma destas formas: "
                        "\"quando aparecer PCT, leia PACOTE\" · "
                        "\"nunca vincule o item 3062 ao produto 4033002200\" · "
                        "\"só grave sozinho com 95%\".")}


# --------------------------------------------------------------------------- #
# O TESTE HISTORICO - o que separa ensinar de chutar
# --------------------------------------------------------------------------- #
_SQL_VERDADE = """
    SELECT D.CDFORNECED, D.CDPRODUTOXML, D.NMPRODUTOXML, D.CDPRODUTO
    FROM   TEKNISA.PRODFORNXML D
    WHERE  D.IDATIVO = 'S'
      AND  D.NMPRODUTOXML IS NOT NULL
      %s
"""


def testar(cur, regra, limite=None, max_fornecedores=None):
    """Roda a regra contra os vinculos que JA existem e estao CERTOS.

    O gabarito sao os proprios De/Para ativos: para aquele fornecedor e aquele
    item, o produto correto ja e conhecido. Entao da para perguntar, item a
    item: com a regra, o robo continuaria acertando?

    ESCOPO ESTREITO DE PROPOSITO. Testar 85 mil vinculos por regra seria caro e
    inutil: uma regra "PCT -> PACOTE" so pode mudar o resultado de itens cuja
    descricao contem PCT. Entao o teste roda so nos AFETADOS, e informa quantos
    foram.

    Devolve: testados, ganhou, quebrou e exemplos dos dois lados.
    """
    _t0 = time.time()
    tipo = regra.get("tipo")
    forn = regra.get("forn") if regra.get("escopo") == "FORNECEDOR" else None
    # A AMOSTRA DEPENDE DO ALCANCE, e isso nao e detalhe.
    # Regra de fornecedor afeta so os vinculos dele, entao uma amostra pequena
    # ja cobre quase tudo e o teste sai em segundos. Regra GLOBAL muda o
    # comportamento para todo mundo, e amostra pequena PERDE o estrago: medido,
    # com 41 vinculos a regra absurda "KG -> BANANA" passou como se nao
    # quebrasse nada, e com 250 ela mostrou 12 vinculos quebrados. Mudar o
    # sistema inteiro custa a espera.
    if limite is None:
        limite = 120 if forn else 300
    if max_fornecedores is None:
        max_fornecedores = 1 if forn else 30
    binds = {}
    onde = ""
    if forn:
        onde += " AND D.CDFORNECED = :f"
        binds["f"] = forn
    if tipo == "SINONIMO":
        onde += " AND UPPER(D.NMPRODUTOXML) LIKE :t"
        binds["t"] = "%" + (regra.get("termo") or "").upper() + "%"
    elif tipo == "NEGATIVA":
        onde += " AND D.CDPRODUTOXML = :cx"
        binds["cx"] = regra.get("cprod_xml")

    # AGRUPADO POR FORNECEDOR de proposito: o custo do teste e buscar a lista
    # de candidatos de cada fornecedor (~440 ms cada). Sem agrupar, uma amostra
    # de 250 vinculos toca ~100 fornecedores e o teste leva mais de 2 minutos -
    # medido. Agrupando e limitando os fornecedores, cai para segundos, e a
    # amostra continua honesta porque a tela informa quantos foram testados.
    # quantos vinculos a regra afeta NO TOTAL. Sem este numero a tela diria
    # "conferi 41" sem dizer 41 de quantos, e 41 de 45 e muito diferente de
    # 41 de 3.800.
    try:
        cur.execute("SELECT COUNT(*) FROM (%s)" % (_SQL_VERDADE % onde), binds)
        afetados = int(cur.fetchone()[0] or 0)
    except Exception:
        afetados = None
    cur.execute((_SQL_VERDADE % onde) + " ORDER BY D.CDFORNECED", binds)
    verdade, vistos = [], []
    for linha in cur:
        f = linha[0]
        if f not in vistos:
            if len(vistos) >= max_fornecedores:
                continue
            vistos.append(f)
        verdade.append(linha)
        if len(verdade) >= limite:
            break
    if not verdade:
        return {"testados": 0, "ganhou": 0, "quebrou": 0, "exemplos": [],
                "resumo": ("Nenhum vínculo existente é afetado por esta regra. "
                           "Ela não muda nada do que já está gravado.")}

    # candidatos por fornecedor, uma vez so
    from core.ai_matcher import candidatos
    por_forn = {}

    def _cands_de(cd_forn):
        hit = _CANDS.get(cd_forn)
        if hit and (time.time() - hit[0]) < _CANDS_TTL:
            return hit[1]
        try:
            lista = candidatos(cur, cd_forn)
        except Exception:
            lista = []
        _CANDS[cd_forn] = (time.time(), lista)
        return lista
    ganhou = quebrou = 0
    exemplos = []
    for cd_forn, cprod, desc, certo in verdade:
        if cd_forn not in por_forn:
            por_forn[cd_forn] = _cands_de(cd_forn)
        cands = por_forn[cd_forn] or []
        if not cands:
            continue
        desc = str(desc or "")
        un_de = {c[0]: (c[2] if len(c) > 2 else None) for c in cands}

        antes = _decide(desc, cands, un_de, LIMIAR_AUTO)
        # com a regra
        d2, c2, lim = desc, cands, LIMIAR_AUTO
        if tipo == "SINONIMO":
            d2 = aplicar_sinonimos(desc, [regra], cd_forn)
        elif tipo == "NEGATIVA":
            c2 = filtrar_proibidos(cands, [regra], cd_forn, cprod)
        elif tipo == "LIMIAR":
            lim = regra.get("valor") or LIMIAR_AUTO
        depois = _decide(d2, c2, un_de, lim)

        if antes == depois:
            continue
        acertou_antes = (antes == certo)
        acertou_depois = (depois == certo)
        if acertou_depois and not acertou_antes:
            ganhou += 1
            if len(exemplos) < 8:
                exemplos.append({"lado": "ganhou", "desc": desc[:60],
                                 "antes": antes or "(nenhum)", "depois": depois,
                                 "certo": certo})
        elif acertou_antes and not acertou_depois:
            quebrou += 1
            if len(exemplos) < 8:
                exemplos.insert(0, {"lado": "quebrou", "desc": desc[:60],
                                    "antes": antes, "depois": depois or "(nenhum)",
                                    "certo": certo})

    # A COBERTURA ENTRA NO TEXTO. "Não quebrou nada" em 41 de 3.800 não é a
    # mesma afirmação que em 41 de 45, e quem lê precisa saber a diferença.
    if afetados and afetados > len(verdade):
        cobertura = (" Conferido em %d de %d vínculo(s) afetado(s)."
                     % (len(verdade), afetados))
    else:
        cobertura = " Conferido em todos os %d vínculo(s) afetados." % len(verdade)

    if quebrou:
        resumo = ("Esta regra QUEBRA %d vínculo(s) que hoje estão certos e "
                  "ganha %d. Não recomendo adotar.%s"
                  % (quebrou, ganhou, cobertura))
    elif ganhou:
        resumo = ("Esta regra ganha %d vínculo(s) e não quebra nenhum.%s"
                  % (ganhou, cobertura))
    else:
        resumo = ("Esta regra não muda nenhum vínculo existente. É segura, mas "
                  "também não melhora o que já está gravado.%s" % cobertura)
    return {"testados": len(verdade), "ganhou": ganhou, "quebrou": quebrou,
            "fornecedores": len(vistos), "afetados": afetados,
            "segundos": round(time.time() - _t0), "exemplos": exemplos,
            "resumo": resumo}


def _decide(desc, cands, un_de, limiar):
    """O que o robo gravaria sozinho, ou None. Usa as funcoes do matcher."""
    if not cands:
        return None
    melhor, _ = escolher(desc, cands, None)
    if not melhor:
        return None
    if not pode_auto(desc, melhor, None, un_de.get(melhor[0]), cands):
        return None
    if melhor[2] < limiar:
        return None
    return melhor[0]
