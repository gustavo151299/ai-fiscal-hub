# -*- coding: utf-8 -*-
"""Testa a logica do painel isolada do banco: matcher, classificacao,
mensagens de erro e o comportamento fail-safe."""
import sys, os, re
sys.path.insert(0, os.getcwd())
from core.ai_matcher import _sim, sim_pref_generico, escolher, pode_auto, faixa, _tem_grama

T=[];F=[0]
def chk(d,c,det=""):
    ok=bool(c);F[0]+=0 if ok else 1;T.append(("OK " if ok else "FALHA",d,det))

print("="*72);print(" MATCHER - casos reais e limites");print("="*72)
# casos reais vistos em producao/homologacao
casos=[
 ("ACUCAR REFINADO DA BARRA 1KG","ACUCAR REFINADO - PCT 1 KG","auto"),
 ("OLEO DE SOJA PET ACME 900ML","OLEO SOJA - PET 900ML","auto"),
 ("CEBOLA KG","CEBOLA TAM M TIPO 2","auto"),
 ("OLEO VEGETAL FRY SEBELLA 14,5KG","PANGA PREMIUM FILE CONG - CX 10 KG","baixa"),
 ("Agua sem gas","AVE FESTA RESF TEMPERADA","baixa"),
 ("ARROZ PARBOILIZADO URBANO 5KG","ARROZ PARBOILIZADO TIPO 1 - PCT 1 KG","baixa"),
 ("MOELA DE FRANGO ALMF COOPAVEL","FRANGO CONG MOELA - PCT 1 KG",None),
]
for d,i,esp in casos:
    s=_sim(d,i); fx=faixa(s)
    if esp: chk("%-32s -> %-8s (%.2f)"%(d[:32],fx,s), fx==esp, "esperado %s"%esp)
    else:   chk("%-32s -> %-8s (%.2f)"%(d[:32],fx,s), True)

# limites / entradas estranhas (nao pode explodir)
print()
for d,i in [("",""),(None,"X"),("X",None),("   ","   "),("A"*500,"B"*500),
            ("PRODUTO 'COM ASPA'","PRODUTO COM ASPA"),("ITEM <b>","ITEM B"),
            ("100G 200G 300G","PROD 100G"),("R$ 1,50 / KG","PROD KG")]:
    try:
        s=_sim(d or "", i or ""); ok = 0.0 <= s <= 1.0
    except Exception as e:
        ok=False; s=str(e)[:40]
    chk("entrada estranha %-24s -> score valido"%(repr(d)[:24]), ok, str(s))

print()
print("="*72);print(" DECISAO DE AUTO-VINCULO (o que grava sozinho)");print("="*72)
CANDS=[("1010261400","BOV RESF ACEM CUBOS 45G"),("1010207000","BOV CONG ACEM")]
b,bs=escolher("ACEM EM CUBOS KG",CANDS)
chk("sem gramatura: sugere o generico", bs[0]=="1010207000", bs[1])
chk("sem gramatura: NAO auto-vincula", pode_auto("ACEM EM CUBOS KG",b)==False)
b2,_=escolher("BOV RESF ACEM CUBOS 45G",CANDS)
chk("descricao identica: auto-vincula", pode_auto("BOV RESF ACEM CUBOS 45G",b2)==True, str(round(b2[2],3)))
chk("candidatos vazios: nao quebra e nao vincula", escolher("X",[])==(None,None))
chk("pode_auto(None) e False", pode_auto("X",None)==False)

print()
print("="*72);print(" CLASSIFICACAO DOS MOTIVOS (codigos do Teknisa)");print("="*72)
src=open('interface/painel/app.py',encoding='utf-8').read()
# recorta o dicionario CONSIST + _classifica e roda isolado, sem subir o Flask.
# O fim do recorte e a PRIMEIRA rota depois do dicionario - nao uma rota
# especifica: antes estava fixo em "/api/simular" e qualquer rota nova criada
# nesse meio entrava no exec() e quebrava com "name 'app' is not defined".
i=src.find('CONSIST = {'); j=src.find('@app.route', i)
ns={'re':re}; exec(src[i:j],ns); _cl=ns['_classifica']
for cods,esp in [("71","ROBO"),("032","ROBO"),("14","ROBO"),("31","COMPRAS"),
                 ("56","COMPRAS"),("55","COMPRAS"),("54","COMPRAS"),("50","COMPRAS"),
                 ("6","FISCAL"),("25,27","FISCAL"),("30","SISTEMA"),("999","OUTRO"),
                 ("","OUTRO"),("55,71","ROBO")]:
    q,t,a=_cl(cods); chk("codigo %-8s -> %-8s %s"%(cods or "(vazio)",q,t[:30]), q==esp, "esperado "+esp)

print()
print("="*72);print(" MENSAGENS DE ERRO (o que o usuario ve)");print("="*72)
i=src.find('def _erro_amigavel'); j=src.find('def _gravar_vinculo')
ns2={'re':re,'print':lambda *a,**k:None}; exec(src[i:j],ns2); ea=ns2['_erro_amigavel']
for err,deve_ter in [("ORA-00942: table or view does not exist","tabela"),
                     ("ORA-00904: invalid identifier","coluna"),
                     ("ORA-01031: insufficient privileges","permiss"),
                     ("DPY-4011: connection closed","conex"),
                     ("ORA-01722: invalid number","ORA-01722")]:
    m=ea(Exception(err))
    vaza = ("ORA-" in m and "código" not in m) or "Traceback" in m
    chk("%-38s -> mensagem util e sem vazar tecnicalidade"%err[:38],
        (deve_ter.lower() in m.lower()) and not vaza, m[:60])

print()
for st,d,det in T: print("  %-6s %s%s"%(st,d,("   -> "+det) if st!="OK " and det else ""))
print(); print("RESULTADO: %d/%d passaram"%(len(T)-F[0],len(T)))
sys.exit(1 if F[0] else 0)
