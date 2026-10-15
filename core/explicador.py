# -*- coding: utf-8 -*-
r"""core/explicador.py - O RACIOCINIO DO ROBO, EM PORTUGUES.

POR QUE ISTO EXISTE
    A pessoa via a conclusao ("vinculou em 1060394603, 94%") e nao via o
    caminho. Sem o caminho ela nao consegue julgar se a conclusao faz sentido,
    e muito menos apontar ONDE o raciocinio errou.

    O robo nao e caixa preta: ele decide por regras deterministicas, todas
    legiveis. Este modulo percorre as MESMAS funcoes do matcher e devolve, passo
    a passo, o que foi considerado e o que decidiu.

NAO DUPLICA REGRA. Tudo aqui chama core.ai_matcher. Se a regra mudar la, a
explicacao muda junto. Reimplementar a regra aqui seria criar duas verdades -
o erro que a tela "Simular" cometeu e que custou a confianca no ensaio.

SO LEITURA: nao grava, nao decide nada, nao altera o resultado do matcher.
"""
from core.ai_matcher import (
    _ABREV, _core, _prep, _sim, _tamanhos, _tem_grama, _partes, escolher,
    faixa, normaliza_un, pode_auto, sim_pref_generico, un_conflita,
    unico_que_contem, LIMIAR_AUTO, LIMIAR_SUGESTAO,
)

TOPO = 6          # quantos candidatos mostrar no ranking


def _abrev_usadas(texto):
    """As abreviacoes que o robo expandiu ao ler este texto."""
    achadas = []
    for p in (texto or "").upper().replace("/", " ").replace("-", " ").split():
        limpo = "".join(c for c in p if c.isalnum())
        if limpo in _ABREV and (limpo, _ABREV[limpo]) not in achadas:
            achadas.append((limpo, _ABREV[limpo]))
    return achadas


def _cobertura(desc, nome):
    """A conta do score, em palavras: quanto do nome interno o fornecedor
    cobre. A formula pesa 0,85 nesse lado e 0,15 no contrario."""
    ca, cb = _core(desc), _core(nome or "")
    inter = ca & cb
    return {
        "palavras_fornecedor": sorted(ca),
        "palavras_produto": sorted(cb),
        "em_comum": sorted(inter),
        "faltou_no_fornecedor": sorted(cb - ca),
        "sobrou_no_fornecedor": sorted(ca - cb),
        "cobre": ("%d de %d palavra(s) do produto" % (len(inter), len(cb))
                  if cb else "produto sem palavras comparaveis"),
    }


def explicar(descricao, cands, un_xml=None, un_de=None):
    """O raciocinio completo para UM item de nota.

    descricao : o texto que o fornecedor mandou no XML
    cands     : a lista de candidatos que o robo avaliou (mesma do matcher)
    un_xml    : unidade do XML;  un_de: dicionario codigo -> unidade interna

    Devolve um dicionario pronto para a tela, sem nenhum efeito colateral.
    """
    un_de = un_de or {}
    lido = _prep(descricao)
    tam = sorted(_tamanhos(descricao))
    exp = {
        "texto_original": descricao,
        "texto_lido": lido,
        "abreviacoes": [{"de": a, "para": b} for a, b in _abrev_usadas(descricao)],
        "palavras_consideradas": sorted(_core(descricao)),
        "tamanho_declarado": tam,
        "tem_gramatura": _tem_grama(descricao),
        "unidade_xml": un_xml,
        "unidade_normalizada": normaliza_un(un_xml) if un_xml else None,
        "candidatos_avaliados": len(cands or []),
        "limiar_auto": LIMIAR_AUTO,
        "limiar_sugestao": LIMIAR_SUGESTAO,
        "ranking": [],
        "bloqueios": [],
        "decisao": "humano",
        "regra": "",
    }
    if not cands:
        exp["regra"] = ("Nenhum candidato para comparar: este fornecedor não "
                        "tem produtos vinculados que sirvam de referência.")
        return exp

    # ---- o ranking, com a conta aberta ------------------------------------
    postos = []
    for c in cands:
        cd, nm, un = _partes(c)
        s = _sim(descricao, nm or "")
        postos.append((s, cd, nm, un))
    postos.sort(key=lambda x: -x[0])

    melhor, sugestao = escolher(descricao, cands, un_xml)
    unico = unico_que_contem(descricao, cands)

    for s, cd, nm, un in postos[:TOPO]:
        cob = _cobertura(descricao, nm)
        tb = sorted(_tamanhos(nm or ""))
        item = {
            "cd_produto": cd,
            "nome": nm,
            "unidade": un,
            "score": round(s * 100),
            "faixa": faixa(s),
            # so marca escolhido quando o candidato de fato alcanca o piso;
            # abaixo disso o topo do ranking e apenas o primeiro da ordenacao
            "escolhido": (bool(melhor) and cd == melhor[0]
                          and melhor[2] >= LIMIAR_SUGESTAO),
            "unidade_conflita": bool(un_conflita(un_xml, un)),
            "tamanho_produto": tb,
            "tamanho_conflita": bool(tam and tb and set(tam).isdisjoint(tb)),
            "tem_gramatura": _tem_grama(nm or ""),
            "score_generico": round(sim_pref_generico(descricao, nm or "") * 100),
        }
        item.update(cob)
        exp["ranking"].append(item)

    if not melhor:
        exp["regra"] = "Nenhum candidato pontuou o suficiente para ser avaliado."
        return exp

    # ---- por que decidiu (ou nao) ----------------------------------------
    s = melhor[2]
    un_cand = un_de.get(melhor[0])
    auto = pode_auto(descricao, melhor, un_xml, un_cand, cands)

    if s >= LIMIAR_AUTO:
        exp["regra"] = ("O texto bate em %d%%, acima dos %d%% exigidos para "
                        "gravar sozinho." % (round(s * 100), round(LIMIAR_AUTO * 100)))
    elif unico and unico[0] == melhor[0] and s >= LIMIAR_SUGESTAO:
        exp["regra"] = ("Bate em %d%%, abaixo dos %d%% de sempre, mas este é o "
                        "ÚNICO produto do fornecedor que contém todas as "
                        "palavras da nota. Sem concorrente, não há o que uma "
                        "pessoa decidir." % (round(s * 100), round(LIMIAR_AUTO * 100)))
    elif s < LIMIAR_SUGESTAO:
        # ABAIXO DO PISO nao existe "melhor": o topo do ranking e so o primeiro
        # da ordenacao. Dizer "existe mais de um parecido" seria mentira, e a
        # precisao do texto e o ponto desta tela.
        exp["regra"] = ("Nenhum produto deste fornecedor se parece com o texto "
                        "da nota. O melhor bate em apenas %d%%, e o mínimo para "
                        "sugerir é %d%%."
                        % (round(s * 100), round(LIMIAR_SUGESTAO * 100)))
    else:
        exp["regra"] = ("Bate em %d%%, abaixo dos %d%% exigidos, e existe mais "
                        "de um produto parecido."
                        % (round(s * 100), round(LIMIAR_AUTO * 100)))

    # ---- o que barrou, quando barrou -------------------------------------
    if s < LIMIAR_SUGESTAO:
        exp["bloqueios"].append(
            "O texto bate em apenas %d%%, abaixo do mínimo de %d%% para o "
            "robô sequer sugerir." % (round(s * 100), round(LIMIAR_SUGESTAO * 100)))
    if s < LIMIAR_AUTO and not (unico and unico[0] == melhor[0]):
        outros = [c for c in cands if _core(descricao) <= _core(c[1] or "")]
        if len(outros) > 1:
            exp["bloqueios"].append(
                "Existem %d produtos que contêm todas as palavras da nota, "
                "então a escolha é genuinamente de uma pessoa." % len(outros))
    if (not _tem_grama(descricao)) and _tem_grama(melhor[1] or ""):
        exp["bloqueios"].append(
            "A nota não declara gramatura e o produto declara. Vincular sem "
            "isso pode trocar a versão do item.")
    tb = _tamanhos(melhor[1] or "")
    if tam and tb and set(tam).isdisjoint(tb):
        exp["bloqueios"].append(
            "Tamanho declarado diferente: a nota diz %s e o produto diz %s."
            % (", ".join(tam), ", ".join(sorted(tb))))
    if un_conflita(un_xml, un_cand):
        exp["bloqueios"].append(
            "Unidade incompatível: a nota veio em %s (granel) e o produto é %s "
            "(embalagem). A quantidade entraria sem conversão."
            % (un_xml, un_cand))

    exp["decisao"] = "auto" if auto else ("sugestao" if s >= LIMIAR_SUGESTAO
                                          else "humano")
    if s >= LIMIAR_SUGESTAO:
        exp["escolhido"] = {"cd_produto": melhor[0], "nome": melhor[1],
                            "score": round(s * 100)}
    if sugestao and sugestao[0] != melhor[0]:
        exp["sugestao_alternativa"] = {
            "cd_produto": sugestao[0], "nome": sugestao[1],
            "score": round(sugestao[2] * 100),
            "por_que": ("A nota não declara gramatura, então o robô também "
                        "considera a versão genérica do produto.")}
    return exp
