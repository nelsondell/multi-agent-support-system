# Retrieval threshold calibration

22 in-domain questions the corpus answers and 15 out-of-domain ones, from `evals/retrieval_calibration.jsonl`, each run through the hybrid retriever over an index built from the committed snapshot with one embedder. Confidence is the best dense similarity among the fused hits. Written by `uv run python -m evals.run_retrieval_calibration` on 2026-10-14.

| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC | Best threshold | Kept / rejected at it | Search p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chroma default (all-MiniLM-L6-v2) | 0.500 / 0.706 | 0.510 / 0.599 | -0.100 | 0.958 | 0.620 | 19/22 kept, 15/15 rejected | 44 ms |
| openai: `text-embedding-3-small` | 0.588 / 0.794 | 0.329 / 0.503 | +0.085 | 1.000 | 0.588 | 22/22 kept, 15/15 rejected | 457 ms |
| openai: `text-embedding-3-large` | 0.574 / 0.780 | 0.307 / 0.464 | +0.110 | 1.000 | 0.574 | 22/22 kept, 15/15 rejected | 510 ms |

Gap is the lowest in-domain score minus the highest out-of-domain one: positive means one threshold separates the groups completely. AUC is the chance that a random in-domain question outscores a random out-of-domain one.

## Per query

| Query | Group | chroma default (all-MiniLM-L6-v2) | openai: `text-embedding-3-small` | openai: `text-embedding-3-large` |
| --- | --- | --- | --- | --- |
| I01 Qual é a diferença entre a maquininha Get Clássica e a Get Smart? | in_domain | 0.640 | 0.588 | 0.643 |
| I02 É necessário ter uma conta bancária para receber as vendas via Pix? | in_domain | 0.705 | 0.658 | 0.574 |
| I03 Como funciona a antecipação de recebíveis na Getnet? | in_domain | 0.587 | 0.721 | 0.668 |
| I04 Em quantas parcelas posso dividir uma venda no crediário? | in_domain | 0.620 | 0.633 | 0.726 |
| I05 É possível vender pelo WhatsApp usando o Link de Pagamento? | in_domain | 0.657 | 0.649 | 0.609 |
| I06 Até quantas parcelas posso fazer e como sei os juros para informar ao cliente? | in_domain | 0.655 | 0.835 | 0.792 |
| I07 O que é o chip Algar? | in_domain | 0.840 | 0.831 | 0.847 |
| I08 Como coloco o chip na maquininha? | in_domain | 0.629 | 0.796 | 0.806 |
| I09 Quem pode abrir uma Get Conta? | in_domain | 0.720 | 0.875 | 0.836 |
| I10 Quais modalidades de crédito parcelado estão disponíveis? | in_domain | 0.727 | 0.690 | 0.700 |
| I11 Como fazer um Pix pela Get Conta? | in_domain | 0.743 | 0.876 | 0.867 |
| I12 O que é chargeback? | in_domain | 0.627 | 0.799 | 0.840 |
| I13 Como faço uma venda digitada? | in_domain | 0.717 | 0.760 | 0.685 |
| I14 Como funciona a pré-autorização? | in_domain | 0.537 | 0.719 | 0.768 |
| I15 Por quanto tempo ainda posso usar a máquina depois de pedir o cancelamento? | in_domain | 0.757 | 0.867 | 0.807 |
| I16 Como o aluguel é cobrado depois do cancelamento da máquina? | in_domain | 0.839 | 0.792 | 0.814 |
| I17 O que é NFC e por que preciso dele no celular? | in_domain | 0.752 | 0.866 | 0.828 |
| I18 Qual é a mensalidade da Get Smart? | in_domain | 0.500 | 0.667 | 0.632 |
| I19 Como cuidar da bateria da Get Smart para durar mais? | in_domain | 0.755 | 0.859 | 0.851 |
| I20 O que é cessão de recebíveis? | in_domain | 0.706 | 0.836 | 0.762 |
| I21 O cliente do Link de Pagamento pode receber em D+2? | in_domain | 0.788 | 0.880 | 0.852 |
| I22 Quais formas de pagamento são aceitas na solução de dívidas? | in_domain | 0.676 | 0.599 | 0.574 |
| O01 Qual é a previsão do tempo em Porto Alegre amanhã? | out_of_domain | 0.564 | 0.477 | 0.437 |
| O02 Qual foi o resultado do jogo do Grêmio ontem? | out_of_domain | 0.446 | 0.243 | 0.240 |
| O03 Como trocar o óleo do carro? | out_of_domain | 0.510 | 0.329 | 0.307 |
| O04 Qual é a capital da Austrália? | out_of_domain | 0.345 | 0.268 | 0.265 |
| O05 Você pode indicar um bom contador em Porto Alegre? | out_of_domain | 0.558 | 0.458 | 0.387 |
| O06 Qual é a cotação do euro hoje? | out_of_domain | 0.520 | 0.276 | 0.381 |
| O07 Como fazer um bolo de cenoura? | out_of_domain | 0.521 | 0.257 | 0.242 |
| O08 Quem ganhou a Copa do Mundo de 2002? | out_of_domain | 0.499 | 0.194 | 0.194 |
| O09 Qual foi o melhor filme de 2025? | out_of_domain | 0.441 | 0.282 | 0.291 |
| O10 Como declarar o imposto de renda de pessoa física? | out_of_domain | 0.558 | 0.429 | 0.339 |
| O11 Qual é o horário de funcionamento dos Correios? | out_of_domain | 0.566 | 0.417 | 0.352 |
| O12 Como emitir a segunda via da conta de luz? | out_of_domain | 0.494 | 0.503 | 0.464 |
| O13 Qual é a taxa Selic atual? | out_of_domain | 0.495 | 0.359 | 0.381 |
| O14 Quanto custa uma passagem de ônibus em São Paulo? | out_of_domain | 0.599 | 0.369 | 0.299 |
| O15 Qual é a distância da Terra até a Lua? | out_of_domain | 0.413 | 0.217 | 0.214 |
