# Demo: real outputs

Two runs, each on a clean copy of the tree (no git history) on macOS, Darwin arm64:

| Sections | Run | Tree |
| --- | --- | --- |
| Summary and §4 | 2026-10-26, a Monday, 09:23 to 09:32 in Brazil | `main` at `73337dd`: T-62 and #12's dependencies |
| §1 | 2026-10-26, a clean clone | `main` at `4f8fe15`: T-65 too |
| §2 and §3 | 2026-10-24, 00:00 to 00:20 in Brazil | the T-55 tree, before T-57 to T-62 |

The service ran `gpt-6-luna` at low effort, `text-embedding-3-large`, live Tavily and RRF without
a reranker. The Tavily key reached the container through a compose override from the shell's
environment, not `.env`. Linux runs `make verify` in CI, on ubuntu with the network cut.

## Summary

From the 2026-10-26 run.

| # | Question | Agents | Status | Answer, shortened |
| --- | --- | --- | --- | --- |
| S01 | What's the difference between the Get Clássica and the Get Smart? | knowledge | answered | Smart: Android, menus, stock and kitchen messages on the terminal. Clássica: credit in 2 business days, a single rate from 1,69% on debit and 3,79% on credit. R$ 79,90 and R$ 129,90 a month when the revenue for exemption is not reached. It says there is nothing to compare the Clássica's features with. Three pages, the Get Smart page among them. |
| S02 | What's the weather forecast in Porto Alegre tomorrow? | knowledge (web) | degraded | "No momento não consigo consultar essa informação na internet com segurança." No result covered 27/10: the "tomorrow" page found was a cached 23/10, the others held fishing winds and the current conditions. |
| S03 | When will the money from yesterday's sales be deposited? | support | answered | "O dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 26/10/2026." |
| S04 | Do I need a bank account to receive my sales via Pix? | knowledge | answered | The Get Conta, a free digital account, receives and sells through Pix; nothing says whether a separate bank account is needed. |
| S05 | My card machine won't connect to the internet, what should I do? | knowledge | answered | From the Get Mini manual: the steps for error ADQ 1-18 (weak signal) and for ADQ 1-87 (chip not seated); no general reconnect steps. |
| S06 | How does receivables advance (antecipação) work with Getnet? | knowledge | answered | Receive credit sales before their term for a fee; the Getnet Brasil app shows eligible receivables, simulates the cost and shows the net amount before confirming. |
| S07 | What's the euro exchange rate today? | knowledge (web) | degraded | "No momento não consigo consultar essa informação na internet com segurança." The results held the euro only in dollars, so no quote in reais. |
| S08 | My card machine is showing a transaction decline error. | support | answered | "A transação de R$ 1.240,00 foi negada porque, segundo o banco emissor, havia saldo ou limite insuficiente no cartão." |
| S09 | How many installments can I split a sale into with the crediário? | knowledge | answered | Up to 48 installments; the limit depends on the card issuer's credit policy. |
| S10 | Can I sell through WhatsApp using the Payment Link? | knowledge | answered | "Sim. Com o Link de Pagamento Getnet, você pode vender pelo WhatsApp: basta criar o link e compartilhá-lo por onde quiser." |
| Follow-up | "Quando cai o dinheiro das vendas de ontem?" → "E o de amanhã?" | support, support | answered | R$ 231,26 today; then R$ 60,18 crediting 27/10/2026. |
| Compound | "Quando cai o dinheiro de ontem e como funciona a antecipação?" | support, knowledge | answered | Both parts in one reply, account and website sources. |
| Human review | refund case → operator reply → "E em que dia foi feita essa venda?" | support | paused → answered | The operator's reply delivered with the next message; then "A venda de R$ 89,50 no Mastercard final 7788 foi feita em 25/10/2026." |
| Write action | a terminal still offline → "Pode abrir, confirmo." → "Confirmo." | support | pending confirmation → answered | The ticket operation rendered by code, run once on confirmation: TKT-698AE7E1; the repeated confirmation returns the same receipt. |

## 1. Before any container: `make verify`

From a clean clone of `4f8fe15` on 2026-10-26, as in [`reports/verify.md`](../reports/verify.md).
No key in any check's environment: 379 passed, 3 skipped, the guardrail eval 28/28, and the eval
replay at pass-all 95/100 with the red team at 40/40. All checks passed in 24.7 s after the sync.

## 2. Replicas: `make scale`

From the T-55 run on 2026-10-24; not run again since.

Postgres, two replicas and the round-robin balancer came up healthy, and `/ready` on port 8080
answered `{"status":"ready","storage":"postgres","chunks":270}`. The same walkthrough as §4, sent
through the balancer, ran to its end:
- its 18 merchant messages split 9 and 9 between the replicas;
- the human-review loop finished through the balancer;
- the write was proposed on `api-a` and confirmed on `api-b` (same trace id in both replicas'
  logs), and the repeated confirmation returned the same ticket.

## 3. Observability: `make observe`

From the T-55 run on 2026-10-24; not run again since.

After one merchant message:
- **Prometheus** scraped `support-api` (`up`), with the four alert rules loaded:
  `NodeLatencyP95High`, `EscalationSpike`, `VendorFailures`, `CostPerResolutionHigh`.
- **Grafana** served the dashboard "Support: cost, resolution and vendors".
- **Jaeger** held the turn's trace: `POST /chat`; `invoke_agent guardrail`, `router`, `knowledge`,
  `collect`, `compose` and `remember`; and two `chat gpt-6-luna` spans.

## 4. One node: `make up`, then `./scripts/demo.sh`

From the 2026-10-26 run. The walkthrough runs:
- the ten challenge scenarios, a follow-up and a compound request;
- the human-review loop: the case, the operator's reply, the next message that receives it, the
  stored conversation and the audit log;
- the LGPD erasure;
- the confirmed write.

Answers are in pt-BR, as a merchant gets them, and web answers change with the live results. The
script trims each response to what a reader checks. S02 and S07 answered that they could not look
it up: the search answered, and the web prompt found no result that covered the day asked or gave
the euro in reais, so it withheld the answer instead of guessing.

```
$ ./scripts/demo.sh
{"status":"ok"}

=== S01 product comparison ===
> What's the difference between the Get Clássica and the Get Smart?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA Get Smart é um equipamento Android que permite realizar operações diretamente na maquininha, como incluir cardápio, controlar estoque e trocar mensagens entre cozinha e salão. A Get Clássica tem recebimento em 2 dias úteis e taxa única nas principais bandeiras, com taxas a partir de 1,69% no débito e 3,79% no crédito à vista. A mensalidade de utilização é R$ 79,90 para a Get Clássica e R$ 129,90 para a Get Smart caso não sejam atingidos os faturamentos indicados para isenção ou redução do aluguel. Não há informação sobre as operações ou recursos disponíveis na Get Clássica que permita compará-los com os da Get Smart.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/get-smart/",
    "kb: https://site.getnet.com.br/maquininha/get-classica/",
    "kb: https://site.getnet.com.br/maquininha/get-smart/"
  ]
}

=== S02 weather, from the web ===
> What's the weather forecast in Porto Alegre tomorrow?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nNo momento não consigo consultar essa informação na internet com segurança.\nTente de novo em instantes ou confira em uma fonte oficial.",
  "sources": []
}

=== S03 settlement, from the account ===
> When will the money from yesterday's sales be deposited?
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 26/10/2026.",
  "sources": [
    "account: get_settlement_schedule"
  ]
}

=== S04 Pix without a bank account ===
> Do I need a bank account to receive my sales via Pix?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nPara receber vendas via Pix pela Getnet, você pode usar a Get Conta, uma conta digital gratuita que permite receber dinheiro e vender por Pix. Não há informação sobre a necessidade de ter uma conta bancária separada.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/",
    "kb: https://site.getnet.com.br/duvidas/pix-mais-respostas/"
  ]
}

=== S05 connectivity, from the manuals ===
> My card machine won't connect to the internet, what should I do?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nSe a maquininha Get Mini apresentar o erro ADQ 1-18, que indica sinal fraco e falha para concluir a transação, pressione Função > 25 > Verde > digite a Senha do Operador > Verde > Função > 46 > Verde e faça uma venda teste. Para o erro ADQ 1-87, confirme se o chip está devidamente inserido e siga as instruções específicas do manual. Não há informação sobre passos gerais para reconectar a maquininha à internet.",
  "sources": [
    "kb: https://site.getnet.com.br/wp-content/uploads/2026/06/getnet_12_06-26_Manul_GetMini_V2.pdf"
  ]
}

=== S06 receivables advance ===
> How does receivables advance (antecipação) work with Getnet?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA antecipação permite receber antes do prazo os valores de vendas no crédito, à vista ou parceladas, mediante uma taxa. Pelo app Getnet Brasil, você consulta os recebíveis elegíveis, simula o custo e vê o valor líquido antes de confirmar; a disponibilidade depende da solução contratada.",
  "sources": [
    "kb: https://site.getnet.com.br/quando-vale-a-pena-antecipar-as-suas-vendas-no-cartao/"
  ]
}

=== S07 euro, from the web ===
> What's the euro exchange rate today?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nNo momento não consigo consultar essa informação na internet com segurança.\nTente de novo em instantes ou confira em uma fonte oficial.",
  "sources": []
}

=== S08 declined transaction ===
> My card machine is showing a transaction decline error.
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA transação de R$ 1.240,00 foi negada porque, segundo o banco emissor, havia saldo ou limite insuficiente no cartão.",
  "sources": [
    "account: get_transactions"
  ]
}

=== S09 crediário installments ===
> How many installments can I split a sale into with the crediário?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO Crediário permite parcelamento em até 48 vezes. O limite efetivo depende das políticas de crédito do emissor do cartão; você pode consultar as opções na simulação feita diretamente na maquininha.",
  "sources": [
    "kb: https://site.getnet.com.br/crediario/",
    "kb: https://site.getnet.com.br/duvidas/crediario/"
  ]
}

=== S10 Payment Link on WhatsApp ===
> Can I sell through WhatsApp using the Payment Link?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nSim. Com o Link de Pagamento Getnet, você pode vender pelo WhatsApp: basta criar o link e compartilhá-lo por onde quiser.",
  "sources": [
    "kb: https://site.getnet.com.br/link-de-pagamento/"
  ]
}

=== Follow-up, turn 1 ===
> Quando cai o dinheiro das vendas de ontem?
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 26/10/2026.",
  "sources": [
    "account: get_settlement_schedule"
  ]
}

=== Follow-up, turn 2 ===
> E o de amanhã?
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "O dinheiro das vendas de 24/10/2026, R$ 60,18 líquidos, está previsto para cair em 27/10/2026.",
  "sources": [
    "account: get_settlement_schedule"
  ]
}

=== Compound request ===
> Quando cai o dinheiro de ontem e como funciona a antecipação?
{
  "status": "answered",
  "agents": [
    "support",
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 26/10/2026. A antecipação permite receber antes do prazo original os valores de vendas no crédito, à vista ou parceladas, mediante uma taxa. Não é um empréstimo: é o adiantamento de valores de vendas já realizadas, sem contratação de uma nova dívida. No app Getnet Brasil, você pode consultar os recebíveis elegíveis, simular o custo e ver o valor líquido antes de confirmar a operação. A disponibilidade depende de as vendas serem elegíveis dentro da solução contratada.",
  "sources": [
    "account: get_settlement_schedule",
    "kb: https://site.getnet.com.br/quando-vale-a-pena-antecipar-as-suas-vendas-no-cartao/"
  ]
}

=== Human review 1: an individual case pauses ===
> Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?
{
  "status": "pending_human",
  "agents": [
    "support"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nNão consegui resolver isso com segurança por aqui, então vou encaminhar para um atendente humano.\nEle já recebe o resumo desta conversa, então você não precisa repetir nada.\nGuarde o número de protocolo ESC-225752819D para acompanhar o caso.",
  "sources": [],
  "escalation": {
    "reason": "individual_case",
    "protocol": "ESC-225752819D"
  }
}

=== Human review 2: the operator reads the case ===
{
  "status": "open",
  "case": {
    "protocol": "ESC-225752819D",
    "reason": "individual_case",
    "attempts": [
      "router: individual_case, plan support, confidence 0.99",
      "step 1 (support) stopped: an individual case, which only a person may decide; query: Por que o estorno da venda no Mastercard final 7788 ainda não apareceu?"
    ],
    "proposed_draft": "Entendo sua preocupação com o estorno da venda no Mastercard final 7788. Nossa equipe está verificando o caso e vai analisar o que aconteceu."
  }
}

=== Human review 3: the operator replies ===
{
  "status": "answered",
  "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis.",
  "sources": [
    "operator: ESC-225752819D"
  ]
}

=== Human review 4: the next message receives the reply, then builds on it ===
> E em que dia foi feita essa venda?
{
  "delivered": [
    {
      "protocol": "ESC-225752819D",
      "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis."
    }
  ],
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A venda de R$ 89,50 no Mastercard final 7788 foi feita em 25/10/2026.",
  "sources": [
    "account: get_transactions"
  ]
}

=== Human review 5: the conversation as stored ===
{
  "status": "active",
  "messages": [
    "merchant: Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?",
    "assistant: Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis.",
    "merchant: E em que dia foi feita essa venda?",
    "assistant: A venda de R$ 89,50 no Mastercard final 7788 foi feita em 25/10/2026."
  ]
}

=== Human review 6: a supervisor reads the audit log ===
[
  "ana (operator) case.reply ESC-225752819D",
  "ana (operator) case.read ESC-225752819D"
]

=== LGPD: the merchant erases the conversation ===
DELETE /me/conversations: 204
GET /conversations afterwards: 404

=== Write action 1: the agent proposes a technical ticket ===
> Minha maquininha continua sem conexão mesmo depois de reiniciar e testar o chip.
{
  "status": "pending_confirmation",
  "pending_action": {
    "id": "698ae7e17fe047899ddc75a58f99c45a",
    "operation": "open_technical_ticket(terminal_id=\"GC-1180-7742\", symptom=\"A maquininha continua sem conexão mesmo depois de reiniciar e testar o chip.\")"
  },
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nPosso abrir um chamado técnico para a sua maquininha. A operação que vai rodar é exatamente esta:\n\nopen_technical_ticket(terminal_id=\"GC-1180-7742\", symptom=\"A maquininha continua sem conexão mesmo depois de reiniciar e testar o chip.\")\n\nEla só acontece se você confirmar, com o código 698ae7e17fe047899ddc75a58f99c45a. Qualquer outra mensagem cancela."
}

=== Write action 2: the merchant confirms it ===
> Pode abrir, confirmo.
{
  "status": "answered",
  "answer": "Chamado técnico TKT-698AE7E1 aberto para a maquininha GC-1180-7742. Guarde esse número para acompanhar o chamado."
}

=== Write action 3: the same confirmation again returns the same receipt ===
> Confirmo.
{
  "status": "answered",
  "answer": "Chamado técnico TKT-698AE7E1 aberto para a maquininha GC-1180-7742. Guarde esse número para acompanhar o chamado."
}
```

## Changes since the 2026-10-24 run

| | 2026-10-24 (T-55 tree) | 2026-10-26 (`73337dd`) |
| --- | --- | --- |
| Voice | "O site informa...", "O site não informa..." | states the fact; a missing one is "Não há informação sobre..." (T-60) |
| S01 | three pages, the offers page among them; left out the exemption tiers | the Get Smart page is retrieved (T-62); both monthly prices and the Clássica's rates |
| S02 | answered from one page, with the source's date | degraded: no result covered the day asked |
| S05 | two manuals, ADQ 1-18 and ADQ 1-87 | the Get Mini manual, the same two codes |
| S07 | degraded: at midnight no page had the day's rate | degraded: the results gave the euro only in dollars (T-62 never quotes a rate without the real) |
| S08 | the reason only | the reason and the amount, R$ 1.240,00 |
| Dates | settlement on 24/10, sale on 23/10 | settlement on 26/10, sale on 25/10: the mock account data is relative to today |
| Audit log | also listed a ticket from an earlier run on the same volume | a fresh volume: only this run's two operator actions |
| Dependencies | before #12 | fastapi 0.156.8, langchain 1.4.6, uvicorn 0.54.0 and eight more (#12) |
