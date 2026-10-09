# Sincronização dos chamados atuais

Execute na raiz do projeto:

```powershell
python scripts/sync_current_tickets.py
```

O comando busca os chamados criados após o maior ID do banco e atualiza os
chamados anteriores que continuam abertos nas rotas `/tickets` e `/tickets/past`.
Depois reconcilia em lotes os registros históricos ainda marcados como abertos
que não aparecem na fotografia atual, atualizando as datas de resolução e
fechamento sem substituir a conversa original por dados resumidos. A paginação usa o ID
como cursor, faz retry em erros temporários e grava cada página em uma
transação. É seguro executar novamente após interrupção.

O payload completo, com ações e metadados de anexos, fica em `raw_tickets`.
Os campos organizados ficam em `refined_tickets`. A fila confirmada na última
sincronização completa está na view `current_open_tickets`. Chamados cancelados,
resolvidos e encerrados não entram nessa fila. Registros históricos com status
antigo também não aparecem nela sem confirmação recente da API.
As cargas feitas antes da consulta às duas rotas usam `coverage_version=1` e
não entram na curva de backlog da tela de vazão; as novas usam versão 2.

O painel administrativo de chamados usa `current_open_ticket_facts`, que contém
os dados congelados em `movidesk_open_snapshot.dashboard_data` para a última
sincronização concluída. Assim, uma carga nova ou parcial não altera as contagens
que o painel está exibindo. O arquivo `infra/002_current_ticket_snapshot.sql`
cria essa estrutura e preenche a última carga ao atualizar uma instalação antiga;
o sincronizador o executa antes de iniciar cada nova carga.

No painel, cada chamado ocupa uma única categoria de empresa. Os IDs de
organização distintos em `clients` determinam a categoria: nenhum ID significa
"Não identificada"; dois ou mais significam "Empresa ambígua". Um mesmo ID
repetido entre contatos continua sendo uma empresa. O responsável é o `owner`
atual do Movidesk; a equipe é `ownerTeam`. O tema é uma classificação sugerida
por palavras-chave, não uma causa confirmada.

Na visão geral, a distribuição por equipe usa uma categoria separada para
chamados da equipe Suporte com status contendo "Aguardando Desenvolvimento".
Esses chamados saem do card Suporte e não entram no card Desenvolvimento;
permanecem no total geral. A faixa "Fila por status" é independente da equipe
e, por isso, pode apresentar outro total para desenvolvimento.

Para ver os chamados atuais atribuídos a uma pessoa:

```sql
SELECT t.external_id, t.subject, t.status, t.urgency, t.current_as_of
FROM current_open_tickets t
JOIN raw_tickets w ON w.id = t.raw_ticket_id
WHERE w.payload->'owner'->>'businessName' = 'Lucas Souza'
ORDER BY t.created_date DESC;
```

Os chamados novos são material de investigação. Este comando não cria
`ticket_knowledge_nodes`, não aprova soluções e não envia respostas aos
clientes. Uma solução só deve entrar no RAG após validação humana.
# Exceções de chamado indisponível

Se um chamado anteriormente registrado como aberto desaparecer das duas rotas de listagem e a consulta direta retornar 404, a sincronização registra o ID em `movidesk_sync_unavailable` para a execução. Ela não inventa uma data de resolução ou fechamento. Esses IDs exigem conferência na origem; a fotografia de backlog é o conjunto de chamados abertos alcançáveis pela API, com exceções informadas à parte.

`older_open_count` soma os registros atualizados pelas rotas recente (`/tickets`) e histórica (`/tickets/past`). A carga concluída em 06/10/2026 encontrou um chamado indisponível (ID 129639); a tela de vazão mostra essa exceção separadamente do backlog observado.

