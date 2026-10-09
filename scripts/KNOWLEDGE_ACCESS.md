# Busca e cobertura da base

## Correção geral de cobertura e aplicabilidade — 07/10/2026

A auditoria encontrou 448 históricos Gold/Silver sem nó pesquisável. A manutenção
lexical incluiu 321; 126 ainda estavam abertos e um tinha solução insuficiente.
`knowledge_index.py` reconcilia os elegíveis antes da busca compartilhada, no máximo
uma vez por minuto por processo. Não depende de embeddings, API externa ou IDs de
chamados específicos. Pode ser executado diretamente para reconciliação imediata.
Usa transação e trava entre processos; falhas não marcam a cobertura como atualizada.
Essa manutenção escreve apenas no índice local, por conexão própria; os originais
e nós existentes, inclusive suas exclusões editoriais, são preservados.

Critérios: organização única, fonte original presente, Gold/Silver, resolvido ou
fechado, sem bloqueio de qualidade e solução com pelo menos 45 caracteres. Os
novos nós são históricos prováveis, nunca documentos oficiais. A consulta também
confere o estado atual dos históricos selecionados, para impedir que um chamado
reaberto/bloqueado continue sustentando uma correção automática.

`applicability()` confere mensagens técnicas, códigos contextualizados, objetos,
integração e correspondência temática. HTTP 400 e identificadores opacos isolados
não justificam uma causa. A avaliação compartilhada impede substituir uma fonte
primária inaplicável por outra menos correspondente. Rascunhos, RAG e síntese técnica
usam essa avaliação. A síntese antiga não inventa mais telas, atalhos ou percentuais
de confiança a partir de palavras-chave. Logs recebidos não são substituídos por
pedidos repetidos do mesmo log no fallback do rascunho.

Validação: `test_evidence_policy.py`, `test_ticket_simulator.py` e a avaliação de
recuperação em `evaluate_knowledge_access.py`. Esta última cobre oito consultas
conhecidas de vários módulos e quatro controles negativos; não mede precisão
diagnóstica nem garante ausência de alucinações. A manutenção cobre lacunas de
indexação elegíveis, não transcreve vídeos e não reescreve soluções incompletas.
Os casos REVIEW/IGNORE e históricos abertos não são promovidos automaticamente.

## Entrega

`knowledge_access.py` centraliza a busca usada por `lexical_search`, `hybrid_search`, pesquisa de evidências e referências semelhantes. O módulo RAG recebe os limites de evidência e o reranker preserva a pontuação de recuperação. Não há treino de modelo nesta etapa.

`knowledge_map.py` examina todos os nós pesquisáveis e reconcilia os IDs com os chamados/artigos originais. Exporta `output/knowledge-curation/coverage.html` e `coverage.json`. Famílias são inferidas e sobrepostas; seus totais não devem ser somados como documentos únicos.

```powershell
python scripts/knowledge_map.py
python scripts/knowledge_map.py --query "como cadastrar produtos e grades"
python scripts/evaluate_knowledge_access.py
python -m unittest discover -s scripts -p test_knowledge_access.py -v
```

O mapa pode ser aberto offline ou em http://127.0.0.1:8769/coverage.html, se o servidor local do piloto estiver em execução.

## Caminho de busca

1. Verifica que o corpus atual pertence a uma única organização: os nós legados não carregam tenant.
2. Extrai assinatura Oracle, nome de objeto, rejeição contextualizada ou protocolo explícito. Um número de pedido não vira automaticamente um código de erro.
3. Procura assinaturas literais; `_` e `%` não viram curingas SQL.
4. Para linguagem natural, pesquisa títulos, documentos oficiais e texto completo em faixas próprias. Isso evita que chamados repetitivos ocupem todo o limite de candidatos e escondam um artigo curto.
5. Aplica sinônimos controlados e flexões para termos de cadastro, grades, NF-e, integração e boletos.
6. Quando existe embedding, acrescenta candidatos vetoriais; correspondência técnica explícita continua prioritária. A avaliação executada aqui foi lexical, sem chamadas externas.
7. Expõe a origem, termos encontrados, uso recomendado e lacunas. Artigos são abertos na tabela original de artigos; não como se fossem chamados de mesmo número.

Consulta vazia ou assinatura desconhecida retorna falta de evidência. Não há fallback universal para títulos `%`. A busca histórica paralela antiga do painel foi desativada para não ultrapassar os mesmos critérios. Cache de referências do painel foi versionado.

## Confiança e limites

`retrieval_score` é ordenação, não probabilidade. A tela do técnico passou a rotular o número como relevância, sem porcentagem de confiança.

- `procedure_reference`: documento oficial com texto aparentemente suficiente; exige conferir aplicabilidade.
- `supporting_reference`: histórico/manual para apoiar análise; não confirma a causa atual.
- `investigation_only`: texto insuficiente, resolução genérica, vídeo sem transcrição detectável ou orientação arriscada a revisar.

Se a primeira fonte estiver incompleta, o resultado sinaliza `primary_source_incomplete`: não compensar os passos ausentes com um caso menos correspondente. O contexto do RAG leva esse aviso. Isso não é uma prova formal contra toda alucinação: ainda exige avaliação das respostas finais e revisão técnica.

Heurísticas de texto podem produzir falsos positivos e negativos. Data recente ou Gold não significa solução validada. Verificar o arquivo original de um manual comprova disponibilidade, não qualidade da extração.

## Como transformar lacunas em trabalho

| Sinal | Próxima ação | Precisa escrever conteúdo novo? |
|---|---|---|
| Versão/tela não estruturada | Extrair o que já existe no texto, anexo e documentação do DEV | Só se a informação realmente não existir |
| Artigo em vídeo | Transcrever, marcar etapas/timestamps e revisar telas | Geralmente reaproveitar material |
| Resolução “sistema atualizado” | Registrar defeito, alteração, versão e teste de confirmação | Sim, com quem executou a correção |
| Histórico sem solução | Verificar últimas ações e chamados relacionados | Talvez |
| Mesmo texto repetido | Consolidar referências preservando cenários distintos | Geralmente não |
| Fonte original sem vínculo | Corrigir caminho/ID e conferir extração | Geralmente não |
| Chamado fora do índice | Auditar motivo de descarte antes de reindexar | Não necessariamente |

Campos mínimos de um novo procedimento:

```text
Título e termos usados pelos técnicos:
Produto / módulo / integração:
Quando aplicar (sintoma e pré-condições):
Quando não aplicar:
Mensagem completa / assinatura técnica:
Versões e ambiente em que foi validado:
Caminho de tela:
Como confirmar a causa:
Passos executados:
Resultado esperado e como verificar:
Riscos / reversão / responsável por executar:
Evidências (chamado + ação / artigo / vídeo + instante):
Revisor e data:
```

## Verificação e pendências

`search-evaluation.json` registra oito consultas conhecidas de produtos, estoque, financeiro, vendas e Safetech e quatro controles negativos. São testes de regressão; não medem precisão independente. `test_knowledge_access.py` verifica assinaturas, consultas vazias, resolução genérica, fontes incompletas e recuperação correta de artigos.

Na suíte existente de evidências, há um teste de bloqueio de rede externa que falha porque o middleware `internal_simulator_only` já contém `pass` para clientes externos. Essa configuração não foi alterada por este trabalho. Não publicar o servidor como aplicação pública sem revisar essa fronteira. Os demais testes de evidências/revisão foram executados.

Mudanças no código do servidor exigem recarregar o processo se ele não estiver com auto-reload. Nenhum processo pré-existente foi encerrado. O mapa é snapshot e deve ser reconstruído após novas ingestões. Persistir consultas sem resultado e incorporar feedback de respostas finais ainda são etapas seguintes; este lote não cria um monitor ou job recorrente.
