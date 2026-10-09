# Painel Cortex

Endereço local: http://127.0.0.1:8001/admin

Em produção no Portainer, o acesso é `https://<CORTEX_DOMAIN>/admin` com login obrigatório. Veja `docs/DEPLOY_PORTAINER.md` na raiz para imagem, variáveis e migração dos dados.

O receptor de webhooks Movidesk é um serviço separado na porta local 8003. Consulte `WEBHOOK_RECEIVER.md` antes de publicar uma URL externa; não exponha a porta administrativa 8001.

Para iniciar após reiniciar a máquina, execute na pasta do projeto:

```powershell
python -m uvicorn simulator_app:app --app-dir scripts --host 127.0.0.1 --port 8001
```

## Demonstração

1. **Visão geral:** volume de chamados, prioridades, temas e status da última carga local.
2. **Chamados abertos:** busca por assunto, descrição, organização ou número; filtros por tema e status.
3. **Base de conhecimento:** navegue pelos módulos e assuntos (por exemplo, Produtos > Grade e variações ou Fiscal > Rejeições). Filtre pendentes, aprovados, excluídos, sem procedimento e sem versão. Em **Revisar registro**, compare o texto original, ajuste módulo, assunto, título, cenário, pré-requisitos, passos, resultado esperado e versão; registre o motivo. Use **Pendente** para continuar a redação, **Aprovado** quando o procedimento tiver sido conferido e **Excluído** para retirar uma fonte inadequada da busca. A aprovação exige passos e resultado esperado preenchidos.
4. **Agente e políticas:** pausa, fontes permitidas, intervalo mínimo, limite por minuto e simultaneidade. Salvar aplica os limites às novas chamadas de `/api/draft`.
5. **Desempenho > Testar resposta:** informa contexto, consulta `/api/search` e gera pré-resposta com o provedor configurado. Isso pode consumir a API. O teste entra no histórico e nos indicadores, sem enviar mensagens ao cliente.
6. **Revisão de respostas:** confere texto/contexto/referências e registra aprovação, correção ou rejeição. Correções e rejeições exigem observação.
7. **Histórico de alterações:** últimas 100 mudanças e avaliações locais.
8. **Vazão e backlog:** entradas, resoluções, fechamentos e a última fotografia de abertos por dia em Brasília. Clique nas contagens para auditar os chamados. A taxa `resolvidos / entradas` e os eventos históricos são provisórios enquanto os registros antigos são reconciliados; o backlog vem das cargas completas. Não há atribuição de ganho à IA sem vínculo entre geração, uso da resposta e ID do chamado.

## Interpretação

- Aprovação = aprovadas sem ajustes / respostas avaliadas. Não é uma medida automática de verdade ou de resolução do chamado.
- Cobertura = avaliadas / gerações concluídas. Respostas não avaliadas não contam como acerto.
- Média e p95 usam apenas gerações concluídas. Falhas são contadas separadamente.
- Período seleciona gerações iniciadas nos últimos 7, 30 ou 90 dias. Gráfico diário usa UTC.
- Limites controlam a admissão de novas gerações, não a velocidade do modelo ou um agendamento de envio. Ao exceder um limite, a API retorna 429 e Retry-After; é necessário tentar novamente.
- As novas métricas abrangem `/api/draft`, inclusive testes neste painel. Outros fluxos de diálogo existentes não são contabilizados.
- A base histórica continua refletindo a última importação; “Recarregar painel” não sincroniza o Movidesk.
- “Atualizar chamados” inicia uma sincronização real com o Movidesk em segundo plano. O painel mantém a última carga completa até a nova terminar; ao concluir, atualiza os indicadores e as categorias sugeridas. O estado e a falha da última execução aparecem na faixa sob o título. O agendamento usa o horário de Brasília e roda apenas enquanto este servidor estiver ligado. A agenda fica em `scripts/output/ticket_sync_schedule.json` e vem desativada por padrão.
- Essa sincronização atualiza os dados operacionais e a classificação do painel, mas não promove chamados automaticamente à base RAG (`knowledge_nodes_created: 0`). Uma fonte para resposta ao cliente deve passar por curadoria antes da indexação de embeddings. Após alterar texto aprovado, reindexar o embedding da fonte para que a busca vetorial reflita a versão vigente.
- A sincronização da vazão percorre `/tickets` e `/tickets/past` para capturar a fila atual e reconcilia em lotes os registros históricos ainda marcados como abertos fora da fotografia. Registra resolução e fechamento sem substituir a conversa original por dados resumidos da listagem. O histórico anterior a essa correção permanece provisório até a auditoria.
- A curva de backlog considera apenas sincronizações com `coverage_version >= 2` (ambas as rotas), para que a correção da coleta não seja confundida com aumento real de demanda.
- Módulos e assuntos iniciais são heurísticos; sua classificação é uma fila de curadoria, não prova de validade. A contagem de "sem procedimento" considera o campo estruturado de solução e pode incluir artigos cujo conteúdo completo só existe no texto original.
- Revisões pendentes não alteram o texto utilizado pelo agente. Aprovar aplica os campos revisados; excluir impede a fonte de entrar na busca e na geração. A busca lexical considera o texto aprovado, enquanto embeddings importados permanecem como foram gerados até reindexação.

## Persistência e validação

Configurações: `scripts/output/agent_admin_state.json`.
Gerações, avaliações e auditoria: `scripts/output/agent_operations.sqlite3`.
Curadoria e histórico de revisões: tabelas PostgreSQL `knowledge_curation` e `knowledge_curation_history`. As tabelas importadas permanecem intactas.
Esses arquivos precisam ser preservados para manter o histórico.

O painel é local, sem contas individuais. A auditoria identifica a operação local, não um usuário autenticado. Uma implantação compartilhada exige autenticação e definição de acesso.

## Interface móvel e instalação futura

O painel tem temas claro/escuro, navegação inferior em telas pequenas, filtros recolhíveis e cartões de chamados para leitura no celular. O manifesto web em `scripts/static/cortex.webmanifest` prepara a apresentação em modo app, sem armazenar respostas ou dados de chamados para uso offline.

O endereço `127.0.0.1` e a proteção local da API permitem acesso somente nesta máquina. Para o gestor instalar e usar o Cortex no próprio celular, ainda será necessário publicar o serviço em um endereço HTTPS acessível ao aparelho, com autenticação e autorização por usuário antes de liberar acesso fora do computador local.

Testes isolados, sem chamada ao provedor e sem registros fictícios no histórico real:

```powershell
cd scripts
python -m unittest test_agent_operations -v
python -m unittest test_knowledge_curation -v
```
