"""Confere se TODO :bind do SQL tem argumento correspondente e vice-versa.
E a classe de bug que so aparece em producao (ORA-01008 / ORA-01036)."""
import ast, re, sys

def binds_no_sql(s):
    # ignora ::cast e strings tipo 'a:b'
    return set(re.findall(r"(?<![:\w]):([a-zA-Z_]\w*)", s))

def texto_da_sql(node):
    """Extrai o texto SQL mesmo com concatenacao de literais."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        e, oke = texto_da_sql(node.left)
        d, okd = texto_da_sql(node.right)
        return (e or "") + (d or ""), (oke and okd)
    if isinstance(node, ast.JoinedStr):      # f-string
        partes = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                partes.append(str(v.value))
        return "".join(partes), False        # dinamico: analise parcial
    return "", False

problemas, analisadas, parciais = [], 0, 0
for arq in sys.argv[1:]:
    src = open(arq, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args):
            continue
        sql, completo = texto_da_sql(node.args[0])
        if not sql.strip():
            continue
        analisadas += 1
        if not completo:
            parciais += 1
        usados = binds_no_sql(sql)
        # binds passados por keyword
        passados = {k.arg for k in node.keywords if k.arg}
        # ou por dict como 2o argumento posicional -> nao da para conferir aqui
        dict_pos = len(node.args) > 1
        if dict_pos or not (usados or passados):
            continue
        faltando = usados - passados
        sobrando = passados - usados
        if faltando or sobrando:
            problemas.append((arq, node.lineno, sorted(faltando), sorted(sobrando),
                              completo))

print("consultas analisadas: %d (%d parciais/dinamicas)" % (analisadas, parciais))
if not problemas:
    print("OK - nenhum bind faltando ou sobrando")
for a, ln, f, s, comp in problemas:
    tag = "" if comp else "  [SQL dinamico - conferir a mao]"
    print("  %s:%d  faltando=%s sobrando=%s%s" % (a, ln, f or "-", s or "-", tag))
