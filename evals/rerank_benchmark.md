# Reranker benchmark

82 in-domain questions from `evals/retrieval_calibration.jsonl`, with 146 gold chunks (drafted by a reasoning model; every row reviewed by the owner, 0 edits, in `evals/retrieval_review.csv`). Each ran through the hybrid retriever over an index of the committed snapshot (270 chunks, `text-embedding-3-large`), and each reranker reordered the same 20 fused candidates. Written by `uv run --group rerank python -m evals.run_rerank_benchmark` on 2026-10-23, Darwin arm64, local models on the CPU.

| Reranker | recall@5 | MRR | Rerank p50 | Rerank p95 | Within the 1 s p95 budget |
| --- | --- | --- | --- | --- | --- |
| none | 0.939 (77/82) | 0.851 | — | — | — |
| bge | 0.963 (79/82) | 0.891 | 5278 ms | 5734 ms | no |
| qwen | 0.951 (78/82) | 0.876 | 10075 ms | 11847 ms | no |
| cohere | 0.963 (79/82) | 0.894 | 415 ms | 618 ms | yes |

recall@5 counts a question when any of its gold chunks is in the top 5. MRR is the mean of 1/rank of the first gold chunk among the 20 candidates, 0 when none is there. Rerank time is the reranker's own, per search, after a first call that loads it. A search where the reranker failed keeps the fused order, as in the service, and its time still counts.

**Rule:** the rule in T-53 picks `cohere`: on only if it raises recall@5 or MRR within the latency budget. The default is decided in `docs/adr/0002-reranker-off-by-default.md`.

## Rank of the first gold chunk

| Question | Origin | none | bge | qwen | cohere |
| --- | --- | --- | --- | --- | --- |
| I01 Qual é a diferença entre a maquininha Get Clássica e a Get Smart? | set | 8 | 3 | 3 | 5 |
| I02 É necessário ter uma conta bancária para receber as vendas via Pix? | set | 7 | 6 | 11 | 7 |
| I03 Como funciona a antecipação de recebíveis na Getnet? | set | 1 | 1 | 1 | 1 |
| I04 Em quantas parcelas posso dividir uma venda no crediário? | set | 1 | 1 | 1 | 1 |
| I05 É possível vender pelo WhatsApp usando o Link de Pagamento? | set | 1 | 1 | 1 | 1 |
| I06 Até quantas parcelas posso fazer e como sei os juros para informar ao cliente? | set | 1 | 1 | 1 | 1 |
| I07 O que é o chip Algar? | set | 1 | 1 | 1 | 1 |
| I08 Como coloco o chip na maquininha? | set | 2 | 1 | 1 | 1 |
| I09 Quem pode abrir uma Get Conta? | set | 1 | 1 | 1 | 1 |
| I10 Quais modalidades de crédito parcelado estão disponíveis? | set | 1 | 1 | 1 | 1 |
| I11 Como fazer um Pix pela Get Conta? | set | 1 | 1 | 1 | 1 |
| I12 O que é chargeback? | set | 1 | 1 | 1 | 1 |
| I13 Como faço uma venda digitada? | set | 1 | 3 | 1 | 1 |
| I14 Como funciona a pré-autorização? | set | 1 | 1 | 1 | 1 |
| I15 Por quanto tempo ainda posso usar a máquina depois de pedir o cancelamento? | set | 1 | 1 | 1 | 1 |
| I16 Como o aluguel é cobrado depois do cancelamento da máquina? | set | 1 | 1 | 1 | 1 |
| I17 O que é NFC e por que preciso dele no celular? | set | 1 | 1 | 1 | 1 |
| I18 Qual é a mensalidade da Get Smart? | set | 3 | 1 | 2 | 1 |
| I19 Como cuidar da bateria da Get Smart para durar mais? | set | 1 | 1 | 1 | 1 |
| I20 O que é cessão de recebíveis? | set | 1 | 1 | 1 | 1 |
| I21 O cliente do Link de Pagamento pode receber em D+2? | set | 1 | 1 | 1 | 1 |
| I22 Quais formas de pagamento são aceitas na solução de dívidas? | set | 1 | 1 | 1 | 1 |
| N01 Minha maquininha não conecta na internet, o que eu faço? | trace S05 | 1 | 1 | 1 | 1 |
| N02 A maquininha mostrou um erro e a transação foi negada, o que significa? | trace S08 | 1 | 3 | 3 | 1 |
| N03 Quando cai na conta o dinheiro das vendas de ontem? | trace S03 | 1 | 1 | 1 | 2 |
| N04 A Get Clássica imprime comprovante? | trace K02 | 9 | 13 | 13 | 9 |
| N05 Tem taxa para receber Pix na maquininha? | trace K03 | 1 | 1 | 2 | 5 |
| N06 Em quanto tempo eu recebo as vendas no crédito? | trace K04 | 6 | 2 | 2 | 2 |
| N07 Dá para usar a maquininha só no Wi-Fi, sem o chip? | trace K06 | 1 | 1 | 1 | 1 |
| N08 Como cancelo uma venda que fiz ontem? | trace K09 | 3 | 3 | 2 | 3 |
| N09 Preciso ter CNPJ para usar a Getnet? | trace K10 | 7 | 2 | 5 | 3 |
| N10 Quais bandeiras de cartão a Get Smart aceita? | trace K12 | 1 | 1 | 1 | 1 |
| N11 Como faço o teste de comunicação da maquininha? | trace K13 | 3 | 1 | 1 | 1 |
| N12 Qual o telefone da central de atendimento? | trace K14 | 3 | 3 | 4 | 2 |
| N13 A Getnet tem conta digital? | trace K15 | 1 | 11 | 7 | 6 |
| N14 Minha maquininha não liga, o que eu faço? | trace R01 | 4 | 1 | 1 | 1 |
| N15 Passei a mesma venda duas vezes sem querer na Get Smart, e agora? | trace R03 | 1 | 1 | 1 | 1 |
| N16 A maquininha está dando erro de comunicação. | trace R04 | 1 | 1 | 1 | 1 |
| N17 Já sou cliente, consigo mudar as taxas da minha maquininha? | page /duvidas/receba-ja-2-dias/ | 1 | 1 | 1 | 1 |
| N18 A maquininha não consegue ler o chip do cartão do cliente. | trace R06 | 4 | 4 | 4 | 3 |
| N19 A Get Smart aceita Pix? | trace C04 | 2 | 1 | 1 | 1 |
| N20 Como funciona o estorno de uma venda? | trace C07 | 1 | 1 | 1 | 1 |
| N21 Qual a diferença entre gravame e cessão de recebíveis? | page /duvidas/antecipacao-de-vendas/ | 1 | 1 | 1 | 1 |
| N22 Em que horário posso fazer a antecipação das vendas? | page /duvidas/antecipacao-de-vendas/ | 1 | 1 | 1 | 1 |
| N23 Por quais canais consigo antecipar minhas vendas? | page /duvidas/antecipacao-de-vendas/ | 1 | 1 | 1 | 1 |
| N24 Como sei quais vendas já foram antecipadas? | page /duvidas/antecipacao-de-vendas/ | 1 | 1 | 1 | 1 |
| N25 Tenho antecipação automática, como vejo os lançamentos futuros? | page /duvidas/antecipacao-de-vendas/ | 1 | 1 | 1 | 1 |
| N26 Vale a pena antecipar as vendas depois de datas de muito movimento? | page /quando-vale-a-pena-antecipar-as-suas-vendas-no-cartao/ | 1 | 1 | 1 | 1 |
| N27 Como faço a antecipação de vendas pela Get Smart? | manual Get Smart | 1 | 1 | 1 | 1 |
| N28 Como mudo o limite do Pix na Get Conta? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N29 O que é o MED do Pix? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N30 Recebi um Pix por engano, como eu devolvo? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N31 Dá para cancelar um Pix agendado? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N32 Recebi um Pix e o valor ficou bloqueado, o que faço? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N33 Posso colocar o QR Code da Get Conta na minha maquininha? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N34 Fiz uma venda no Pix e não saiu o comprovante, o que faço? | page /duvidas/pix/ | 1 | 1 | 1 | 1 |
| N35 O botão do Pix não aparece no portal, o que devo fazer? | page /duvidas/pix/ | 1 | 1 | 1 | 1 |
| N36 O que é Pix Saque e Pix Troco? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N37 Disseram que minha chave Pix foi reivindicada por outra pessoa, o que faço? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N38 Quantas chaves Pix preciso cadastrar na Getnet? | page /duvidas/pix/ | 2 | 1 | 2 | 1 |
| N39 Existe limite de quantidade de transações no Pix? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N40 Como recebo um Pix por QR Code na Get Conta? | page /duvidas/pix-mais-respostas/ | 1 | 1 | 1 | 1 |
| N41 Como contrato o crediário e qual é a taxa? | page /duvidas/crediario/ | 1 | 2 | 1 | 2 |
| N42 Onde vejo o pagamento do crediário e quando recebo? | page /duvidas/crediario/ | 1 | 1 | 1 | 1 |
| N43 Qualquer loja pode oferecer o crediário? | page /duvidas/crediario/ | 1 | 1 | 1 | 1 |
| N44 O que é o Receba Já? | page /duvidas/receba-ja-2-dias/ | 1 | 1 | 1 | 1 |
| N45 Quais maquininhas podem ter o Receba Já? | page /duvidas/receba-ja-2-dias/ | 1 | 1 | 1 | 1 |
| N46 Se eu contratar o Receba Já D+2, as vendas que já fiz entram no prazo novo? | page /duvidas/receba-ja-2-dias/ | 1 | 1 | 2 | 1 |
| N47 A Get Smart emite nota fiscal eletrônica? | page /duvidas/get-smart/ | 1 | 1 | 1 | 1 |
| N48 Qual a diferença entre a Get Smart e o TEF móvel? | page /duvidas/get-smart/ | 1 | 1 | 1 | 1 |
| N49 As vendas da Get Smart aparecem no conciliador que eu já uso? | page /duvidas/get-smart/ | 1 | 1 | 1 | 1 |
| N50 Como faço o fechamento de vendas na Get Smart? | manual Get Smart | 1 | 1 | 1 | 1 |
| N51 Como cobro por aproximação na Get Smart? | page /duvidas/get-smart/ | 1 | 1 | 1 | 1 |
| N52 Como confiro as últimas vendas na Get Smart? | manual Get Smart | 1 | 1 | 1 | 1 |
| N53 Como troco a senha do lojista na maquininha? | page /duvidas/operacional/ | 4 | 3 | 7 | 2 |
| N54 Como reimprimo o comprovante de uma venda? | page /duvidas/operacional/ | 1 | 1 | 1 | 1 |
| N55 Quais relatórios consigo imprimir na maquininha? | page /duvidas/operacional/ | 1 | 1 | 1 | 1 |
| N56 Como configuro o Wi-Fi na Get Mini? | manual Get Mini | 2 | 1 | 1 | 1 |
| N57 Como habilito minha Get Clássica quando ela chega? | manual Get Clássica | 1 | 1 | 1 | 1 |
| N58 O que vem na caixa da Get Mini e qual é a garantia? | manual Get Mini | 1 | 1 | 1 | 1 |
| N59 O que significam os ícones na tela da Get Clássica? | manual Get Clássica | 2 | 1 | 1 | 1 |
| N60 A maquininha mostra que não está inicializada, como resolvo? | manual Get Clássica and Get Mini | 2 | 1 | 1 | 1 |
