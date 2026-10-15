# Assistente de IA (Gemini) — guia do teste

Add-on **experimental e removível** para testar, em **homologação**, quanto a
IA melhora o acerto do casamento de produto (De/Para). Ele dá uma **segunda
opinião** nos itens em dúvida da "Fila do robô" — aparece ao lado da sugestão
do robô, e você compara.

> **Isolado por natureza:** se a chave não existir, a IA falhar, ficar sem
> internet ou o teste for desligado, o sistema funciona **exatamente igual**.
> A IA **não** decide nada sozinha — é só uma sugestão a mais para você.
>
> **Privacidade:** envia à IA **apenas a descrição do produto** e os nomes dos
> candidatos. **Não** envia chave da NF-e, CNPJ, valores nem dado fiscal.

---

## Como LIGAR (em homologação)

**1. Gere uma chave grátis** no Google AI Studio (aistudio.google.com) — em
"Get API key". É grátis e não pede cartão.

**2. Crie a tabela do teste.** Conectado como **AIHUB**, rode a **PARTE D** do
`sql\INSTALAR_PRODUCAO_COMPLETO.sql`
(cria `AIHUB.AIHUB_GEMINI` e a chave `USA_GEMINI`, que nasce desligada).

**3. Coloque a chave no atalho.** Abra o `INICIAR_PAINEL_ESCRITA.bat` e
preencha a linha:
```
set GEMINI_API_KEY=SUA_CHAVE_AQUI
```
(O modelo você escolhe **na própria tela** — o painel lista os que a sua chave
pode usar. O padrão é `gemini-flash-latest`, um alias que o Google mantém
sempre no flash atual, então não fica desatualizado.)

**4. Reinicie o painel** por esse atalho.

**5. Ligue o teste.** No painel, **Configurações → "Assistente de IA
(Gemini) — teste"** → ligue a chave. Se faltar a chave da API, o próprio
painel avisa.

Pronto. A cada **30 minutos**, para os itens da fila que ainda não têm
opinião, o painel consulta a IA e grava a sugestão. Ela aparece na "Fila do
robô" numa caixa roxa **"Opinião da IA (Gemini)"**, com o motivo e um botão
**"Usar sugestão da IA"**.

---

## Como acompanhar o "aprendizado"

- Compare, item a item, a **sugestão do robô** (matcher) com a **opinião da
  IA**. Onde elas divergem, veja quem acertou.
- Quando você aprova um vínculo (do robô ou da IA), ele vira De/Para e o robô
  **aprende** — nas próximas notas ele reconhece sozinho.
- Foque nas faixas **Média** e **Baixa** confiança: é onde a IA tende a ajudar
  mais.

---

## Custo e limites (plano grátis)

- Roda em lote a cada 30 min, no máximo ~20 itens novos por rodada, com pausa
  entre chamadas (respeita ~15 req/min). Fica **muito abaixo** do limite grátis
  (~1.000 req/dia).
- Só avalia itens **novos** (que ainda não têm opinião) → volume naturalmente
  baixo. Custo em tokens: praticamente nada.
- Lembrete: no **plano grátis** o Google **pode usar os dados** enviados. Por
  isso este teste é para **homologação**. Em produção, o certo é o **plano
  pago** (dados não usados).

---

## Como REMOVER de vez (volta ao sistema sem IA)

1. No painel, **Configurações** → desligue "Assistente de IA".
   (ou: `UPDATE AIHUB.AIHUB_CONFIG SET VALOR='0' WHERE CHAVE='USA_GEMINI'; COMMIT;`)
2. Tire a chave do `.bat` (`set GEMINI_API_KEY=`).
3. Opcional, para limpar tudo:
   - `DROP TABLE AIHUB.AIHUB_GEMINI;`
   - apague o arquivo `core\ai_gemini.py`.

Feito isso, o sistema volta a rodar **só com o matcher**, sem nenhuma
dependência da IA.
