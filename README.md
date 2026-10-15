# Gestão de Recebimento de NF-e (AI Fiscal Hub)

Automatiza o **miolo** do recebimento de notas fiscais de entrada no Teknisa:
casa o produto da nota com o produto interno (De/Para) e confirma o recebimento
pelas rotinas nativas do ERP. O que o robô não reconhece com certeza vai para uma
fila de revisão humana — e **cada aprovação humana vira De/Para permanente**, de
modo que a base de reconhecimento cresce sozinha com o uso.

O robô **não** faz a importação da SEFAZ nem a aprovação final por alçada: ele
leva a nota de `I` (integrada) até `P` (pendente de aprovação), e para aí.

**Cliente:** um grupo de alimentacao coletiva · **ERP:** Teknisa (Oracle)

---

## Como rodar

1. Copie `CONFIG_LOCAL.EXEMPLO.bat` para `CONFIG_LOCAL.bat` e preencha a senha
   e o DSN do banco. Esse arquivo **não** acompanha o código.
2. Instale as dependências: `py -m pip install -r requirements.txt`
3. Dois cliques em:

| Atalho | O que faz |
|---|---|
| `interface\painel\INICIAR_PAINEL.bat` | painel **somente leitura** |
| `interface\painel\INICIAR_PAINEL_ESCRITA.bat` | painel que **grava** De/Para |
| `robo\RODAR_ROBO.bat` | uma rodada do robô (para a Tarefa Agendada) |

O painel abre em http://localhost:5000 e **se recusa a subir** sem `TEKNISA_DB_DSN`
e `TEKNISA_DB_PASS` — proteção contra apontar para o banco errado.

**Duas travas independentes** governam a escrita, e as duas precisam estar
ligadas para o robô agir sozinho:
1. `AIHUB_MODO_ESCRITA=1` no atalho — autoriza gravar;
2. "Grava sozinho" ligado no painel (`MODO_AUTO` em `AIHUB_CONFIG`) — autoriza automatizar.

## Estrutura (o que está vivo)

```
ai-fiscal-hub/
├── interface/painel/app.py      backend Flask (~36 rotas) - arquivo central
├── interface/painel/index.html  o painel inteiro: HTML + CSS + JS num arquivo
├── robo/servico_robo.py         lê notas, casa produto, confirma recebimento
├── core/ai_matcher.py           o comparador De/Para (rapidfuzz)
├── core/ai_gemini.py            add-on removível de IA (segunda opinião)
├── sql/INSTALAR_PRODUCAO_COMPLETO.sql   instalação de produção (idempotente)
└── testes/                      4 baterias que rodam SEM banco
```

A pasta só tem o que roda. Os restos das versões anteriores foram removidos em
03/08/2026 — se você encontrar `main.py`, `pipeline/`, `database/` ou
`notifications/` em alguma cópia, é uma cópia velha.

## Testes (não precisam de banco)

```
py testes\simular_robo.py       16 cenários do robô (banco simulado, fluxo real)
py testes\simular_painel.py     40 testes de matcher, classificação e erros
py testes\tipos_oracle.py       6 tipos que o Oracle devolve (inclui CLOB)
py testes\conferir_binds.py interface\painel\app.py robo\servico_robo.py
```

Rodar sempre depois de mexer no código. Todos devem passar 100%.

## Instalação no banco

Use o script único `sql/INSTALAR_PRODUCAO_COMPLETO.sql` — faz tudo, na ordem, e
pode rodar mais de uma vez sem erro. Detalhes em `INSTALACAO_COMPLETA.md` e
`PRODUCAO_CHECKLIST.md`.

> Os índices (`PARTE C`) são o item de maior risco: sem eles as consultas variam
> de 2 a 5 minutos.

---

## Segurança e dados

Este repositório é **público e sanitizado**. Nada que identifique o cliente, o
servidor ou os fornecedores acompanha o código:

| Fora do repositório | Por quê |
|---|---|
| `CONFIG_LOCAL.bat`, `CONFIG_PRODUCAO_LEITURA.bat` | senha do banco e endpoint do RDS |
| `termos_sensiveis.txt` | a lista de nomes privados a vigiar: dentro do código, ela mesma seria o vazamento |
| `docs/transcricao/` | transcrições de trabalho, com senha e endpoints colados |
| `base_conhecimento/` | manuais em PDF do fornecedor do ERP, obra de terceiro |
| `desfazer_demo.sql` | chaves de NF-e reais |

Nomes de fornecedor viraram `FORNECEDOR A`, `B`, `C`; CNPJs viraram
`J00000000000000`; matrículas de operador viraram `<matricula>`. Os números
medidos e o raciocínio de engenharia ficaram intactos.

**Antes de cada publicação:**

```
py testes\conferir_publicacao.py
```

Ele varre **só o que o git versionaria** (respeita o `.gitignore`) procurando
credencial, endereço de servidor, CNPJ, matrícula e chave de NF-e. Sai com
código 1 se achar algo e com **2 se não tiver conferido tudo** — porque "não
procurei" não é "está limpo".

Para rodar o projeto você precisa do seu próprio banco Teknisa e de um
`CONFIG_LOCAL.bat` criado a partir do `CONFIG_LOCAL.EXEMPLO.bat`.
