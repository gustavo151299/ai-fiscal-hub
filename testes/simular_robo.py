# -*- coding: utf-8 -*-
"""Simula o robo com BANCO FALSO: roda o fluxo real de processar_nota()."""
import sys, types, os
sys.path.insert(0, os.getcwd())
ora = types.ModuleType("oracledb"); ora.STRING = "STRING"
ora.Timestamp = lambda *a: "TS"
ora.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sem banco"))
sys.modules["oracledb"] = ora
os.environ.setdefault("TEKNISA_DB_DSN","fake"); os.environ.setdefault("TEKNISA_DB_PASS","x")
from robo import servico_robo as R

class Var:
    def __init__(self, v="N"): self.v = v
    def getvalue(self): return self.v

class Cur:
    def __init__(self, cen):
        self.cen = cen; self.sqls = []; self.rows = []; self.description = []
        self.rowcount = 1; self.procs = []
    def execute(self, sql, *a, **kw):
        self.sqls.append(" ".join(sql.split()))
        u = " ".join(sql.upper().split())
        if "TEKNISA.FORNECEDOR" in u:
            self.rows=[("FORNECEDOR TESTE",)]; self.description=[("NMRAZSOCFORN",)]
        elif "FROM TEKNISA.ITEMPRODUTO" in u:
            self.rows=self.cen["itens"]
            self.description=[("NRSEQITPROD",),("CDPRODEMIS",),("NMPRODUTOXML",),
                              ("CDPRODUTO",),("CDMARCA",),("QTITEMPROD",),
                              ("SGUNIDADEXML",)]
        elif "SELECT CDPRODUTO FROM TEKNISA.PRODFORNXML" in u:
            v=self.cen["depara"].get(kw.get("c")); self.rows=[(v,)] if v else []
            self.description=[("CDPRODUTO",)]
        elif "SELECT DISTINCT CDPRODUTO" in u:
            self.rows=self.cen["cands"]
            self.description=[("CDPRODUTO",),("NMPRODUTO",),("SGUNIDADE",)]
        elif "SELECT CDTIPOOPER" in u:
            self.rows=[("01",)]; self.description=[("CDTIPOOPER",)]
        elif "SELECT IDSTATUS FROM TEKNISA.NOTAFISCAL" in u:
            self.rows=[(self.cen.get("status_apos","P"),)]; self.description=[("IDSTATUS",)]
        elif "SELECT COUNT(*)" in u:
            self.rows=[(0,)]; self.description=[("N",)]
        else:
            self.rows=[]; self.description=[]
        return self
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)
    def var(self, t): return Var(self.cen.get("erro_proc","N"))
    def callproc(self, nome, args=None, keyword_parameters=None):
        self.procs.append(nome)
        if nome.endswith("CONFIRMACAO_AUTOMATICA_NF") and self.cen.get("explode"):
            raise RuntimeError("ORA-20001: procedure falhou")
    def close(self): pass

class Con:
    def __init__(self, cen):
        self.cen=cen; self.c=Cur(cen); self.commits=0; self.rollbacks=0
    def cursor(self): return self.c
    def commit(self): self.commits+=1
    def rollback(self): self.rollbacks+=1
    def close(self): pass

NOTA={"CDFILIAL":"9999","NRLANCTONF":"123","NRACESSONFE":"4"*44,"CDFORNECED":"J01",
      "CDTIPOOPER":"01"}   # 01 = NF Compra (automacao completa)
# candidatos agora vem com a UNIDADE do produto interno (3o item)
CANDS=[("1010261400","BOV RESF ACEM CUBOS 45G","KG"),("1010207000","BOV CONG ACEM","KG"),
       ("2010480102","TOUCA SANFONADA BRANCA DESCARTAVEL - PCT 100 UN","PC")]
# item da nota: o 7o campo e a unidade que o FORNECEDOR usou no XML
it=lambda seq,cp,d,un=None:(seq,cp,d,None,None,1,un)

def roda(cen, dry=False, auto=True, tipo="01"):
    nota=dict(NOTA, CDTIPOOPER=tipo)
    con=Con(cen); r=R.processar_nota(con,nota,dry,auto)
    s=" || ".join(con.c.sqls).upper()
    return {"r":r,"grava":"MERGE INTO TEKNISA.PRODFORNXML" in s,
            "pend":"INSERT INTO AIHUB.AIHUB_PENDENCIA" in s,
            "desativa":"UPDATE TEKNISA.PRODFORNXML SET IDATIVO='N'" in s,
            "sp":"SAVEPOINT ANTES_CONFIRMAR" in s,
            "rbsp":"ROLLBACK TO SAVEPOINT" in s,"procs":con.c.procs}

T=[];F=[0]
def chk(d,c,det=""):
    ok=bool(c); F[0]+= 0 if ok else 1; T.append(("OK " if ok else "FALHA",d,det))

print("="*72);print(" SIMULACAO DO ROBO - fluxo real, banco falso");print("="*72)
base=lambda **k: dict({"cands":CANDS,"depara":{}},**k)

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"}))
chk("1. Itens ja vinculados -> CONFIRMA a nota", r["r"]=="CONFIRMADA", r["r"])
chk("   chama as procedures nativas (baixa AF, valida, confirma)", len(r["procs"])>=3, str(r["procs"]))
chk("   protege com SAVEPOINT antes de confirmar", r["sp"])

r=roda(base(itens=[it("1","P9","XPTO PRODUTO INEXISTENTE")]))
chk("2. Item desconhecido -> vai para a FILA humana", r["r"]=="PENDENTE_HUMANO", r["r"])
chk("   abre pendencia e NAO grava vinculo", r["pend"] and not r["grava"])

r=roda(base(itens=[it("1","P2","TOUCA SANFONADA BRANCA DESCARTAVEL PCT 100 UN")]))
chk("3. Match alto -> robo vincula sozinho", r["grava"], r["r"])
chk("   desativa vinculo anterior (anti-duplicidade)", r["desativa"])

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"}),auto=False)
chk("4. Modo automatico OFF -> NAO confirma", r["r"]!="CONFIRMADA", r["r"])
chk("   nao chama a procedure de confirmacao",
    not any("CONFIRMACAO" in p for p in r["procs"]), str(r["procs"]))

r=roda(base(itens=[it("1","P2","TOUCA SANFONADA BRANCA DESCARTAVEL PCT 100 UN"),
                   it("2","P9","XPTO")]),dry=True)
chk("5. Simulacao (dry) -> nao grava, nao abre fila, nao chama procedure",
    (not r["grava"]) and (not r["pend"]) and (not r["procs"]))

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"},explode=True))
chk("6. Procedure de confirmacao explode -> ERRO_CONFIRMA", r["r"]=="ERRO_CONFIRMA", r["r"])
chk("   desfaz SO a confirmacao (rollback ao savepoint)", r["rbsp"])

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"},status_apos="I"))
chk("7. Confirmou mas a nota continua em 'I' -> acusa erro (nao mente)",
    r["r"]=="ERRO_CONFIRMA", r["r"])

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"},erro_proc="S"))
chk("8. Erro na baixa da AF -> nao confirma", r["r"]=="ERRO_CONFIRMA", r["r"])

r=roda(base(itens=[it("1","P3","ACEM EM CUBOS KG")]))
chk("9. Nota sem gramatura -> NAO auto-vincula especifico (trava)", not r["grava"], r["r"])
chk("   manda para a fila com sugestao do generico", r["pend"])

# ---- UNIDADE DE MEDIDA: granel x embalagem (correcao de 31/07/2026) ----
# O fornecedor mandou CX (caixa) mas o produto interno e KG (granel). Vincular
# assim faz 1 caixa virar 1 quilo no estoque - tem que ir para o humano.
r=roda(base(itens=[it("1","P4","BOV CONG ACEM","CX")]))
chk("10. Unidade CONFLITA (XML=CX x interno=KG) -> NAO auto-vincula",
    not r["grava"], r["r"])
chk("    manda para a fila humana", r["pend"])

# Mesma descricao, agora com a unidade certa: pode vincular sozinho.
r=roda(base(itens=[it("1","P4","BOV CONG ACEM","KG")]))
chk("11. Unidade BATE (XML=KG x interno=KG) -> auto-vincula", r["grava"], r["r"])

# KILO e KG sao a mesma coisa escrita diferente (11.593 itens no banco real).
r=roda(base(itens=[it("1","P4","BOV CONG ACEM","KILO")]))
chk("12. Sinonimo de unidade (KILO = KG) -> auto-vincula", r["grava"], r["r"])

# Sem unidade no XML: nao inventa conflito, decide so pelo texto (como antes).
r=roda(base(itens=[it("1","P4","BOV CONG ACEM")]))
chk("13. Sem unidade no XML -> comportamento antigo, sem travar", r["grava"], r["r"])

# ---- BONIFICACAO (tipo 06): automacao completa ----
# A operacao confirmou em 31/07/2026 que o robo pode tratar por completo
# ("as bonificacoes o robo pode fazer sim"). Tem AF obrigatoria como a compra.
# O Teknisa ja classifica como 06 quando o XML vem com CFOP 5910/6910.
r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"}),tipo="06")
chk("14. Bonificacao com item JA vinculado -> CONFIRMA, como compra",
    r["r"]=="CONFIRMADA", r["r"])
chk("    chama as procedures nativas", len(r["procs"])>=3, str(r["procs"]))

r=roda(base(itens=[it("1","P4","BOV CONG ACEM","KG")]),tipo="06")
chk("15. Bonificacao com match alto -> vincula sozinha", r["grava"], r["r"])

# a mesma nota como COMPRA se comporta igual - prova que a politica e por tipo
r=roda(base(itens=[it("1","P4","BOV CONG ACEM","KG")]),tipo="01")
chk("16. A MESMA nota como compra (01) -> mesmo comportamento", r["grava"], r["r"])

r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"}),tipo="99")
chk("17. Tipo desconhecido -> nao faz nada automatico", r["r"]!="CONFIRMADA", r["r"])

# ---- PENTE-FINO 03/08: De/Para existente alinha o CDPRODUTO do item ----
# O item foi importado com CDPRODUTO='9999' (velho), mas o De/Para ativo do
# fornecedor aponta para '1010261400'. A NF_BAIXA_AF tem que receber o valor do
# De/Para, nao o velho da ITEMPRODUTO. (bug achado no pente-fino)
def roda_captura(cen,tipo="01"):
    nota=dict(NOTA,CDTIPOOPER=tipo); con=Con(cen)
    itens_ref=cen["itens_dict"]
    R.processar_nota(con,nota,False,True)
    return itens_ref
cen=base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"})
cen["itens"]=[(1,"P1","ACUCAR","9999","M1",1,"KG")]  # item com produto VELHO 9999
cen["itens_dict"]=None
# processa e confere que a NF_BAIXA_AF recebeu o produto do De/Para, nao o 9999
class ConSpy(Con):
    def __init__(s,c): super().__init__(c); s.baixa_args=[]
    def cursor(s): return s.c
class CurSpy(Cur):
    def callproc(s,nome,args=None,keyword_parameters=None):
        if nome.endswith("NF_BAIXA_AF") and args: s.baixa.append(args[6])  # P_NEWCDPRODUTO
        return super().callproc(nome,args,keyword_parameters)
c2=Con(cen); c2.c=CurSpy(cen); c2.c.baixa=[]
R.processar_nota(c2,dict(NOTA,CDTIPOOPER="01"),False,True)
chk("18b. De/Para existente -> NF_BAIXA_AF recebe o produto do vinculo, nao o velho",
    (not c2.c.baixa) or all(x=="1010261400" for x in c2.c.baixa),
    "recebeu: "+str(c2.c.baixa))

# ---- status estrito: so 'P' (ou 'A') conta como confirmado ----
r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"},status_apos="C"))
chk("18c. confirmou mas nota foi para status 'C' (inesperado) -> ERRO, nao mente",
    r["r"]=="ERRO_CONFIRMA", r["r"])
r=roda(base(itens=[it("1","P1","ACUCAR")],depara={"P1":"1010261400"},status_apos="P"))
chk("18d. confirmou e nota foi para 'P' (esperado) -> CONFIRMADA", r["r"]=="CONFIRMADA", r["r"])

# ---- RAMO 6 (Operador Logistico): o mesmo produto nas duas arvores ----
# O De/Para do DFE aponta para os ramos 1..5 em 99,98% dos casos; o ramo 6 e
# assunto do modulo OL (setor de Compras). Empatando, ganha o produto direto.
import core.ai_matcher as M
_gemeos=[("6010127203","FRANGO CONG MOELA - CX 18 KG","CX"),   # versao do OL
         ("1010127203","FRANGO CONG MOELA - CX 18 KG","CX")]   # versao direta
_b,_s = M.escolher("FRANGO CONG MOELA - CX 18 KG", _gemeos, "CX")
chk("18. Produto identico nas duas arvores -> escolhe o DIRETO, nao o do OL",
    _b[0]=="1010127203", _b[0])
chk("    e a sugestao mostrada tambem", _s[0]=="1010127203", _s[0])
# invertendo a ordem da lista: a escolha nao pode depender de quem vem antes
_b2,_ = M.escolher("FRANGO CONG MOELA - CX 18 KG", list(reversed(_gemeos)), "CX")
chk("19. A escolha nao depende da ordem da lista", _b2[0]=="1010127203", _b2[0])
# so o ramo 6 disponivel: ai vincula nele mesmo, nao trava
_b3,_ = M.escolher("FRANGO CONG MOELA - CX 18 KG", [_gemeos[0]], "CX")
chk("20. So existe a versao do OL -> usa ela (nao trava o item)",
    _b3[0]=="6010127203", _b3[0])

print()
for st,d,det in T: print("  %-6s %s%s"%(st,d,("   -> "+det) if st!="OK " and det else ""))
print(); print("RESULTADO: %d/%d passaram"%(len(T)-F[0],len(T)))
sys.exit(1 if F[0] else 0)
