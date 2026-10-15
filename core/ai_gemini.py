"""
core/ai_gemini.py  -  Assistente de IA (Gemini)  -  ADD-ON DE TESTE (removivel)

Da uma "segunda opiniao" no casamento de produto (De/Para) usando a API do
Gemini. E TOTALMENTE ISOLADO: se a chave nao existir, se a API falhar, ficar
sem internet ou o teste for desligado, o sistema continua funcionando IGUAL
(cai no matcher normal). Nao afeta a importacao nem a confirmacao das notas.

PRIVACIDADE: envia SO a descricao do item e os NOMES dos produtos candidatos.
NAO envia chave da NF-e, CNPJ, valores nem qualquer dado fiscal.

AUTENTICACAO: as chaves novas do Google comecam com "AQ." e funcionam melhor
pelo cabecalho x-goog-api-key (usado abaixo). Chaves antigas "AIza" tambem
funcionam por esse cabecalho.

COMO LIGAR / REMOVER: ver GEMINI_TESTE.md.
"""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.error

# A chave pode vir de DOIS lugares:
#   1. do atalho (.bat), pela variavel de ambiente  -> vale no arranque
#   2. do PAINEL, gravada em AIHUB_CONFIG           -> vence a do atalho
# A do painel vence de proposito: trocar a chave passa a ser um campo na tela,
# sem editar arquivo no servidor nem reiniciar o painel.
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ORIGEM_CHAVE = "atalho" if API_KEY else "nenhuma"


def definir_chave(chave, origem="painel"):
    """Troca a chave em tempo de execucao. Chave vazia volta para a do atalho."""
    global API_KEY, ORIGEM_CHAVE
    chave = (chave or "").strip()
    if chave:
        API_KEY, ORIGEM_CHAVE = chave, origem
    else:
        API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
        ORIGEM_CHAVE = "atalho" if API_KEY else "nenhuma"
    return API_KEY


def chave_mascarada():
    """Como mostrar a chave na tela SEM revela-la. Nunca devolver a chave crua:
    o painel ainda nao tem login, entao qualquer um na rede veria o valor."""
    if not API_KEY:
        return ""
    if len(API_KEY) <= 12:
        return API_KEY[:3] + "..."
    return "%s...%s" % (API_KEY[:6], API_KEY[-4:])
# MODELO: nome de modelo ENVELHECE. O Google descontinua modelos para chaves
# novas (ex.: "gemini-2.5-flash-lite" virou indisponivel, dando HTTP 404
# "no longer available to new users"). Por isso NAO cravamos um nome fixo e
# torcemos: o painel LISTA os modelos que a chave realmente pode usar
# (ver listar_modelos) e a pessoa ESCOLHE. O default abaixo e so o palpite
# inicial ate ela escolher - se falhar, o proprio erro manda escolher outro.
# DEFAULT = "gemini-flash-latest": e um ALIAS que o Google mantem sempre
# apontando para o flash atual. E o unico nome que NAO envelhece - por isso e o
# ponto de partida. A pessoa pode trocar por um modelo fixo na tela; a lista de
# modelos disponiveis vem da propria conta (listar_modelos).
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
ORIGEM_MODELO = "atalho" if os.getenv("GEMINI_MODEL") else "padrao"
# Proxy da empresa, se houver (ex.: http://usuario:senha@proxy:8080). Se vazio,
# usa o proxy do sistema (registro do Windows). Sem proxy, vai direto.
PROXY = os.getenv("GEMINI_PROXY", "").strip() or os.getenv("HTTPS_PROXY", "").strip()
_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"
_URL_LISTA = "https://generativelanguage.googleapis.com/v1beta/models"


def definir_modelo(nome, origem="painel"):
    """Troca o modelo em runtime. Vazio volta ao default/atalho."""
    global MODEL, ORIGEM_MODELO
    nome = (nome or "").strip().replace("models/", "")
    if nome:
        MODEL, ORIGEM_MODELO = nome, origem
    else:
        MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
        ORIGEM_MODELO = "atalho" if os.getenv("GEMINI_MODEL") else "padrao"
    return MODEL


def _abrir(req, timeout):
    """urlopen respeitando o proxy da empresa, se houver."""
    if PROXY:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}))
        return op.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def listar_modelos(timeout=20):
    """Pergunta ao Google quais modelos ESTA chave pode usar para generateContent.
    Retorna (lista_de_nomes, erro). E o antidoto para nome de modelo que envelhece:
    em vez de adivinhar, mostramos o que a propria conta oferece."""
    if not API_KEY:
        return None, "Sem chave."
    try:
        req = urllib.request.Request(
            _URL_LISTA + "?pageSize=200",
            headers={"x-goog-api-key": API_KEY}, method="GET")
        r = _abrir(req, timeout)
        try:
            dados = json.loads(r.read().decode("utf-8"))
        finally:
            r.close()
        nomes = []
        for m in dados.get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                nomes.append((m.get("name") or "").replace("models/", ""))
        # os "flash" (baratos e rapidos) primeiro - e o que serve para o De/Para
        nomes.sort(key=lambda n: (0 if "flash" in n else 1, n))
        return nomes, None
    except urllib.error.HTTPError as e:
        try:
            corpo = e.read().decode("utf-8")[:200]
        except Exception:
            corpo = ""
        return None, "HTTP %s - %s" % (e.code, corpo)
    except Exception as e:
        return None, str(e)[:200]


def disponivel():
    """True se ha chave configurada (o teste ainda depende do toggle no banco)."""
    return bool(API_KEY)


def _chamar(body, timeout=30):
    """Chamada crua a API. Retorna (dados, erro_texto). Nunca levanta excecao."""
    if not API_KEY:
        return None, "Sem chave (GEMINI_API_KEY vazio)."
    try:
        req = urllib.request.Request(
            _URL % MODEL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
            method="POST")
        r = _abrir(req, timeout)
        try:
            return json.loads(r.read().decode("utf-8")), None
        finally:
            r.close()
    except urllib.error.HTTPError as e:
        try:
            corpo = e.read().decode("utf-8")[:300]
        except Exception:
            corpo = ""
        msg = "HTTP %s - %s" % (e.code, corpo)
        print(" Gemini erro:", msg, flush=True)
        return None, msg
    except Exception as e:
        print(" Gemini erro:", str(e)[:200], flush=True)
        return None, str(e)[:200]


def sugerir(descricao, candidatos, timeout=35):
    """descricao = texto do item como veio na nota.
    candidatos   = lista de (codigo, nome) de produtos internos.
    Retorna {'cd','nome','motivo'} ou None."""
    if not API_KEY or not descricao or not candidatos:
        return None
    lista = "\n".join("- %s: %s" % (c[0], (c[1] or "")) for c in candidatos[:40])
    prompt = (
        "Voce ajuda a casar um produto de uma nota fiscal de entrada com o "
        "produto interno correto do estoque. Dada a DESCRICAO do item na nota e "
        "uma lista de CANDIDATOS (codigo: nome), escolha o UNICO melhor "
        "correspondente. REGRAS IMPORTANTES:\n"
        "1) A ORDEM das palavras e as ABREVIACOES NAO mudam o produto. Ex.: "
        "'MOELA DE FRANGO' e o mesmo que 'FRANGO CONG MOELA'; 'ACEM EM CUBOS' e "
        "o mesmo que 'BOV RESF ACEM CUBOS'. Compare o PRODUTO, nao a escrita.\n"
        "2) NAO escolha candidatos com a palavra 'INATIVO' no nome, a menos que "
        "seja o unico correspondente possivel.\n"
        "3) Se de fato nenhum servir, use cd null.\n"
        "Responda SOMENTE em JSON, sem texto extra, no formato "
        '{"cd":"<codigo ou null>","motivo":"<motivo curto em portugues>"}.\n\n'
        "DESCRICAO: " + str(descricao) + "\n\nCANDIDATOS:\n" + lista
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 250,
                             "responseMimeType": "application/json"},
    }
    data, err = _chamar(body, timeout)
    if data is None:
        return None
    try:
        txt = data["candidates"][0]["content"]["parts"][0]["text"]
        obj = json.loads(txt)
        cd = obj.get("cd")
        motivo = (obj.get("motivo") or "").strip()
        if not cd or str(cd).strip().lower() in ("null", "none", ""):
            return {"cd": None, "nome": None, "motivo": motivo or "Nenhum candidato serve."}
        nome = None
        for c in candidatos:
            if str(c[0]) == str(cd):
                nome = c[1]
                break
        return {"cd": str(cd), "nome": nome, "motivo": motivo}
    except Exception:
        return None


def testar(timeout=45):
    """Faz uma chamada minima so para DIAGNOSTICO. Retorna (ok, detalhe)."""
    if not API_KEY:
        return False, "Sem chave: preencha GEMINI_API_KEY no atalho e reinicie."
    body = {"contents": [{"parts": [{"text": "Responda apenas: ok"}]}],
            "generationConfig": {"maxOutputTokens": 10}}
    data, err = _chamar(body, timeout)
    if data is None:
        return False, (err or "falhou")
    try:
        txt = data["candidates"][0]["content"]["parts"][0]["text"]
        return True, "Conectou. Modelo %s. Resposta: %s" % (MODEL, (txt or "").strip()[:50])
    except Exception:
        return True, "Conectou (modelo %s), mas resposta em formato inesperado." % MODEL
