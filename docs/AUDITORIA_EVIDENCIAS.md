# Auditoria de evidências — 13/10/2026

Escopo verificado: código do worker Movidesk, busca compartilhada e corpus do PostgreSQL local deskcortex. Não confirma versão nem conteúdo implantados no servidor de produção.

## Evidências confirmadas

- Corpus local: 1.400 artigos (1.400 embeddings), 158 manuais (139 embeddings), 25.556 casos prováveis (25.203 embeddings), 2.833 casos validados (2.832 embeddings).
- A busca aceita embedding opcional, mas o worker não o fornece: nesse caminho, a recuperação é lexical e por identificadores. Não há comprovação de busca vetorial ativa no atendimento.
- Artigos 207361 e 343261 sobre cadastro de produto têm conteúdo textual vazio e vídeos incorporados A6nUw8j7TE4 e W9iEU0PuBfY. Vídeos sem transcrição eram classificados para investigação e excluídos das referências da nota.
- Criar não expandia para cadastrar/cadastro. Ranking local da pergunta real priorizava margem, importação e kits. Com expansão e ajuste de intenção, os dois tutoriais básicos passam às primeiras posições.
- Metadados de imagens existem em artigos, mas incluem URLs assinadas temporárias antigas. O worker só incorpora imagens revisadas locais; não recupera automaticamente essas imagens dos artigos.
- Histórico utilizável da empresa interrompe a busca geral. A busca adicional de casos resolvidos existe, mas ainda não representa auditoria completa da recuperação de todos os documentos.

## Correções desta revisão

Expansão criar/cadastrar, ranking de cadastro básico, recuperação estruturada de URLs YouTube do HTML original, preservação de referência mesmo sem transcrição, resposta de encaminhamento ao tutorial sem passos inventados, renderização de vídeo validado e instrução explícita contra menus genéricos sem fonte.

## Pendências

Renovação autorizada/proxy das imagens privadas, vínculo imagem-etapa fora dos roteiros revisados, transcrições dos vídeos, busca híbrida efetiva com embeddings, avaliação Recall@K/MRR em conjunto rotulado, rastreamento por etapa e validação no servidor. Esta revisão não afirma concluir essas frentes nem comprova o conteúdo dos vídeos assistindo a eles.
