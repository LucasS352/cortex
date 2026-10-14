# Aprendizado supervisionado no Cortex

## Operação
1. Nas novas notas internas do Cortex, use **Avaliar esta resposta e encaminhar à curadoria**. O link abre o Cortex autenticado; o clique não aprova nem grava nada.
2. Para respostas anteriores, entre em **Treinamento da IA**, informe o ticket (por exemplo, 131726) e clique em **Avaliar resposta**. Escolha a ação exata. O histórico precisa estar sincronizado.
3. Selecione o módulo e registre **Boa resposta — enviar para revisão** ou **Precisa melhorar**, com comentário. Boa resposta cria candidata, nunca publica automaticamente.
4. Ao final do dia, revise a **Fila de curadoria**: sintoma, causa, passos, pré-requisitos, resultado, escopo e imagens. As avaliações positivas e negativas ficam também na aba **Avaliações dos técnicos**.
5. Escolha novo procedimento, revisão ou evidência complementar. Informe o motivo. Módulo “A classificar” não pode ser publicado.
6. Procedimentos ativos e aplicáveis têm prioridade sobre históricos e receitas fixas. A resposta conserva os passos aprovados e as imagens vinculadas. Revisões preservam versões anteriores; inativação retira o procedimento da busca.

## Módulos e imagens
**Gerenciar módulos** cria e renomeia módulos. A chave permanente mantém os vínculos mesmo com alteração do nome. Subtópicos existentes são preservados; módulos novos começam em Geral / outros.

O editor permite selecionar o catálogo de prints revisados, informar legenda e vincular cada print a um passo numerado existente. Novos uploads e imagens arbitrárias do chamado não são incorporados automaticamente. Essa seleção exige conferir conteúdo e dados visíveis antes de publicar.

## Garantias e limites
- Avaliação é de uma resposta específica, com ticket, ação, autor da sessão, comentário e data; não é aprovação do ticket inteiro.
- Uma candidata aberta por ticket evita revisões concorrentes de respostas diferentes. Conclua ou rejeite a anterior antes de avaliar outra resposta positivamente.
- A IA extrai uma proposta; não considera a própria resposta confirmação do cliente. O revisor confere a solução.
- Conhecimento aprovado é uma referência versionada, não uma verdade absoluta. Versão do Master, UF, integração, regime e validade limitam sua aplicação. Escopo não confirmado impede reutilização automática do procedimento.
- Login atual do Cortex usa a identidade da sessão configurada. Conta compartilhada registra esse mesmo usuário; não identifica automaticamente o técnico logado no Movidesk. Não foram adicionados usuários individuais nesta mudança.
- Não há aprovação automática por horário nem treinamento dos pesos do modelo: há publicação na base consultada pelo sistema.
- O ticket 131726 não foi aprovado automaticamente. Use a ação específica desejada na interface após sincronização.

## Validação
Testes unitários cobrem seleção da resposta, avaliação sem publicação, classificação obrigatória, imagens por passo, link para a ação, validade e versão vigente. Teste PostgreSQL transacional cobre módulo novo/renomeado, avaliação, candidata, aprovação, recuperação canônica, revisão e inativação; todos os dados de teste são revertidos. Interface verificada em navegador local com dados fictícios.

Migration 006 é idempotente e reaplicada pela aplicação no acesso ao treinamento. A atualização da imagem no Portainer ainda é necessária após o pipeline do Git. Nenhum chamado real foi alterado durante os testes.
