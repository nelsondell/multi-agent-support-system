# Demo: real outputs

`OPERATOR_TOKEN=... ./scripts/demo.sh` against the container built from the code at T-46
(`gpt-6-luna` at low effort, `text-embedding-3-large`, live Tavily), on 2026-10-15 at about 06:30
in Brazil. It runs the ten challenge scenarios, a follow-up, a compound request and the
human-review loop: the case, the operator's reply, the next message that receives it, and the
stored conversation. Answers are in pt-BR, as a merchant gets them; web answers change with the
live results. At that hour no page had published the day's euro rate yet, so S07 answers that it
cannot look it up now instead of quoting an older rate as today's. The script trims each response
to what a reader checks (status, agents, answer, sources, escalation, delivered).

```
$ OPERATOR_TOKEN=... ./scripts/demo.sh
{"status":"ok"}

=== S01 product comparison ===
> What's the difference between the Get Clássica and the Get Smart?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "A principal diferença informada é o custo conforme o faturamento: na oferta da Get Smart, o aluguel é de R$ 50,00 por mês a partir de R$ 10 mil em vendas mensais, R$ 10,00 a partir de R$ 30 mil e grátis a partir de R$ 60 mil. Na Get Clássica, o site informa a possibilidade de máquina grátis a partir de R$ 10 mil ou R$ 60 mil em vendas mensais, conforme a oferta, e aluguel de R$ 79,90 por mês caso não atinja os faturamentos para isenção. As duas informam recebimento em 2 dias úteis e taxas únicas; as taxas anunciadas variam conforme a oferta. O site não informa diferenças de funcionalidades ou especificações entre os modelos.",
  "sources": [
    "kb: https://site.getnet.com.br/ofertas-b/",
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
  "answer": "Para sexta-feira (16), há previsão de variação de nebulosidade e chuviscos ou chuva fraca isolada nas regiões fora do Nordeste do Rio Grande do Sul. As temperaturas no estado devem ficar entre 10°C e 17°C nas mínimas e entre 13°C e 24°C nas máximas. A fonte não traz uma previsão específica para Porto Alegre.\n\nNão consegui confirmar a data de publicação desta informação.",
  "sources": [
    "web: https://www.defesacivil.rs.gov.br/upload/arquivos/202610/14095752-boletim-hidrometeorolo-gico-diurno-14-10-2026.pdf"
  ]
}

=== S03 settlement, from the account ===
> When will the money from yesterday's sales be deposited?
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "O dinheiro das vendas de ontem (14/10/2026), R$ 231,26 líquidos, está previsto para cair hoje, 15/10/2026.",
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
  "answer": "A Get Conta é uma conta digital que permite vender por meio do Pix. O site não informa se é necessário ter outra conta bancária para receber essas vendas.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== S05 connectivity, not in the corpus ===
> My card machine won't connect to the internet, what should I do?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Se a sua máquina usa o Chip Algar, ele se conecta a várias operadoras e pode ser usado em locais com cobertura 2G/3G. O site não informa etapas para solucionar problemas de conexão.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== S06 receivables advance ===
> How does receivables advance (antecipação) work with Getnet?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "A antecipação de vendas permite receber antes do prazo de compensação os valores de vendas feitas no crédito, à vista ou parceladas. A Antecipação Automática é uma condição para obter isenção no plano “Receba Conforme suas Vendas” e depende de aprovação de crédito, com avaliação individual de cada contrato. O site não informa aqui as taxas nem os demais detalhes de funcionamento da antecipação.",
  "sources": [
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== S07 euro, from the web ===
> What's the euro exchange rate today?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "No momento não consigo consultar essa informação na internet com segurança.\nTente de novo em instantes ou confira em uma fonte oficial.",
  "sources": []
}

=== S08 declined transaction ===
> My card machine is showing a transaction decline error.
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A transação foi negada por saldo ou limite insuficiente no cartão, segundo o banco emissor.",
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
  "answer": "No Crediário, é possível parcelar em até 48 vezes. O prazo máximo pode variar conforme o emissor do cartão e as políticas de crédito dele; a simulação na maquininha mostra as opções disponíveis.",
  "sources": [
    "kb: https://site.getnet.com.br/crediario/",
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== S10 Payment Link on WhatsApp ===
> Can I sell through WhatsApp using the Payment Link?
{
  "status": "answered",
  "agents": [
    "knowledge"
  ],
  "answer": "Sim. O site informa que você pode vender pelo WhatsApp usando o Link de Pagamento. Depois de criar o link, é só compartilhá-lo pelo canal que quiser.",
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
  "answer": "O dinheiro das vendas de ontem (14/10/2026), R$ 231,26 líquidos, está previsto para cair hoje, 15/10/2026.",
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
  "answer": "O dinheiro das vendas de 13/10/2026, R$ 60,18 líquidos, está previsto para cair em 16/10/2026.",
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
  "answer": "O dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 15/10/2026. A antecipação de vendas permite receber antes do prazo de compensação os valores de vendas feitas no crédito, à vista ou parceladas. O site não informa como solicitar a antecipação nem quais são as taxas aplicadas.",
  "sources": [
    "account: get_settlement_schedule",
    "kb: https://site.getnet.com.br/duvidas/"
  ]
}

=== Human review 1: an individual case pauses ===
> Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?
{
  "status": "pending_human",
  "agents": [
    "support"
  ],
  "answer": "Não consegui resolver isso com segurança por aqui, então vou encaminhar para um atendente humano.\nGuarde o número de protocolo ESC-584F039CF7 para acompanhar o caso.",
  "sources": [],
  "escalation": {
    "reason": "individual_case",
    "protocol": "ESC-584F039CF7"
  }
}

=== Human review 2: the operator reads the case ===
{
  "status": "open",
  "case": {
    "protocol": "ESC-584F039CF7",
    "reason": "individual_case",
    "attempts": [
      "router: individual_case, plan support, confidence 0.99",
      "step 1 (support) stopped: an individual case, which only a person may decide; query: Por que o estorno da venda no Mastercard final 7788 ainda não apareceu?"
    ],
    "proposed_draft": "Entendo sua dúvida sobre o estorno da venda no Mastercard final 7788. Nossa equipe está verificando o caso e vai analisar o que aconteceu."
  }
}

=== Human review 3: the operator replies ===
{
  "status": "answered",
  "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis.",
  "sources": [
    "operator: ESC-584F039CF7"
  ]
}

=== Human review 4: the next message receives the reply, then builds on it ===
> E em que dia foi feita essa venda?
{
  "delivered": [
    {
      "protocol": "ESC-584F039CF7",
      "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis."
    }
  ],
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A venda foi feita em 14/10/2026.",
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
    "assistant: A venda foi feita em 14/10/2026."
  ]
}
```
