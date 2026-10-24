# Retrieval threshold calibration

82 in-domain questions the corpus answers and 15 out-of-domain ones, from `evals/retrieval_calibration.jsonl`, each run through the hybrid retriever over an index built from the committed snapshot with one embedder. Confidence is the best dense similarity among the fused hits. Written by `uv run python -m evals.run_retrieval_calibration` on 2026-10-23.

| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC | Best threshold | Kept / rejected at it | Search p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| chroma default (all-MiniLM-L6-v2) | 0.456 / 0.675 | 0.519 / 0.657 | -0.201 | 0.937 | 0.579 | 76/82 kept, 13/15 rejected | 41 ms |
| openai: `text-embedding-3-small` | 0.555 / 0.742 | 0.336 / 0.516 | +0.039 | 1.000 | 0.555 | 82/82 kept, 15/15 rejected | 409 ms |
| openai: `text-embedding-3-large` | 0.532 / 0.738 | 0.320 / 0.464 | +0.067 | 1.000 | 0.532 | 82/82 kept, 15/15 rejected | 494 ms |

Gap is the lowest in-domain score minus the highest out-of-domain one: positive means one threshold separates the groups completely. AUC is the chance that a random in-domain question outscores a random out-of-domain one.

## Per query

| Query | Group | chroma default (all-MiniLM-L6-v2) | openai: `text-embedding-3-small` | openai: `text-embedding-3-large` |
| --- | --- | --- | --- | --- |
| I01 Qual é a diferença entre a maquininha Get Clássica e a Get Smart? | in_domain | 0.640 | 0.670 | 0.722 |
| I02 É necessário ter uma conta bancária para receber as vendas via Pix? | in_domain | 0.702 | 0.702 | 0.627 |
| I03 Como funciona a antecipação de recebíveis na Getnet? | in_domain | 0.748 | 0.724 | 0.755 |
| I04 Em quantas parcelas posso dividir uma venda no crediário? | in_domain | 0.620 | 0.633 | 0.726 |
| I05 É possível vender pelo WhatsApp usando o Link de Pagamento? | in_domain | 0.657 | 0.649 | 0.609 |
| I06 Até quantas parcelas posso fazer e como sei os juros para informar ao cliente? | in_domain | 0.655 | 0.835 | 0.793 |
| I07 O que é o chip Algar? | in_domain | 0.840 | 0.832 | 0.847 |
| I08 Como coloco o chip na maquininha? | in_domain | 0.634 | 0.796 | 0.806 |
| I09 Quem pode abrir uma Get Conta? | in_domain | 0.720 | 0.875 | 0.836 |
| I10 Quais modalidades de crédito parcelado estão disponíveis? | in_domain | 0.727 | 0.690 | 0.700 |
| I11 Como fazer um Pix pela Get Conta? | in_domain | 0.743 | 0.876 | 0.867 |
| I12 O que é chargeback? | in_domain | 0.627 | 0.798 | 0.840 |
| I13 Como faço uma venda digitada? | in_domain | 0.717 | 0.758 | 0.682 |
| I14 Como funciona a pré-autorização? | in_domain | 0.579 | 0.718 | 0.768 |
| I15 Por quanto tempo ainda posso usar a máquina depois de pedir o cancelamento? | in_domain | 0.757 | 0.867 | 0.807 |
| I16 Como o aluguel é cobrado depois do cancelamento da máquina? | in_domain | 0.839 | 0.792 | 0.814 |
| I17 O que é NFC e por que preciso dele no celular? | in_domain | 0.752 | 0.866 | 0.828 |
| I18 Qual é a mensalidade da Get Smart? | in_domain | 0.658 | 0.740 | 0.688 |
| I19 Como cuidar da bateria da Get Smart para durar mais? | in_domain | 0.755 | 0.859 | 0.851 |
| I20 O que é cessão de recebíveis? | in_domain | 0.706 | 0.836 | 0.762 |
| I21 O cliente do Link de Pagamento pode receber em D+2? | in_domain | 0.788 | 0.880 | 0.850 |
| I22 Quais formas de pagamento são aceitas na solução de dívidas? | in_domain | 0.676 | 0.599 | 0.576 |
| N01 Minha maquininha não conecta na internet, o que eu faço? | in_domain | 0.607 | 0.559 | 0.583 |
| N02 A maquininha mostrou um erro e a transação foi negada, o que significa? | in_domain | 0.620 | 0.610 | 0.585 |
| N03 Quando cai na conta o dinheiro das vendas de ontem? | in_domain | 0.549 | 0.555 | 0.621 |
| N04 A Get Clássica imprime comprovante? | in_domain | 0.520 | 0.573 | 0.548 |
| N05 Tem taxa para receber Pix na maquininha? | in_domain | 0.658 | 0.734 | 0.678 |
| N06 Em quanto tempo eu recebo as vendas no crédito? | in_domain | 0.654 | 0.727 | 0.743 |
| N07 Dá para usar a maquininha só no Wi-Fi, sem o chip? | in_domain | 0.649 | 0.578 | 0.623 |
| N08 Como cancelo uma venda que fiz ontem? | in_domain | 0.696 | 0.685 | 0.624 |
| N09 Preciso ter CNPJ para usar a Getnet? | in_domain | 0.617 | 0.664 | 0.644 |
| N10 Quais bandeiras de cartão a Get Smart aceita? | in_domain | 0.706 | 0.837 | 0.855 |
| N11 Como faço o teste de comunicação da maquininha? | in_domain | 0.666 | 0.607 | 0.615 |
| N12 Qual o telefone da central de atendimento? | in_domain | 0.633 | 0.594 | 0.587 |
| N13 A Getnet tem conta digital? | in_domain | 0.600 | 0.644 | 0.654 |
| N14 Minha maquininha não liga, o que eu faço? | in_domain | 0.599 | 0.599 | 0.623 |
| N15 Passei a mesma venda duas vezes sem querer na Get Smart, e agora? | in_domain | 0.628 | 0.620 | 0.606 |
| N16 A maquininha está dando erro de comunicação. | in_domain | 0.680 | 0.610 | 0.577 |
| N17 Já sou cliente, consigo mudar as taxas da minha maquininha? | in_domain | 0.637 | 0.770 | 0.714 |
| N18 A maquininha não consegue ler o chip do cartão do cliente. | in_domain | 0.657 | 0.653 | 0.532 |
| N19 A Get Smart aceita Pix? | in_domain | 0.456 | 0.636 | 0.723 |
| N20 Como funciona o estorno de uma venda? | in_domain | 0.607 | 0.716 | 0.741 |
| N21 Qual a diferença entre gravame e cessão de recebíveis? | in_domain | 0.749 | 0.874 | 0.894 |
| N22 Em que horário posso fazer a antecipação das vendas? | in_domain | 0.716 | 0.730 | 0.759 |
| N23 Por quais canais consigo antecipar minhas vendas? | in_domain | 0.617 | 0.670 | 0.689 |
| N24 Como sei quais vendas já foram antecipadas? | in_domain | 0.735 | 0.710 | 0.689 |
| N25 Tenho antecipação automática, como vejo os lançamentos futuros? | in_domain | 0.843 | 0.753 | 0.761 |
| N26 Vale a pena antecipar as vendas depois de datas de muito movimento? | in_domain | 0.669 | 0.629 | 0.594 |
| N27 Como faço a antecipação de vendas pela Get Smart? | in_domain | 0.742 | 0.744 | 0.714 |
| N28 Como mudo o limite do Pix na Get Conta? | in_domain | 0.707 | 0.865 | 0.846 |
| N29 O que é o MED do Pix? | in_domain | 0.669 | 0.809 | 0.794 |
| N30 Recebi um Pix por engano, como eu devolvo? | in_domain | 0.756 | 0.808 | 0.768 |
| N31 Dá para cancelar um Pix agendado? | in_domain | 0.878 | 0.862 | 0.803 |
| N32 Recebi um Pix e o valor ficou bloqueado, o que faço? | in_domain | 0.686 | 0.832 | 0.795 |
| N33 Posso colocar o QR Code da Get Conta na minha maquininha? | in_domain | 0.904 | 0.866 | 0.774 |
| N34 Fiz uma venda no Pix e não saiu o comprovante, o que faço? | in_domain | 0.688 | 0.758 | 0.710 |
| N35 O botão do Pix não aparece no portal, o que devo fazer? | in_domain | 0.674 | 0.785 | 0.791 |
| N36 O que é Pix Saque e Pix Troco? | in_domain | 0.704 | 0.899 | 0.895 |
| N37 Disseram que minha chave Pix foi reivindicada por outra pessoa, o que faço? | in_domain | 0.775 | 0.762 | 0.767 |
| N38 Quantas chaves Pix preciso cadastrar na Getnet? | in_domain | 0.718 | 0.814 | 0.850 |
| N39 Existe limite de quantidade de transações no Pix? | in_domain | 0.787 | 0.891 | 0.789 |
| N40 Como recebo um Pix por QR Code na Get Conta? | in_domain | 0.868 | 0.867 | 0.867 |
| N41 Como contrato o crediário e qual é a taxa? | in_domain | 0.617 | 0.684 | 0.762 |
| N42 Onde vejo o pagamento do crediário e quando recebo? | in_domain | 0.592 | 0.780 | 0.737 |
| N43 Qualquer loja pode oferecer o crediário? | in_domain | 0.539 | 0.693 | 0.651 |
| N44 O que é o Receba Já? | in_domain | 0.582 | 0.825 | 0.798 |
| N45 Quais maquininhas podem ter o Receba Já? | in_domain | 0.666 | 0.677 | 0.703 |
| N46 Se eu contratar o Receba Já D+2, as vendas que já fiz entram no prazo novo? | in_domain | 0.701 | 0.834 | 0.825 |
| N47 A Get Smart emite nota fiscal eletrônica? | in_domain | 0.757 | 0.794 | 0.739 |
| N48 Qual a diferença entre a Get Smart e o TEF móvel? | in_domain | 0.731 | 0.838 | 0.862 |
| N49 As vendas da Get Smart aparecem no conciliador que eu já uso? | in_domain | 0.811 | 0.835 | 0.800 |
| N50 Como faço o fechamento de vendas na Get Smart? | in_domain | 0.733 | 0.756 | 0.695 |
| N51 Como cobro por aproximação na Get Smart? | in_domain | 0.662 | 0.640 | 0.587 |
| N52 Como confiro as últimas vendas na Get Smart? | in_domain | 0.721 | 0.702 | 0.715 |
| N53 Como troco a senha do lojista na maquininha? | in_domain | 0.607 | 0.775 | 0.737 |
| N54 Como reimprimo o comprovante de uma venda? | in_domain | 0.835 | 0.806 | 0.708 |
| N55 Quais relatórios consigo imprimir na maquininha? | in_domain | 0.726 | 0.703 | 0.740 |
| N56 Como configuro o Wi-Fi na Get Mini? | in_domain | 0.652 | 0.779 | 0.749 |
| N57 Como habilito minha Get Clássica quando ela chega? | in_domain | 0.514 | 0.626 | 0.643 |
| N58 O que vem na caixa da Get Mini e qual é a garantia? | in_domain | 0.547 | 0.588 | 0.746 |
| N59 O que significam os ícones na tela da Get Clássica? | in_domain | 0.617 | 0.589 | 0.613 |
| N60 A maquininha mostra que não está inicializada, como resolvo? | in_domain | 0.662 | 0.575 | 0.559 |
| O01 Qual é a previsão do tempo em Porto Alegre amanhã? | out_of_domain | 0.564 | 0.477 | 0.437 |
| O02 Qual foi o resultado do jogo do Grêmio ontem? | out_of_domain | 0.509 | 0.243 | 0.251 |
| O03 Como trocar o óleo do carro? | out_of_domain | 0.541 | 0.330 | 0.320 |
| O04 Qual é a capital da Austrália? | out_of_domain | 0.345 | 0.268 | 0.265 |
| O05 Você pode indicar um bom contador em Porto Alegre? | out_of_domain | 0.558 | 0.458 | 0.387 |
| O06 Qual é a cotação do euro hoje? | out_of_domain | 0.519 | 0.336 | 0.381 |
| O07 Como fazer um bolo de cenoura? | out_of_domain | 0.657 | 0.293 | 0.287 |
| O08 Quem ganhou a Copa do Mundo de 2002? | out_of_domain | 0.492 | 0.200 | 0.220 |
| O09 Qual foi o melhor filme de 2025? | out_of_domain | 0.441 | 0.300 | 0.291 |
| O10 Como declarar o imposto de renda de pessoa física? | out_of_domain | 0.558 | 0.429 | 0.344 |
| O11 Qual é o horário de funcionamento dos Correios? | out_of_domain | 0.566 | 0.516 | 0.427 |
| O12 Como emitir a segunda via da conta de luz? | out_of_domain | 0.494 | 0.503 | 0.464 |
| O13 Qual é a taxa Selic atual? | out_of_domain | 0.514 | 0.435 | 0.418 |
| O14 Quanto custa uma passagem de ônibus em São Paulo? | out_of_domain | 0.603 | 0.369 | 0.299 |
| O15 Qual é a distância da Terra até a Lua? | out_of_domain | 0.445 | 0.217 | 0.215 |
