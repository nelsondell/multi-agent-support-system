# LLM-as-judge report

Written by `uv run python -m evals.run_judge` on 2026-10-14, at `c9b176a` plus the uncommitted changes that add this runner: 3 independent runs of every line, aggregated, no run chosen. Chat model `gpt-6-luna` at low effort; embedder `text-embedding-3-large` with a retrieval threshold of 0.52; judge `gpt-6-sol` at medium effort. Every answered turn is scored 1 to 5 for groundedness (each claim is in the evidence the system had for that turn: excerpts, account data, web pages or the operator's reply) and relevance (it answers what was asked). A turn that paused for a human is counted, not scored: its answer is a fixed handoff notice. Web answers use live search results, so they change from run to run.

| Line | Turn | Outcome over 3 runs | Groundedness mean / min | Relevance mean / min | Note from its lowest-scored run |
| --- | --- | --- | --- | --- | --- |
| S01 |  | answered 3/3 | 4.33 / 3 | 3.67 / 3 | A resposta usa valores presentes na evidência, mas omite a diferença central: a Clássica pode ficar grátis a partir de R$ 10 mil/mês, enquanto a Smart exige R$ 60 mil/mês. |
| S02 |  | answered 3/3 | 4.33 / 3 | 4.33 / 3 | A previsão está na evidência, mas a ressalva erra ao dizer que a fonte não informa a data de publicação; ela também acrescenta algo que não foi perguntado. |
| S03 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data prevista e o valor líquido constam na evidência, e a resposta atende diretamente à pergunta. |
| S04 |  | answered 3/3 | 5.00 / 5 | 4.33 / 4 | A resposta se mantém fiel à evidência, mas não esclarece diretamente que a Get Conta é uma conta pela qual é possível vender via Pix. |
| S05 |  | answered 3/3 | 5.00 / 5 | 4.00 / 4 | A resposta se limita ao que a evidência informa sobre o chip e a cobertura, mas não oferece passos para resolver a falha, pois eles não constam na evidência. |
| S06 |  | answered 3/3 | 5.00 / 5 | 4.67 / 4 | A resposta se apoia na evidência e explica o recebimento antecipado, mas não detalha como contratar o serviço, informação ausente dos trechos fornecidos. |
| S07 |  | answered 3/3 | 4.33 / 3 | 4.00 / 4 | A cotação e a variação constam da evidência, mas a ressalva ignora que as fontes trazem a data de hoje; a resposta informa a cotação, porém não esclarece se ela é de hoje. |
| S08 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta informa o motivo da recusa registrado na consulta e atende diretamente à dúvida sobre a transação negada. |
| S09 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o limite de até 48 parcelas, sua variação conforme o emissor e a consulta das opções na maquininha; a resposta atende à pergunta. |
| S10 |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma expressamente vendas pelo WhatsApp e o compartilhamento do Link de Pagamento; a resposta atende diretamente à pergunta. |
| FOLLOWUP-A | turn 1 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data prevista e o valor líquido constam na evidência, e a resposta informa diretamente quando o dinheiro deve cair. |
| FOLLOWUP-A | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta informa o valor, a data das vendas e a previsão de crédito para amanhã, todos confirmados pela evidência. |
| FOLLOWUP-B | turn 1 | answered 3/3 | 5.00 / 5 | 4.33 / 3 | As informações estão na evidência; a resposta explica o uso do link, mas acrescenta preços e planos que não foram perguntados. |
| FOLLOWUP-B | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A evidência confirma o envio pelo WhatsApp e as opções de compartilhar ou copiar o link; a resposta atende diretamente à pergunta. |
| COMPOUND |  | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data, o valor líquido previsto e a explicação da antecipação constam da evidência; a resposta atende às duas perguntas sem inventar prazos ou custos. |
| HITL | turn 1 | escalated (individual_case) 3/3 | — | — | not scored: a handoff notice |
| HITL | operator reply | answered by the operator 3/3 | 5.00 / 5 | 5.00 / 5 | A resposta reproduz as informações da evidência e explica que o estorno, aprovado hoje, pode levar até 2 dias úteis para aparecer ao comprador. |
| HITL | turn 2 | answered 3/3 | 5.00 / 5 | 5.00 / 5 | A data informada corresponde à transação na evidência e responde diretamente à pergunta. |

## Run to run

| Run | Scored turns | Escalated turns | Groundedness | Relevance |
| --- | --- | --- | --- | --- |
| 1 | 17 | 1 | 4.88 | 4.65 |
| 2 | 17 | 1 | 4.88 | 4.76 |
| 3 | 17 | 1 | 4.88 | 4.59 |

Across runs: groundedness 4.88 ± 0.00 (range 4.88 to 4.88), relevance 4.67 ± 0.09 (range 4.59 to 4.76), mean ± standard deviation of the run means.

## Retrieval threshold

S09 (crediário) retrieved at 0.726, 0.726, 0.726 across the runs, against a threshold of 0.52 that comes from `evals/retrieval_calibration.md`:

| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC | Best threshold | Kept / rejected at it | Search p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chroma default (all-MiniLM-L6-v2) | 0.500 / 0.706 | 0.510 / 0.599 | -0.100 | 0.958 | 0.620 | 19/22 kept, 15/15 rejected | 44 ms |
| openai: `text-embedding-3-small` | 0.588 / 0.794 | 0.329 / 0.503 | +0.085 | 1.000 | 0.588 | 22/22 kept, 15/15 rejected | 457 ms |
| openai: `text-embedding-3-large` | 0.574 / 0.780 | 0.307 / 0.464 | +0.110 | 1.000 | 0.574 | 22/22 kept, 15/15 rejected | 510 ms |

## Every score below 4

**S01, run 1** — groundedness 3, relevance 3. A resposta usa valores presentes na evidência, mas omite a diferença central: a Clássica pode ficar grátis a partir de R$ 10 mil/mês, enquanto a Smart exige R$ 60 mil/mês.

> As duas oferecem recebimento em 2 dias úteis e podem ficar grátis mediante vendas a partir de R$ 60.000, conforme as condições publicadas. A principal diferença informada é o faturamento para o aluguel da Get Smart: R$ 50,00 por mês a partir de R$ 10 mil/mês, R$ 10,00 a partir de R$ 30 mil/mês e grátis a partir de R$ 60 mil/mês. Para a Get Clássica, o site informa aluguel de R$ 79,90/mês caso não sejam atingidas as faixas de faturamento para isenção; também apresenta oferta para novos clientes de adesão sem aluguel. As taxas divulgadas variam entre as páginas: por exemplo, débito a partir de 1,69% nas páginas dos produtos e 1,39% na oferta da Clássica. O site não informa diferenças de recursos ou especificações entre as maquininhas.

**S01, run 3** — groundedness 5, relevance 3. A resposta se apoia na evidência, mas deixa de comparar diferenças importantes de preço e faturamento mínimo para isenção.

> As duas oferecem recebimento em 2 dias úteis e podem ficar grátis mediante as condições de faturamento descritas no site. A diferença informada é que a Get Smart é exclusiva para CNPJ com faturamento acima de R$ 10 mil por mês; na página da Get Clássica, ela é indicada para CPF ou CNPJ. O site também informa valores de utilização quando não se atingem as faixas de faturamento, mas não detalha diferenças de recursos ou características físicas entre as maquininhas.

**S02, run 3** — groundedness 3, relevance 3. A previsão está na evidência, mas a ressalva erra ao dizer que a fonte não informa a data de publicação; ela também acrescenta algo que não foi perguntado.

> Para amanhã, 15 de outubro de 2026, a previsão em Porto Alegre é de trovoada, com máxima de 22.6C, mínima de 17.3C e 86% de chance de chuva.
> 
> Atenção: a fonte não informa a data de publicação, então não posso confirmar que o dado está atualizado.

**S07, run 2** — groundedness 3, relevance 4. A cotação e a variação constam da evidência, mas a ressalva ignora que as fontes trazem a data de hoje; a resposta informa a cotação, porém não esclarece se ela é de hoje.

> O euro comercial está cotado a 5,6099, com variação de -0,20%.
> 
> Atenção: a fonte não informa a data de publicação, então não posso confirmar que o dado está atualizado.

**FOLLOWUP-B turn 1, run 2** — groundedness 5, relevance 3. As informações estão na evidência; a resposta explica o uso do link, mas acrescenta preços e planos que não foram perguntados.

> Você cria o link pelo Portal Minha Conta ou pelo Aplicativo Getnet Brasil: acesse Produtos e Serviços, selecione Link de Pagamento, escolha “Criar Link de Pagamento” e informe os dados do produto, frete e opções de pagamento. Depois, compartilhe ou copie o link para enviar por onde quiser. O comprador pode pagar com débito, crédito, Pix ou boleto; também é possível personalizar o link e incluir vários produtos no mesmo carrinho.
> 
> O Link de Pagamento Inicial é grátis, com cobrança de R$ 1,00 por link pago — somente quando o pagamento for aprovado. Há também o plano Básico por R$ 28/mês, com 60 transações de cartão por mês e R$ 0,50 por transação excedente. Taxas exclusivas e condições podem variar conforme o produto contratado.
