# LLM-as-judge report

Written by `uv run python -m evals.run_judge` on 2026-10-14, at `ae021f9` plus the uncommitted changes that add this runner: 3 independent runs of every line, aggregated, no run chosen. Chat model `gpt-6-luna` at low effort; embedder `text-embedding-3-large` with a retrieval threshold of 0.52; judge `gpt-6-sol` at medium effort. Every answered turn is scored 1 to 5 for groundedness (each claim is in the evidence the system had for that turn: excerpts, account data, web pages or the operator's reply) and relevance (it answers what was asked). A turn that paused for a human is counted, not scored: its answer is a fixed handoff notice. Web answers use live search results, so they change from run to run.

| Line | Turn | Outcome over 3 runs | Groundedness mean / min | Relevance mean / min | Note from its lowest-scored run |
| --- | --- | --- | --- | --- | --- |
| S01 |  | answered 3/3 | 5.00 / 5 | 3.67 / 3 | As afirmações têm respaldo na evidência, mas a comparação omite que a Clássica fica grátis a partir de R$ 10 mil mensais em vendas, uma diferença importante de custo. |
| S02 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta usa a previsão apresentada na evidência para amanhã, 15 de outubro, e atende diretamente à pergunta. |
| S03 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data prevista e o valor líquido constam na evidência, e a resposta atende diretamente à pergunta. |
| S04 |  | answered 3/3 | 5.00 / 5 | 4.00 / 4 | A resposta se apoia na evidência e reconhece que ela não esclarece se é necessária outra conta bancária; por isso, atende à pergunta apenas parcialmente. |
| S05 |  | answered 2/3, escalated (insufficient_sources) 1/3 | 5.00 / 5 | 4.00 / 4 | A resposta se limita ao que a evidência informa sobre o chip e deixa claro que não há instruções de solução; por isso, ajuda apenas parcialmente com o problema. |
| S06 |  | answered 3/3 | 5.00 / 5 | 4.33 / 4 | A resposta se apoia na evidência e explica o funcionamento básico, mas não detalha a contratação nem as condições da antecipação, que os trechos fornecidos não esclarecem. |
| S07 |  | answered 3/3 | 3.00 / 3 | 4.67 / 4 | A cotação principal está na evidência, mas a atribuição à Rede Câmbio Seguro e o horário aplicado ao euro não estão sustentados; a resposta atende à pergunta, embora acrescente outra cotação. |
| S08 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta informa o motivo da recusa registrado na consulta e atende diretamente à dúvida sobre a transação negada. |
| S09 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta informa o limite de até 48 parcelas, ressalva que ele depende do emissor e explica como consultar as opções disponíveis, tudo conforme a evidência. |
| S10 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma a venda pelo WhatsApp e os passos descritos; a resposta atende diretamente à pergunta. |
| FOLLOWUP-A | turn 1 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data prevista e o valor líquido constam na evidência, e a resposta informa diretamente quando o dinheiro das vendas de ontem deve cair. |
| FOLLOWUP-A | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o valor líquido e o crédito previsto para amanhã, respondendo à pergunta no contexto da conversa. |
| FOLLOWUP-B | turn 1 | answered 3/3 | 5.00 / 5 | 3.67 / 3 | A evidência sustenta as informações apresentadas, mas a resposta acrescenta preços e planos que não foram perguntados. |
| FOLLOWUP-B | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o envio pelo WhatsApp e as opções de compartilhar ou copiar; a resposta atende diretamente à pergunta. |
| COMPOUND |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data e o valor previstos constam na consulta, e a explicação da antecipação segue a evidência e atende às duas perguntas. |
| HITL | turn 1 | escalated (individual_case) 3/3 | — | — | not scored: a handoff notice |
| HITL | operator reply | answered by the operator 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta usa apenas as informações verificadas e explica que o estorno ainda pode não ter aparecido porque foi aprovado hoje e pode levar até 2 dias úteis. |
| HITL | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data de 13/10/2026 consta na transação identificada na evidência, e a resposta atende diretamente à pergunta. |

## Run to run

| Run | Scored turns | Escalated turns | Groundedness | Relevance |
| --- | --- | --- | --- | --- |
| 1 | 16 | 2 | 4.88 | 4.75 |
| 2 | 17 | 1 | 4.88 | 4.71 |
| 3 | 17 | 1 | 4.88 | 4.59 |

Across runs: groundedness 4.88 ± 0.00 (range 4.88 to 4.88), relevance 4.68 ± 0.08 (range 4.59 to 4.75), mean ± standard deviation of the run means.

## Retrieval threshold

S09 (crediário) retrieved at 0.726, 0.726, 0.726 across the runs, against a threshold of 0.52 that comes from `evals/retrieval_calibration.md`:

| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC | Best threshold | Kept / rejected at it | Search p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chroma default (all-MiniLM-L6-v2) | 0.500 / 0.706 | 0.510 / 0.599 | -0.100 | 0.958 | 0.620 | 19/22 kept, 15/15 rejected | 44 ms |
| openai: `text-embedding-3-small` | 0.588 / 0.794 | 0.329 / 0.503 | +0.085 | 1.000 | 0.588 | 22/22 kept, 15/15 rejected | 457 ms |
| openai: `text-embedding-3-large` | 0.574 / 0.780 | 0.307 / 0.464 | +0.110 | 1.000 | 0.574 | 22/22 kept, 15/15 rejected | 510 ms |

## Every score below 4

**S01, run 2** — groundedness 5, relevance 3. As afirmações têm respaldo na evidência, mas a comparação omite que a Clássica fica grátis a partir de R$ 10 mil mensais em vendas, uma diferença importante de custo.

> A Get Clássica aparece como opção para CPF ou CNPJ; a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil por mês. Nas condições publicadas, a Clássica tem aluguel de R$ 79,90 por mês caso não sejam atingidos os faturamentos para isenção. Na Smart, o aluguel varia conforme o faturamento: R$ 50,00 a partir de R$ 10 mil por mês, R$ 10,00 a partir de R$ 30 mil e grátis a partir de R$ 60 mil. Ambas oferecem recebimento em 2 dias úteis pela Get Conta. O site não informa diferenças de recursos físicos ou tecnológicos entre as maquininhas.

**S01, run 3** — groundedness 5, relevance 3. A resposta se apoia na evidência, mas omite uma diferença importante: a Get Clássica pode ficar grátis a partir de R$ 10 mil/mês em vendas.

> A Get Clássica aparece como opção para CPF ou CNPJ, enquanto a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil por mês. Nas condições publicadas, a Clássica tem aluguel de R$ 79,90 por mês quando não são atingidas as faixas de faturamento para isenção; na Smart, o aluguel varia conforme o faturamento: R$ 50,00 a partir de R$ 10 mil/mês, R$ 10,00 a partir de R$ 30 mil/mês e grátis a partir de R$ 60 mil/mês. Ambas indicam recebimento em 2 dias úteis pela Get Conta. O site não detalha diferenças de recursos ou especificações entre as maquininhas.

**S07, run 1** — groundedness 3, relevance 5. A cotação do euro está na evidência, mas o horário de 17h17 e o atraso de 15 minutos aparecem associados ao Ibovespa, não ao euro; a resposta atende à pergunta.

> O euro comercial está cotado a R$ 5,6099 em 14/10/2026, atualizado às 17:17 (com atraso de 15 minutos), segundo o Valor Data. A cotação vem dos sites consultados e pode mudar.

**S07, run 2** — groundedness 3, relevance 4. A cotação principal está na evidência, mas a atribuição à Rede Câmbio Seguro e o horário aplicado ao euro não estão sustentados; a resposta atende à pergunta, embora acrescente outra cotação.

> O euro comercial está em R$ 5,6099, segundo o Valor Data, com atualização em 14/10/2026 às 17:17 (com atraso de 15 minutos). A Rede Câmbio Seguro informa R$ 5,63 em 14 de outubro de 2026. Os valores vêm dos sites consultados e podem mudar.

**S07, run 3** — groundedness 3, relevance 5. A cotação está na evidência, mas o horário e o atraso de 15 minutos aparecem associados ao Ibovespa, não explicitamente ao euro; a resposta atende à pergunta.

> O euro comercial está cotado a R$ 5,6099, com variação de -0,20%, segundo o Valor Data. A página informa atualização em 14/10/2026 às 17:17, com atraso de 15 minutos. Os valores são dos sites consultados e podem mudar.

**FOLLOWUP-B turn 1, run 1** — groundedness 5, relevance 3. A evidência sustenta as informações apresentadas, mas a resposta acrescenta preços e planos que não foram perguntados.

> Com o Link de Pagamento, você cria um link para vender online e compartilha por WhatsApp, redes sociais ou onde quiser. Para criar: acesse o Portal Minha Conta ou o Aplicativo Getnet Brasil, selecione “Link de Pagamento” em Produtos e Serviços, informe os dados do produto, frete e opções de pagamento e compartilhe o link. O comprador pode pagar com débito, crédito, Pix ou boleto. O link também permite personalização e pode incluir diversos produtos no mesmo carrinho.
> 
> Há opções Inicial, grátis, com cobrança de R$ 1,00 por link pago, e Básico, por R$ 28 por mês, com 60 transações de cartão mensais e cobrança de R$ 0,50 por transação excedente. As condições e taxas podem variar conforme os produtos e serviços contratados.

**FOLLOWUP-B turn 1, run 3** — groundedness 5, relevance 3. A resposta está apoiada na evidência e explica como criar e compartilhar o link, mas acrescenta preços de planos que não foram perguntados.

> Com o Link de Pagamento, você cria um link para vender online e compartilha por WhatsApp, redes sociais ou outro meio. No Portal Minha Conta ou no aplicativo Getnet Brasil, acesse “Produtos e Serviços”, selecione “Link de Pagamento” e “Criar Link de Pagamento”; informe os dados do produto, o frete e as opções de pagamento e, depois, copie ou compartilhe o link. O comprador pode pagar por débito, crédito, Pix ou boleto. Há opções de link Inicial, grátis, com cobrança de R$ 1,00 por link pago, e Básico, por R$ 28/mês; as condições e taxas variam conforme o produto contratado.
