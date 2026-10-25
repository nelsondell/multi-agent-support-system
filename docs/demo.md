# Demo: real outputs

A clean copy of the tree (no git history) on macOS, Darwin arm64, on 2026-10-24 between 00:00 and
00:20 in Brazil. The service ran `gpt-6-luna` at low effort, `text-embedding-3-large`, live
Tavily, and RRF without a reranker. Linux runs `make verify` in CI, on ubuntu with the network cut.

## Summary

| # | Question | Agents | Status | Answer, shortened |
| --- | --- | --- | --- | --- |
| S01 | What's the difference between the Get Clássica and the Get Smart? | knowledge | answered | Smart: Android, menus, stock and kitchen messages on the terminal; Clássica: Pix, QR Code, contactless and chip; R$ 79,90 and R$ 129,90 a month when the revenue for exemption is not reached. It leaves out the exemption tiers the excerpts give (failure mode 4). Three pages cited. |
| S02 | What's the weather forecast in Porto Alegre tomorrow? | knowledge (web) | answered | Showers on 25 October, 20.4 °C maximum, 13.5 °C minimum, 75% chance of rain; ends with "Data informada pela própria fonte: 25/10/2026." |
| S03 | When will the money from yesterday's sales be deposited? | support | answered | "O dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 24/10/2026." |
| S04 | Do I need a bank account to receive my sales via Pix? | knowledge | answered | The Get Conta is a digital account that receives and sells through Pix; the site doesn't say whether another bank account is needed. |
| S05 | My card machine won't connect to the internet, what should I do? | knowledge | answered | From the terminal manuals: the steps for error ADQ 1-18 (weak signal) and for ADQ 1-87 (chip not seated); no general reconnect steps without an error code. |
| S06 | How does receivables advance (antecipação) work with Getnet? | knowledge | answered | Receive credit sales before their term for a fee; the Getnet Brasil app shows eligible receivables, simulates the cost and shows the net amount before confirming. |
| S07 | What's the euro exchange rate today? | knowledge (web) | degraded | At midnight no page had the day's rate: "No momento não consigo consultar essa informação na internet com segurança." |
| S08 | My card machine is showing a transaction decline error. | support | answered | "A transação foi negada porque, segundo o banco emissor, havia saldo ou limite insuficiente no cartão." |
| S09 | How many installments can I split a sale into with the crediário? | knowledge | answered | Up to 48 installments; the limit depends on the card issuer's credit policy. |
| S10 | Can I sell through WhatsApp using the Payment Link? | knowledge | answered | "Sim. Você pode vender pelo WhatsApp usando o Link de Pagamento: crie o link e compartilhe por onde quiser." |
| Follow-up | "Quando cai o dinheiro das vendas de ontem?" → "E o de amanhã?" | support, support | answered | R$ 231,26 today; then R$ 60,18 crediting 25/10/2026. |
| Compound | "Quando cai o dinheiro de ontem e como funciona a antecipação?" | support, knowledge | answered | Both parts in one reply, account and website sources. |
| Human review | refund case → operator reply → "E em que dia foi feita essa venda?" | support | paused → answered | The operator's reply delivered with the next message; then "A venda foi feita em 23/10/2026." |
| Write action | a terminal still offline → "Pode abrir, confirmo." → "Confirmo." | support | pending confirmation → answered | The ticket operation rendered by code, run once on confirmation: TKT-92808675; the repeated confirmation returns the same receipt. |

## 1. Before any container: `make verify`

No key in any check's environment: 361 passed, 3 skipped, the guardrail eval 28/28, and the eval
replay at pass-all 94/100 with the red team at 40/40. All checks passed in 38.7 s after the sync,
with other jobs running on the machine.

## 2. Replicas: `make scale`

Postgres, two replicas and the round-robin balancer came up healthy, and `/ready` on port 8080
answered `{"status":"ready","storage":"postgres","chunks":270}`. The same walkthrough as §4, sent
through the balancer, ran to its end:
- its 18 merchant messages split 9 and 9 between the replicas;
- the human-review loop finished through the balancer;
- the write was proposed on `api-a` and confirmed on `api-b` (same trace id in both replicas'
  logs), and the repeated confirmation returned the same ticket.

## 3. Observability: `make observe`

After one merchant message:
- **Prometheus** scraped `support-api` (`up`), with the four alert rules loaded:
  `NodeLatencyP95High`, `EscalationSpike`, `VendorFailures`, `CostPerResolutionHigh`.
- **Grafana** served the dashboard "Support: cost, resolution and vendors".
- **Jaeger** held the turn's trace: `POST /chat`; `invoke_agent guardrail`, `router`, `knowledge`,
  `collect`, `compose` and `remember`; and two `chat gpt-6-luna` spans.

## 4. One node: `make up`, then `./scripts/demo.sh`

The walkthrough runs:
- the ten challenge scenarios, a follow-up and a compound request;
- the human-review loop: the case, the operator's reply, the next message that receives it, the
  stored conversation and the audit log;
- the LGPD erasure;
- the confirmed write.

Answers are in pt-BR, as a merchant gets them, and web answers change with the live results.
- **S07:** at midnight no page had the day's euro rate, so it answers that it cannot look it up
  now instead of quoting an older rate as today's.
- **S05:** it now answers from the terminal manuals added in T-53.

The script trims each response to what a reader checks. The audit log also lists the ticket an
earlier run of the walkthrough opened on the same container, whose state volume persists.

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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA Get Smart é um equipamento Android que permite realizar operações diretamente na maquininha, como cadastrar cardápio, controlar estoque e trocar mensagens entre cozinha e salão. O site também informa que todas as vendas da Get Smart são processadas pela Getnet. Para a Get Clássica, informa que aceita Pix, QR Code, aproximação e chip. O valor de utilização indicado é de R$ 79,90/mês para a Get Clássica e R$ 129,90/mês para a Get Smart quando não se atingem os faturamentos informados; esse valor pode variar conforme a oferta e a condição de faturamento. O site não informa se a Get Clássica oferece os mesmos recursos Android da Get Smart.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/get-smart/",
    "kb: https://site.getnet.com.br/maquininha/get-classica/",
    "kb: https://site.getnet.com.br/ofertas-b/"
  ]
}

=== S02 weather, from the web ===
> What's the weather forecast in Porto Alegre tomorrow?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nAmanhã, 25 de outubro, em Porto Alegre, a previsão é de aguaceiros, com máxima de 20.4C, mínima de 13.5C e 75% de chance de chuva.\n\nData informada pela própria fonte: 25/10/2026.",
  "sources": [
    "web: https://www.bra1.com.br/clima/id-698754/previsao_do_tempo_em_porto_alegre__nublado__61__de_chuva_e_maxima_de_28.7_c"
  ]
}

=== S03 settlement, from the account ===
> When will the money from yesterday's sales be deposited?
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 24/10/2026.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO site informa que a Get Conta é uma conta digital para empresas e permite receber dinheiro e vender por meio do Pix. Não informa se é necessário ter outra conta bancária para receber as vendas via Pix.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== S05 connectivity, from the manuals ===
> My card machine won't connect to the internet, what should I do?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nSe aparecer o erro ADQ 1-18, que indica sinal fraco e impede a conclusão da transação, pressione Função > digite 25 > Verde > informe a Senha do Operador > Verde > Função 46 > Verde e faça uma venda teste. Para o ADQ 1-87, confirme se o chip está bem inserido no terminal e siga as instruções indicadas para esse código no manual. O site não informa passos gerais para reconectar a maquininha à internet sem um código de erro.",
  "sources": [
    "kb: https://site.getnet.com.br/wp-content/uploads/2026/06/getnet_12_06-26_Manul_GetMini_V2.pdf",
    "kb: https://site.getnet.com.br/wp-content/uploads/2026/06/getnet_12_06-26_Manul_GetClassica_V2.pdf"
  ]
}

=== S06 receivables advance ===
> How does receivables advance (antecipação) work with Getnet?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA antecipação permite receber antes do prazo original os valores de vendas no crédito, à vista ou parceladas, mediante uma taxa. No app Getnet Brasil, você pode consultar os recebíveis elegíveis, simular o custo e ver o valor líquido antes de confirmar. A disponibilidade depende da elegibilidade das vendas na solução contratada.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nA transação foi negada porque, segundo o banco emissor, havia saldo ou limite insuficiente no cartão.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nNo Crediário, é possível parcelar em até 48 vezes. O prazo máximo pode variar conforme o emissor do cartão e as políticas de crédito dele.",
  "sources": [
    "kb: https://site.getnet.com.br/crediario/"
  ]
}

=== S10 Payment Link on WhatsApp ===
> Can I sell through WhatsApp using the Payment Link?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nSim. Você pode vender pelo WhatsApp usando o Link de Pagamento: crie o link e compartilhe por onde quiser, inclusive pelo WhatsApp.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 24/10/2026.",
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
  "answer": "O dinheiro das vendas de 22/10/2026, R$ 60,18 líquidos, está previsto para cair em 25/10/2026.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nO dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 24/10/2026.\n\nA antecipação de recebíveis permite receber antes do prazo original os valores de vendas no crédito, à vista ou parceladas, mediante uma taxa. Não é um empréstimo: é o adiantamento de um valor proveniente de vendas já realizadas. No app Getnet Brasil, você pode consultar os recebíveis elegíveis, simular o custo e ver o valor líquido antes de confirmar a operação.",
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
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nNão consegui resolver isso com segurança por aqui, então vou encaminhar para um atendente humano.\nEle já recebe o resumo desta conversa, então você não precisa repetir nada.\nGuarde o número de protocolo ESC-958D43C798 para acompanhar o caso.",
  "sources": [],
  "escalation": {
    "reason": "individual_case",
    "protocol": "ESC-958D43C798"
  }
}

=== Human review 2: the operator reads the case ===
{
  "status": "open",
  "case": {
    "protocol": "ESC-958D43C798",
    "reason": "individual_case",
    "attempts": [
      "router: individual_case, plan support, confidence 0.99",
      "step 1 (support) stopped: an individual case, which only a person may decide; query: Por que o estorno da venda no Mastercard final 7788 ainda não apareceu?"
    ],
    "proposed_draft": "Entendo sua preocupação com o estorno da venda no Mastercard final 7788. Nossa equipe está verificando o caso para entender o que aconteceu e retornará com mais informações."
  }
}

=== Human review 3: the operator replies ===
{
  "status": "answered",
  "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. Segundo a informação da operadora, o valor volta ao comprador em até 2 dias úteis.",
  "sources": [
    "operator: ESC-958D43C798"
  ]
}

=== Human review 4: the next message receives the reply, then builds on it ===
> E em que dia foi feita essa venda?
{
  "delivered": [
    {
      "protocol": "ESC-958D43C798",
      "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. Segundo a informação da operadora, o valor volta ao comprador em até 2 dias úteis."
    }
  ],
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A venda foi feita em 23/10/2026.",
  "sources": [
    "account: get_transactions"
  ]
}

=== Human review 5: the conversation as stored ===
{
  "status": "active",
  "messages": [
    "merchant: Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?",
    "assistant: Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. Segundo a informação da operadora, o valor volta ao comprador em até 2 dias úteis.",
    "merchant: E em que dia foi feita essa venda?",
    "assistant: A venda foi feita em 23/10/2026."
  ]
}

=== Human review 6: a supervisor reads the audit log ===
[
  "ana (operator) case.reply ESC-958D43C798",
  "ana (operator) case.read ESC-958D43C798",
  "cliente2042 (merchant) ticket.opened TKT-60008CED"
]

=== LGPD: the merchant erases the conversation ===
DELETE /me/conversations: 204
GET /conversations afterwards: 404

=== Write action 1: the agent proposes a technical ticket ===
> Minha maquininha continua sem conexão mesmo depois de reiniciar e testar o chip.
{
  "status": "pending_confirmation",
  "pending_action": {
    "id": "928086754b1d4122abd05dae3d129b36",
    "operation": "open_technical_ticket(terminal_id=\"GC-1180-7742\", symptom=\"A maquininha continua sem conexão depois de reiniciar e testar o chip.\")"
  },
  "answer": "Olá! Sou o assistente virtual da Getnet: as respostas desta conversa são automáticas.\n\nPosso abrir um chamado técnico para a sua maquininha. A operação que vai rodar é exatamente esta:\n\nopen_technical_ticket(terminal_id=\"GC-1180-7742\", symptom=\"A maquininha continua sem conexão depois de reiniciar e testar o chip.\")\n\nEla só acontece se você confirmar, com o código 928086754b1d4122abd05dae3d129b36. Qualquer outra mensagem cancela."
}

=== Write action 2: the merchant confirms it ===
> Pode abrir, confirmo.
{
  "status": "answered",
  "answer": "Chamado técnico TKT-92808675 aberto para a maquininha GC-1180-7742. Guarde esse número para acompanhar o chamado."
}

=== Write action 3: the same confirmation again returns the same receipt ===
> Confirmo.
{
  "status": "answered",
  "answer": "Chamado técnico TKT-92808675 aberto para a maquininha GC-1180-7742. Guarde esse número para acompanhar o chamado."
}
```
