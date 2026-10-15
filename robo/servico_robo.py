"""
robo/servico_robo.py - O ROBO AUTONOMO do AI Fiscal Hub.

O que faz, sozinho, a cada rodada:
  1. Busca notas de INTEGRACAO novas (estado 'I', compra, nao aprovadas).
  2. Para cada item da nota:
       - ja tem De/Para?  usa o que existe.
       - senao, roda o matcher contra os itens da AF do fornecedor:
           * score >= LIMIAR_AUTO  -> grava De/Para sozinho (alta certeza).
           * senao                 -> joga na FILA DE REVISAO (AIHUB_PENDENCIA)
                                       para a pessoa decidir no painel.
  3. Se TODOS os itens ficaram resolvidos -> confirma o recebimento
     (NF_BAIXA_AF -> VERIFICA_INCONS_NF -> CONFIRMACAO_AUTOMATICA_NF), levando
     a nota de 'I' para 'P' (pendente de aprovacao humana, na alcada).
  4. Registra tudo em AIHUB_PROC_LOG.

Modos:
  python servico_robo.py            -> uma rodada e sai (ideal p/ Tarefa Agendada)
  python servico_robo.py --loop     -> fica rodando a cada AIHUB_POLL segundos
  python servico_robo.py --dry       -> NAO grava nem confirma (so mostra o que faria)

Conexao e comportamento vem de variaveis de ambiente (ver config/settings.py):
  TEKNISA_DB_USER / TEKNISA_DB_PASS / TEKNISA_DB_DSN
  AIHUB_MODO_ESCRITA=1 -> AUTORIZA gravar. Sem isto, o robo roda em simulacao,
                          igual ao painel. E a MESMA chave nos dois.
  AIHUB_POLL=300     -> intervalo do --loop (segundos)
  AIHUB_LOTE=25      -> quantas notas por rodada

DUAS TRAVAS, INDEPENDENTES, e as duas precisam estar ligadas para o robo agir:
  1. AIHUB_MODO_ESCRITA=1  (no atalho/agendador) - autoriza escrever
  2. MODO_AUTO='1'         (em AIHUB_CONFIG, pelo painel) - autoriza automatizar
Faltando qualquer uma, o robo so observa e enche a fila humana.
"""
from __future__ import annotations
import datetime
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import oracledb
from core.ai_matcher import (_sim, sim_pref_generico, _tem_grama, candidatos,
                             escolher, pode_auto, LIMIAR_AUTO, LIMIAR_SUGESTAO)

DB_USER = os.getenv("TEKNISA_DB_USER", "AIHUB")
DB_PASS = os.getenv("TEKNISA_DB_PASS", "")
# SEM DSN PADRAO: antes o default apontava para o banco de HOMOLOGACAO, entao
# rodar sem a variavel conectava no ambiente errado sem avisar.
DB_DSN = os.getenv("TEKNISA_DB_DSN", "").strip()
# MESMA CHAVE DO PAINEL. Sem ela o robo roda em simulacao (dry), mesmo chamado
# direto pela Tarefa Agendada. Antes so o painel respeitava esta variavel: quem
# rodasse o RODAR_ROBO.bat gravava de verdade, sem nenhum aviso, e a unica trava
# era o MODO_AUTO do banco. Nasce DESLIGADA de proposito - autorizar escrita tem
# que ser um ato explicito.
MODO_ESCRITA = os.getenv("AIHUB_MODO_ESCRITA", "0") == "1"
POLL = int(os.getenv("AIHUB_POLL", "300"))
LOTE = int(os.getenv("AIHUB_LOTE", "25"))
# Ate quantos dias para tras a fila de importacao olha. Ver buscar_para_importar:
# e desempenho, nao regra de negocio - devolve as mesmas notas e evita ordenar a
# tabela inteira. Generosa (6x os 30 dias em que a AF ainda vive, achado 49).
JANELA_FILA_DIAS = int(os.getenv("AIHUB_JANELA_FILA", "180"))
# alcanca a nota fresca (sem CDTIPOOPER) pelo CFOP. Liga por padrao, e o
# desligamento existe para o dia em que alguem precisar voltar atras sem
# mexer no codigo: AIHUB_CFOP_FILA=0
_CFOP_LIGADO = os.getenv("AIHUB_CFOP_FILA", "1") == "1"
OPERADOR = os.getenv("AIHUB_OPERADOR", "AIHUB_ROBO")
# estados de recusa no historico do XML; ver nota_bloqueada()
_HIST_RECUSA = ("B", "X")
# Teto diario de confirmacoes automaticas (freio de seguranca). 0 = sem teto.
TETO_DIA = int(os.getenv("AIHUB_TETO_DIA", "200"))
# Para o robo apos N erros seguidos na mesma rodada (evita repetir falha em lote).
MAX_ERROS = int(os.getenv("AIHUB_MAX_ERROS", "5"))


# --------------------------------------------------------------------------- #
# O QUE O ROBO ATENDE, POR TIPO DE OPERACAO DE ENTRADA
#
# Fica aqui, num lugar so, em vez de espalhado como CDTIPOOPER='01' pelas
# consultas. Assim da para responder "o robo trata este tipo?" lendo 5 linhas.
#
#   vincula_sozinho -> pode gravar De/Para sem passar por humano
#   confirma        -> pode confirmar o recebimento (mexe em estoque)
#
# ATENCAO: o codigo do tipo NAO e unico - ele se repete entre entrada e saida
# com sentidos opostos ('01' e "NF Compra" na entrada e "NF Venda" na saida).
# Por isso toda consulta usa (IDENTRSAIDOP='E', CDTIPOOPER).
#
# Tipos deliberadamente DE FORA, e por que:
#   08 "NF Produtor Rural"      -> 100% digitadas, nao existe XML para ler.
#                                  Produtor pessoa fisica nao emite NF-e; quem
#                                  emite a nota de entrada e a propria empresa.
#   29 "NF P Rural nao Movta Estoque" -> 3 notas, a ultima de 2017. Morto.
#   40 "NF Compra sem AF"       -> sem AF nao ha contra o que casar os itens.
# --------------------------------------------------------------------------- #
POLITICA_TIPO = {
    # NF Compra (Mod 55): o caso normal, automacao completa
    "01": {"vincula_sozinho": True,  "confirma": True},
    # NF Bonificacoes (Mod 55): mercadoria recebida sem cobranca.
    # A operacao confirmou em 31/07/2026 que o robo pode tratar por completo:
    # "as bonificacoes o robo pode fazer sim". Tem AF obrigatoria, igual a
    # compra, e e sempre liberada na operacao 06.
    # O Teknisa ja classifica sozinho como 06 quando o XML vem com CFOP 5910 ou
    # 6910 (tabela CFOPXMLTPOPER) - por isso identificamos pelo TIPO e nao pelo
    # CFOP: pegar o tipo cobre os dois CFOPs e respeita a parametrizacao deles.
    "06": {"vincula_sozinho": True,  "confirma": True},
}
TIPOS_ATENDIDOS = tuple(sorted(POLITICA_TIPO))

# Origem "Manual" (coluna INTG_NOTAFISCAL.DSTAGORIGFILI, a "Origem Filial (XML)"
# da tela de Importacao de XML do Teknisa). Decisao da operacao (agosto/2026): a
# nota MANUAL fica FORA da automacao. Motivo: na entrada manual o estoque so pode
# entrar quando a mercadoria chega FISICAMENTE, e por isso e a gerente quem lanca,
# no momento do recebimento. Se o robo importasse/confirmasse sozinho, daria
# entrada em produto que ainda nao chegou. Alem disso, "manual" nao e um lote
# homogeneo de compra (inclui transferencia, reembolso, RH, guias, servico) e 72%
# nao tem AF - nao ha o que casar nem contra o que validar.
ORIGEM_MANUAL = "Manual"


def _nao_manual(alias):
    """Fragmento SQL: exclui notas de origem MANUAL. Nulo (automatica) passa."""
    return ("(%s.DSTAGORIGFILI IS NULL OR %s.DSTAGORIGFILI <> '%s')"
            % (alias, alias, ORIGEM_MANUAL))


def politica(cd_tipo):
    """Politica do tipo de operacao. Tipo desconhecido = nada automatico."""
    return POLITICA_TIPO.get((cd_tipo or "").strip(),
                             {"vincula_sozinho": False, "confirma": False})


def log(msg):
    print(time.strftime("%d/%m %H:%M:%S"), msg, flush=True)


def conn():
    if not DB_DSN or not DB_PASS:
        raise RuntimeError(
            "Faltam TEKNISA_DB_DSN e/ou TEKNISA_DB_PASS. Configure no .bat "
            "antes de rodar (evita conectar no banco errado).")
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)


def ler_modo_auto(cur):
    """Le o modo do banco (o painel controla). FAIL-SAFE: se NAO conseguir ler a
    config (sem grant, tabela fora do ar, erro de rede), assume DESLIGADO. Nunca
    ligar o automatico por acidente - so liga se o banco disser explicitamente."""
    try:
        cur.execute("SELECT VALOR FROM AIHUB.AIHUB_CONFIG WHERE CHAVE='MODO_AUTO'")
        r = cur.fetchone()
        if r is not None:
            return str(r[0]).strip() == "1"
        # CHAVE AUSENTE = o banco NAO disse que pode. Antes este caminho caia no
        # ambiente (AIHUB_MODO_AUTO, que nascia LIGADO), entao uma instalacao sem
        # a carga inicial da AIHUB_CONFIG - ou a chave apagada - fazia o robo
        # gravar e confirmar sozinho. Isso contrariava a propria regra desta
        # funcao: so liga se o banco disser explicitamente.
        log("AVISO: MODO_AUTO nao existe em AIHUB_CONFIG. Assumindo DESLIGADO "
            "(rode a carga inicial da instalacao ou ligue pelo painel).")
        return False
    except Exception as e:
        log("AVISO: nao consegui ler MODO_AUTO (%s). Assumindo DESLIGADO." % str(e)[:80])
        return False


def ler_modo_importa(cur):
    """Importacao automatica (chamar a GERA nativa). DESLIGADA por padrao,
    porque gera nota fiscal - so liga pelo painel apos validar em producao.
    FAIL-SAFE: qualquer duvida -> desligado."""
    try:
        cur.execute("SELECT VALOR FROM AIHUB.AIHUB_CONFIG WHERE CHAVE='MODO_IMPORTA'")
        r = cur.fetchone()
        if r is not None:
            return str(r[0]).strip() == "1"
    except Exception as e:
        log("AVISO: nao consegui ler MODO_IMPORTA (%s). Assumindo DESLIGADO." % str(e)[:80])
    return False


# --------------------------------------------------------------------------- #
# IMPORTACAO (chama a rotina nativa GERA_NOTAFISCAL_CORPO_NFE - a mesma da tela)
# --------------------------------------------------------------------------- #
def _recorte_lote(lote):
    """O recorte por lote de chegada: devolve (fragmento_SQL, binds).

    UMA FUNCAO SO DECIDE, e por um motivo concreto: a primeira versao tinha
    duas - uma montava o SQL olhando "tem lote?" e a outra montava os binds
    olhando "o lote e valido?". Com um lote malformado elas divergiam: a
    consulta saia com :lote_ini e o bind nao vinha junto, e o Oracle recusava.
    Enquanto forem duas, ha o dia em que uma muda e a outra nao.

    O valor NUNCA entra no texto da consulta - vira bind. Concatenar data vinda
    da tela dentro de SQL e o caminho classico de injecao, e aqui a origem e um
    parametro de rota.

    Fail-safe: lote que nao da para entender vira 'sem recorte', com aviso, em
    vez de derrubar a rodada. Rodada que morre leva junto o teto diario e o
    contador de erros, que dependem dela terminar."""
    if not lote:
        return "", {}
    try:
        ini = datetime.datetime.strptime(str(lote).strip()[:13], "%Y-%m-%d %H")
    except Exception:
        log("  aviso: lote '%s' nao entendido; usando a fila inteira" % lote)
        return "", {}
    return (" AND G.DTINCLUSAO >= :lote_ini AND G.DTINCLUSAO < :lote_fim ",
            {"lote_ini": ini,
             "lote_fim": ini + datetime.timedelta(hours=1)})


def buscar_para_importar(cur, limite, lote=None):
    """(a documentacao completa segue abaixo)

    'lote' recorta por CHEGADA (INTG_NOTAFISCAL.DTINCLUSAO), no formato
    'AAAA-MM-DD HH'. Serve para validar o sistema em pedacos pequenos: em vez
    de soltar o robo na fila inteira, a pessoa escolhe um lote de 20 e confere
    o resultado antes de continuar.

    ATENCAO AO CAMPO: chegada e DTINCLUSAO, que NAO e a emissao. O filtro de
    periodo das telas e por emissao, e confundir os dois ja fez uma tela
    devolver zero com o contador dizendo 23 (achado do lote na tela "Falta a
    AF")."""
    """NF de compra na integracao que ainda NAO viraram lancamento.

    POR QUE O FILTRO DE TIPO FICA, mesmo custando cobertura.
    Medido em producao (06/08/2026), notas paradas sem AF nos ultimos 30 dias:
        com tipo 01/06 (o robo alcanca) : 109  (26%)
        SEM tipo (o robo NAO alcanca)   : 311  (74%)
    Ou seja, o robo so chega em 1 de cada 4 notas que precisariam de vinculo.
    E tentador tirar o filtro. Nao da:

    Sem o tipo nao se sabe se a nota e COMPRA. Testei a natureza da operacao
    (DSNATOPER) como substituto e ela NAO discrimina: "VENDA DE MERCADORIA"
    aparece em 1.346 compras e tambem em notas que nao sao compra. O motivo e
    que a natureza e escrita do ponto de vista do FORNECEDOR - para ele toda
    compra nossa e uma venda. Devolucao, transferencia e comodato chegam com
    texto parecido.

    Vincular uma AF de compra numa devolucao baixaria um pedido que nao foi
    atendido. Entre perder cobertura e correr esse risco, fica a cobertura.

    A nota sem tipo NAO fica orfa: ela aparece na tela "Aguardando importacao"
    do painel, com a AF ja sugerida, e uma pessoa confirma em um clique. O
    automatico cobre o que e seguro; o resto vira um clique, nao trabalho.

    A ORDEM E OBRIGATORIA, e a mais nova vem primeiro.
    Esta consulta tinha FETCH FIRST SEM ORDER BY - o mesmo defeito ja corrigido
    em buscar_notas_novas, que nao tinha sido aplicado aqui. Sem ordenacao o
    Oracle devolve QUALQUER N linhas, e na pratica devolvia as mais antigas da
    tabela: medido em 11/08/2026, as 25 notas da fila tinham emissao entre
    26/10/2018 e 02/09/2019, e NENHUMA com menos de 30 dias.
    Isso torna a rodada inteira inutil: pelo achado 49, acima de 30 dias 100%
    das AFs ja foram consumidas, entao af_ainda_valida barra tudo com
    AF_VENCIDA. O robo trabalhava sempre o passivo morto e nunca alcancava a
    nota fresca, que e a unica em que ele consegue agir (ate 7 dias, so 5%
    barram). Era o motivo de "rodei o robo e nao aconteceu nada".

    A JANELA EXISTE POR DESEMPENHO, e nao muda o resultado.
    Ordenar sem indice obriga o banco a varrer e classificar a tabela inteira
    antes de pegar as primeiras linhas. Medido em homologacao, pedindo 10
    notas: sem janela 36.543 ms; com 180 dias 855 a 2.045 ms - e as notas
    devolvidas sao AS MESMAS. A janela e generosa de proposito (6 vezes o
    limite de 30 dias em que a AF ainda vive, achado 49), entao nao descarta
    nota que o robo conseguiria resolver; o passivo mais antigo nao e
    automatizavel (achado 50) e continua visivel nas telas do painel.

    CUIDADO: o ganho vem do PLANO, nao do tamanho da janela. Medido: 90 dias
    da 14.318 ms e 365 dias da 32.016 ms - com mais linhas estimadas o Oracle
    troca o nested loop pelo HASH JOIN ANTI, que e o rapido. Depender disso e
    fragil, e por isso existe IX_INTGNF_EMISSAO em sql/CRIAR_INDICES.sql: com
    o indice a ordenacao sai do indice e o tempo deixa de depender de sorte."""
    # NAO da para filtrar entrada/saida aqui: a INTG_NOTAFISCAL nao tem a coluna
    # IDENTRSAIDOP (conferido no banco). Nao e problema - essa tabela e a
    # integracao dos XML RECEBIDOS, entao e entrada por natureza. Nas consultas
    # sobre NOTAFISCAL o filtro IDENTRSAIDOP='E' e obrigatorio (ver abaixo).
    # os MESMOS tipos que a fila de processamento atende (01 compra, 06
    # bonificacao). Antes importava so '01', entao bonificacao nunca era gerada
    # pela importacao automatica, embora a politica autorize confirma-la - medido
    # no banco: 313 notas 06 na integracao ainda sem lancamento.
    _tipos = ",".join("'%s'" % t for t in TIPOS_ATENDIDOS)
    _cond, _bl = _recorte_lote(lote)     # decidido UMA vez, usado nas duas fontes
    cur.execute("""
        SELECT G.NRACESSONFE, G.DTEMISSAO
        FROM   TEKNISA.INTG_NOTAFISCAL G
        WHERE  G.CDTIPOOPER IN (""" + _tipos + """)
          AND  """ + _nao_manual("G") + """
          AND  G.DTEMISSAO >= SYSDATE - :dias
          AND  NOT EXISTS (SELECT 1 FROM TEKNISA.NOTAFISCAL N
                           WHERE N.CDFILIAL=G.CDFILIAL AND N.NRLANCTONF=G.NRLANCTONF)
          """ + _cond + """
        ORDER  BY G.DTEMISSAO DESC NULLS LAST,   -- a mais nova primeiro
                  G.CDFILIAL, G.NRLANCTONF       -- desempate deterministico
        FETCH  FIRST :lim ROWS ONLY
    """, lim=limite, dias=JANELA_FILA_DIAS, **_bl)
    com_tipo = [(r[0], True, r[1]) for r in cur.fetchall()]

    # ---------------------------------------------------------------- #
    # SEGUNDA FONTE: a nota FRESCA, que ainda nao tem CDTIPOOPER.
    # ---------------------------------------------------------------- #
    # POR QUE ISTO PRECISOU EXISTIR. O filtro de tipo, sozinho, fazia o robo
    # nunca ver a nota nova. Medido em producao (21/08/2026) no lote das 11h,
    # uma hora depois de chegar: **0 de 37 notas tinham CDTIPOOPER**. Duas
    # horas depois, 53 de 56 tinham - porque o tipo nasce da acao humana na
    # tela "Relacionar AF" (achado 101), que e justamente o trabalho que este
    # robo existe para poupar. O robo so alcancava a nota DEPOIS de a pessoa
    # ter feito a parte dificil.
    #
    # O CFOP quebra o circulo, e ele ja estava construido e medido (achado 82,
    # `core/cfop_natureza.py`, 95,4% de acerto) - so nunca tinha sido ligado
    # aqui. Medido nos mesmos lotes: das 37 notas das 11h ele disse COMPRA em
    # 36 e SEM_AF em 1; das 13 que ninguem tocou em 18 horas, disse SEM_AF nas
    # 13 (eram servico e devolucao). Avaliando as 19 que ja tinham filial, o
    # motor resolveria **16 sozinho, com 16 acertos e nenhuma divergencia**
    # contra a escolha que as pessoas fizeram depois.
    #
    # SO 'COMPRA' ENTRA: 'DUVIDA' e 'SEM_AF' ficam de fora, e CFOP que a tabela
    # medida nao conhece nunca vira COMPRA por conta propria.
    #
    # E ELA NAO E IMPORTAVEL (por enquanto). Vem marcada com pode_importar =
    # False: o robo VINCULA a AF, que e o trabalho pesado, e deixa a importacao
    # para quando a nota tiver tipo. Medido: importar sem tipo praticamente nao
    # acontece (5 de 7.765 em 30 dias, achado 101), e nao da para provar em
    # leitura que a nativa aceitaria. Alargar o vinculo e ganho comprovado;
    # alargar a importacao seria aposta.
    _so_tipo = [(ch, True) for ch, _p, _d in com_tipo]
    if not _CFOP_LIGADO:
        return _so_tipo
    try:
        from core.cfop_natureza import natureza, COMPRA
    except Exception:
        return _so_tipo
    try:
        cur.execute("""
            SELECT G.NRACESSONFE, G.DTEMISSAO,
                   (SELECT MIN(I.CDFISOPEITEM) FROM TEKNISA.INTG_ITEMPRODUTO I
                     WHERE I.NRACESSONFE = G.NRACESSONFE)
            FROM   TEKNISA.INTG_NOTAFISCAL G
            WHERE  G.CDTIPOOPER IS NULL
              AND  G.CDFILIRECE IS NOT NULL
              AND  """ + _nao_manual("G") + """
              AND  G.DTEMISSAO >= SYSDATE - :dias
              AND  G.DTLANCAMENNF IS NULL
              AND  G.DTUTILIZAXML IS NULL
              AND  NOT EXISTS (SELECT 1 FROM TEKNISA.INTG_ITEMPRODUTO I
                                WHERE I.NRACESSONFE = G.NRACESSONFE
                                  AND I.NRAUTOFORN IS NOT NULL)
              """ + _cond + """
            ORDER  BY G.DTEMISSAO DESC NULLS LAST, G.CDFILIAL, G.NRLANCTONF
            FETCH  FIRST :lim ROWS ONLY
        """, lim=limite, dias=JANELA_FILA_DIAS, **_bl)
        novas = cur.fetchall()
    except Exception as e:
        log("  aviso: nao consegui ler a fila sem tipo (%s)" % str(e)[:80])
        return _so_tipo
    # A FILIAL E OBRIGATORIA e por isso esta no WHERE. Sem ela nao ha como
    # saber qual unidade recebeu, e o pedido e por unidade. Medido: no lote das
    # 11h, 19 notas tinham filial e 18 nao - e a correlacao com "ja tem AF" foi
    # PERFEITA (19 sem filial + sem AF, 18 com filial + com AF), ou seja, a
    # filial chega junto com a acao humana. Nao da para deduzir do CNPJ: um
    # CNPJ do grupo cobre 312 filiais.
    por_cfop = [(ch, False, dtem) for ch, dtem, cfop in novas
                if natureza(cfop)[0] == COMPRA]
    # O LIMITE QUE O CHAMADOR PEDIU TEM DE VALER. Somar as duas listas devolvia
    # ate o DOBRO: o ensaio pediu 5 notas, recebeu 10, e passou de 10s para 68s
    # sem ninguem ter mexido nele. Quem pede N espera N.
    #
    # E o criterio do corte e a EMISSAO, nao a origem da lista: as duas fontes
    # ja vem ordenadas pela mais nova, e trabalhar a nota fresca e onde existe
    # resultado (achado 88 e 97). Cortar por fonte deixaria a nota de ontem de
    # fora para caber uma de tres meses atras.
    juntas = com_tipo + por_cfop
    juntas.sort(key=lambda x: (x[2] is None, x[2]), reverse=True)
    juntas = juntas[:limite]
    n_novas = sum(1 for _c, pode, _d in juntas if not pode)
    if n_novas:
        log("  fila: %d nota(s), sendo %d nova(s) alcancada(s) pelo CFOP"
            % (len(juntas), n_novas))
    return [(ch, pode) for ch, pode, _d in juntas]


def _gera_igual_xml(cur):
    """Valor do parametro do CLIENTE "Gera Nota Fiscal Identica ao Arquivo XML"
    (PARAMGERAL.IDGERNFIGUALXML).

    As DUAS procedures nativas que chamamos declaram esse parametro como
    PARAMGERAL.IDGERNFIGUALXML%TYPE - nao e constante nossa:
        GERA_NOTAFISCAL_CORPO_NFE(P_NRACESSONFE, P_GERAIGUALXML, ...)
        VERIFICA_INCONS_NF(P_NRACESSONFE, P_IDGERNFIGUALXML, ...)
    e a GERA muda de comportamento em dois pontos (IF P_GERAIGUALXML = 'S').

    Passavamos 'N' fixo nos dois. Hoje coincide (medido em producao: 'N'), mas
    se o cliente ligar o parametro o robo continuaria com 'N' e passaria a
    divergir da tela nativa em silencio.

    Fail-safe: nao conseguir ler devolve 'N', que e o comportamento anterior e
    o mais restritivo."""
    try:
        cur.execute("SELECT NVL(IDGERNFIGUALXML,'N') FROM TEKNISA.PARAMGERAL")
        r = cur.fetchone()
        return (r[0] if r else "N") or "N"
    except Exception:
        return "N"


def itens_sem_depara(cur, chave):
    """Itens da nota (ainda na integracao) que NAO tem produto interno.

    Uma consulta so, com LEFT JOIN, em vez de uma por item: a rota roda com a
    pessoa esperando, e cada ida ao banco custa ~150 ms de rede.

    Fail-safe ao contrario do usual, e de proposito: erro ao conferir devolve
    lista VAZIA, ou seja, "segue e deixa a nativa decidir". Aqui o fail-safe
    seguro e nao barrar - quem barra de verdade e o Teknisa; esta funcao so
    evita a espera quando a recusa e certa."""
    try:
        cur.execute("""
            SELECT I.NRSEQITPROD, I.CDPRODUTO, I.NMPRODUTO
            FROM   TEKNISA.INTG_ITEMPRODUTO I
            JOIN   TEKNISA.INTG_NOTAFISCAL  G ON G.NRACESSONFE = I.NRACESSONFE
            WHERE  I.NRACESSONFE = :c
              AND  NOT EXISTS (SELECT 1 FROM TEKNISA.PRODFORNXML D
                                WHERE D.CDFORNECED   = G.CDFORNECED
                                  AND D.CDPRODUTOXML = I.CDPRODUTO
                                  AND D.IDATIVO      = 'S')
            ORDER  BY I.NRSEQITPROD
        """, c=chave)
        return cur.fetchall()
    except Exception:
        return []


def nota_bloqueada(cur, chave):
    """A OPERACAO JA RECUSOU esta nota? Devolve (True/False, motivo).

    Os estados de recusa saem do proprio dado de producao (HISTXMLNFE dos
    ultimos 20 dias):
      B = "Arquivo XML bloqueado pelo usuario."   (269 eventos)
      X = "Desconhecimento da Operacao.",
          "Operacao nao realizada - ...",
          "Nota fiscal nao recebida pela filial - ..."
    Os demais (N, S) sao eventos normais: importacao, inclusao, aprovacao,
    troca de filial e o proprio DESBLOQUEIO - que entra como 'N' e por isso
    devolve a nota ao trabalho sem precisar de regra extra (achado 3).

    POR QUE ESTA TRAVA EXISTE, e ela e das mais importantes que ja entraram.
    Medido em producao (20/08/2026), sobre as 300 notas mais novas da fila do
    robo: 236 (79%) estao com o XML bloqueado por uma pessoa AGORA, 213 (71%)
    foram canceladas, estornadas ou tiveram a operacao desconhecida, e 263
    (88%) caem em pelo menos um dos dois casos. Sem esta trava, das 9 notas que
    o motor resolveria sozinho naquele momento, 6 eram nota MORTA - caso real:
    a NF 006160988 foi bloqueada em 19/08 e estornada pela NF 6162602, e o
    motor escolheria a AF 0001475066 para ela.

    O ESTRAGO NAO E "gravar a toa". A AF e perecivel e duas notas disputam o
    mesmo pedido (achados 49 e 104): pendurar a AF numa nota que a operacao ja
    matou pode tirar aquela AF da nota VIVA que veio no lugar. No caso acima, a
    AF ficaria presa na nota estornada em vez de ir para a 6162602.

    A REGRA E O ULTIMO EVENTO, por NRSEQHISTOR - a mesma ordenacao que o painel
    ja usa em /api/nota. Nao da para olhar "existe algum bloqueio": o
    desbloqueio e um evento posterior, e nota liberada volta a ser trabalho.
    Ordenar por DTHISTORICO nao serve: varios eventos caem no mesmo instante.

    FAIL-SAFE FECHADO: nao conseguir ler o historico responde BLOQUEADA. Uma
    trava que se abre quando falha nao e trava."""
    try:
        cur.execute("""
            SELECT IDSTATUS, SUBSTR(DSOPERACAO, 1, 90), CDOPERADOR
            FROM   TEKNISA.HISTXMLNFE
            WHERE  NRACESSONFE = :c
            ORDER  BY NRSEQHISTOR DESC
            FETCH  FIRST 1 ROWS ONLY
        """, c=chave)
        r = cur.fetchone()
    except Exception as e:
        return True, "nao consegui conferir o historico do XML (%s)" % str(e)[:60]
    if not r:
        return False, ""                 # sem historico: nada recusou
    st = (r[0] or "").strip().upper()
    if st not in _HIST_RECUSA:
        return False, ""
    ds = (r[1] or "").strip() or ("status %s" % st)
    quem = (r[2] or "").strip()
    return True, ("a operação já recusou esta nota: %s%s"
                  % (ds, (" (operador %s)" % quem) if quem
                     and quem != "AUTOMATICO" else ""))


def importar_uma(con, chave):
    """Chama a GERA nativa numa nota. Importa se passar nas regras (AF etc.);
    se barrar, o proprio Teknisa registra a inconsistencia. Retorna o status.

    ANTES DE IMPORTAR, confere se a AF ja gravada continua viva. A AF e
    perecivel: entre gravar e importar, outra entrega pode consumi-la, e ai a
    importacao baixaria um pedido ja atendido. Medido em producao: 11.034 notas
    esperam com AF gravada, e quase metade ja esta com a AF morta."""
    # TRAVA PROPRIA. Esta funcao CRIA DOCUMENTO FISCAL (chama a GERA nativa).
    # Hoje os tres chamadores ja barram (rodar_importacao pelo dry, e as duas
    # rotas do painel pelo MODO_ESCRITA), mas depender de quem chama lembrar e
    # fragil para uma operacao deste peso: um chamador novo furaria a trava sem
    # ninguem perceber. Aqui ela nao depende de mais ninguem.
    if not MODO_ESCRITA:
        log("  nota %s NAO importada: modo consulta (AIHUB_MODO_ESCRITA=0)."
            % chave[-8:])
        return "MODO_CONSULTA"
    cur = con.cursor()
    try:
        # ANTES DE QUALQUER COISA: a operacao ja recusou esta nota?
        # Vem primeiro de proposito. Perguntar "a AF ainda vale" numa nota que
        # foi estornada e responder a pergunta errada com capricho.
        morta, porque = nota_bloqueada(cur, chave)
        if morta:
            log("  nota %s NAO importada: %s" % (chave[-8:], porque))
            return "RECUSADA_PELA_OPERACAO"
        from core.af_matcher import af_ainda_valida
        ok, mortas = af_ainda_valida(cur, chave)
        if not ok:
            # DIZER QUEM LEVOU O PEDIDO, e nao so "a AF venceu". Medido em
            # producao: em 100% dos casos outra nota ja lancada consumiu a AF,
            # quase sempre do mesmo fornecedor e com 1 ou 2 dias de diferenca.
            # Sem isso a pessoa recebe um bloqueio sem saber o que fazer.
            from core.af_matcher import quem_baixou_af
            detalhe = ""
            for _af in (mortas or []):
                _nf, _dt = quem_baixou_af(cur, _af)
                if _nf:
                    detalhe = (" A AF %s ja foi baixada pela NF %s em %s."
                               % (_af, str(_nf).lstrip("0"), _dt))
                    break
            log("  nota %s NAO importada: a AF %s nao esta mais em aberto com "
                "saldo. Importar agora baixaria pedido ja atendido.%s"
                % (chave[-8:], ", ".join(mortas) or "(nao consegui conferir)",
                   detalhe))
            return "AF_VENCIDA"
        # NAO CHAMAR A NATIVA QUANDO A RESPOSTA JA E CONHECIDA.
        #
        # Medido em homologacao: a GERA leva MINUTOS mesmo quando vai recusar -
        # ela roda a validacao inteira antes de decidir. Item sem De\Para sempre
        # volta com o codigo 014 ("produtos do XML nao relacionados"), e isso
        # nos conseguimos conferir com uma consulta de milissegundos. Entao a
        # pessoa recebe a mesma resposta na hora, em vez de esperar a validacao
        # inteira para ouvir o que ja dava para saber.
        #
        # Conferimos pelo DADO (falta De\Para), nao pela INTG_INCONSISTENCIA: la
        # a linha fica ativa para sempre (achado 1), entao uma inconsistencia
        # antiga ja resolvida faria recusar nota boa.
        faltam = itens_sem_depara(cur, chave)
        if faltam:
            log("  nota %s NAO importada: %d item(ns) sem produto vinculado. "
                "A nativa recusaria com o codigo 014." % (chave[-8:], len(faltam)))
            return "SEM_PRODUTO"
        # P_GERAIGUALXML e o parametro do CLIENTE, nao constante nossa.
        # Os demais ficam no default 'N' de proposito: sao as flags de LIBERAR
        # inconsistencia (P_LIBERAXML, P_LIBERAOPER). O robo nunca passa por
        # cima de trava do Teknisa.
        cur.callproc("TEKNISA.GERA_NOTAFISCAL_CORPO_NFE",
                     keyword_parameters={"P_NRACESSONFE": chave,
                                         "P_GERAIGUALXML": _gera_igual_xml(cur)})
        cur.execute("SELECT MAX(NRLANCTONF) FROM TEKNISA.NOTAFISCAL WHERE NRACESSONFE=:c", c=chave)
        lanc = cur.fetchone()[0]
        con.commit()
        return "IMPORTADA" if lanc else "BLOQUEADA"
    except Exception as e:
        con.rollback()
        log("ERRO import %s: %s" % (chave, e))
        return "ERRO"
    finally:
        cur.close()


def _segundo_lugar(desc, cands, un_xml, un_de, best):
    """O concorrente mais proximo, so para a trilha.

    Roda APENAS nos itens que o robo de fato vinculou (um punhado por rodada),
    nunca na lista inteira: o explicador refaz a comparacao contra todos os
    candidatos, e pagar isso tambem nos itens recusados dobraria o custo da
    rodada sem nada em troca.

    Nunca levanta excecao: trilha nao pode derrubar gravacao."""
    try:
        from core.explicador import explicar
        rk = explicar(desc, cands, un_xml, un_de).get("ranking") or []
        for r in rk:
            if r.get("cd_produto") != best[0]:
                return r
    except Exception:
        pass
    return None


def _auditar_depara(cur, chave, forn, it, desc, best, piso, segundo, n_cands):
    """Registra POR QUE o robo escolheu este produto para este item.

    Ate aqui a trilha guardava a AF (achado 51) mas NAO o De/Para: o vinculo
    era gravado sem nenhum registro do raciocinio. Quem fosse conferir depois
    via o produto escolhido e mais nada - nem o score, nem quem era o segundo
    colocado, nem o piso que valia no dia.

    E o piso MUDA: a pessoa ensina regra por fornecedor, e o limiar passa a
    ser outro. Entao recalcular hoje pode dar resposta diferente da que valeu
    na epoca, e sem este registro ninguem saberia dizer qual das duas foi a
    real. Por isso o numero fica gravado, e nao so reconstruido.

    Guarda tambem o texto LIDO quando a regra mudou o original, senao a conta
    do score nao fecha para quem confere ("por que 94% se os textos diferem?").

    Mesma transacao da gravacao, pelo motivo do _auditar_af: trilha que pode
    faltar nao serve."""
    orig = (it.get("desc_xml") or "").strip()
    lido = ""
    if desc and desc != orig:
        lido = ' | lido como "%s" (regra ensinada)' % desc[:70]
    seg = ""
    if segundo:
        seg = (" | 2o lugar: %s %s %d%%"
               % (segundo.get("cd_produto"), (segundo.get("nome") or "?")[:40],
                  segundo.get("score") or 0))
    det = ('[%s] item %s "%s"%s -> %s %s | score %d%% (piso %d%%)'
           ' | un XML %s | %d candidato(s) avaliado(s)%s'
           % (OPERADOR, it.get("seq") or "?", orig[:70], lido,
              best[0], (best[1] or "")[:40], round(best[2] * 100),
              round(piso * 100), (it.get("un") or "-"), n_cands, seg))
    cur.execute("""
        INSERT INTO AIHUB.AIHUB_PROC_LOG
          (NUMERO_NF, CHAVE_NF, CDFORNECED, STATUS, DETALHE, SCORE_MATCH)
        VALUES ('-', :ch, :fo, 'DEPARA_ROBO', :det, :sc)
    """, ch=chave[:60], fo=(forn or "")[:21], det=det[:3900],
         sc=round(float(best[2]), 4))


def resolver_depara_do_xml(con, chave, dry, detalhe=None):
    """Cria o De/Para dos itens da nota AINDA NAO IMPORTADA, quando o matcher
    tem certeza.

    QUEBRA UM CIRCULO. Ate aqui o matcher so rodava na FILA, ou seja, depois da
    importacao. Mas a importacao precisa da AF, a AF precisa do produto interno,
    e o produto interno vem do De/Para - que so nascia depois de importar. Nota
    de produto novo, entao, nunca saia do lugar.

    Medido em producao (60 notas paradas recentes, 255 itens): 34 itens sem
    De/Para, e o matcher resolveria 11 deles SOZINHO com score alto (ALHO KG,
    BETERRABA KG, LARANJA PERA KG...). Sao 33% das notas travadas por isso.

    Usa a MESMA regra do resto do sistema (pode_auto), entao nao afrouxa nada:
    o que ele grava aqui e o que gravaria depois, so que a tempo de destravar.

    Devolve (quantos_criou, quantos_continuam_sem)."""
    from core.af_matcher import itens_do_xml
    cur = con.cursor()
    criou = faltando = 0
    try:
        cur.execute("""SELECT CDFORNECED FROM TEKNISA.INTG_NOTAFISCAL
                       WHERE NRACESSONFE=:c""", c=chave)
        r = cur.fetchone()
        if not r:
            return 0, 0
        forn = r[0]
        itens = itens_do_xml(cur, chave)
        pendentes = []
        for it in itens:
            if not it["cprod_xml"]:
                continue
            if depara_existente(cur, forn, it["cprod_xml"]):
                continue
            pendentes.append(it)
        if not pendentes:
            return 0, 0
        cands = candidatos_af(cur, forn)
        if not cands:
            return 0, len(pendentes)
        un_de = {c[0]: (c[2] if len(c) > 2 else None) for c in cands}
        # O QUE A PESSOA ENSINOU. As regras agem nas BORDAS, nunca dentro da
        # comparacao: o texto passa por aplicar_sinonimos ANTES, a lista de
        # candidatos por filtrar_proibidos ANTES, e o piso vai como parametro.
        # Assim a regra do matcher continua uma so. Fail-safe: erro ao ler
        # regra devolve lista vazia, e o robo volta ao comportamento padrao.
        try:
            from core.regras import (carregar, aplicar_sinonimos,
                                     filtrar_proibidos, limiar_de)
            regras = carregar(cur)
        except Exception:
            regras = []
        piso = limiar_de(regras, forn) if regras else LIMIAR_AUTO
        for it in pendentes:
            desc = (it.get("desc_xml") or "").strip()
            if not desc:
                faltando += 1
                continue
            if regras:
                desc = aplicar_sinonimos(desc, regras, forn)
                cands_it = filtrar_proibidos(cands, regras, forn,
                                             it.get("cprod_xml"))
            else:
                cands_it = cands
            best, _ = escolher(desc, cands_it, it.get("un"))
            # passa 'cands_it': habilita a excecao do nome generico, quando o
            # melhor e o UNICO candidato que contem as palavras do fornecedor
            auto = bool(best) and pode_auto(desc, best, it.get("un"),
                                            un_de.get(best[0]), cands_it)
            # o piso ensinado, quando existe, so pode APERTAR ou afrouxar de
            # forma explicita: nunca entra sem a pessoa ter testado a regra
            if auto and best[2] < piso:
                auto = False
            # TRILHA OPCIONAL, para a tela de ensaio poder mostrar o RACIOCINIO
            # item a item. Fica aqui, dentro da funcao de verdade, e nao numa
            # copia na rota: ensaio que reimplementa a regra acaba divergindo do
            # robo e passa a mentir sobre ele - foi o que aconteceu com a tela
            # "Simular", que simula uma etapa que quase nao ocorre.
            if detalhe is not None:
                detalhe.append({
                    "seq": it.get("seq"),
                    "cod_fornecedor": it["cprod_xml"],
                    "desc_xml": desc,
                    "un_xml": it.get("un"),
                    "escolhido": (best[0] if best else None),
                    "nome_escolhido": (best[1] if best and len(best) > 1 else None),
                    # O SCORE E FRACAO (LIMIAR_AUTO = 0.92) e a tela escreve '%'
                    # do lado. Sem o x100, um casamento de 95% aparecia como
                    # "1%" e qualquer coisa abaixo de 0,5 como "0%" - numero que
                    # faz a pessoa desconfiar de um vinculo que esta certo. O
                    # resto do painel ja fazia Math.round(score*100).
                    "score": (round(best[2] * 100) if best and len(best) > 2 else None),
                    "auto": auto,
                })
            if auto:
                if dry:
                    log("  [simulacao] criaria De/Para %s -> %s (%s)"
                        % (it["cprod_xml"], best[0], desc[:30]))
                    criou += 1
                elif gravar_depara(cur, best[0], forn, it["cprod_xml"], desc, OPERADOR):
                    criou += 1
                    # a trilha vai JUNTO com o vinculo, na mesma transacao
                    _auditar_depara(cur, chave, forn, it, desc, best, piso,
                                    _segundo_lugar(desc, cands_it, it.get("un"),
                                                   un_de, best),
                                    len(cands_it))
                else:
                    # outra sessao esta com o item; nao conta como resolvido
                    faltando += 1
            else:
                faltando += 1
        if criou and not dry:
            con.commit()
    except Exception as e:
        con.rollback()
        log("  ERRO ao resolver De/Para de %s: %s" % (chave[-8:], str(e)[:110]))
    finally:
        cur.close()
    return criou, faltando


def _auditar_af(cur, chave, afs, n_itens, motivo):
    """Registra em AIHUB_PROC_LOG qual AF o robo pos em qual nota, e por que.

    E a unica forma de, mais tarde, responder "quais notas o robo vinculou?" -
    pergunta que aparece no dia em que alguem achar um vinculo estranho. Sem
    isto, so restaria conferir nota a nota no Teknisa.

    Fica na MESMA transacao da gravacao (nao comita aqui): ou grava a AF e a
    trilha juntas, ou nenhuma das duas. Trilha que pode faltar nao serve.

    NUMERO_NF vai como '-' pelo mesmo motivo do _auditar do painel: em algumas
    instalacoes a coluna e NOT NULL e o INSERT com NULL falharia calado."""
    cur.execute("""
        INSERT INTO AIHUB.AIHUB_PROC_LOG
          (NUMERO_NF, CHAVE_NF, STATUS, DETALHE)
        VALUES ('-', :ch, 'AF_VINCULO_ROBO', :det)
    """, ch=chave[:60],
         det=("[%s] AF %s em %d item(ns) | %s"
              % (OPERADOR, ", ".join(afs), n_itens, motivo))[:3900])


def vincular_af_sozinho(con, chave, dry):
    """Acha e grava a AF desta nota, SE nao houver duvida.

    E o passo que hoje e manual no Teknisa (a pessoa marca a AF numa lista) e
    que este projeto existe para automatizar. Medido em producao sobre 149 notas
    ja importadas: quando o motor decide sozinho, acerta 100% - nenhuma escolha
    errada. Quando ha mais de uma AF possivel ele NAO escolhe: deixa para a
    pessoa decidir no painel.

    Devolve: 'VINCULADA' | 'JA_TINHA' | 'AMBIGUA' | 'SEM_AF' | 'ERRO'."""
    from core.af_matcher import resolver, AUTOMATICA
    # A OPERACAO JA RECUSOU ESTA NOTA? Isto vem antes de tudo, inclusive do
    # De/Para. Nao e so economia de trabalho: a nota costuma ser bloqueada
    # JUSTAMENTE porque o produto nao era aquele ("Produto nao recebido" e um
    # dos motivos vistos em producao), e aprender De/Para dali ensinaria o
    # vinculo errado - que depois vale para todas as notas do fornecedor.
    cur = con.cursor()
    try:
        morta, porque = nota_bloqueada(cur, chave)
    finally:
        cur.close()
    if morta:
        log("  nota %s: %s" % (chave[-8:], porque))
        return "RECUSADA_PELA_OPERACAO"
    # Resolve o De/Para que faltar. Sem o produto interno nao ha como achar a
    # AF, e o matcher so rodaria depois da importacao - que e justamente o que
    # esta travado. Ver a funcao acima.
    resolver_depara_do_xml(con, chave, dry)
    cur = con.cursor()
    try:
        out = resolver(cur, chave)
        if "já está relacionada" in out.get("motivo", ""):
            return "JA_TINHA"
        if out["decisao"] != AUTOMATICA:
            return "AMBIGUA" if out.get("opcoes") else "SEM_AF"
        # 'automatica' vem de dois caminhos: UMA AF para a nota inteira, ou uma
        # AF POR ITEM (entrega que cumpre mais de um pedido - 24.125 notas em
        # producao sao assim). Os dois sao automaticos.
        # monta o plano item a item nos dois casos: uma AF para a nota toda, ou
        # AFs diferentes por item. A gravacao e a mesma, e e atomica.
        from core.af_matcher import gravar_af, itens_do_xml
        if out.get("por_item"):
            plano = [p for p in (out.get("plano") or []) if p.get("af")]
        else:
            af = out["af"]["af"]
            plano = [{"seq": i["seq"], "af": af} for i in itens_do_xml(cur, chave)]
        afs = sorted({p["af"] for p in plano})

        if dry:
            log("  [simulacao] nota %s: %d itens em %d AF(s) (%s)"
                % (chave[-8:], len(plano), len(afs), ", ".join(afs)))
            return "VINCULADA"

        n, recusa = gravar_af(cur, chave, plano)
        if recusa:
            con.rollback()
            log("  nota %s NAO vinculada: %s" % (chave[-8:], recusa))
            return "AMBIGUA"
        # TRILHA: registra QUE AF foi posta em QUE nota, e por que.
        # Sem isto, um erro so seria descoberto quando alguem reclamasse, e nao
        # haveria como saber QUAIS notas o robo tocou para desfazer. Vai na
        # MESMA transacao da gravacao: ou os dois acontecem, ou nenhum.
        _auditar_af(cur, chave, afs, n, out.get("motivo", ""))
        con.commit()
        log("  nota %s: %d itens ligados a %d AF(s) (%s)"
            % (chave[-8:], n, len(afs), ", ".join(afs)))
        return "VINCULADA"
    except Exception as e:
        con.rollback()
        log("  ERRO ao vincular AF em %s: %s" % (chave[-8:], str(e)[:120]))
        return "ERRO"
    finally:
        cur.close()


def rodar_importacao(con, limite, dry=False, vincula_af=True, lote=None):
    """Importa as notas pendentes. ANTES de importar, tenta resolver a AF
    sozinho: sem AF a importacao nativa barra a nota, e ela fica parada para
    sempre (medido em producao: 662 notas paradas ha mais de 30 dias)."""
    cur = con.cursor()
    chaves = buscar_para_importar(cur, limite, lote)
    cur.close()
    # AF_VENCIDA: a AF ja gravada morreu antes da importacao (ver importar_uma)
    # MODO_CONSULTA: a trava propria do importar_uma recusou (nao deveria
    # acontecer aqui, porque o dry ja barra antes - mas o contador nao pode
    # estourar se acontecer)
    # SEM_PRODUTO: a nota tem item sem De\Para e a nativa recusaria (codigo
    # 014). O importar_uma corta antes de chamar a procedure, que leva minutos
    # mesmo para recusar. Sem esta chave o contador estoura com KeyError - e o
    # comentario acima ja avisava que isso nao pode acontecer.
    # RECUSADA_PELA_OPERACAO entra nos DOIS contadores: o status sai tanto do
    # vincular_af_sozinho quanto do importar_uma. Faltar a chave em um deles
    # derruba a rodada com KeyError - e a rodada e quem controla o teto diario
    # e a contagem de erros, entao a queda leva as travas junto. E a segunda
    # vez que este mesmo descuido aparece (ver SEM_PRODUTO, acima).
    r = {"IMPORTADA": 0, "BLOQUEADA": 0, "ERRO": 0, "AF_VENCIDA": 0,
         "MODO_CONSULTA": 0, "SEM_PRODUTO": 0, "RECUSADA_PELA_OPERACAO": 0,
         "AGUARDA_TIPO": 0}
    af = {"VINCULADA": 0, "JA_TINHA": 0, "AMBIGUA": 0, "SEM_AF": 0, "ERRO": 0,
          "RECUSADA_PELA_OPERACAO": 0}
    for item in chaves:
        # a fila agora devolve (chave, pode_importar). Aceita a chave crua
        # tambem, para quem chamar esta funcao com uma lista propria.
        ch, pode_importar = item if isinstance(item, tuple) else (item, True)
        if vincula_af:
            af[vincular_af_sozinho(con, ch, dry)] += 1
        if dry:
            continue
        if not pode_importar:
            # nota sem CDTIPOOPER: a AF ja foi vinculada acima, que e o
            # trabalho pesado. A importacao fica para quando a nota tiver
            # tipo - ver o comentario em buscar_para_importar.
            r["AGUARDA_TIPO"] += 1
            continue
        r[importar_uma(con, ch)] += 1
    if vincula_af:
        log("AF: %d vinculadas sozinho, %d ja tinham, %d ambiguas (humano), "
            "%d sem AF, %d erro"
            % (af["VINCULADA"], af["JA_TINHA"], af["AMBIGUA"], af["SEM_AF"], af["ERRO"]))
    if not dry:
        log("Importacao: %d importadas, %d bloqueadas (falta AF/cadastro), "
            "%d sem produto vinculado, %d com AF vencida, %d erro"
            % (r["IMPORTADA"], r["BLOQUEADA"], r["SEM_PRODUTO"],
               r["AF_VENCIDA"], r["ERRO"]))
        if r["AGUARDA_TIPO"]:
            log("  %d nota(s) com a AF ja vinculada, aguardando o tipo de "
                "operacao para entrar" % r["AGUARDA_TIPO"])
    return r


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def buscar_notas_novas(cur, limite):
    """NF de compra, integradas ('I'), nao aprovadas, ainda nao confirmadas
    pelo robo. So as que existem na integracao (vieram de XML).

    BUG CORRIGIDO (28/07/2026): esta consulta usava FETCH FIRST SEM ORDER BY.
    Sem ordenacao o Oracle devolve QUALQUER 25 linhas - na pratica quase sempre
    as MESMAS. Se essas 25 estivessem travadas esperando vinculo humano, o robo
    ficava reprocessando eternamente o mesmo punhado e as demais notas NUNCA
    eram alcancadas. Era exatamente o sintoma de "sempre as mesmas notas em
    'A processar' e nunca andam".

    Agora: (1) ordena de forma DETERMINISTICA e (2) da PRIORIDADE as notas que
    conseguem avancar - as que estao esperando vinculo humano vao para o fim da
    fila, para nao consumir o lote inteiro sem produzir nada."""
    _tipos = ",".join("'%s'" % t for t in TIPOS_ATENDIDOS)   # constantes do codigo
    cur.execute("""
        SELECT N.CDFILIAL, N.NRLANCTONF, N.NRACESSONFE, N.CDFORNECED, N.CDTIPOOPER,
               CASE WHEN EXISTS (SELECT 1 FROM AIHUB.AIHUB_PENDENCIA P
                                 WHERE P.CDFILIAL = N.CDFILIAL
                                   AND P.NRLANCTONF = N.NRLANCTONF
                                   AND P.STATUS = 'ABERTO')
                    THEN 1 ELSE 0 END AS ESPERANDO_HUMANO
        FROM   TEKNISA.NOTAFISCAL N
        WHERE  N.IDENTRSAIDOP='E' AND N.IDSTATUS='I'
          AND  N.CDTIPOOPER IN (""" + _tipos + """)
          AND  N.DTAPROVACAO IS NULL
          AND  EXISTS (SELECT 1 FROM TEKNISA.INTG_NOTAFISCAL G
                        WHERE G.CDFILIAL=N.CDFILIAL AND G.NRLANCTONF=N.NRLANCTONF
                          AND """ + _nao_manual("G") + """)
          AND  NOT EXISTS (SELECT 1 FROM AIHUB.AIHUB_PROC_LOG L
                            WHERE L.CHAVE_NF=N.NRACESSONFE AND L.STATUS='CONFIRMADA')
        ORDER  BY ESPERANDO_HUMANO,        -- primeiro as que podem andar
                  N.DTEMISSAO,             -- depois as mais antigas
                  N.CDFILIAL, N.NRLANCTONF
        FETCH FIRST :lim ROWS ONLY
    """, lim=limite)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _texto(v):
    """Colunas de texto longo (CLOB) voltam como objeto LOB, nao como string -
    e ai o matcher compararia com '<LOB>' em vez da descricao do produto.
    Le o conteudo quando for LOB."""
    if v is not None and hasattr(v, "read"):
        try:
            v = v.read()
        except Exception:
            return None
    return v


def itens_da_nota(cur, cd_filial, nr_lancto):
    # SGUNIDADEXML e a unidade que o FORNECEDOR usou no XML (KG, CX, FD...).
    # E o sinal mais forte para escolher o produto certo: bate com a unidade do
    # produto interno em 76,8% dos vinculos que ja existem. Ver core/ai_matcher.
    cur.execute("""
        SELECT NRSEQITPROD, CDPRODEMIS, NMPRODUTOXML, CDPRODUTO, CDMARCA,
               QTITEMPROD, SGUNIDADEXML
        FROM   TEKNISA.ITEMPRODUTO
        WHERE  CDFILIAL=:f AND NRLANCTONF=:n
        ORDER  BY NRSEQITPROD
    """, f=cd_filial, n=nr_lancto)
    cols = [c[0] for c in cur.description]
    return [{c: _texto(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def depara_existente(cur, cd_forn, cprod_xml):
    """Vinculo ATIVO deste item do fornecedor. ORDER BY garante resultado
    DETERMINISTICO (antes, com 2 vinculos ativos, o retorno era aleatorio)."""
    cur.execute("""
        SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML
        WHERE  CDFORNECED=:f AND CDPRODUTOXML=:c AND IDATIVO='S'
        ORDER  BY DTINCLUSAO DESC NULLS LAST, CDPRODUTO
        FETCH FIRST 1 ROWS ONLY
    """, f=cd_forn, c=cprod_xml)
    r = cur.fetchone()
    return r[0] if r else None


def candidatos_af(cur, cd_forn):
    """Fonte UNICA (core.ai_matcher.candidatos): a mesma lista que o painel e a
    IA usam. Antes o robo tinha a sua propria consulta, mais restrita - o painel
    sugeria produtos que o robo nunca consideraria."""
    return candidatos(cur, cd_forn)


def nome_fornecedor(cur, cd_forn):
    cur.execute("SELECT NMRAZSOCFORN FROM TEKNISA.FORNECEDOR WHERE CDFORNECED=:f", f=cd_forn)
    r = cur.fetchone()
    return r[0] if r else cd_forn


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #
def gravar_depara(cur, cd_produto, cd_forn, cprod_xml, desc_xml, operador="AIHUB_ROBO"):
    """Grava o De/Para garantindo UM UNICO vinculo ativo por (fornecedor, item XML).

    Antes, a chave do MERGE incluia CDPRODUTO: se ja existisse vinculo ativo para
    o produto A, gravar o produto B INSERIA uma segunda linha ativa - e qual delas
    valia era indeterminado. Agora: desativa o(s) anterior(es) diferentes e so
    entao grava/reativa o escolhido."""
    # 0) TRAVA as linhas deste item antes de mexer.
    #
    # Sem isto ha corrida: duas sessoes gravando o MESMO item do MESMO
    # fornecedor com produtos diferentes (o robo e uma pessoa no painel, por
    # exemplo). Cada UPDATE so enxerga o que ja esta comitado, entao nenhuma
    # desativa a linha da outra, e as duas inserem - a nota fica com DOIS
    # vinculos ativos e qual vale passa a depender de ordenacao.
    #
    # Medido em producao: 0 casos hoje. Mas e seguranca por dado, nao por
    # construcao, e o dano seria silencioso.
    #
    # WAIT 3 pelo mesmo motivo do gravar_af: espera infinita congelaria a
    # rodada. Se nao liberar, deixa para a proxima passada - o item volta.
    try:
        cur.execute("""SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML
                       WHERE CDFORNECED=:forn AND CDPRODUTOXML=:cx
                       FOR UPDATE WAIT 3""", forn=cd_forn, cx=cprod_xml)
        cur.fetchall()
    except Exception as e:
        if "ORA-30006" in str(e) or "ORA-00054" in str(e):
            log("  De/Para de %s/%s em uso por outra sessao; fica para a "
                "proxima rodada." % (cd_forn, cprod_xml))
            return False
        raise
    # 1) desativa qualquer vinculo ativo deste item que aponte para OUTRO produto
    cur.execute("""
        UPDATE TEKNISA.PRODFORNXML
           SET IDATIVO='N'
         WHERE CDFORNECED=:forn AND CDPRODUTOXML=:cx
           AND IDATIVO='S' AND CDPRODUTO<>:cprod
    """, forn=cd_forn, cx=cprod_xml, cprod=cd_produto)
    # 2) grava (ou reativa) o vinculo escolhido
    cur.execute("""
        MERGE INTO TEKNISA.PRODFORNXML d
        USING (SELECT :cprod CDPRODUTO,:forn CDFORNECED,:cx CDPRODUTOXML FROM dual) s
           ON (d.CDPRODUTO=s.CDPRODUTO AND d.CDFORNECED=s.CDFORNECED AND d.CDPRODUTOXML=s.CDPRODUTOXML)
         WHEN MATCHED THEN
           UPDATE SET d.IDATIVO='S', d.NMPRODUTOXML=:nx
         WHEN NOT MATCHED THEN
           INSERT (CDPRODUTO,CDFORNECED,CDPRODUTOXML,NMPRODUTOXML,NRORG,IDATIVO,DTINCLUSAO,CDOPERINCLUSAO)
           VALUES (:cprod,:forn,:cx,:nx,1,'S',SYSDATE,:op)
    """, cprod=cd_produto, forn=cd_forn, cx=cprod_xml,
         # OS CORTES SAO OS TAMANHOS REAIS DAS COLUNAS, conferidos no schema:
         # PRODFORNXML.NMPRODUTOXML tem 80 (o corte antigo era 240) e
         # CDOPERINCLUSAO tem 12 - como TODA coluna CDOPER% do Teknisa.
         #
         # O painel estourava aqui com ORA-12899: o operador dele e
         # 'PAINEL:127.0.0.1' (16 caracteres). O robo nao estourava porque
         # grava 'AIHUB_ROBO', que tem 10 - a mesma assimetria entre robo e
         # painel que ja apareceu antes (achado 76). Corrigido nos DOIS.
         nx=(desc_xml or "")[:80], op=(operador or "")[-12:])
    return True


def abrir_pendencia(cur, nota, item, nm_forn, sug):
    # nao duplica pendencia aberta para o mesmo item
    cur.execute("""
        SELECT COUNT(*) FROM AIHUB.AIHUB_PENDENCIA
        WHERE CDFILIAL=:f AND NRLANCTONF=:n AND NRSEQITPROD=:s AND STATUS='ABERTO'
    """, f=nota["CDFILIAL"], n=nota["NRLANCTONF"], s=item["NRSEQITPROD"])
    if cur.fetchone()[0]:
        return
    cur.execute("""
        INSERT INTO AIHUB.AIHUB_PENDENCIA
          (CHAVE_NF,CDFILIAL,NRLANCTONF,NRSEQITPROD,CDFORNECED,NM_FORNECED,
           CPROD_XML,DESC_XML,CD_SUGERIDO,NM_SUGERIDO,SCORE,STATUS)
        VALUES (:chave,:f,:n,:s,:forn,:nmf,:cx,:dx,:cds,:nms,:sc,'ABERTO')
    """, chave=nota["NRACESSONFE"], f=nota["CDFILIAL"], n=nota["NRLANCTONF"],
         s=item["NRSEQITPROD"], forn=nota["CDFORNECED"], nmf=(nm_forn or "")[:150],
         cx=item["CDPRODEMIS"], dx=(item.get("NMPRODUTOXML") or "")[:300],
         cds=(sug[0] if sug else None), nms=((sug[1] or "")[:150] if sug else None),
         sc=(round(sug[2] * 100, 2) if sug else 0))


def registrar_log(cur, nota, status, detalhe, score=None):
    cur.execute("""
        INSERT INTO AIHUB.AIHUB_PROC_LOG
          (NUMERO_NF,CHAVE_NF,CDFORNECED,FILIAL_AF,STATUS,DETALHE,SCORE_MATCH)
        VALUES (:nf,:chave,:forn,:fil,:st,:det,:sc)
    """, nf=nota["NRLANCTONF"], chave=nota["NRACESSONFE"], forn=nota["CDFORNECED"],
         fil=nota["CDFILIAL"], st=status, det=(detalhe or "")[:3900], sc=score)


# --------------------------------------------------------------------------- #
# Confirmacao (I -> P)  usando as procedures nativas ja validadas
# --------------------------------------------------------------------------- #
def item_com_conversao(cur, nota, itens):
    """Itens em que o produto da NOTA e diferente do produto da AF.

    POR QUE ISTO IMPORTA (risco de estoque, nao de cadastro).
    Lendo o fonte da NF_BAIXA_AF:
      - o cursor C_ITEMAUF le QTPROGENTAF da ITEMAUTF -> unidade da AF
      - P_NEWQTITEMPROD e o que NOS passamos  -> unidade da NOTA
      - a baixa e min(P_NEWQTITEMPROD, saldo_da_AF)
      - V_VRFATOCONV existe declarada na procedure e NUNCA e usada
    Ou seja, ela NAO converte. Quando os produtos sao o mesmo (92% dos itens
    medidos em producao) as unidades coincidem e nao ha problema. Quando
    diferem (8%), o min() compara grandezas diferentes: num caso real, a nota
    tem 8 KG e a AF 3,5 PC - passar 8 faria baixar os 3,5 inteiros quando o
    certo, pelo fator, seriam 2,286.

    A conversao existe, mas e feita na IMPORTACAO (GERA_NOTAFISCAL_CORPO_NFE,
    linha 1343: qt * fator_nota / fator_af), que ja grava a NFRELACAF
    convertida. O que a NF_BAIXA_AF faz depois e somar em QTNFEFETAF.

    NAO DA PARA DECIDIR ISSO LENDO O CODIGO: depende de o Delphi passar a
    quantidade crua ou ja convertida, e o fonte da tela nao esta na base. Ate
    um teste controlado responder, esses itens NAO sao confirmados sozinhos.
    Custa 8% das confirmacoes e evita divergencia silenciosa de estoque.

    Devolve a lista de itens nessa situacao (vazia = pode confirmar)."""
    fora = []
    for it in itens:
        cd = it.get("CDPRODUTO")
        if not cd:
            continue
        try:
            cur.execute("""
                SELECT COUNT(*)
                FROM   TEKNISA.NFRELACAF R
                WHERE  R.CDFILIAL = :f AND R.NRLANCTONF = :n
                  AND  R.CDPRODUTO <> :p
                  AND  R.NRSEQITPRODREL = :s
            """, f=nota["CDFILIAL"], n=nota["NRLANCTONF"], p=cd,
                 s=it["NRSEQITPROD"])
            r = cur.fetchone()
            if r and int(r[0] or 0) > 0:
                fora.append(it["NRSEQITPROD"])
        except Exception:
            # nao consegui conferir -> trata como se precisasse de conversao
            # (fail-safe no sentido de NAO confirmar)
            fora.append(it["NRSEQITPROD"])
    return fora


def af_ja_baixada(cur, nota):
    """A AF desta nota JA foi baixada? Se sim, nao chamar NF_BAIXA_AF de novo.

    ESTE E O RISCO MAIS PERIGOSO QUE APARECEU, e ele nao vem de um erro nosso:
    vem de os dois ambientes se comportarem de forma diferente.

    Medido em producao (06/08/2026): a nota importada nasce em IDSTATUS='L',
    com DTLANCAMENNF ja preenchido (413 de 413) e com a NFRELACAF ja montada,
    uma linha por item (nota de 9 itens -> 9 linhas). Ou seja, a IMPORTACAO
    (GERA_NOTAFISCAL_CORPO_NFE) ja lanca a nota e ja baixa a AF; o que falta
    depois e so a aprovacao humana (DTAPROVACAO), que leva de 'L' para 'A'.
    O status 'I', que a nossa busca de confirmacao procura, nao existe la:
    4 notas em toda a base, ZERO no ultimo ano. Em homologacao ele existe -
    foi onde esta parte foi escrita e testada.

    Consequencia pratica: em producao a etapa de confirmacao do robo nao acha
    nada, e isso NAO e defeito, e o fluxo de la nao precisar dela. O perigo e
    alguem "consertar" trocando o filtro de 'I' para 'L': o robo passaria a
    chamar NF_BAIXA_AF em nota cuja AF ja foi baixada, baixando DUAS VEZES o
    mesmo pedido - e a baixa mexe em estoque.

    Por isso a trava fica aqui, na funcao que baixa, e nao na consulta: quem
    mudar a consulta continua protegido. Fail-safe: erro ao conferir responde
    'ja baixada' (nao baixa de novo)."""
    try:
        cur.execute("""SELECT COUNT(*) FROM TEKNISA.NFRELACAF
                       WHERE CDFILIAL = :f AND NRLANCTONF = :n""",
                    f=nota["CDFILIAL"], n=nota["NRLANCTONF"])
        r = cur.fetchone()
        return bool(r) and int(r[0] or 0) > 0
    except Exception:
        return True


def confirmar_recebimento(cur, nota, itens):
    """Vincula cada item a AF, valida, e confirma o recebimento. Retorna
    (ok, mensagem). Nao aprova (isso e a alcada humana)."""
    # tipo de operacao lido da propria nota (nunca fixa)
    cur.execute("SELECT CDTIPOOPER FROM TEKNISA.NOTAFISCAL WHERE CDFILIAL=:f AND NRLANCTONF=:n",
                f=nota["CDFILIAL"], n=nota["NRLANCTONF"])
    row = cur.fetchone()
    tipoop = row[0] if row else "01"

    # NUNCA BAIXAR AF QUE JA FOI BAIXADA. Ver af_ja_baixada(): em producao a
    # propria importacao ja monta a NFRELACAF, e uma segunda baixa consumiria
    # o mesmo pedido em dobro - e a baixa mexe em estoque.
    #
    # PULA SO A BAIXA, nao a funcao inteira: a VERIFICA_INCONS_NF e a
    # CONFIRMACAO_AUTOMATICA_NF nao tocam em quantidade de AF, so validam e
    # mudam o status. Bloquear tudo aqui tiraria do robo a unica parte que
    # ainda faz sentido nesse cenario.
    # ESTA CHAMADA E INERTE, E ISSO ESTA PROVADO - nao e suposicao.
    #
    # Teste controlado em homologacao (06/08/2026), com a AF reaberta e o saldo
    # devolvido de proposito para o resultado nao ser mascarado pelo min():
    # a procedure devolveu 'N' (sem erro) e NAO moveu QTEFETENTRAF nem criou
    # linha em NFRELACAF. Tudo dentro de transacao, com rollback conferido.
    #
    # O motivo esta no fonte dela (ALL_SOURCE, 648 linhas):
    #    314  IF P_IDSTATUS = 'A' THEN
    #    315     V_QTRESTNF := P_NEWQTITEMPROD - P_OLDQTITEMPROD;
    # Passamos 'A' com OLD IGUAL a NEW, entao a quantidade e ZERO por
    # construcao. E os cursores dela (linhas 89-137) fazem JOIN com NFRELACAF:
    # ela AJUSTA uma baixa que ja existe, nao cria baixa nenhuma. Quem cria e a
    # importacao (GERA_NOTAFISCAL_CORPO_NFE), ja convertida.
    #
    # NAO trocar 'A' por 'I' sem antes resolver a conversao de unidade: com 'I'
    # a procedure usa P_NEWQTITEMPROD inteiro (linha 311), e ai a quantidade
    # crua passaria a valer de verdade. Ha teste (B76) que falha se isso mudar.
    ja_baixada = af_ja_baixada(cur, nota)
    if ja_baixada:
        log("  nota %s: AF ja baixada na importacao; pulo a baixa e sigo para "
            "a validacao." % nota["NRLANCTONF"])
    else:
        for it in itens:
            ierro = cur.var(oracledb.STRING)
            cur.callproc("TEKNISA.NF_BAIXA_AF", [
                nota["CDFILIAL"], nota["NRLANCTONF"], it["NRSEQITPROD"], "A",
                it["CDPRODEMIS"], it["CDPRODEMIS"], it["CDPRODUTO"], it["CDPRODUTO"],
                it["CDMARCA"], it["CDMARCA"], it["QTITEMPROD"], it["QTITEMPROD"], ierro])
            if (ierro.getvalue() or "N").strip().upper() != "N":
                return False, "NF_BAIXA_AF item %s: %s" % (it["NRSEQITPROD"], ierro.getvalue())

    # P_IDGERNFIGUALXML e o mesmo parametro do CLIENTE que a importacao usa.
    # Uma leitura so, em _gera_igual_xml(), para as duas chamadas nao poderem
    # divergir entre si.
    gera_igual = _gera_igual_xml(cur)

    erro = cur.var(oracledb.STRING)
    # os dois 'N' que sobram sao P_LIBERAXML e P_LIBERAOPER: NAO liberar
    # inconsistencia. E a escolha conservadora, e tem que continuar assim -
    # liberar aqui seria o robo passando por cima de trava do Teknisa.
    cur.callproc("TEKNISA.VERIFICA_INCONS_NF", [
        nota["NRACESSONFE"], gera_igual, None, nota["NRLANCTONF"],
        nota["CDFILIAL"], "N", tipoop, "N", erro])
    v = (erro.getvalue() or "").strip().upper()
    if v != "N":
        return False, "VERIFICA_INCONS_NF: %s" % erro.getvalue()

    try:
        cur.callproc("TEKNISA.CONFIRMACAO_AUTOMATICA_NF",
                     [nota["NRACESSONFE"], oracledb.Timestamp(*time.localtime()[:6]),
                      OPERADOR, 1])
    except Exception as e:
        return False, "CONFIRMACAO_AUTOMATICA_NF falhou: %s" % str(e)[:200]

    # CONFERE DE VERDADE: rele o status da nota. Antes retornava True fixo, entao
    # o log dizia "CONFIRMADA" mesmo se a procedure nao tivesse confirmado nada.
    try:
        cur.execute("""SELECT IDSTATUS FROM TEKNISA.NOTAFISCAL
                       WHERE CDFILIAL=:f AND NRLANCTONF=:n""",
                    f=nota["CDFILIAL"], n=nota["NRLANCTONF"])
        r = cur.fetchone()
        st = (r[0] if r else "") or ""
    except Exception as e:
        return False, "nao consegui reler o status da nota: %s" % str(e)[:150]

    # VALIDACAO ESTRITA. O fluxo esperado e I -> P (pendente de aprovacao humana),
    # confirmado no banco: 'P' e o estado real de nota confirmada e nao aprovada.
    # Antes aceitava QUALQUER status != 'I' como sucesso - se a procedure levasse
    # a nota para cancelada/bloqueada/outro, o robo gravava CONFIRMADA no log
    # mentindo. Agora so 'P' (e 'A', caso o Teknisa esteja com aprovacao
    # automatica ligada) contam como confirmado; o resto e tratado como erro,
    # com o status real na mensagem para a pessoa conferir.
    if st == "I":
        return False, ("a nota continua em 'A processar' (status I) - a confirmacao "
                       "nao teve efeito. Confira a nota no Teknisa.")
    if st not in ("P", "A"):
        return False, ("apos confirmar, a nota foi para o status inesperado '%s' "
                       "(esperado 'P'). Confira a nota no Teknisa antes de seguir." % st)
    return True, "recebimento confirmado (status atual: %s)" % st


# --------------------------------------------------------------------------- #
# Processamento de uma nota
# --------------------------------------------------------------------------- #
def processar_nota(con, nota, dry, modo_auto):
    cur = con.cursor()
    nm_forn = nome_fornecedor(cur, nota["CDFORNECED"])
    itens = itens_da_nota(cur, nota["CDFILIAL"], nota["NRLANCTONF"])
    if not itens:
        registrar_log(cur, nota, "SEM_ITENS", "Nota sem itens.")
        con.commit()
        return "SEM_ITENS"

    # o que este TIPO de nota autoriza (ver POLITICA_TIPO no topo)
    pol = politica(nota.get("CDTIPOOPER"))
    pode_vincular = pol["vincula_sozinho"] and modo_auto
    pode_confirmar = pol["confirma"] and modo_auto

    cands = candidatos_af(cur, nota["CDFORNECED"])
    pendentes = 0
    auto = 0

    # menor score entre os vinculos que o robo gravou sozinho nesta nota. E o
    # elo mais fraco da confirmacao - o que interessa numa auditoria depois.
    # Antes o SCORE_MATCH do log ficava SEMPRE nulo (nenhum chamador passava
    # score), entao nao dava para saber com que confianca a nota foi confirmada.
    score_min = None
    # unidade do candidato, para a checagem granel x embalagem no pode_auto
    un_de = {c[0]: (c[2] if len(c) > 2 else None) for c in cands}
    for it in itens:
        cprod = it["CDPRODEMIS"]
        desc = (it.get("NMPRODUTOXML") or "").strip()
        un_xml = it.get("SGUNIDADEXML")
        # ja mapeado?
        cd_existente = depara_existente(cur, nota["CDFORNECED"], cprod)
        if cd_existente:
            # ALINHA O ITEM COM O DE/PARA ATIVO (mesmo motivo do auto-vinculo
            # abaixo). O it["CDPRODUTO"] veio da ITEMPRODUTO, gravado quando a
            # nota foi importada; o De/Para pode ter mudado DEPOIS. Sem esta
            # linha, a NF_BAIXA_AF receberia o produto VELHO (P_NEWCDPRODUTO com
            # o valor desatualizado), amarrando o item ao produto errado.
            # Antes so o caminho do auto-vinculo fazia esse alinhamento; o do
            # vinculo ja existente ficava de fora - dois itens em situacao
            # identica tinham tratamento diferente so por QUANDO o De/Para nasceu.
            if cd_existente != it.get("CDPRODUTO"):
                it["CDPRODUTO"] = cd_existente
            continue
        # sem descricao do fornecedor nao da para casar -> humano
        if not desc:
            pendentes += 1
            if not dry:
                abrir_pendencia(cur, nota, it, nm_forn, None)
            continue
        # decisao centralizada no core (mesma regra do painel):
        #   best     -> score BRUTO, define se pode gravar sozinho
        #   best_sug -> preferindo o generico, e o que a pessoa ve na fila
        # a unidade do XML desempata entre candidatos parecidos
        best, best_sug = escolher(desc, cands, un_xml)
        gravou = False
        if pode_auto(desc, best, un_xml, un_de.get(best[0]) if best else None,
                     cands) and pode_vincular and not dry:
            gravou = gravar_depara(cur, best[0], nota["CDFORNECED"], cprod,
                                   desc, OPERADOR)
        if gravou:
            auto += 1
            score_min = best[2] if score_min is None else min(score_min, best[2])
            # ALINHA O ITEM COM O VINCULO QUE ACABAMOS DE CRIAR.
            #
            # Os itens sao lidos no inicio desta funcao; o vinculo e gravado
            # agora. Sem esta linha, o dict 'it' continuaria com o CDPRODUTO
            # ANTIGO, e era esse valor velho que iria para a NF_BAIXA_AF la
            # embaixo - amarrando o item da nota ao produto errado.
            #
            # Medido no banco: existem 1.484 itens cujo CDPRODUTO difere do
            # De/Para ativo do fornecedor. Ou seja, a divergencia e real, nao
            # teorica. (O caso de CDPRODUTO nulo NAO existe: nota so vira
            # NOTAFISCAL depois que o Teknisa ja resolveu o produto de todos
            # os itens - 241.412 de 241.412 preenchidos.)
            it["CDPRODUTO"] = best[0]
        else:
            pendentes += 1
            if not dry:
                abrir_pendencia(cur, nota, it, nm_forn, best_sug)

    if pendentes == 0 and pode_confirmar and not dry:
        # A AF PODE TER MORRIDO ENTRE A IMPORTACAO E AGORA.
        #
        # Nao e o mesmo caso do importar_uma. Aqui a nota JA entrou (status I) e
        # pode ter ficado dias na fila esperando alguem vincular um produto. No
        # meio-tempo, outra entrega consome a mesma AF. Confirmar depois disso
        # baixaria um pedido ja atendido - e a baixa aqui e de verdade, mexe em
        # estoque, diferente da importacao.
        #
        # Medido em producao: a AF morre em cerca de 30 dias (acima disso, 100%
        # das AFs de notas paradas estao fechadas ou sem saldo).
        #
        # AS DUAS CHECAGENS ABAIXO SO FAZEM SENTIDO SE FORMOS BAIXAR A AF.
        # Quando a importacao ja baixou (ver af_ja_baixada), elas passariam a
        # barrar por motivo que nao existe mais: a conversao ja foi feita por
        # quem baixou, e a AF aparece "sem saldo" justamente porque esta nota
        # a consumiu. Barrar ai seria o robo se recusando a terminar o proprio
        # trabalho.
        vai_baixar = not af_ja_baixada(cur, nota)

        # ITEM QUE PRECISA DE CONVERSAO DE UNIDADE nao e confirmado sozinho
        # ate um teste controlado provar como a NF_BAIXA_AF espera receber a
        # quantidade. Ver item_com_conversao(): risco de baixar a AF inteira
        # no lugar da fracao convertida.
        conv = item_com_conversao(cur, nota, itens) if vai_baixar else []
        if conv:
            registrar_log(cur, nota, "PENDENTE_HUMANO",
                          "item(ns) %s tem produto diferente do produto da AF "
                          "(precisa de fator de conversao). Nao confirmo sozinho "
                          "ate isso ser validado em teste controlado."
                          % ", ".join(map(str, conv)))
            con.commit()
            return "PENDENTE_CONVERSAO"

        from core.af_matcher import af_ainda_valida
        af_ok, af_mortas = (af_ainda_valida(cur, nota["NRACESSONFE"])
                            if vai_baixar else (True, []))
        if not af_ok:
            registrar_log(cur, nota, "ERRO_CONFIRMA",
                          "AF %s nao esta mais em aberto com saldo; confirmar "
                          "agora baixaria pedido ja atendido."
                          % (", ".join(af_mortas) or "(nao consegui conferir)"))
            con.commit()
            return "AF_VENCIDA"
        # SAVEPOINT: se a confirmacao falhar, desfaz SO a confirmacao - os
        # vinculos e pendencias gravados acima no laco de itens sao preservados.
        # (antes o rollback jogava fora o trabalho todo e a proxima rodada refazia)
        #
        # E REAL, NAO ILUSORIO: conferido no fonte PL/SQL das nativas -
        # NF_BAIXA_AF (1.064 linhas) e VERIFICA_INCONS_NF (4.836 linhas) nao tem
        # NENHUM commit nem PRAGMA AUTONOMOUS_TRANSACTION, entao participam da
        # nossa transacao e o ROLLBACK TO desfaz mesmo as baixas ja feitas.
        cur.execute("SAVEPOINT antes_confirmar")
        ok, msg = confirmar_recebimento(cur, nota, itens)
        if ok:
            registrar_log(cur, nota, "CONFIRMADA",
                          "%d itens (auto=%d). %s" % (len(itens), auto, msg),
                          score=score_min)
            con.commit()
            return "CONFIRMADA"
        else:
            cur.execute("ROLLBACK TO SAVEPOINT antes_confirmar")
            registrar_log(cur, nota, "ERRO_CONFIRMA", msg)
            con.commit()
            return "ERRO_CONFIRMA"
    else:
        # DIZ O MOTIVO de a nota nao ter avancado. Antes o log so registrava
        # "PENDENTE_HUMANO" e ninguem sabia se era falta de vinculo ou se o
        # modo automatico estava desligado - a nota ficava em "A processar"
        # eternamente sem explicacao.
        if pendentes > 0:
            motivo = ("%d item(ns) sem vinculo - esperando alguem vincular em "
                      "'Produtos a vincular'" % pendentes)
        elif not pol["confirma"]:
            # o tipo de nota nao autoriza confirmacao automatica (ex.: bonificacao)
            motivo = ("tipo de operacao %s nao confirma automaticamente por regra "
                      "da operacao - a confirmacao do recebimento e humana"
                      % (nota.get("CDTIPOOPER") or "?"))
        elif not modo_auto:
            motivo = ("todos os itens reconhecidos, mas o MODO AUTOMATICO esta "
                      "DESLIGADO - ligue em Configuracoes para o robo confirmar")
        elif dry:
            motivo = "simulacao (painel em modo consulta) - nada foi gravado"
        else:
            motivo = "sem itens pendentes, mas a confirmacao nao foi executada"
        registrar_log(cur, nota, "PENDENTE_HUMANO",
                      "%d itens, %d na fila, auto=%d. MOTIVO: %s"
                      % (len(itens), pendentes, auto, motivo))
        con.commit()
        return "PENDENTE_HUMANO"


def confirmadas_hoje(cur):
    """Quantas notas ja foram confirmadas automaticamente hoje (teto de seguranca).

    FAIL-SAFE: se a contagem falhar, devolve None = "nao sei". Quem chama trata
    "nao sei" como teto atingido e desliga o automatico na rodada. Antes isto
    devolvia 0, o que fazia o teto NUNCA disparar justamente quando o log estava
    inacessivel - a protecao se desligava sozinha, em silencio, na hora em que
    ela era mais necessaria."""
    try:
        cur.execute("""SELECT COUNT(*) FROM AIHUB.AIHUB_PROC_LOG
                       WHERE STATUS='CONFIRMADA' AND DT_PROC >= TRUNC(SYSDATE)""")
        return int(cur.fetchone()[0])
    except Exception as e:
        log("AVISO: nao consegui contar as confirmacoes de hoje (%s). "
            "Automatico PAUSADO nesta rodada por seguranca." % str(e)[:80])
        return None


def _tentar_lock(con):
    """Evita DUAS rodadas ao mesmo tempo (painel + servico, ou dois painies),
    o que poderia confirmar a mesma nota duas vezes. Marca a rodada em
    AIHUB_CONFIG; se ja houver uma rodando ha menos de 30 min, nao inicia."""
    try:
        cur = con.cursor()
        cur.execute("""MERGE INTO AIHUB.AIHUB_CONFIG c
                       USING (SELECT 'RODADA_EM_CURSO' CHAVE FROM dual) s
                          ON (c.CHAVE = s.CHAVE)
                       WHEN MATCHED THEN UPDATE SET VALOR = TO_CHAR(SYSDATE,'YYYYMMDDHH24MISS')
                            WHERE NVL(c.VALOR,'0') = '0'
                               OR TO_DATE(c.VALOR,'YYYYMMDDHH24MISS') < SYSDATE - 30/1440
                       WHEN NOT MATCHED THEN
                            INSERT (CHAVE, VALOR) VALUES ('RODADA_EM_CURSO',
                                    TO_CHAR(SYSDATE,'YYYYMMDDHH24MISS'))""")
        pegou = cur.rowcount > 0
        con.commit()
        cur.close()
        return pegou
    except Exception as e:
        # FAIL-SAFE: sem conseguir marcar o lock, NAO da para garantir que nao ha
        # outra rodada em curso - e duas rodadas juntas podem confirmar a mesma
        # nota duas vezes. Antes isto devolvia True ("segue assim mesmo"), que e
        # exatamente o cenario que o lock existe para impedir. Perder uma rodada
        # custa alguns minutos; confirmar a nota duas vezes custa muito mais.
        log("AVISO: nao consegui marcar a rodada em curso (%s). Rodada IGNORADA "
            "por seguranca." % str(e)[:80])
        return False


def _liberar_lock(con):
    try:
        cur = con.cursor()
        cur.execute("UPDATE AIHUB.AIHUB_CONFIG SET VALOR='0' WHERE CHAVE='RODADA_EM_CURSO'")
        con.commit()
        cur.close()
    except Exception:
        pass


def rodada(dry=False, limite=None, lote=None):
    """Uma passada do robo.

    'limite' e 'lote' existem para VALIDAR EM PEDACOS. O padrao continua sendo
    AIHUB_LOTE (25 notas, fila inteira); quem chama pode pedir menos, ou so as
    notas que chegaram numa hora, e conferir o resultado antes de soltar o
    resto. Nada disso afrouxa trava: o teto diario, o MODO_AUTO, o lock e o
    MODO_ESCRITA continuam valendo igual."""
    con = conn()
    tem_lock = False
    try:
        if not dry:
            tem_lock = _tentar_lock(con)
            if not tem_lock:
                log("Ja existe uma rodada em curso - esta foi ignorada.")
                return {"IGNORADA_LOCK": 1}

        cur = con.cursor()
        modo_auto = ler_modo_auto(cur)
        modo_importa = ler_modo_importa(cur)
        feitas_hoje = confirmadas_hoje(cur) if not dry else 0
        cur.close()

        # FREIO DE SEGURANCA: teto diario de confirmacoes automaticas.
        # feitas_hoje = None significa "nao consegui contar" -> trata como teto
        # atingido (ver confirmadas_hoje).
        if TETO_DIA and modo_auto:
            if feitas_hoje is None:
                log("Sem saber quantas notas ja foram confirmadas hoje. "
                    "Automatico pausado; as notas vao para a fila humana.")
                modo_auto = False
            elif feitas_hoje >= TETO_DIA:
                log("TETO DIARIO atingido (%d confirmacoes hoje). Automatico pausado; "
                    "as notas vao para a fila humana." % feitas_hoje)
                modo_auto = False

        # PASSO 0 (opcional): importar as notas da integracao (chama a GERA nativa).
        # So roda se ligado no painel - porque gera nota fiscal.
        if modo_importa and not dry:
            rodar_importacao(con, limite or LOTE, lote=lote)

        cur = con.cursor()
        notas = buscar_notas_novas(cur, LOTE)
        cur.close()
        log("Rodada: %d nota(s) nova(s). auto=%s importa=%s dry=%s"
            % (len(notas), modo_auto, modo_importa, dry))
        resumo = {}
        erros_seguidos = 0
        # confirmacoes feitas DENTRO desta rodada, para o teto valer nota a nota.
        # Antes o teto so era conferido no inicio: com LOTE=25, uma rodada podia
        # passar do teto em ate 24 notas antes de alguem perceber.
        confirmadas_nesta = 0
        for nota in notas:
            if (TETO_DIA and modo_auto and feitas_hoje is not None
                    and feitas_hoje + confirmadas_nesta >= TETO_DIA):
                log("TETO DIARIO atingido durante a rodada (%d). As notas restantes "
                    "vao para a fila humana." % (feitas_hoje + confirmadas_nesta))
                modo_auto = False
            try:
                r = processar_nota(con, nota, dry, modo_auto)
                if r == "CONFIRMADA":
                    confirmadas_nesta += 1
                erros_seguidos = 0
            except Exception as e:
                con.rollback()
                log("ERRO nota %s/%s: %s" % (nota["CDFILIAL"], nota["NRLANCTONF"], e))
                try:
                    cur = con.cursor()
                    registrar_log(cur, nota, "ERRO", str(e)[:500])
                    con.commit()
                    cur.close()
                except Exception as e2:
                    # nao perde o rastro do erro se ate o log falhar
                    log("ERRO ao gravar o log da nota %s: %s" % (nota["NRACESSONFE"], e2))
                r = "ERRO"
                erros_seguidos += 1
                if MAX_ERROS and erros_seguidos >= MAX_ERROS:
                    log("PARANDO a rodada: %d erros seguidos. Verifique o banco/logs."
                        % erros_seguidos)
                    resumo["PARADA_POR_ERROS"] = 1
                    break
            resumo[r] = resumo.get(r, 0) + 1
        log("Fim da rodada: %s" % (resumo or "nada a fazer"))
        return resumo
    finally:
        if tem_lock:
            _liberar_lock(con)
        try:
            con.close()
        except Exception:
            pass


def main():
    # dry = pedido explicitamente (--dry) OU escrita nao autorizada no ambiente.
    dry = ("--dry" in sys.argv) or (not MODO_ESCRITA)
    loop = "--loop" in sys.argv
    if dry and "--dry" not in sys.argv:
        log("SIMULACAO: AIHUB_MODO_ESCRITA nao esta ligado, entao nada sera "
            "gravado. Para valer, defina AIHUB_MODO_ESCRITA=1 no atalho.")
    log("AI Fiscal Hub - ROBO iniciado. escrita=%s loop=%s dry=%s"
        % (MODO_ESCRITA, loop, dry))
    if not loop:
        rodada(dry)
        return
    while True:
        try:
            rodada(dry)
        except Exception:
            log("ERRO na rodada:\n" + traceback.format_exc())
        time.sleep(POLL)


if __name__ == "__main__":
    main()
