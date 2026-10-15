r"""
validar_sistema.py - Valida o sistema INTEIRO contra o banco de HOMOLOGACAO.

Roda as MESMAS consultas do painel (via test_client do Flask) e ainda confere:
  - conexao (pool) como AIHUB
  - permissoes (grants) necessarias no schema TEKNISA
  - tabelas do proprio robo (AIHUB)
  - deteccao automatica de colunas (data, AF, valor)
  - cada tela/endpoint do painel (indicadores, caixa, paradas, fila,
    aprovadas, relatorios, detalhe da nota, busca, revalidar, etc.)

E 100% SOMENTE-LEITURA: nao gera nota, nao grava nem altera nada.
(O reprocesso e a gravacao de vinculo NAO sao exercidos aqui de proposito.)

Como rodar (na pasta interface\painel):
    py validar_sistema.py
ou de dois cliques no VALIDAR_SISTEMA.bat
"""
import os
import sys

# A conexao vem do ambiente (CONFIG_LOCAL.bat, carregado pelo VALIDAR_SISTEMA.bat).
# NAO ha mais senha nem DSN escritos aqui: este arquivo acompanha o projeto, e
# quem tivesse a pasta tinha o banco. So o usuario tem um padrao, por ser publico.
os.environ.setdefault("TEKNISA_DB_USER", "AIHUB")

if not os.getenv("TEKNISA_DB_DSN") or not os.getenv("TEKNISA_DB_PASS"):
    print("\n" + "=" * 62)
    print(" NAO CONSIGO VALIDAR: faltam TEKNISA_DB_DSN e/ou TEKNISA_DB_PASS.")
    print(" Rode pelo VALIDAR_SISTEMA.bat, que carrega o CONFIG_LOCAL.bat.")
    print("=" * 62 + "\n")
    raise SystemExit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as P  # o proprio painel (mesmas conexoes e consultas)

PASS = FAIL = WARN = 0


def ok(nome, det=""):
    global PASS; PASS += 1
    print("  [OK]   %-40s %s" % (nome, det))


def falha(nome, det=""):
    global FAIL; FAIL += 1
    print("  [X ]   %-40s %s" % (nome, det))


def aviso(nome, det=""):
    global WARN; WARN += 1
    print("  [! ]   %-40s %s" % (nome, det))


def secao(t):
    print("\n" + t)


def hit(cli, url):
    r = cli.get(url)
    try:
        return r.status_code, r.get_json()
    except Exception:
        return r.status_code, None


def main():
    print("=" * 72)
    print(" VALIDACAO DO SISTEMA - Recebimento de NF-e (HOMOLOGACAO, so-leitura)")
    print("=" * 72)

    # 1) conexao
    secao("1) Conexao com o banco")
    try:
        con = P._conn(); cur = con.cursor()
        cur.execute("SELECT 1 FROM dual"); cur.fetchone()
        ok("Conectou como AIHUB (pool de conexoes)")
    except Exception as e:
        falha("Conexao", str(e)[:140])
        print("\nSem conexao - nao da para continuar. Confira a rede/senha.")
        return

    # 2) grants
    secao("2) Permissoes (grants) no schema TEKNISA")
    req_sel = ["ARQXMLNFE", "NOTAFISCAL", "AUTOFORNE", "ITEMAUTF", "PRODUTO",
               "FORNECEDOR", "RELACXMLAF", "PRODINCONSISTENTE", "INTG_NOTAFISCAL",
               "ITEMPRODUTO", "NOTADESTINADA", "INTG_INCONSISTENCIA", "PRODFORNXML"]
    req_exec = ["NF_BAIXA_AF", "VERIFICA_INCONS_NF", "CONFIRMACAO_AUTOMATICA_NF",
                "GERA_NOTAFISCAL_CORPO_NFE"]
    try:
        cur.execute("SELECT TABLE_NAME, PRIVILEGE FROM USER_TAB_PRIVS_RECD WHERE OWNER='TEKNISA'")
        got = {}
        for t, p in cur.fetchall():
            got.setdefault(t, set()).add(p)
        for t in req_sel:
            (ok if "SELECT" in got.get(t, set()) else falha)("SELECT " + t,
                "" if "SELECT" in got.get(t, set()) else "faltando GRANT")
        for need in ("INSERT", "UPDATE"):
            (ok if need in got.get("PRODFORNXML", set()) else aviso)(need + " PRODFORNXML",
                "" if need in got.get("PRODFORNXML", set()) else "faltando (grava vinculo)")
        for pr in req_exec:
            (ok if "EXECUTE" in got.get(pr, set()) else falha)("EXECUTE " + pr,
                "" if "EXECUTE" in got.get(pr, set()) else "faltando GRANT")
    except Exception as e:
        aviso("Leitura de grants", str(e)[:110])

    # 3) tabelas do robo
    secao("3) Tabelas do proprio robo (schema AIHUB)")
    for t in ("AIHUB_CONFIG", "AIHUB_PENDENCIA", "AIHUB_PROC_LOG"):
        try:
            cur.execute("SELECT COUNT(*) FROM AIHUB.%s" % t)
            ok(t, "%d linha(s)" % cur.fetchone()[0])
        except Exception as e:
            falha(t, str(e)[:90])

    # 4) deteccao de colunas
    secao("4) Deteccao automatica de colunas")
    try:
        ok("Coluna de data (NOTAFISCAL)", P._col_data_nf(cur) or "nenhuma (periodo limitado)")
        ok("Coluna de AF (ITEMPRODUTO)", P._col_af_item(cur) or "nao existe (AF por fornecedor)")
        ok("Coluna de valor (item)", P._col_val_item(cur) or "nao encontrada")
        ok("Coluna de valor (NOTADESTINADA)", P._col_val_dest(cur) or "nao encontrada")
    except Exception as e:
        aviso("Deteccao de colunas", str(e)[:110])

    # amostras para os testes de detalhe/busca/revalidar
    chave_nota = chave_parada = chave_aprov = af = cnpj = None
    def uma(sql):
        try:
            cur.execute(sql); r = cur.fetchone(); return r[0] if r else None
        except Exception:
            return None
    chave_nota = uma("SELECT NRACESSONFE FROM TEKNISA.NOTAFISCAL WHERE IDENTRSAIDOP='E' AND CDTIPOOPER='01' AND NRACESSONFE IS NOT NULL AND ROWNUM<=1")
    chave_aprov = uma("SELECT NRACESSONFE FROM TEKNISA.NOTAFISCAL WHERE IDENTRSAIDOP='E' AND IDSTATUS='A' AND CDTIPOOPER='01' AND NRACESSONFE IS NOT NULL AND ROWNUM<=1")
    chave_parada = uma("SELECT NRACESSONFE FROM TEKNISA.INTG_INCONSISTENCIA WHERE IDATIVO='S' AND ROWNUM<=1")
    af = uma("SELECT NRAUTOFORN FROM TEKNISA.AUTOFORNE WHERE NRAUTOFORN IS NOT NULL AND ROWNUM<=1")
    if chave_nota and len(str(chave_nota)) >= 20:
        cnpj = str(chave_nota)[6:20]
    cur.close(); con.close()

    # 5) endpoints (mesmas consultas do painel)
    secao("5) Telas / endpoints do painel (consultas reais)")
    cli = P.app.test_client()

    def check(nome, url, valida):
        try:
            st, d = hit(cli, url)
            if st != 200 or d is None:
                falha(nome, "HTTP %s" % st); return
            msg = valida(d)
            if isinstance(msg, str) and msg.startswith("ERRO"):
                falha(nome, msg)
            else:
                ok(nome, msg if isinstance(msg, str) else "")
        except Exception as e:
            falha(nome, str(e)[:110])

    def erro(d):
        return ("ERRO: " + str(d.get("erro"))[:80]) if isinstance(d, dict) and d.get("erro") else None

    check("/api/config", "/api/config",
          lambda d: ("versao " + str(d.get("versao"))) if d.get("versao") else "ERRO sem versao")
    check("/api/kpis (indicadores)", "/api/kpis",
          lambda d: erro(d) or ("caixa=%s paradas=%s a_aprovar=%s aprovadas=%s"
                                % (d.get("caixa"), d.get("paradas"), d.get("pendentes"), d.get("aprovadas"))))
    check("/api/caixa (entrada)", "/api/caixa",
          lambda d: erro(d) or ("%d nota(s)" % d.get("total", 0)))
    check("/api/travadas (paradas)", "/api/travadas",
          lambda d: erro(d) or ("%d parada(s)" % d.get("total", 0)))
    check("/api/fila (no sistema)", "/api/fila",
          lambda d: erro(d) or ("%d nota(s)" % len(d.get("notas", []))))
    check("/api/aprovadas", "/api/aprovadas",
          lambda d: erro(d) or ("%d nota(s)" % len(d.get("notas", []))))
    check("/api/relatorio", "/api/relatorio",
          lambda d: erro(d) or ("%d fornecedor(es)" % len(d.get("top_fornecedores", []))))
    check("/api/pendencias (a vincular)", "/api/pendencias",
          lambda d: "%d item(ns)" % d.get("total", 0))
    check("/api/divergencias (produto)", "/api/divergencias",
          lambda d: erro(d) or ("%d nota(s) com produto divergente" % d.get("total", 0)))
    check("/api/robo-resolvidos (conferencia)", "/api/robo-resolvidos",
          lambda d: erro(d) or ("%d vinculo(s) do robo" % len(d.get("itens", []))))
    check("/api/modo (GET)", "/api/modo",
          lambda d: "auto=%s importa=%s" % (d.get("auto"), d.get("importa")))
    check("/api/robo (GET)", "/api/robo",
          lambda d: "ligado=%s intervalo=%s" % (d.get("ligado"), d.get("intervalo")))
    if chave_nota:
        check("/api/nota (detalhe)", "/api/nota?chave=" + str(chave_nota),
              lambda d: "ERRO sem dados" if (not d.get("cabecalho") and not d.get("motivos"))
              else ("itens=%d onde=%s" % (len(d.get("itens", [])), d.get("onde"))))
        check("/api/buscar (por chave)", "/api/buscar?chave=" + str(chave_nota),
              lambda d: "%d resultado(s)" % len(d.get("notas", [])))
        check("/api/nota-sugestoes", "/api/nota-sugestoes?chave=" + str(chave_nota),
              lambda d: ("%d item(ns) com sugestao" % len(d.get("itens", [])))
              if d.get("itens") else ("info: " + str(d.get("msg", ""))[:55]))
    else:
        aviso("/api/nota e /api/buscar", "sem nota de compra para amostrar")
    if af:
        check("/api/buscar (por AF %s)" % af, "/api/buscar?af=" + str(af),
              lambda d: "AF localizada" if d.get("af") else "ERRO: AF nao localizada")
    if chave_aprov:
        check("/api/revalidar (aprovada)", "/api/revalidar?chave=" + str(chave_aprov),
              lambda d: "ok=%s achados=%d" % (d.get("ok"), len(d.get("achados", []))))
    else:
        aviso("/api/revalidar", "sem nota aprovada para amostrar")
    if cnpj:
        check("/api/fornecedor-nome", "/api/fornecedor-nome?cnpj=" + cnpj,
              lambda d: (d.get("nome") or "(sem nome cadastrado)"))

    # 6) amostras (conferencia visual)
    secao("6) Amostras usadas (so para conferencia)")
    print("   Chave de nota:  ", chave_nota)
    print("   Chave aprovada: ", chave_aprov)
    print("   Chave parada:   ", chave_parada)
    print("   AF de exemplo:  ", af)

    print("\n" + "=" * 72)
    print(" RESULTADO: %d OK,  %d falha(s),  %d aviso(s)" % (PASS, FAIL, WARN))
    if FAIL == 0:
        print(" >> Sistema validado no banco de homologacao: tudo respondendo.")
    else:
        print(" >> Ha %d ponto(s) a corrigir (ver as linhas [X ] acima)." % FAIL)
    print("=" * 72)


if __name__ == "__main__":
    main()
