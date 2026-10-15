"""
teste_importacao.py - testa SO a importacao do robo (homologacao).

Roda a GERA nativa (pelo codigo do robo) em algumas notas da integracao e mostra
o resultado. Em homologacao as notas nao tem AF, entao o esperado e BLOQUEADA -
o que prova que o robo chama a rotina certa e recusa nota sem AF com seguranca
(nao gera lancamento errado).

Como rodar (na pasta robo, com a conexao configurada):
  py teste_importacao.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from servico_robo import conn, buscar_para_importar, importar_uma

QTD = int(os.getenv("QTD", "3"))


def main():
    con = conn()
    cur = con.cursor()
    # a fila devolve (chave, pode_importar); aqui so interessam as importaveis
    chaves = [ch for ch, pode in buscar_para_importar(cur, QTD) if pode]
    cur.close()
    print("=" * 64)
    print(" TESTE DE IMPORTACAO - %d nota(s) da integracao" % len(chaves))
    print("=" * 64)
    for ch in chaves:
        st = importar_uma(con, ch)
        print("  %s  ->  %s" % (ch, st))
    con.close()
    print("-" * 64)
    print(" BLOQUEADA = a GERA recusou (falta AF/cadastro) = CORRETO e SEGURO.")
    print(" IMPORTADA = gerou o lancamento (so acontece com nota que tem AF).")
    print("=" * 64)


if __name__ == "__main__":
    main()
