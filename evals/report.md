# LLM-as-judge report

Written by `uv run python -m evals.run_judge` on 2026-10-15, at `681c165` plus the changes committed with this report: 3 independent runs of every line, aggregated, no run chosen. Chat model `gpt-6-luna` at low effort; embedder `text-embedding-3-large` with a retrieval threshold of 0.52; judge `gpt-6-sol` at medium effort. Every answered turn is scored 1 to 5 for groundedness (each claim is in the evidence the system had for that turn: excerpts, account data, web pages or the operator's reply) and relevance (it answers what was asked). A turn that paused for a human is counted, not scored: its answer is a fixed handoff notice. Web answers use live search results, so they change from run to run.

| Line | Turn | Outcome over 3 runs | Groundedness mean / min | Relevance mean / min | Note from its lowest-scored run |
| --- | --- | --- | --- | --- | --- |
| S01 |  | answered 3/3 | 5.00 / 5 | 3.67 / 3 | Os custos e prazos citados estão na evidência, mas a resposta omite uma diferença importante: a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil/mês, enquanto a Get Clássica aceita CPF e CNPJ. |
| S02 |  | answered 3/3 | 5.00 / 5 | 4.00 / 4 | A resposta reproduz a previsão estadual da evidência e deixa claro que faltam dados específicos de Porto Alegre; por isso, atende apenas parcialmente à pergunta. |
| S03 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data prevista e o valor líquido estão na evidência, e a resposta atende diretamente à pergunta. |
| S04 |  | answered 3/3 | 5.00 / 5 | 4.00 / 4 | A resposta se limita ao que a evidência informa, mas não dá uma resposta definitiva sobre a necessidade de outra conta bancária. |
| S05 |  | escalated (insufficient_sources) 2/3, answered 1/3 | 5.00 / 5 | 4.00 / 4 | A resposta reproduz corretamente o que a evidência diz sobre o chip e esclarece que ela não traz passos para resolver a falha, mas não oferece uma solução prática. |
| S06 |  | answered 3/3 | 5.00 / 5 | 4.00 / 4 | A resposta se apoia na evidência e explica o conceito e a condição da modalidade automática, mas não detalha taxas nem todo o funcionamento. |
| S07 |  | answered 3/3 | 4.33 / 3 | 2.33 / 2 | A evidência não traz a cotação de hoje, mas não sustenta a alegação de que não é possível consultar a internet; a resposta também omite a cotação anterior disponível. |
| S08 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A consulta registra uma transação negada por saldo ou limite insuficiente, e a resposta informa diretamente esse motivo. |
| S09 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta informa o máximo de 48 parcelas, ressalva o limite definido pelo emissor e indica a simulação na maquininha, tudo conforme a evidência e pertinente à pergunta. |
| S10 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma que é possível vender pelo WhatsApp criando e compartilhando o Link de Pagamento; a resposta atende diretamente à pergunta. |
| FOLLOWUP-A | turn 1 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o valor líquido e a previsão de crédito para hoje, respondendo diretamente à pergunta. |
| FOLLOWUP-A | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o valor líquido e a previsão de crédito em 16/10, respondendo à pergunta sobre o dinheiro que cai amanhã. |
| FOLLOWUP-B | turn 1 | answered 3/3 | 5.00 / 5 | 3.67 / 3 | A resposta descreve o funcionamento com base na evidência, mas acrescenta preços e planos que não foram perguntados. |
| FOLLOWUP-B | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o envio pelo WhatsApp e os passos para compartilhar; a resposta atende diretamente à pergunta. |
| COMPOUND |  | answered 3/3 | 5.00 / 5 | 4.67 / 4 | A data, o valor líquido e a explicação da antecipação estão na evidência; a resposta cobre as duas perguntas, mas não menciona que a Antecipação Automática depende de aprovação de crédito. |
| HITL | turn 1 | escalated (individual_case) 3/3 | — | — | not scored: a handoff notice |
| HITL | operator reply | answered by the operator 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta reproduz as informações da evidência e explica que o estorno, aprovado hoje, pode levar até 2 dias úteis para aparecer ao comprador. |
| HITL | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A consulta confirma que a venda aprovada de R$ 89,50 no Mastercard final 7788 foi feita em 14/10/2026, respondendo diretamente à pergunta. |

## Run to run

| Run | Scored turns | Escalated turns | Groundedness | Relevance |
| --- | --- | --- | --- | --- |
| 1 | 16 | 2 | 5.00 | 4.44 |
| 2 | 16 | 2 | 4.88 | 4.50 |
| 3 | 17 | 1 | 5.00 | 4.41 |

Across runs (v2 run, 2026-10-15): groundedness 4.96 ± 0.07 (range 4.88 to 5.00), relevance 4.45 ± 0.05 (range 4.41 to 4.50), mean ± standard deviation of the run means.

## Retrieval threshold

S09 (crediário) retrieved at 0.726, 0.726, 0.726 across the runs, against a threshold of 0.52 that comes from `evals/retrieval_calibration.md`:

| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC | Best threshold | Kept / rejected at it | Search p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chroma default (all-MiniLM-L6-v2) | 0.500 / 0.706 | 0.510 / 0.599 | -0.100 | 0.958 | 0.620 | 19/22 kept, 15/15 rejected | 44 ms |
| openai: `text-embedding-3-small` | 0.588 / 0.794 | 0.329 / 0.503 | +0.085 | 1.000 | 0.588 | 22/22 kept, 15/15 rejected | 457 ms |
| openai: `text-embedding-3-large` | 0.574 / 0.780 | 0.307 / 0.464 | +0.110 | 1.000 | 0.574 | 22/22 kept, 15/15 rejected | 510 ms |

## Every score below 4

**S01, run 1** — groundedness 5, relevance 3. Os custos e prazos citados estão na evidência, mas a resposta omite uma diferença importante: a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil/mês, enquanto a Get Clássica aceita CPF e CNPJ.

> As duas oferecem recebimento em 2 dias úteis e taxas por transação. A diferença mais clara nos trechos é o custo de uso: a Get Clássica custa R$ 79,90/mês e a Get Smart, R$ 129,90/mês quando não se atingem os faturamentos indicados. Na oferta da Get Smart, o aluguel varia conforme as vendas: R$ 50,00/mês a partir de R$ 10 mil, R$ 10,00/mês a partir de R$ 30 mil e grátis a partir de R$ 60 mil. A página da Get Clássica informa máquina grátis a partir de R$ 10 mil em vendas, mas não detalha uma tabela de aluguel equivalente. As taxas também variam conforme a oferta: os trechos apresentam taxas iniciais diferentes para débito e crédito à vista. O site não informa diferenças de recursos ou especificações entre as maquininhas.

**S01, run 2** — groundedness 5, relevance 3. As afirmações são sustentadas pela evidência, mas a comparação do aluguel fica incompleta por não trazer os valores e a condição de isenção da Get Clássica.

> A diferença informada é o público e a condição de aluguel: a Get Clássica é válida para CPF e CNPJ; a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil por mês. Na oferta da Get Smart, o aluguel varia conforme o faturamento: R$ 50,00 a partir de R$ 10 mil/mês, R$ 10,00 a partir de R$ 30 mil/mês e grátis a partir de R$ 60 mil/mês. O site informa também recebimento em 2 dias úteis para ambas. Não informa diferenças de funcionalidades ou características físicas entre as maquininhas.

**S07, run 1** — groundedness 5, relevance 3. A evidência não traz a cotação de hoje, e a resposta evita inventá-la; porém, não informa a cotação mais recente disponível nem sua data.

> No momento não consigo consultar essa informação na internet com segurança.
> Tente de novo em instantes ou confira em uma fonte oficial.

**S07, run 2** — groundedness 3, relevance 2. A evidência não traz a cotação de hoje, mas não sustenta a alegação de que não é possível consultar a internet; a resposta também omite a cotação anterior disponível.

> No momento não consigo consultar essa informação na internet com segurança.
> Tente de novo em instantes ou confira em uma fonte oficial.

**S07, run 3** — groundedness 5, relevance 2. A evidência não traz a cotação de hoje, então a resposta evita inventá-la, mas deixa de informar a última cotação disponível e sua data.

> No momento não consigo consultar essa informação na internet com segurança.
> Tente de novo em instantes ou confira em uma fonte oficial.

**FOLLOWUP-B turn 1, run 1** — groundedness 5, relevance 3. A resposta descreve o funcionamento com base na evidência, mas acrescenta preços e planos que não foram perguntados.

> Com o Link de Pagamento, você cria um link para vender online e compartilha por WhatsApp, redes sociais ou onde quiser. Para criar, acesse o Portal Minha Conta ou o Aplicativo Getnet Brasil, escolha “Link de Pagamento” em Produtos e Serviços, informe os dados do produto, frete e opções de pagamento e compartilhe o link. O comprador pode pagar com débito, crédito, Pix ou boleto; também é possível personalizar o link com a imagem do produto e incluir vários produtos no mesmo carrinho.
> 
> Há opções Inicial, gratuita, com cobrança de R$ 1,00 por link pago, e Básico, por R$ 28 por mês, com 60 transações de cartão mensais e R$ 0,50 por transação excedente. As taxas são exclusivas e podem variar conforme as condições dos produtos e serviços contratados.

**FOLLOWUP-B turn 1, run 3** — groundedness 5, relevance 3. A evidência sustenta o funcionamento e os preços informados, mas a resposta acrescenta detalhes de planos e tarifas que não foram pedidos.

> Com o Link de Pagamento, você cria um link para vender online e compartilha onde quiser, como pelo WhatsApp ou pelas redes sociais. Para criar: acesse o Portal Minha Conta ou o Aplicativo Getnet Brasil, selecione “Link de Pagamento” em Produtos e Serviços, preencha as informações do produto, frete e formas de pagamento e, por fim, copie ou compartilhe o link. O comprador pode pagar com débito, crédito, Pix ou boleto; também é possível personalizar o link com a imagem do produto e incluir frete.
> 
> Há opções Inicial, grátis, com cobrança de R$ 1,00 por link pago, e Básico, por R$ 28 por mês, com 60 transações de cartão mensais e cobrança de R$ 0,50 por transação excedente. As condições e taxas estão sujeitas às regras publicadas para cada oferta.

## Prompt cache

Static prompts end at an explicit cache breakpoint with a per-prompt cache key (T-54). Its effect on latency and cost is not measured yet: the benchmark needed a working OpenAI key, which was not available.
