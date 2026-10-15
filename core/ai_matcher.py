"""
core/ai_matcher.py — De/Para semantico entre item do XML e item da AF.

AFINADO com dados reais do banco (mais de mil pares De/Para analisados).
A descricao do fornecedor difere do nome interno por:
  1. MARCA que o fornecedor inclui e o interno nao tem (DA BARRA, APTI, URBANO...)
  2. ABREVIACOES (LT=lata, FAT=fatiado, RESF=resfriado, GR=g, CG=congelado)
  3. ORDEM das palavras
  4. GRADUACAO de tamanho no nome interno (TAM M TIPO 2) que o fornecedor nao usa

Como resolve:
  - Remove graduacao (TAM X / TIPO N), embalagem e marca-ruido antes de comparar.
  - COBERTURA: mede quanto das palavras do nome interno aparecem na descricao do
    fornecedor. Peso maior para "o interno esta contido no fornecedor" (0.85), pois
    o fornecedor traz palavras a mais (marca), que nao devem penalizar.
  - TRAVA DE TAMANHO: se os dois lados declaram peso/volume diferente, derruba o
    score (impede casar embalagem errada, ex.: arroz 5KG no arroz 1KG).

Duas reguas de decisao:
  - LIMIAR_AUTO (0.92): grava sozinho.
  - LIMIAR_SUGESTAO (0.60): mostra como sugestao para o humano aprovar em 1 clique.
  Abaixo de 0.60, o humano procura do zero.

Nota: a analise revelou tambem mapeamentos ERRADOS na base historica (ex.: agua
mapeada como ave, chuchu como cenoura). O matcher corretamente da score ~0 nesses,
ou seja, nao repete o erro. Vale uma limpeza dessa base (assunto a parte).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import unicodedata
import re

try:
    from rapidfuzz import fuzz
    _HAS_FUZZ = True
except ImportError:
    # AVISA EM VOZ ALTA. Sem o rapidfuzz o comparador continua funcionando para
    # o caso comum (comparacao por palavras), mas o caminho de reserva - usado
    # quando um dos lados nao tem nenhuma palavra util - passa a dar score 0.
    # Antes isso acontecia em silencio: o robo simplesmente reconhecia menos
    # produtos, e nada na tela dizia por que.
    _HAS_FUZZ = False
    print(" AVISO: rapidfuzz nao esta instalado - o comparador de produtos vai "
          "reconhecer MENOS itens.\n"
          "        Instale com:  py -m pip install rapidfuzz", flush=True)


LIMIAR_AUTO = 0.92       # grava sozinho
LIMIAR_SUGESTAO = 0.60   # sugere ao humano (aprovacao em 1 clique)

_ABREV = {
    "LT": "LATA", "PCT": "PACOTE", "PC": "PACOTE", "CX": "CAIXA", "VD": "VIDRO",
    "BD": "BALDE", "FD": "FARDO", "TP": "TETRAPAK",
    "FAT": "FATIADO", "RESF": "RESFRIADO", "CONG": "CONGELADO",
    "CONGELAD": "CONGELADO", "CG": "CONGELADO", "INT": "INTEIRO", "PREM": "PREMIUM",
    # ESPECIE DA CARNE - o bloco que mais custava score.
    # Medido no cadastro interno: BOV aparece 4.343 vezes; no XML do fornecedor,
    # BOV 1.729 e SUINO 1.547. Ou seja, os dois lados alternam entre a forma
    # curta e a longa da MESMA palavra, e sem este de-para elas nao casavam.
    # Caso real que ficava em 52%: "ACEM BOVINO BIFE 70G" (fornecedor) contra
    # "BOV RESF ACEM BIFE 70G" (interno) - todas as palavras batem menos esta.
    "BOV": "BOVINO", "SUI": "SUINO", "SUIN": "SUINO",
    "FGO": "FRANGO", "FRG": "FRANGO",
    "BISC": "BISCOITO",
    # QJO = QUEIJO. Padrao medido em 1.200 pares onde o produto certo e
    # conhecido: "QJO MUSSARELA PC+-4 KG" contra "QUEIJO MUSSARELA INTEIRO"
    # ficava em 33%, so por causa desta palavra.
    "QJO": "QUEIJO", "QJ": "QUEIJO",
    # Mineradas dos dados: palavra curta de um lado que e prefixo da longa do
    # outro, dentro de vinculos JA corretos (3.000 pares, 30 dias).
    # Entraram so as inequivocas. Ficaram DE FORA de proposito:
    #   GAL  -> GALINHA   (GAL tambem e GALAO, que ja e generico de embalagem)
    #   PESC -> PESCOCO   (PESC tambem comeca PESCADA, que e outro produto)
    #   FAR  -> FARINHA   (FAR tambem comeca FARELO)
    #   CA   -> CALCADO   (duas letras: casaria com qualquer coisa)
    #   FRES -> FRESUBIN  (marca, nao abreviacao)
    "COX": "COXA", "PEI": "PEITO", "MANT": "MANTEIGA",
    "REFRI": "REFRIGERANTE", "CONC": "CONCENTRADO",
    # OVOS foi o par mais frequente da mineracao (32 casos), mas tem 4 letras e
    # a regra de plural exige 5 - baixar o limite estragaria MAIS, PAIS e afins.
    # Entao entra aqui, explicito.
    "OVOS": "OVO",
}
_UNIDADES = {"KG", "G", "GR", "ML", "L"}
# palavras genericas: embalagem, ligacao e graduacao de tamanho
_GENERICOS = {
    "PACOTE", "CAIXA", "LATA", "VIDRO", "BALDE", "FARDO", "TETRAPAK", "PET",
    "UN", "UNID", "UNIDADE", "TIPO", "DIVERSOS", "TAM", "PO",
    "DE", "DA", "DO", "COM", "SEM", "C", "S", "X",
    "FR", "GA", "GF", "PT", "BAG", "BOB", "RL", "EMB", "BJ", "ROLO",
    "BISNAGA", "BOMBONA", "SACHE", "GARRAFA", "GALAO", "FRASCO", "POTE",
    "P", "M", "GG", "PP", "EGG",
}


def _prep(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = t.upper()
    t = re.sub(r"\bTAM\s+\w+", "", t)      # remove graduacao "TAM X"
    t = re.sub(r"\bTIPO\s+\d+", "", t)     # remove "TIPO N"
    # RUIDO QUE O FORNECEDOR ANEXA NO XML: "- Peso: 19,500".
    # Nao descreve o produto, e as palavras entram na comparacao como se
    # descrevessem. Medido: "MELAO AMARELO - Peso: 19,500" contra
    # "MELAO TAM M TIPO 2" ficava em 90%, a um passo do limiar, so por isso.
    t = re.sub(r"\bPESO\s*:?\s*[\d.,]*", " ", t)
    # "IN NATURA" e descritor generico do cadastro interno que o fornecedor
    # quase nunca escreve. Era o padrao de 168 casos, tipo "MANJERICAO KG"
    # contra "MANJERICAO IN NATURA TAM P TIPO 1" (43%).
    t = re.sub(r"\bIN\s+NATURA\b", " ", t)
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(_singular(_ABREV.get(p, p)) for p in t.split())


def _singular(p: str) -> str:
    """Tira o plural simples. Um lado escreve OVO e o outro OVOS, ISCA e ISCAS,
    e para o comparador viravam palavras diferentes (32 e 5 casos medidos).

    So mexe em palavra com 4+ letras terminada em S, e nao em SS (para nao
    estragar OSSO/OSSOS, que ja casam, nem transformar 'MOUSSE' em 'MOUSE').
    Nao tenta plural irregular: o ganho nao compensa o risco."""
    if len(p) >= 5 and p.endswith("S") and not p.endswith("SS"):
        return p[:-1]
    return p


def _tamanhos(texto: str) -> set:
    if not texto:
        return set()
    # LT e LTS entram como LITRO. Sem eles, "REFRI COCA COLA 2LTS" nao tinha
    # tamanho reconhecido (o \b falhava no T depois do L) e o comparador
    # escolhia a garrafa errada: pegava PET 200ML no lugar de PET 2L. Foram 2
    # vinculos errados medidos em producao, e some ao reconhecer o tamanho.
    # A ordem importa: LTS antes de LT, e ambos antes de L.
    # So conta como litro quando vem COLADO no numero ("2LT"); "LT 350ML"
    # continua sendo lata, porque ali o LT nao e precedido de digito.
    ach = re.findall(r"(\d+(?:[.,]\d+)?)\s*(KG|GR|LTS|LT|ML|L|G)\b",
                     texto.upper().replace(",", "."))
    out = set()
    for num, un in ach:
        un = "G" if un == "GR" else un
        un = "L" if un in ("LT", "LTS") else un
        try:
            v = float(num)
            num = str(int(v)) if v == int(v) else str(v)
        except ValueError:
            pass
        out.add(f"{num}{un}")
    return out


def _core(texto: str) -> set:
    out = set()
    for w in _prep(texto).split():
        if w in _GENERICOS or w in _UNIDADES:
            continue
        if re.fullmatch(r"\d+(KG|GR|G|ML|L)?", w):
            continue
        out.add(w)
    return out


def _sim(desc_fornecedor: str, nome_interno: str) -> float:
    ta, tb = _tamanhos(desc_fornecedor), _tamanhos(nome_interno)
    conflito_tam = bool(ta and tb and ta.isdisjoint(tb))
    ca, cb = _core(desc_fornecedor), _core(nome_interno)
    if not ca or not cb:
        base = (fuzz.token_set_ratio(_prep(desc_fornecedor),
                                     _prep(nome_interno)) / 100.0) if _HAS_FUZZ else 0.0
    else:
        inter = ca & cb
        base = round(0.85 * len(inter) / len(cb) + 0.15 * len(inter) / len(ca), 4)
    # peso/volume declarado diferente: TETO 0.50 (nunca auto). Antes era 0.50 fixo,
    # o que fazia produto SEM relacao (ex.: oleo x peixe) aparecer como 50%.
    if conflito_tam:
        return round(min(base, 0.50), 4)
    return base


def _tem_grama(texto: str) -> bool:
    """True se o texto declara GRAMATURA em gramas (ex.: 45G, 100 GR, 90G).
    NAO conta embalagem em KG/ML/L (ex.: 'CX 10 KG', 'PCT 5 KG')."""
    return bool(re.search(r"(?<![A-Za-z])\d+(?:[.,]\d+)?\s*GR?\b",
                          (texto or "").upper()))


def sim_pref_generico(desc_fornecedor: str, nome_interno: str) -> float:
    """Igual ao _sim, MAS quando a descricao do fornecedor NAO tem gramatura
    (nao veio '45G' etc.), valoriza o produto GENERICO (sem gramatura no nome,
    ex.: 'BOV CONG ACEM') e despreza candidatos com gramatura especifica.
    Serve o caso: fornecedor nao poe a gramatura -> relacionar no generico ('00').

    IMPORTANTE: usar so na RECOMENDACAO / lista de alternativas (revisao humana).
    O auto-vinculo do robo continua no _sim puro (conservador)."""
    s = _sim(desc_fornecedor, nome_interno)
    if not _tem_grama(desc_fornecedor):
        ta, tb = _tamanhos(desc_fornecedor), _tamanhos(nome_interno)
        conflito_tam = bool(ta and tb and ta.isdisjoint(tb))
        if _tem_grama(nome_interno):
            s *= 0.70                       # candidato tem gramatura -> despreza
        elif (not conflito_tam) and (_core(desc_fornecedor) & _core(nome_interno)):
            # candidato generico compativel -> SOBE (nunca rebaixa match alto)
            s = max(s, min(s + 0.12, 0.90))
    return round(s, 4)


def faixa(score: float) -> str:
    """Faixa de confianca EXIBIDA ao usuario. Usa sempre o score BRUTO (_sim),
    o mesmo criterio que o robo usa para decidir se grava sozinho - assim a tela
    nunca rotula 'alta confianca' algo que o robo nao gravaria (e vice-versa)."""
    if score >= LIMIAR_AUTO:
        return "auto"
    if score >= LIMIAR_SUGESTAO:
        return "sugestao"
    return "baixa"


# --------------------------------------------------------------------------- #
# CANDIDATOS - fonte UNICA de verdade.
# Antes existiam tres consultas diferentes (robo, /api/sugerir e o painel), com
# limites e regras distintas: o painel oferecia produtos que o robo nunca veria.
# Agora todos usam esta funcao.
# --------------------------------------------------------------------------- #
SQL_CANDIDATOS = """
    -- OS ITENS DAS AFs DO FORNECEDOR, LIDOS UMA VEZ SO.
    --
    -- Antes o UNION repetia o mesmo caminho nos dois lados, e cada lado fazia
    -- TABLE ACCESS FULL em AUTOFORNE (818 mil linhas, custo 19.867) para achar
    -- as ~100 AFs de um fornecedor. Medido em homologacao: 137 SEGUNDOS para
    -- devolver 56 candidatos. Com o WITH materializado o caminho caro roda uma
    -- vez, e o resto trabalha sobre o resultado.
    --
    -- O indice IX_AUTOFORNE_FORN (sql/CRIAR_INDICES.sql) e o que resolve de
    -- verdade: sem ele nao ha como evitar a varredura, so evitar a segunda.
    WITH itens_af AS (
      SELECT /*+ MATERIALIZE */ DISTINCT I.CDPRODUTO
      FROM   TEKNISA.AUTOFORNE A
      JOIN   TEKNISA.ITEMAUTF  I ON I.CDFILDEST=A.CDFILDEST
             AND I.CDFILLANC=A.CDFILLANC AND I.NRAUTOFORN=A.NRAUTOFORN
      WHERE  A.CDFORNECED=:f
    )
    SELECT DISTINCT CDPRODUTO, NMPRODUTO, SGUNIDADE FROM (
      SELECT P.CDPRODUTO, P.NMPRODUTO, P.SGUNIDADE
      FROM   itens_af T
      JOIN   TEKNISA.PRODUTO P ON P.CDPRODUTO = T.CDPRODUTO
      WHERE  NVL(P.IDATIVO,'S')<>'N'
             AND UPPER(P.NMPRODUTO) NOT LIKE '%INATIVO%'
      UNION
      -- versao BASE "00" (produto de ESTOQUE, sem embalagem especifica), usada
      -- quando o fornecedor entrega a granel e nao informa gramatura
      SELECT PB.CDPRODUTO, PB.NMPRODUTO, PB.SGUNIDADE
      FROM   itens_af T
      JOIN   TEKNISA.PRODUTO PB
             ON PB.CDPRODUTO = SUBSTR(T.CDPRODUTO,1,LENGTH(T.CDPRODUTO)-2)||'00'
      WHERE  NVL(PB.IDATIVO,'S')<>'N'
             AND UPPER(PB.NMPRODUTO) NOT LIKE '%INATIVO%'
    ) FETCH FIRST 800 ROWS ONLY
"""
# TENTATIVA REPROVADA (06/08/2026): incluir aqui as VARIANTES IRMAS do produto
# (mesmo CDPRODESTO, outra embalagem), para o matcher poder escolher a
# embalagem na unidade que o fornecedor usa - o caso "AGUA DESTILADA C/5L" (CX)
# contra o produto em L, que bate 100% no texto e a trava granel x embalagem
# barra.
#
# A hipotese era boa, mas o dado reprovou. Medido em 400 itens reais de notas
# lancadas, contra o produto que o Teknisa usou:
#     ANTES  : 159 vinculos automaticos certos, 1 errado (247 candidatos/item)
#     DEPOIS : 155 certos, 1 errado                      (341 candidatos/item)
# Perdeu 4 acertos e nao corrigiu nenhum erro. Dois motivos: mais candidatos =
# mais competicao para o produto certo, e o FETCH FIRST (sem ORDER BY) passa a
# cortar linhas diferentes, chegando a deixar o produto certo de fora.
#
# Se for tentar de novo, o caminho e outro: trazer a variante irma APENAS do
# produto que o matcher ja escolheu, como desempate de unidade, sem inflar a
# lista para todos os itens.


# --------------------------------------------------------------------------- #
# UNIDADE DE MEDIDA - o sinal mais forte que existe, e o mais barato.
#
# Medido no banco (1,64 milhao de itens ja vinculados): a unidade do XML
# (ITEMPRODUTO.SGUNIDADEXML) e igual a do produto interno (PRODUTO.SGUNIDADE)
# em 76,8% dos casos. E sinal estrutural - nao depende de a descricao do
# fornecedor estar bem escrita, que e o que o resto do matcher tenta adivinhar.
#
# A mesma unidade chega escrita de varias formas no XML: KILO, UND, KG., PCT...
# Sem normalizar, esses casos sao perdidos (so KILO->KG sao 11.593 itens).
# --------------------------------------------------------------------------- #
_UN_SINONIMO = {
    "KILO": "KG", "KILOS": "KG", "QUILO": "KG", "QUILOS": "KG",
    "KGS": "KG", "KG.": "KG", "K": "KG",
    "UND": "UN", "UNID": "UN", "UNIDADE": "UN", "UNIDADES": "UN",
    "UN.": "UN", "UNI": "UN", "PC.": "PC", "PCT": "PC", "PACOTE": "PC",
    "CX.": "CX", "CAIXA": "CX", "CXA": "CX",
    "FARDO": "FD", "FD.": "FD", "FDO": "FD",
    "LITRO": "LT", "LITROS": "LT", "L": "LT", "LT.": "LT",
    "METRO": "MT", "METROS": "MT", "M": "MT", "MTS": "MT", "MT.": "MT",
    "GARRAFA": "GA", "LATA": "LA", "POTE": "PT", "FRASCO": "FR",
    # Achadas na auditoria de risco (unidades frequentes que ninguem mapeava):
    #   DZ 431 ocorrencias, SC 99, PR 76, BDJ 75
    # Todas sao EMBALAGEM/contagem. Nao havia furo de seguranca (o que nao esta
    # em _UN_GRANEL ja e tratado como embalagem, entao a trava granel x
    # embalagem funcionava), mas sem o de-para elas perdiam o bonus de unidade
    # igual no desempate.
    "DUZIA": "DZ", "DUZIAS": "DZ",
    "SACO": "SC", "SACA": "SC", "SC.": "SC",
    "PAR": "PR", "PARES": "PR",
    "BANDEJA": "BDJ", "BJ": "BDJ", "BDJ.": "BDJ",
}
# unidades de PESO/VOLUME (granel). Confundir granel com embalagem e o erro
# caro: a quantidade entra sem conversao e o estoque vai errado.
_UN_GRANEL = {"KG", "LT", "MT", "G", "ML"}


def normaliza_un(u):
    """Sigla de unidade em forma canonica. '' quando nao da para saber."""
    u = (u or "").strip().upper().replace(" ", "")
    if not u:
        return ""
    if u in _UN_SINONIMO:
        return _UN_SINONIMO[u]
    # SUFIXO NUMERICO: alguns fornecedores mandam FDO1, CXA1, UN1 - e a mesma
    # unidade com um numero colado (achado na auditoria: 71, 63 e 39
    # ocorrencias). Tira o numero e tenta de novo, mas so quando sobra letra
    # suficiente para ainda ser uma sigla (evita transformar "5" em "").
    base = u.rstrip("0123456789")
    if base and base != u and len(base) >= 2:
        return _UN_SINONIMO.get(base, base)
    return u


def un_conflita(un_xml, un_interna):
    """True quando as duas unidades sao INCOMPATIVEIS a ponto de nao dar para
    vincular sozinho: uma e granel (KG/LT) e a outra e embalagem (CX/FD/PC).

    Unidades so DIFERENTES nao sao conflito - o fornecedor entregar em CX e o
    produto interno ser PC e normal, e o fator de conversao resolve (20,1% dos
    vinculos reais sao assim). O que nao pode e granel virar embalagem."""
    a, b = normaliza_un(un_xml), normaliza_un(un_interna)
    if not a or not b or a == b:
        return False
    return (a in _UN_GRANEL) != (b in _UN_GRANEL)


# --------------------------------------------------------------------------- #
# CANDIDATOS DE UMA AF - a lista curta.
#
# Quando a AF e conhecida (porque o fornecedor escreveu no XML, ver
# core\xml_af.py), os candidatos deixam de ser "tudo que este fornecedor ja
# forneceu" e passam a ser "o que ESTA AF pediu". Medido em 8 fornecedores:
#
#     sem a AF   2.502 candidatos      com a AF   77      -96,9%
#     J03321599    800 -> 2      J83240333   158 -> 1
#     J14012344    447 -> 22     J73599045    83 -> 3
#
# Isso ataca o que o achado 44 identificou: a ambiguidade se resolve pela LISTA
# DE CANDIDATOS, nao pelo score. 'COUVE FLOR' batia 72% porque havia
# concorrente; com dois candidatos quase nao ha concorrente.
#
# Traz tambem a versao base '00' de cada produto da AF, pelo mesmo motivo da
# consulta grande: a AF costuma estar no produto de COMPRA (embalagem) e a nota
# vir no de ESTOQUE.
# --------------------------------------------------------------------------- #
SQL_CANDIDATOS_AF = """
    SELECT DISTINCT CDPRODUTO, NMPRODUTO, SGUNIDADE FROM (
      SELECT P.CDPRODUTO, P.NMPRODUTO, P.SGUNIDADE
      FROM   TEKNISA.ITEMAUTF I
      JOIN   TEKNISA.PRODUTO  P ON P.CDPRODUTO = I.CDPRODUTO
      WHERE  I.NRAUTOFORN = :af AND I.CDFILDEST = :fil
             AND NVL(P.IDATIVO,'S') <> 'N'
      UNION
      SELECT PB.CDPRODUTO, PB.NMPRODUTO, PB.SGUNIDADE
      FROM   TEKNISA.ITEMAUTF I
      JOIN   TEKNISA.PRODUTO  PB
             ON PB.CDPRODUTO = SUBSTR(I.CDPRODUTO,1,LENGTH(I.CDPRODUTO)-2)||'00'
      WHERE  I.NRAUTOFORN = :af AND I.CDFILDEST = :fil
             AND NVL(PB.IDATIVO,'S') <> 'N'
    )
"""


def _linhas_para_candidatos(cur):
    """(codigo, nome, unidade), com o LOB ja lido.

    Sem isto o nome do produto chegaria como objeto LOB e a comparacao de texto
    viraria lixo - e o sintoma seria score baixo em tudo, sem erro nenhum."""
    saida = []
    for linha in cur.fetchall():
        cd, nm = linha[0], linha[1]
        un = linha[2] if len(linha) > 2 else None
        if nm is not None and hasattr(nm, "read"):
            try:
                nm = nm.read()
            except Exception:
                nm = None
        saida.append((cd, nm, un))
    return saida


def candidatos_da_af(cur, nr_af, cd_filial):
    """So os produtos DESTA AF. Lista curta, para quando a AF ja e conhecida.

    Fail-safe: erro ou AF sem itens devolve lista VAZIA, e quem chama cai na
    lista completa. Aqui a lista curta e um atalho, nunca uma restricao - se
    ela falhar, o sistema tem de continuar funcionando como funcionava."""
    if not (nr_af and cd_filial):
        return []
    try:
        cur.execute(SQL_CANDIDATOS_AF, af=nr_af, fil=str(cd_filial))
        return _linhas_para_candidatos(cur)
    except Exception:
        return []


def produto_bate_com_af(cur, cd_produto, nr_af, cd_filial):
    """O produto escolhido pertence a esta AF? Devolve (bate, produtos_da_af).

    POR QUE ISTO EXISTE, e nao e teoria. Na NF 9668877 uma pessoa vinculou
    'QUEIJO MUSSARELA BUFALA' (1040453000) num item cuja AF pediu
    'QUEIJO MUSSARELA INTEIRO' (1040453202). Salvou sem reclamacao, e so na
    importacao o Teknisa recusou, com o codigo 071: 'Produto relacionado
    1040453000-QUEIJO MUSSARELA BUFALA nao corresponde com o produto da AF'.

    Ou seja: a regra ja existe no ERP, e nos deixavamos gravar algo que ele
    rejeitaria minutos depois. Melhor recusar na hora, com a lista do que a AF
    pediu na mao.

    A COMPARACAO E PELO PRODUTO DE ESTOQUE, nao pelo codigo (achado 36): a AF
    costuma estar no produto de COMPRA (1040453202, embalagem PC fator 4) e a
    nota vir no de ESTOQUE (1040453200, KG). Sao o mesmo item; comparar codigo
    exato recusaria vinculo correto.

    Fail-safe: erro ao conferir, ou AF sem itens, devolve (True, []) - segue
    como antes. Esta funcao existe para EVITAR um erro conhecido, nao para
    virar uma trava nova que barra o que hoje funciona."""
    if not (cd_produto and nr_af and cd_filial):
        return (True, [])
    try:
        cur.execute("""
            SELECT DISTINCT I.CDPRODUTO, P.NMPRODUTO,
                   NVL(P.CDPRODESTO, P.CDPRODUTO)
            FROM   TEKNISA.ITEMAUTF I
            JOIN   TEKNISA.PRODUTO  P ON P.CDPRODUTO = I.CDPRODUTO
            WHERE  I.NRAUTOFORN = :af AND I.CDFILDEST = :fil
        """, af=nr_af, fil=str(cd_filial))
        itens = cur.fetchall()
        if not itens:
            return (True, [])
        estoques = {e for _cd, _nm, e in itens}
        cur.execute("""SELECT NVL(CDPRODESTO, CDPRODUTO) FROM TEKNISA.PRODUTO
                       WHERE CDPRODUTO = :p""", p=cd_produto)
        r = cur.fetchone()
        if not r:
            return (True, [])
        return (r[0] in estoques, [(cd, nm) for cd, nm, _e in itens])
    except Exception:
        return (True, [])


def candidatos(cur, cd_forn, nr_af=None, cd_filial=None):
    """Produtos que podem ser vinculados a este fornecedor (ativos, da AF, mais
    a versao base '00'). Lista IDENTICA para robo, painel e IA.

    Com 'nr_af' informada, tenta primeiro a LISTA CURTA (so os produtos daquela
    AF) e cai na completa quando ela vem vazia.

    POR QUE CAIR NA COMPLETA, E NAO CONFIAR NA CURTA. Se a AF estiver errada -
    o fornecedor digitou um numero trocado, e ja vimos isso acontecer duas vezes
    na auditoria - a lista curta ESCONDE o produto certo, e o matcher devolveria
    o menos ruim de dois produtos errados. Devolver as duas listas deixa a
    decisao com quem chama: usa-se a curta quando ela produz acerto alto, e a
    completa no resto. Quem quiser so a curta chama candidatos_da_af."""
    if nr_af:
        curta = candidatos_da_af(cur, nr_af, cd_filial)
        if curta:
            return curta
    cur.execute(SQL_CANDIDATOS, f=cd_forn)
    return _linhas_para_candidatos(cur)


def _partes(c):
    """Candidato pode vir como (cd, nome) ou (cd, nome, unidade)."""
    return c[0], c[1], (c[2] if len(c) > 2 else None)


# --------------------------------------------------------------------------- #
# RAMO DA ARVORE - o primeiro digito do codigo do produto.
# Os 9 ramos: 1 ALIMENTAR · 2 DESCARTAVEL · 3 LIMPEZA · 4 INFRAESTRUTURA ·
# 5 DESPESA · 6 OPERADOR LOGISTICO · 7 FATURAMENTO · 8 PRODUCAO · 9 VENDA
#
# O ramo 6 tem os produtos do Operador Logistico. O MESMO produto costuma existir
# nas duas arvores, com nome praticamente identico (3.643 pares em RELACPRODOL):
#     6010127240  FRANGO CONG MOELA - CX 18 KG   (ramo 6, OL)
#     1010127203  FRANGO CONG MOELA - CX 18 KG   (ramo 1, direto)
# Comparando so por texto e unidade, os dois EMPATAM - e a escolha viraria sorte.
#
# O De/Para do DFE (que e o que gravamos) aponta para os ramos 1..5 em 99,98%
# dos 82.563 vinculos ativos; o ramo 6 tem 13. A operacao confirmou o porque:
# o relacionamento do DFE e feito nos outros ramos, e o vinculo do ramo 6 e
# assunto do MODULO OL, tratado pelo setor de Compras - nao por este sistema.
_RAMO_OL = "6"


def _e_do_ol(cd_produto):
    """True se o produto e do ramo 6 (Operador Logistico)."""
    return str(cd_produto or "").startswith(_RAMO_OL)


def escolher(descricao, cands, un_xml=None):
    """Decide o melhor candidato. Devolve (melhor_auto, melhor_sugestao):
      - melhor_auto      -> pelo score BRUTO; e o que autoriza (ou nao) o auto-vinculo
      - melhor_sugestao  -> preferindo o generico; e o que se MOSTRA para a pessoa
    Ambos como (codigo, nome, score) ou None.

    'un_xml' e a unidade que o fornecedor usou no XML. Quando informada, ela
    DESEMPATA: entre candidatos de score parecido, ganha o da unidade certa.
    Nao substitui o texto - so decide quando o texto esta em duvida."""
    best = best_sug = None
    for c in cands:
        cd, nm, un = _partes(c)
        casa_un = bool(un_xml) and normaliza_un(un_xml) == normaliza_un(un)
        # ajuste de ORDENACAO (nunca mexe no score devolvido):
        #   + unidade igual a do XML  -> sinal forte, ver normaliza_un()
        #   - produto do ramo 6 (OL)  -> o De/Para do DFE nao e feito nele
        ajuste = (0.02 if casa_un else 0.0) - (0.02 if _e_do_ol(cd) else 0.0)

        s = _sim(descricao, nm or "")
        s_ord = s + ajuste
        if best is None or s_ord > best[3]:
            best = (cd, nm, s, s_ord)

        s2 = sim_pref_generico(descricao, nm or "")
        s2_ord = s2 + ajuste
        if best_sug is None or s2_ord > best_sug[3]:
            best_sug = (cd, nm, s2, s2_ord)

    # devolve no formato de sempre (3 itens): o 4o era so para ordenar
    return (best[:3] if best else None), (best_sug[:3] if best_sug else None)


def unico_que_contem(descricao, cands):
    """O melhor candidato e o UNICO que contem todas as palavras do fornecedor?

    Resolve o padrao mais comum que sobrou (179 casos medidos): o fornecedor
    manda o nome generico e o cadastro interno acrescenta a VARIEDADE.
        MANGA KG   ->  MANGA TOMMY TAM DIVERSOS TIPO 3   (57%)
        LIMAO KG   ->  LIMAO TAITI TAM M TIPO 2          (57%)
        MACA KG    ->  MACA FUJI/GALA TAM M TIPO 2       (43%)
    O score cai porque a formula pesa 0.85 em "quanto do nome interno o
    fornecedor cobre", e a variedade nunca e coberta.

    Baixar esse peso seria errado: com DUAS variedades cadastradas (MANGA TOMMY
    e MANGA PALMER), "MANGA" e ambiguo de verdade e tem que ir para uma pessoa.
    O que decide nao e o score, e a EXISTENCIA de concorrente - e isso so se
    sabe olhando a lista de candidatos, nao comparando dois textos.

    Devolve o unico candidato que contem todas as palavras do fornecedor, ou
    None se houver zero ou mais de um.

    O CASO ESPELHO (interno contido no fornecedor) FOI TESTADO E REPROVADO.
    Seria a situacao "CREME CHANTILLY CESIBON UHT 1L" contra "CHANTILLY", em
    que as palavras a mais pareceriam so marca. Medido em 883 itens reais:
    19 casos onde o melhor era o unico nessa condicao, e a precisao ficou em
    73,7% (14 certos, 5 errados). Motivo: palavra a mais do fornecedor nem
    sempre e marca - "ALHO HIG/PROC KG" tem que ir para ALHO DESCASCADO, nao
    para o ALHO generico, e nao ha como separar marca de qualificador pelo
    texto. Por isso a excecao vale so numa direcao."""
    alvo = _core(descricao)
    if not alvo:
        return None
    achados = [c for c in cands if alvo <= _core(c[1] or "")]
    return achados[0] if len(achados) == 1 else None


def pode_auto(descricao, melhor, un_xml=None, un_candidato=None, cands=None):
    """True se o robo pode gravar sozinho. Regra unica, usada em todo lugar.

    'cands' e opcional: quando vem, habilita a excecao do nome generico (ver
    unico_que_contem). Sem ele o comportamento e exatamente o de antes."""
    if not melhor:
        return False
    if melhor[2] < LIMIAR_AUTO:
        # unica excecao: nao ha concorrente, entao nao ha o que a pessoa decidir
        if not cands:
            return False
        # PISO: abaixo de LIMIAR_SUGESTAO o par nem serve como sugestao, muito
        # menos para gravar sozinho. Medido: sem este piso a excecao ganhava 43
        # vinculos mas trazia 11 errados, TODOS em 57% e todos do mesmo caso
        # ("REPOLHO VERDE" indo para "REPOLHO VERDE HIGIENIZADO" enquanto o
        # historico aponta "REPOLHO BRANCO LIMPO" - dois repolhos diferentes).
        # Os ganhos legitimos estavam todos em 72%.
        if melhor[2] < LIMIAR_SUGESTAO:
            return False
        u = unico_que_contem(descricao, cands)
        if not u or u[0] != melhor[0]:
            return False
        # mesmo sem concorrente, tamanho declarado diferente continua barrando
        if _tamanhos(descricao) and _tamanhos(melhor[1] or "") and \
                _tamanhos(descricao).isdisjoint(_tamanhos(melhor[1] or "")):
            return False
    # nota SEM gramatura + candidato COM gramatura -> humano decide
    if (not _tem_grama(descricao)) and _tem_grama(melhor[1] or ""):
        return False
    # granel x embalagem -> humano decide. Vincular "CX" a um produto de estoque
    # em "KG" faz a quantidade entrar sem conversao: 1 caixa vira 1 quilo.
    if un_conflita(un_xml, un_candidato):
        return False
    return True


def escolher_com_af(cur, descricao, cd_forn, nr_af=None, cd_filial=None,
                    un_xml=None):
    """Escolhe o produto usando a AF para ENCOLHER a lista, sem perder a rede.

    POR QUE ISTO EXISTE. O achado 44 mostrou que a ambiguidade se resolve pela
    LISTA DE CANDIDATOS, nao pelo score: 'COUVE FLOR' batia 72% porque havia
    concorrente. Sabendo a AF, os candidatos caem de centenas para poucos -
    medido em 8 fornecedores, de 2.502 para 77 (-96,9%), com casos de 800 para
    2 e de 447 para 22.

    O PISO E LIMIAR_SUGESTAO, E ISSO FOI MEDIDO. Minha primeira versao exigia
    LIMIAR_AUTO (92%) na lista curta, e o caso real reprovou: NF 9668877, o
    fornecedor escreve 'QJO MUSSARELA PC+-4KG CX+-24KG' e a AF pediu
    'QUEIJO MUSSARELA INTEIRO'. O texto bate 67%, entao a curta era descartada
    e a busca caia nos 639 produtos do fornecedor, onde ela escolhia
    'QUEIJO MUSSARELA BUFALA' - o produto ERRADO, com o certo na mao.

    Exigir score alto aqui contraria o achado 44: a ambiguidade se resolve pela
    LISTA DE CANDIDATOS, nao pelo score. Numa lista de 2 produtos que a AF
    pediu, 67% nao e duvida - e a resposta.

    O PISO NAO PODE SUMIR, e este e o cuidado. A AF pode nao cobrir todos os
    itens da nota (nota com item fora do pedido existe), e ai o produto certo
    NAO esta na lista curta. Sem piso, o matcher devolveria o menos ruim de
    dois produtos errados - vinculo errado com cara de acerto. Com o piso, o
    item de fora pontua baixo e a busca cai na lista completa, como antes.

    Devolve (melhor, sugestao, origem, cands_usados), onde origem e 'af' ou
    'fornecedor' - a tela precisa disso para explicar de onde veio a escolha, e
    quem chama precisa para decidir se grava sozinho.
    """
    if nr_af:
        curta = candidatos_da_af(cur, nr_af, cd_filial)
        if curta:
            melhor, sug = escolher(descricao, curta, un_xml)
            if melhor and melhor[2] >= LIMIAR_SUGESTAO:
                return melhor, sug, "af", curta
    completa = candidatos(cur, cd_forn)
    melhor, sug = escolher(descricao, completa, un_xml)
    return melhor, sug, "fornecedor", completa


@dataclass
class MatchResult:
    cd_produto: Optional[str]
    nm_produto: Optional[str]
    score: float
    automatico: bool          # >= LIMIAR_AUTO: grava sozinho
    sugerido: bool            # >= LIMIAR_SUGESTAO: mostra ao humano


def melhor_match(desc_xml: str, itens_af: list,
                 limiar_auto: float = LIMIAR_AUTO,
                 limiar_sugestao: float = LIMIAR_SUGESTAO) -> MatchResult:
    """
    itens_af: lista de dicts com CDPRODUTO e NMPRODUTO.
    Devolve o melhor candidato, se pode ser gravado sozinho (automatico) e se
    ao menos serve como sugestao para o humano confirmar (sugerido).
    """
    melhor = MatchResult(None, None, 0.0, False, False)
    for it in itens_af:
        s = _sim(desc_xml, it.get("NMPRODUTO", ""))
        if s > melhor.score:
            melhor = MatchResult(it.get("CDPRODUTO"), it.get("NMPRODUTO"),
                                 round(s, 4), s >= limiar_auto, s >= limiar_sugestao)
    return melhor


if __name__ == "__main__":
    casos = [
        ("ACUCAR REFINADO DA BARRA 1KG", "ACUCAR REFINADO - PCT 1 KG"),
        ("CEBOLA KG", "CEBOLA TAM M TIPO 2"),
        ("OLEO DE SOJA PET FORNECEDOR F 900ML", "OLEO SOJA - PET 900ML"),
        ("ARROZ PARBOILIZADO URBANO 5KG", "ARROZ PARBOILIZADO TIPO 1 - PCT 1 KG"),
        ("Agua sem gas", "AVE FESTA RESF TEMPERADA"),
        ("FILE DE PEITO", "FRANGO CONG PEITO S/ OSSO"),
    ]
    print("rapidfuzz: %s  |  auto>=%.0f  sugestao>=%.0f\n"
          % (_HAS_FUZZ, LIMIAR_AUTO * 100, LIMIAR_SUGESTAO * 100))
    for d, i in casos:
        s = _sim(d, i)
        tag = "AUTO" if s >= LIMIAR_AUTO else "SUGERE" if s >= LIMIAR_SUGESTAO else "humano"
        print("%-7s %.2f | %s -> %s" % (tag, s, d, i))
