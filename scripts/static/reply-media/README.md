# Imagens de procedimentos revisados

Arquivos originais conferidos visualmente em 13/10/2026, sem alteração de conteúdo.

- `payment-selection.png`: `Instrucoes_master/nota fiscal/Integrando formas de pagamento e transportadora.docx`, `word/media/image3.png`. Mostra Cadastros → Formas de Pagamento e Ctrl+R.
- `payment-relation.png`: `Instrucoes_master/nota fiscal/Erro de sincronizador.docx`, `word/media/image3.png`. Mostra Relação - Site e Código de fora.
- `product-relation.png`: `Instrucoes_master/nota fiscal/Integrando formas de pagamento e transportadora.docx`, `word/media/image5.png`. Mostra a relação de produto com Código de fora, Adicional e Tipo.
- `state-registration-delivery.png`: imagem revisada do chamado 131662. Mostra o campo RG/IE em Dados de Entrega.
- `state-registration-ccc-query.png`: imagem revisada do chamado 131662. Mostra a consulta por UF e CNPJ no CCC.
- `state-registration-ccc-result.png`: imagem revisada do chamado 131662. Mostra Inscrição Estadual e Situação IE no resultado do CCC.
- `duplicate-nfe-series-number.png`: imagem revisada por Lucas em 13/10/2026. Mostra os campos Série e Número na nota fiscal para investigar a rejeição 539; os valores do print são apenas exemplos.

Os dados e as seleções das imagens são exemplos, não valores a copiar para outro pedido. As legendas da nota explicam isso. A cópia PNG é incorporada na nota para visualização imediata e estes arquivos revisados também são expostos em `/procedure-images/` como alternativa clicável; o restante do Cortex e a biblioteca de manuais permanecem protegidos.

A resposta e seus critérios de seleção estão em `scripts/mapped_replies.py`. Para adicionar outro procedimento, conferir o documento original, a aplicabilidade, os prints e os testes negativos. Não usar a imagem de produto (`image5.png` do primeiro manual) para orientar forma de pagamento. O manual `Erro Anymarket - Sem Forma de Pagamento.docx` trata de `ANY_PAGAMENTO_STATUS`, outro cenário.
