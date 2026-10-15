# -*- coding: utf-8 -*-
"""Roda o diagnostico com os TIPOS que o Oracle devolve de verdade:
None, int, float, Decimal, datetime e LOB. E onde erro de Python aparece."""
import sys, types, os, json, traceback, datetime, decimal
sys.path.insert(0, os.getcwd())
ora=types.ModuleType("oracledb"); ora.STRING="STRING"; ora.Timestamp=lambda *a:"TS"
ora.connect=lambda *a,**k:None; ora.create_pool=lambda *a,**k:None
sys.modules["oracledb"]=ora
os.environ["TEKNISA_DB_DSN"]="fake"; os.environ["TEKNISA_DB_PASS"]="x"
os.environ["AIHUB_MODO_ESCRITA"]="1"
import importlib.util
spec=importlib.util.spec_from_file_location("pa","interface/painel/app.py")
A=importlib.util.module_from_spec(spec); spec.loader.exec_module(A)

class LOB:                      # simula um CLOB do Oracle
    def __init__(self,t): self.t=t
    def read(self): return self.t
    def __str__(self): return "<LOB>"

CENARIOS={
 "tudo string (ideal)": {
   "item":("1","P9","PRODUTO X"), "mot":("Motivo X","31"),
   "log":("PENDENTE","detalhe","28/07/2026 10:00")},
 "numeros como int/float": {
   "item":(1,"P9","PRODUTO X"), "mot":("Motivo X",31),
   "log":("PENDENTE","detalhe","28/07/2026 10:00")},
 "campos NULOS": {
   "item":(None,None,None), "mot":(None,None),
   "log":(None,None,None)},
 "Decimal (NUMBER com escala)": {
   "item":(decimal.Decimal("1"),"P9","PROD"), "mot":("M",decimal.Decimal("31")),
   "log":("PENDENTE","detalhe","28/07/2026")},
 "datetime cru (sem TO_CHAR)": {
   "item":(1,"P9","PROD"), "mot":("M","31"),
   "log":("PENDENTE","detalhe",datetime.datetime(2026,7,28,10,0))},
 "CLOB": {
   "item":(1,"P9",LOB("descricao grande")), "mot":(LOB("motivo grande"),"31"),
   "log":("PENDENTE",LOB("detalhe grande"),"28/07/2026")},
}
class Cur:
    def __init__(s,c): s.c=c; s.rows=[]; s.description=[]
    def execute(s,sql,*a,**kw):
        u=" ".join(sql.upper().split())
        if "AIHUB_CONFIG" in u: s.rows=[("1",)]; s.description=[("VALOR",)]
        elif "FROM TEKNISA.NOTAFISCAL WHERE NRACESSONFE" in u:
            s.rows=[("9999","0003267124","I","J01")]
            s.description=[("CDFILIAL",),("NRLANCTONF",),("IDSTATUS",),("CDFORNECED",)]
        elif "FROM TEKNISA.ITEMPRODUTO I" in u:
            s.rows=[s.c["item"]]; s.description=[("NRSEQITPROD",),("CDPRODEMIS",),("NMPRODUTOXML",)]
        elif "INTG_INCONSISTENCIA" in u:
            s.rows=[s.c["mot"]]; s.description=[("M",),("COD",)]
        elif "AIHUB_PROC_LOG" in u:
            s.rows=[s.c["log"]]; s.description=[("STATUS",),("DETALHE",),("DT",)]
        else: s.rows=[]; s.description=[]
        return s
    def fetchone(s): return s.rows[0] if s.rows else None
    def fetchall(s): return list(s.rows)
    def close(s): pass
class Con:
    def __init__(s,c): s.c=Cur(c)
    def cursor(s): return s.c
    def commit(s):pass
    def rollback(s):pass
    def close(s):pass

print("="*74); print(" DIAGNOSTICO x TIPOS REAIS DO ORACLE"); print("="*74)
falhas=0
for nome,cen in CENARIOS.items():
    A._conn=lambda c=cen: Con(c); A._col_cache.clear()
    with A.app.test_request_context("/api/diagnostico-nota?chave="+"4"*44):
        try:
            r=A.diagnostico_nota()
            body,st=(r[0],r[1]) if isinstance(r,tuple) else (r,200)
            d=json.loads(body.get_data(as_text=True))
            if st==200: print("  OK      %-30s" % nome)
            else:
                falhas+=1
                print("  FALHOU  %-30s -> %s" % (nome, d.get("detalhe") or d.get("msg")))
        except Exception as e:
            falhas+=1
            print("  EXPLODIU %-29s -> %s: %s" % (nome, type(e).__name__, str(e)[:90]))
print()
print("cenarios com problema: %d de %d" % (falhas,len(CENARIOS)))
