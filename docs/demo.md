# Demo: real outputs

`OPERATOR_TOKEN=... ./scripts/demo.sh` against the container built from the code at T-44
(`gpt-6-luna` at low effort, `text-embedding-3-large`, live Tavily), on 2026-10-14. It runs the ten
challenge scenarios, a follow-up, a compound request and the human-review loop: the case, the
operator's reply, the next message that receives it, and the stored conversation. Answers are in
pt-BR, as a merchant gets them; web answers change with the live results. The script trims each
response to what a reader checks (status, agents, answer, sources, escalation, delivered).

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
  "answer": "As duas oferecem recebimento em 2 dias úteis e podem ter aluguel isento conforme as condições de faturamento. Na oferta da Get Smart, o aluguel é R$ 50,00 para vendas a partir de R$ 10 mil/mês, R$ 10,00 a partir de R$ 30 mil/mês e grátis a partir de R$ 60 mil/mês; abaixo das condições, o valor de utilização informado é R$ 129,90/mês. Para a Get Clássica, o site informa aluguel grátis mediante vendas a partir de R$ 10 mil e valor de utilização de R$ 79,90/mês caso não atinja os faturamentos indicados. A Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil/mês; a Get Clássica é válida para CPF e CNPJ. O site não informa diferenças de recursos ou especificações físicas entre as maquininhas.",
  "sources": [
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
  "answer": "Amanhã, em Porto Alegre, a previsão é de trovoadas, com máxima de 22.6C, mínima de 17.3C e 86% de chance de chuva. Também há previsão de chuva moderada a pontualmente forte na região metropolitana de Porto Alegre.\n\nAtenção: a fonte não informa a data de publicação, então não posso confirmar que o dado está atualizado.",
  "sources": [
    "web: https://www.bra1.com.br/clima/id-698754/previsao_do_tempo_em_porto_alegre__parcialmente_nublado__max_21.5_c_e_84__de_chuva",
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
  "answer": "O dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 14/10/2026.",
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
  "answer": "O site informa que a Get Conta é uma conta digital que permite receber dinheiro e vender por Pix. Não informa se é necessário ter outra conta bancária para receber as vendas via Pix.",
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
  "answer": "Se a maquininha usa chip: desligue-a, abra a tampa traseira, insira o chip no local indicado como SIM1 ou SIM2, com a parte metálica voltada para baixo, feche a tampa e ligue a máquina. Aguarde a indicação de rede conectada (2G ou 3G). Para testar a comunicação, pressione o botão de função (F, # ou o da máquina), digite 60 e selecione “2 – manutenção” e depois “1 – teste de comunicação”. O site não informa outras soluções para dificuldades de conexão.",
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
  "answer": "A antecipação permite receber antes do prazo de compensação os valores de vendas feitas no crédito, à vista ou parceladas. A Antecipação Automática é uma condição para obter isenção para clientes que optam pelo plano “Receba Conforme suas Vendas”; sua concessão depende de avaliação individual de crédito pela Getnet. O site não informa neste trecho como solicitar a antecipação nem quais são suas taxas.",
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
  "answer": "O euro comercial está cotado a R$ 5,6099. Outra fonte informa o euro a R$ 5,63.\n\nAtenção: a fonte não informa a data de publicação, então não posso confirmar que o dado está atualizado.",
  "sources": [
    "web: https://valor.globo.com/valor-data",
    "web: https://redecambioseguro.com.br/previsao-de-mercado-cambio-e-ouro/previsao-de-mercado-cambio-e-ouro-14-de-outubro-de-2026"
  ]
}

=== S08 declined transaction ===
> My card machine is showing a transaction decline error.
{
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A transação foi negada porque, segundo o banco emissor, havia saldo ou limite insuficiente no cartão.",
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
  "answer": "O Crediário permite parcelamento em até 48 vezes, mas o limite máximo varia conforme o emissor do cartão e suas políticas de crédito.",
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
  "answer": "Sim. Você pode vender pelo WhatsApp usando o Link de Pagamento: crie o link e compartilhe por onde quiser.",
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
  "answer": "O dinheiro das vendas de ontem, R$ 231,26 líquidos, está previsto para cair hoje, 14/10/2026.",
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
  "answer": "O dinheiro das vendas de 12/10/2026, R$ 60,18 líquidos, está previsto para cair amanhã, 15/10/2026.",
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
  "answer": "O dinheiro das vendas de ontem (13/10/2026), R$ 231,26 líquidos, está previsto para cair hoje, 14/10/2026.\n\nA antecipação permite receber antes os valores das vendas feitas no crédito, à vista ou parceladas, sem esperar o prazo de compensação. O site não informa os passos para solicitar a antecipação nem como são definidos os valores e prazos.",
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
  "answer": "Não consegui resolver isso com segurança por aqui, então vou encaminhar para um atendente humano.\nGuarde o número de protocolo ESC-705417ED1D para acompanhar o caso.",
  "sources": [],
  "escalation": {
    "reason": "individual_case",
    "protocol": "ESC-705417ED1D"
  }
}

=== Human review 2: the operator reads the case ===
{
  "status": "open",
  "case": {
    "protocol": "ESC-705417ED1D",
    "reason": "individual_case",
    "attempts": [
      "router: individual_case, plan support, confidence 0.99",
      "step 1 (support) stopped: an individual case, which only a person may decide; query: Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?"
    ],
    "proposed_draft": "Entendo sua preocupação com o estorno da venda no Mastercard final 7788. Nossa equipe está verificando o caso para entender o que ocorreu e poderá orientar você assim que tiver mais informações."
  }
}

=== Human review 3: the operator replies ===
{
  "status": "answered",
  "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis.",
  "sources": [
    "operator: ESC-705417ED1D"
  ]
}

=== Human review 4: the next message receives the reply, then builds on it ===
> E em que dia foi feita essa venda?
{
  "delivered": [
    {
      "protocol": "ESC-705417ED1D",
      "answer": "Verificamos que o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje. O valor volta ao comprador em até 2 dias úteis."
    }
  ],
  "status": "answered",
  "agents": [
    "support"
  ],
  "answer": "A venda foi feita em 13/10/2026.",
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
    "assistant: A venda foi feita em 13/10/2026."
  ]
}
```
