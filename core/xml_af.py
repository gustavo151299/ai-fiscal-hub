# -*- coding: utf-8 -*-
"""LEITURA DO XML: achar o numero da AF onde quer que o fornecedor tenha posto.

POR QUE ISTO EXISTE
-------------------
O motor de AF (core\\af_matcher.py) escolhe a AF por deducao: fornecedor +
filial + produto + janela de datas + saldo. Funciona, mas depende do De\\Para do
produto estar pronto - e esse e o gargalo medido (achado 40).

Quando o fornecedor ESCREVE o numero da AF no XML, nada disso e preciso: o
numero esta la. So que ele escreve em qualquer lugar e em qualquer formato.
Tres XMLs reais de 23\\06\\2026, tres formatos diferentes:

    NF 62104 (AKS)   <infCpl>...^^AF - 1445341^HC ^DATA - 23-06^^</infCpl>
    NF 69757 (P.A.S) <xPed>1444896</xPed>          (repetido nos 14 itens)
    NF   839 (FORNECEDOR J)  <infCpl>...CATARINENSE PHARMA. AF.1445241.</infCpl>

Nos tres, a AF real tinha 10 digitos com zeros a esquerda (0001445341,
0001444896, 0001445241) e o fornecedor escreveu 7. Normalizar e obrigatorio.

O QUE NAO SE PODE FAZER: CONFIAR NO TEXTO
-----------------------------------------
O mesmo XML da P.A.S traz, no infAdFisco:

    Ordem Compra: 14448961444896 ... Ped: 63197

O primeiro e a AF COLADA DUAS VEZES (1444896 + 1444896); o segundo e o pedido
interno do fornecedor, que NAO e a nossa AF. Pegar "o numero perto da palavra
pedido" amarraria a nota na AF errada - e amarrar errado baixa pedido de outra
entrega, mexendo em estoque.

Por isso este modulo NUNCA devolve um numero so porque ele apareceu. Todo
candidato passa por conferir_af(), que exige: a AF existe, e deste fornecedor,
e desta filial de destino, e esta em aberto. O texto so LEVANTA candidatos; quem
decide e o banco.

FAIL-SAFE: erro em qualquer ponto devolve "nao achei", nunca um palpite.
"""

import re

# --------------------------------------------------------------------------- #
# ONDE PROCURAR
#
# xPed e a tag oficial da NF-e para "numero do pedido de compra" e e a fonte
# mais limpa: vem sozinha, sem texto em volta. As demais sao texto livre.
# Medido em amostra dos XMLs guardados: <xPed> aparece em 23% e <infCpl> em 48%.
# --------------------------------------------------------------------------- #
_TAGS_LIMPAS = ("xPed",)
_TAGS_TEXTO = ("infCpl", "infAdFisco", "obsCont", "xTexto", "xCampo", "xInf")

# Rotulos que o fornecedor usa antes do numero. O separador e opcional de
# proposito: a auditoria de 450 notas com gabarito (investigacao\\auditar_
# formatos.py) mostrou que ele escreve de tudo -
#
#     "AF - 1445341"   "AF.1445241"   "AF : 0000636355"   "AF/123"
#     "AFs 1439794"    "COM VALTERAF.1234"   "oc0233973"
#     "Autorizacao de Fornecimento:0001445109"
#     "NUMERO DO(S) PEDIDO(S) DO CLIENTE: 1444527"
#
# O "oc0233973" foi o UNICO formato que a auditoria pegou o extrator perdendo:
# a sigla colada no numero, sem separador nenhum. Por isso {0,4} e nao {1,4}.
#
# 'OC' e 'PEDIDO' entram aqui porque na pratica sao usados como sinonimo de AF
# ("Ordem(ns) de compra(s): 0001443956"). Nao ha risco em levantar demais: a
# validacao contra AUTOFORNE descarta o que nao for AF daquele fornecedor -
# medido, ela recusou 30,5% dos numeros levantados.
#
# A parte do rotulo aceita qualquer LETRA no fim ([^\W\d_]*), e nao \w*: a
# auditoria achou "Autorizaco de Fornecimento0000240492", sem cedilha, sem til
# e sem dois pontos, e exigir a grafia correta perdia a linha. Mas \w INCLUI
# DIGITO, e o teste flagrou: com \w* o rotulo engolia "Fornecimento000024" e
# sobrava "0492", devolvendo a AF errada 0000000492. Frouxo nas letras,
# rigoroso nos numeros.
_ROTULADO = re.compile(
    r"\b(?:AFS?|A\.F\.?|O\.?C\.?|PED(?:IDO)?S?|"
    r"AUT(?:\.|ORIZA[^\W\d_]*)?\s*(?:DE\s*)?FORN[^\W\d_]*)"
    r"[\s.:/#\-º°nN]{0,4}(\d{4,12})\b", re.I)

# numero solto, sem rotulo. So entra porque a validacao contra o banco filtra;
# sozinho seria chute.
_SOLTO = re.compile(r"\b(\d{5,12})\b")


def _tag(xml, nome):
    """Conteudo de todas as ocorrencias de uma tag (com ou sem namespace)."""
    return re.findall(r"<(?:\w+:)?%s\b[^>]*>(.*?)</(?:\w+:)?%s>" % (nome, nome),
                      xml or "", re.S | re.I)


def normaliza_af(n):
    """'1445341' -> '0001445341'. A AF tem 10 posicoes com zeros a esquerda.

    Devolve None quando nao da para normalizar com seguranca (mais de 10
    digitos nao e AF: e outra coisa, como o '14448961444896' da P.A.S, que e a
    AF colada duas vezes)."""
    d = re.sub(r"\D", "", str(n or ""))
    d = d.lstrip("0")
    if not d or len(d) > 10:
        return None
    return d.zfill(10)


def candidatos_no_xml(xml):
    """Todos os numeros que PODEM ser a AF, com a origem de cada um.

    Devolve lista de (af_normalizada, origem, trecho), da fonte mais confiavel
    para a menos. Nao valida nada: quem valida e conferir_af().
    """
    achados = []
    vistos = set()

    def guardar(bruto, origem, trecho):
        af = normaliza_af(bruto)
        if af and af not in vistos:
            vistos.add(af)
            achados.append((af, origem, (trecho or "").strip()[:120]))

    # 1) tag oficial: o valor JA e o numero, sem texto em volta
    for tag in _TAGS_LIMPAS:
        for v in _tag(xml, tag):
            guardar(v, "tag %s" % tag, v)

    # 2) texto livre COM rotulo ("AF - 1445341")
    for tag in _TAGS_TEXTO:
        for txt in _tag(xml, tag):
            for m in _ROTULADO.finditer(txt):
                ini = max(0, m.start() - 30)
                guardar(m.group(1), "rotulo em %s" % tag, txt[ini:m.end() + 15])

    # 3) texto livre SEM rotulo - o mais fraco, e vem por ultimo de proposito
    for tag in _TAGS_TEXTO:
        for txt in _tag(xml, tag):
            for m in _SOLTO.finditer(txt):
                ini = max(0, m.start() - 30)
                guardar(m.group(1), "numero solto em %s" % tag,
                        txt[ini:m.end() + 15])
    return achados


# --------------------------------------------------------------------------- #
# VALIDACAO CONTRA O BANCO - e aqui que o mecanismo fica seguro
# --------------------------------------------------------------------------- #
_SQL_CONFERE = """
    SELECT NVL(A.IDBAIXAAF,'1'), NVL(A.IDAFEXPIRADA,'N'),
           NVL(A.IDAFFORALIM,'N'), A.IDSITUATUAAF, A.CDFILLANC
    FROM   TEKNISA.AUTOFORNE A
    WHERE  A.NRAUTOFORN = :af
      AND  A.CDFORNECED = :forn
      AND  A.CDFILDEST  = :fil
"""


def conferir_af(cur, af, cd_forn, cd_filial):
    """Este numero e mesmo uma AF deste fornecedor nesta filial, e ela serve?

    DUAS PERGUNTAS, NAO UMA - e separa-las foi o que os tres XMLs reais
    ensinaram. Testando o extrator, ele achou a AF certa nas tres notas, mas
    duas foram recusadas: a da NF 839 estava com IDBAIXAAF='2' (a propria nota
    ja a consumiu) e a da NF 69757 com IDAFEXPIRADA='S' (AF de 19\\06, lida em
    07\\08 - o vencimento de ~30 dias do achado 49).

    Responder so "nao achei" nesses casos joga fora a informacao mais util. A
    pessoa precisa ouvir "o fornecedor escreveu a AF 0001444896 no XML, e ela
    esta expirada", que aponta o que fazer. Por isso devolvemos:

        (existe, usavel, motivo)

    'existe'  -> o numero e uma AF real deste fornecedor nesta filial
    'usavel'  -> alem de existir, esta em aberto (mesmos filtros do predicado
                 nativo que o af_matcher copiou do PL\\SQL, achado 35)

    Fail-safe: erro devolve (False, False, ...). Nao confirmar e sempre mais
    seguro que amarrar a nota numa AF que ninguem checou."""
    if not (af and cd_forn and cd_filial):
        return (False, False, "faltou fornecedor ou filial")
    try:
        cur.execute(_SQL_CONFERE, af=af, forn=cd_forn, fil=str(cd_filial))
        r = cur.fetchone()
    except Exception:
        return (False, False, "nao consegui conferir no banco")
    if not r:
        return (False, False, "nao e AF deste fornecedor nesta filial")
    baixa, expirada, foralim, situacao, _lanc = r
    if baixa not in ("1",):
        return (True, False, "a AF existe, mas ja foi baixada")
    if expirada != "N":
        return (True, False, "a AF existe, mas esta expirada")
    if foralim != "N":
        return (True, False, "a AF existe, mas e de fora do fornecimento")
    if situacao is not None:
        return (True, False, "a AF existe, mas esta em situacao especial")
    return (True, True, "AF em aberto")


def af_do_xml(cur, xml, cd_forn, cd_filial):
    """O numero da AF que o fornecedor escreveu no XML, JA VALIDADO.

    Devolve um dicionario, nao uma tupla, porque "achei e serve", "achei e nao
    serve" e "nao achei" sao tres respostas diferentes e a tela precisa das
    tres:

        {"af": ..., "usavel": bool, "origem": ..., "trecho": ..., "motivo": ...}

    A ordem dos candidatos vai da fonte mais confiavel (tag xPed) para a mais
    fraca (numero solto), entao o primeiro que o banco reconhece e tambem o
    melhor. Preferimos sempre um candidato USAVEL; so quando nenhum serve
    devolvemos o que existe, para explicar por que nao deu.

    Este resultado NAO substitui o motor de AF: ele entra como mais um sinal.
    Quando confirma a escolha do motor, sobe para automatico; quando aponta
    outra AF, a divergencia vai para a pessoa - duas fontes discordando e
    exatamente o caso em que ninguem deve decidir sozinho.
    """
    vazio = {"af": None, "usavel": False, "origem": "", "trecho": ""}
    try:
        cands = candidatos_no_xml(xml)
    except Exception:
        return dict(vazio, motivo="não consegui ler o XML")
    if not cands:
        return dict(vazio, motivo="o XML não traz nenhum número de AF")
    achou = None
    for af, origem, trecho in cands:
        existe, usavel, motivo = conferir_af(cur, af, cd_forn, cd_filial)
        if usavel:
            return {"af": af, "usavel": True, "origem": origem,
                    "trecho": trecho, "motivo": "AF em aberto"}
        if existe and achou is None:
            achou = {"af": af, "usavel": False, "origem": origem,
                     "trecho": trecho, "motivo": motivo}
    if achou:
        return achou
    return dict(vazio, motivo=("achei %d número(s) no XML, mas nenhum é AF "
                               "deste fornecedor nesta filial" % len(cands)))
