# Mesa do Técnico — evidências e revisão

Implementação de 09/09/2026 no simulador existente e na API NestJS. O foco desta entrega é a conferência da pré-resposta do Romário com chamados históricos reais. O fluxo completo de autenticação ERP/browser e notificações continua especificado no documento 02.13, sem ser apresentado como implementado.

## Usar

1. Com o PostgreSQL local ativo, executar `python scripts/simulator_app.py` a partir da raiz do projeto.
2. Abrir `http://127.0.0.1:8000`, iniciar um atendimento e abrir **Mesa do Técnico**.
3. Conferir **Chamados semelhantes e evidências**, abrir o protocolo e comparar a conversa original com o resumo processado.
4. Usar a busca independente por texto ou `#protocolo` para investigar outra causa. Selecionar as referências aplicáveis, editar a resposta e explicar a correção.
5. **Registrar resposta revisada** guarda sugestão original, resposta final, motivo, referências e hashes da fonte. Aprovação do texto não encerra o chamado nem significa envio ao cliente.

Se a pré-resposta não tiver registrado suas fontes, o painel informa essa ausência e busca exemplos separadamente. Referências fornecidas ao gerador não são apresentadas como citações confirmadas. O texto do procedimento agora é entregue ao gerador pelo DTO interno `to_generation_dict`; o DTO público de referências continua sem esse conteúdo.

## Estrutura

- `tech_evidence.py`: consultas parametrizadas, deduplicação, recuperação de fontes e conversão de HTML original para texto seguro.
- `tech_desk.py`: rotas de referências e revisão, validação de versão e registro durável em SQLite para o simulador local.
- `static/tech-evidence.js` e `.css`: painel, diálogo com histórico original, busca, seleção de referências e editor. Respostas atrasadas de outra sessão não substituem o caso aberto.
- `output/tech_reviews.sqlite3`: auditoria local das revisões; contém conteúdo interno e deve seguir a política de retenção/backup da equipe. Não é incluído no Git.
- `test_tech_evidence.py`: testes de dados, falhas, concorrência, idempotência e limites de acesso.
- `preview_tech_evidence.py`: servidor separado de conferência visual com dados explicitamente fictícios, sem IA ou banco real. Abre em `http://127.0.0.1:8001/qa/tech`; não usar como servidor da aplicação.

## Contratos implementados

| Método/rota | Comportamento |
| --- | --- |
| `GET /api/tech/report/{session_id}` | Inclui revisão criptográfica da conversa para aprovação otimista. |
| `GET /api/tech/references/{session_id}?q=...&offset=0` | Fontes da última pré-resposta e até 5 outros chamados, com paginação; busca sem IA. |
| `GET /api/tech/evidence/{node_id}` | Resumo separado de ações originais, autoria, datas, versão registrada e hash da fonte. |
| `POST /api/tech/review` | Grava revisão durável e idempotente; rejeita versão antiga ou segunda aprovação concorrente. |
| `GET /api/v1/search/tickets?q=...&top_k=7` | Na API NestJS: pesquisa textual sem geração/embedding. |
| `GET /api/v1/search/evidence/{id}` | Na API NestJS: nó processado e histórico original, sob autenticação interna. |
| `POST /api/v1/copilot/diagnose` | Retorna todas as referências recuperadas deduplicadas e só aceita IDs citados que estavam no contexto. |

O NestJS também preserva a busca textual quando embeddings falham, não considera GOLD como confirmação do caso atual e deixa de montar links genéricos para o Movidesk. `CORTEX_API_KEY` precisa estar explicitamente configurada e ser enviada somente no header `x-api-key`; a chave não é aceita em query string e não existe segredo padrão embutido no guard.

## Limites reais desta entrega

- O simulador é local e interno, com sessões em memória. Foi restringido a loopback e hosts locais porque ainda não há login individual nem RBAC. Não deve ser exposto por proxy ao público. Anotações internas não entram no contexto público de investigação.
- A base atual de conhecimento não possui `tenant_id`. As novas consultas recusam bases com mais de uma organização, evitando misturar protocolos entre tenants. Ainda é necessário migrar o schema e implantar autenticação individual antes do uso SaaS ou por várias equipes.
- A ingestão não identifica de forma confiável a ação exata da resolução. O painel mostra a conversa original inteira e não atribui esse papel automaticamente à última mensagem. Também não infere build/BETA ou validação quando esses metadados não existem.
- Status e histórico refletem a última importação, não uma consulta em tempo real ao Movidesk. Anexos não são carregados pelo novo leitor de texto. Fonte ausente aparece como indisponível, sem reconstrução pela IA.
- Revisão em SQLite é durável no simulador; distribuição por múltiplas instâncias, atribuição nominal de técnicos, outbox, resposta pública, SSE, polling ERP, e-mail e OTT ainda precisam da camada transacional de atendimento descrita em 02.13.
- Rascunhos são preservados ao buscar, abrir exemplos e alternar casos na mesma página. Ainda não há salvamento de rascunho entre reinícios/recarregamentos. A auditoria das revisões salvas permanece no arquivo SQLite.

## Verificação

Na raiz: `python -m unittest discover -s scripts -p test_tech_evidence.py -v`.

Em `deskcortex-api`: `npm run build` e `npm test -- --runInBand`.

A conferência visual usa somente chamados fictícios. A consulta ao PostgreSQL real deve ser somente leitura, verificando contagem de resultados e disponibilidade de ações, sem copiar dados de clientes para fixtures ou relatórios.
