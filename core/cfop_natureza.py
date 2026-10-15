# -*- coding: utf-8 -*-
"""O CFOP DIZ SE A NOTA E COMPRA COM AF - e isso destrava a maior fila.

O PROBLEMA
----------
Medido em homologacao (07\\08\\2026): das 53.786 notas paradas, **42.316 (78,7%)
estao sem CDTIPOOPER**. E o robo so trabalha nota com tipo (TIPOS_ATENDIDOS),
porque sem ele nao da para saber se e compra - e vincular AF de compra numa
devolucao baixaria pedido nao atendido.

Pelo achado 20, a coluna vem da AF, nao do CFOP: fica vazia justamente porque
ninguem relacionou a AF ainda. Circulo fechado: precisa da AF para ter o tipo,
e precisa do tipo para procurar a AF.

O CFOP quebra o circulo. Ele esta em **100% dos 316.193 itens** de notas paradas
(NCM em 98,8%, CST em 99,2%), e vem do XML, nao de decisao de ninguem.

A PERGUNTA CERTA
----------------
Minha primeira tentativa foi prever o CDTIPOOPER pelo CFOP. Funciona, mas mal:
com piso de 98% de acerto o alcance e de so 19,8% das paradas, porque os CFOPs
que carregam o volume (5102 com 16.823 notas) ficam em 97,8%.

A pergunta que o robo REALMENTE faz nao e "qual o tipo", e sim "esta nota e
compra com AF?". Medindo assim, contra 466 mil notas ja importadas, a separacao
e limpa:

    5102  95,6% terminaram com AF     ->  COMPRA
    5101  97,2%                       ->  COMPRA
    6105  99,8%                       ->  COMPRA
    5656   8,8%                       ->  SEM_AF
    5929  12,5%                       ->  SEM_AF
    6353   0,9%                       ->  SEM_AF

Nao ha meio-termo relevante: os CFOPs de compra ficam acima de 75% e os de
servico\\bonificacao\\remessa abaixo de 13%. So tres codigos caem no meio, e
esses vao para a pessoa.

A FAMILIA DO CFOP JA SEPARA MUITA COISA
---------------------------------------
O CFOP e escrito da otica do EMITENTE. Familia 5\\6\\7 e SAIDA dele, ou seja,
venda para nos. Familia 1\\2\\3 e ENTRADA dele: devolucao, retorno, ajuste.

Medido: das 466 mil notas importadas, so **20** eram de familia 1 ou 2, e
apenas 6 com AF. Mas entre as paradas sem tipo sao **7.513 notas (17,7%)**.
Isso e fila entupida com documento que nunca vai ter AF - o caso da FORNECEDOR I
(NF 7168609, CFOP 1202, "DEVOLUCAO DE VENDA") e o retrato.

O QUE ESTE MODULO NAO FAZ
-------------------------
Nao grava nada. O CDTIPOOPER continua sendo preenchido pelo Teknisa na
importacao. Este modulo responde uma pergunta interna do robo, e por isso NAO
adiciona uma terceira tabela de escrita (o teste B68 exige que sejam duas:
PRODFORNXML e INTG_ITEMPRODUTO).

FAIL-SAFE: tudo que nao esta medido responde DUVIDA, que manda para a pessoa.
CFOP desconhecido nunca vira COMPRA por conta propria.
"""

COMPRA = "COMPRA"    # o robo pode procurar AF
SEM_AF = "SEM_AF"    # nota legitima, mas que nao tem AF (servico, bonificacao)
DUVIDA = "DUVIDA"    # a pessoa decide

# Pisos da classificacao. Vieram da distribuicao medida, nao de gosto: entre
# 20% e 75% existem apenas 3 CFOPs, e todos com base pequena.
_PISO_COMPRA = 0.75
_TETO_SEM_AF = 0.20
_BASE_MINIMA = 30    # abaixo disso a proporcao nao se sustenta

# --------------------------------------------------------------------------- #
# MEDIDO EM HOMOLOGACAO, 07\08\2026, sobre 466.902 notas ja importadas.
# Formato: CFOP -> (natureza, % que terminou com AF, quantas notas na base)
#
# Para refazer esta medicao quando a base mudar, ver medir() no fim do arquivo.
# --------------------------------------------------------------------------- #
_MEDIDO = {
    "5101": (COMPRA,  97.2,  67724),
    "5102": (COMPRA,  95.6, 312582),
    "5103": (COMPRA, 100.0,    515),
    "5104": (COMPRA,  75.1,   2219),
    "5105": (COMPRA,  94.7,     38),
    "5124": (COMPRA,  97.4,     39),
    "5352": (SEM_AF,  11.9,    109),
    "5353": (SEM_AF,   0.0,   1285),
    "5401": (COMPRA,  96.8,    444),
    "5403": (COMPRA,  97.7,   6616),
    "5405": (COMPRA,  92.3,  18999),
    "5655": (SEM_AF,  10.0,     30),
    "5656": (SEM_AF,   8.8,   8718),
    "5908": (SEM_AF,   2.0,     98),
    "5910": (DUVIDA,  29.9,     87),
    "5929": (SEM_AF,  12.5,   7775),
    "5932": (SEM_AF,   0.0,    180),
    "6101": (COMPRA,  95.7,  13952),
    "6102": (COMPRA,  88.5,  16359),
    "6105": (COMPRA,  99.8,   1265),
    "6106": (DUVIDA,  71.9,     57),
    "6107": (COMPRA,  97.1,     35),
    "6108": (DUVIDA,  64.5,    121),
    "6353": (SEM_AF,   0.9,   3404),
    "6401": (COMPRA,  99.4,    702),
    "6403": (COMPRA,  97.8,   1486),
    "6404": (COMPRA,  79.3,    352),
    "6656": (SEM_AF,   7.9,    279),
    "6908": (SEM_AF,   0.3,    319),
    "6910": (SEM_AF,   3.9,     76),
    "6929": (COMPRA,  93.8,     32),
    "6932": (SEM_AF,   0.8,    738),
    "6933": (SEM_AF,   0.0,     77),
}

# Familias de ENTRADA no emitente: devolucao, retorno, ajuste. Nao ha AF a
# vincular nelas. Isto NAO e regra estatistica, e a definicao do CFOP - por
# isso vale mesmo para codigo que nunca apareceu na base.
_FAMILIAS_DEVOLUCAO = ("1", "2", "3")


def _limpa(cfop):
    return "".join(ch for ch in str(cfop or "") if ch.isdigit())


def natureza(cfop):
    """Devolve (natureza, motivo em portugues).

    Ordem deliberada: a familia do CFOP vem ANTES da tabela medida. Devolucao e
    devolucao por definicao, e nao por quantas vezes apareceu - a base tem so 20
    notas de familia 1\\2, entao a estatistica nao teria como saber."""
    c = _limpa(cfop)
    if not c:
        return (DUVIDA, "a nota não informou o CFOP")
    if c[0] in _FAMILIAS_DEVOLUCAO:
        return (SEM_AF, "CFOP %s é entrada no emitente (devolução ou retorno), "
                        "não é compra nossa" % c)
    achado = _MEDIDO.get(c)
    if not achado:
        return (DUVIDA, "CFOP %s não tem histórico suficiente para decidir "
                        "sozinho" % c)
    nat, pct, base = achado
    if nat == COMPRA:
        return (COMPRA, "CFOP %s: %.0f%% das %s notas com esse código "
                        "terminaram com AF" % (c, pct, base))
    if nat == SEM_AF:
        return (SEM_AF, "CFOP %s: só %.0f%% das %s notas com esse código "
                        "tiveram AF, então não é compra com pedido"
                        % (c, pct, base))
    return (DUVIDA, "CFOP %s fica no meio do caminho (%.0f%% com AF): a pessoa "
                    "decide" % (c, pct))


_SQL_CFOP_DA_NOTA = """
    SELECT MAX(I.CDFISOPEITEM) KEEP (DENSE_RANK FIRST
             ORDER BY NVL(I.VRTOTAITEM,0) DESC, I.NRSEQITPROD)
    FROM   TEKNISA.INTG_ITEMPRODUTO I
    WHERE  I.NRACESSONFE = :c
"""


def cfop_da_nota(cur, chave):
    """O CFOP que representa a nota: o do item de MAIOR VALOR.

    Nota com varios CFOPs e comum (a NF 69757 tinha 5102 em 13 itens e 5405 em
    1). Usar o primeiro item seria sorteio; usar o de maior valor pega o que
    define a operacao. Empate desempata pela sequencia, para a resposta nao
    variar entre execucoes."""
    try:
        cur.execute(_SQL_CFOP_DA_NOTA, c=chave)
        r = cur.fetchone()
        return (r[0] if r else None)
    except Exception:
        return None


def nota_e_compra(cur, chave):
    """A nota e compra com AF? Devolve (natureza, motivo).

    E a pergunta que o robo faz antes de procurar AF. Hoje ele responde olhando
    CDTIPOOPER, que esta vazio em 78,7% das paradas; com isto ele passa a
    responder pelo CFOP, que esta em 100%.

    Fail-safe: qualquer erro devolve DUVIDA. Nao conseguir classificar manda
    para a pessoa, nunca libera."""
    try:
        c = cfop_da_nota(cur, chave)
    except Exception:
        return (DUVIDA, "não consegui ler o CFOP da nota")
    return natureza(c)


def medir(cur, base_minima=_BASE_MINIMA):
    """Refaz a medicao contra o banco e devolve a tabela atualizada.

    Existe para a tabela acima nao virar folclore: quando a base mudar, roda-se
    isto e compara-se. Nao e usada em producao - a tabela fica no codigo de
    proposito, para que uma mudanca de comportamento seja uma alteracao
    revisada, e nao algo que aconteceu sozinho numa madrugada."""
    cur.execute("""
      SELECT CFOP, COUNT(*), SUM(TEM_AF) FROM (
        SELECT G.NRACESSONFE,
               MAX(I.CDFISOPEITEM) KEEP (DENSE_RANK FIRST
                    ORDER BY NVL(I.VRTOTAITEM,0) DESC, I.NRSEQITPROD) CFOP,
               MAX(CASE WHEN I.NRAUTOFORN IS NOT NULL THEN 1 ELSE 0 END) TEM_AF
        FROM   TEKNISA.INTG_NOTAFISCAL G
        JOIN   TEKNISA.INTG_ITEMPRODUTO I ON I.NRACESSONFE=G.NRACESSONFE
        WHERE  G.DTUTILIZAXML IS NOT NULL AND I.CDFISOPEITEM IS NOT NULL
        GROUP  BY G.NRACESSONFE)
      WHERE CFOP IS NOT NULL
      GROUP BY CFOP HAVING COUNT(*) >= :b
    """, b=base_minima)
    novo = {}
    for cfop, tot, com_af in cur.fetchall():
        p = float(com_af or 0) / float(tot)
        nat = (COMPRA if p >= _PISO_COMPRA else
               SEM_AF if p <= _TETO_SEM_AF else DUVIDA)
        novo[cfop] = (nat, round(p * 100, 1), int(tot))
    return novo
