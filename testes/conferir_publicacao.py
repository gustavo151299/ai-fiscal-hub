# -*- coding: utf-8 -*-
r"""conferir_publicacao.py - O QUE VAZARIA SE O REPOSITORIO FOSSE PUBLICO.

Varre SO os arquivos que o git versionaria (respeita o .gitignore) e procura o
que nao pode sair da empresa: credencial, endereco de servidor, dado pessoal,
identificador de cliente e de fornecedor.

Roda antes de cada publicacao:
    py testes\conferir_publicacao.py

Sai com codigo 1 se achar qualquer coisa, para poder entrar em hook de commit.
"""
import os
import re
import subprocess
import sys

# Cada regra: (gravidade, rotulo, padrao, excecao_opcional)
# A senha NAO mora aqui, nem em pedacos: num repositorio publico, concatenar
# dois trechos e o mesmo que escrever o segredo. Ela vem do CONFIG_LOCAL.bat,
# que nao e versionado. Sem o arquivo, esta regra e pulada e o relatorio diz isso.
def _senha_local():
    try:
        with open("CONFIG_LOCAL.bat", encoding="utf-8", errors="ignore") as fh:
            m = re.search(r"TEKNISA_DB_PASS=(.+)", fh.read())
        return m.group(1).strip() if m else None
    except Exception:
        return None


_SENHA = _senha_local()

REGRAS = [
    ("CRITICO", "senha do banco em texto puro",
     re.escape(_SENHA) if _SENHA else r"(?!x)x", None),
    ("CRITICO", "string de conexao Oracle", r"\b[\w.-]+:\d{4}/\w+\b",
     r"localhost|127\.0\.0\.1|exemplo|servidor"),
    # ESTA REGRA DEIXOU PASSAR UMA CHAVE REAL. Ela exigia ASPAS depois do "=",
    # e num .bat nao ha aspas: uma linha "set GEMINI_API_KEY=<valor>" passou
    # foi parar num repositorio publico. Agora sao tres regras: o formato das
    # chaves do Google (que e o que de fato identifica), e a atribuicao com OU
    # SEM aspas.
    ("CRITICO", "chave do Google (formato novo)", r"AQ\.[A-Za-z0-9_-]{20,}",
     r"AQ\.chave|EXEMPLO|coloque|placeholder"),
    ("CRITICO", "chave do Google (formato antigo)", r"AIza[A-Za-z0-9_-]{30,}", None),
    ("CRITICO", "chave de API atribuida",
     r"(api[_-]?key|apikey|token|secret|senha|password)\s*[=:]\s*['\"]?[A-Za-z0-9_\-.]{12,}",
     r"getenv|environ|EXEMPLO|coloque|placeholder|<|\$env|%[A-Z_]+%"
     r"|SUA_CHAVE|_AQUI|_ler_segredo|_senha_local|\bxxx|\.\.\."),
    # o zerado e o marcador que substituiu os reais, nao e CNPJ de ninguem
    ("ALTO", "CNPJ de fornecedor", r"[JF]\d{14}", r"[JF]0{14}"),
    ("ALTO", "matricula de operador (dado pessoal)", r"\b0\d{10,11}\b", None),
    ("MEDIO", "chave de NF-e real", r"\b\d{44}\b", None),
    ("MEDIO", "IP de rede interna", r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", None),
]

def _termos_privados(caminho="termos_sensiveis.txt"):
    """Nomes de cliente, servidor e fornecedor - fora do versionamento.

    Mesma razao da senha: uma lista de termos proibidos escrita DENTRO do
    verificador seria, ela propria, o vazamento. Sem o arquivo o relatorio
    avisa, em vez de dizer "limpo" sem ter conferido.
    """
    regras = []
    try:
        with open(caminho, encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "|" not in linha:
                    continue
                grav, rotulo, padrao = [x.strip() for x in linha.split("|", 2)]
                regras.append((grav, rotulo, padrao, None))
    except FileNotFoundError:
        return None
    return regras


BINARIOS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".pyc", ".ico")


def versionados():
    """TUDO o que o git publicaria: o que ja esta rastreado MAIS o que entraria.

    A primeira versao usava so `git add --dry-run`, que depois de um commit
    reporta apenas o que mudou. Resultado: ela varria 2 arquivos de 80 e
    anunciava "LIMPO" - o mesmo erro de dizer que esta limpo sem ter olhado.
    """
    def _git(*args):
        try:
            return subprocess.run(["git"] + list(args), capture_output=True,
                                  text=True, timeout=120).stdout
        except Exception as e:
            print(" nao consegui perguntar ao git (%s)." % e)
            sys.exit(2)

    rastreados = _git("ls-files").splitlines()
    novos = re.findall(r"add '(.+?)'", _git("add", "-An", "."))
    arqs = sorted(set(rastreados) | set(novos))
    # o proprio verificador cita os padroes que procura: ele nao se audita
    return [a for a in arqs
            if not a.lower().endswith(BINARIOS)
            and "conferir_publicacao" not in a]


def main():
    arqs = versionados()
    privadas = _termos_privados()
    regras = REGRAS + (privadas or [])
    print("=" * 76)
    print(" CONFERENCIA DE PUBLICACAO - %d arquivo(s) de texto seriam publicados"
          % len(arqs))
    print("=" * 76)
    if privadas is None:
        print("")
        print("  " + '[! ] termos_sensiveis.txt nao encontrado: nomes de cliente,')
        print("       servidor e fornecedor NAO foram conferidos nesta rodada.")
    if not _SENHA:
        print("")
        print("  " + '[! ] CONFIG_LOCAL.bat nao encontrado: a senha do banco NAO')
        print("       foi conferida nesta rodada.")
    achados = []
    for caminho in arqs:
        try:
            with open(caminho, encoding="utf-8", errors="ignore") as fh:
                linhas = fh.read().splitlines()
        except Exception:
            continue
        for n, linha in enumerate(linhas, 1):
            for grav, rotulo, padrao, excecao in regras:
                m = re.search(padrao, linha, re.I)
                if not m:
                    continue
                if excecao and re.search(excecao, linha, re.I):
                    continue
                achados.append((grav, rotulo, caminho, n, m.group(0)[:46]))

    # NAO CONFERIU NAO E LIMPO. Se as listas privadas nao estavam presentes,
    # o veredito tem de dizer isso e falhar: dizer "limpo" sem ter procurado o
    # nome do cliente nem a senha e o lado permissivo do erro.
    parcial = (privadas is None) or (not _SENHA)
    if not achados and parcial:
        print("")
        print("  CONFERIDO EM PARTE: nada encontrado NAS REGRAS QUE RODARAM,")
        print("  mas as conferencias acima ficaram de fora. Nao publicar assim.")
        print("")
        return 2
    if not achados:
        print("")
        print("  LIMPO: nenhuma credencial, endereco de servidor, dado pessoal")
        print("         ou identificador de cliente nos arquivos versionados.")
        print("")
        return 0

    ordem = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}
    achados.sort(key=lambda a: (ordem[a[0]], a[2], a[3]))
    atual = None
    for grav, rotulo, caminho, n, trecho in achados:
        if rotulo != atual:
            print("\n  [%s] %s" % (grav, rotulo))
            atual = rotulo
        print("      %s:%s   %s" % (caminho, n, trecho))
    print("\n" + "=" * 76)
    print(" %d ocorrencia(s). NAO publicar antes de tratar." % len(achados))
    print("=" * 76)
    return 1


if __name__ == "__main__":
    sys.exit(main())
