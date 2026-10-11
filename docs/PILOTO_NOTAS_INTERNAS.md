# Piloto de notas internas Movidesk — 09/10/2026

## Fluxo implementado

Movidesk envia POST → receptor autentica e persiste → worker consulta o ticket individual atualizado → identifica a última mensagem pública do cliente → lê contexto e até dois prints PNG/JPEG/WebP anexados à ação → pesquisa primeiro os chamados da mesma empresa → usa um histórico encerrado e bem documentado quando aplicável, ou cai para a base geral → Gemini prepara a resposta → worker relê o chamado → adiciona uma ação **interna (`type: 1`) no mesmo ticket**.

Chamados semelhantes ainda abertos aparecem somente como alerta de recorrência ou tratamento em andamento; eles não viram procedimento comprovado. A nota termina com **Tickets semelhantes**, status e links dos casos aplicáveis da mesma empresa. O próprio chamado em análise é sempre excluído dessa lista.

A nota é uma resposta sugerida ao cliente, com identificação do Cortex e fontes para revisão do técnico. O worker não envia ação pública, e não muda status, responsável, clientes ou ações anteriores. Cada mensagem do cliente tem uma chave própria; atualização de metadados, reenvio de webhook e a própria nota não geram outra resposta para a mesma mensagem.

O receptor permanece rápido: nenhuma consulta à API ou chamada ao Gemini acontece durante o POST. A fila, cópias dos tickets e notas ficam no volume `cortex_output`, em SQLite. O worker consulta a API ao vivo; a fotografia geral dos relatórios continua sendo atualizada pela sincronização já existente. Não se trata de um espelho instantâneo de todos os relatórios.

## Ambiente separado

`.env` continua sendo o ambiente local. `.env.server` é exclusivo do servidor, ignorado pelo Git e excluído da imagem Docker. O modelo sem segredos está em `.env.server.example`.

Para criar novamente em outra máquina:

```powershell
python scripts/prepare_server_env.py --from-local
```

O comando não sobrescreve arquivo existente. Reutiliza as chaves Movidesk/Gemini e o login administrativo local quando disponíveis. Gera novas senhas de PostgreSQL, sessão e webhook; não imprime segredos.

Preencher no arquivo, fora do chat:

- `CORTEX_PUBLIC_URL`: URL de acesso, como `http://179.236.68.24` ou `https://cortex.exemplo.com.br`, sem caminho.
- `CORTEX_ADMIN_PASSWORD_HASH`: gerar com `python scripts/create_admin_hash.py` se estiver vazio. Manter aspas simples em torno do hash para preservar `$`.
- `MOVIDESK_BOT_PERSON_ID`: ID real de um agente autorizado a incluir ações internas; recomendado agente exclusivo do Cortex.
- `MOVIDESK_TICKET_UI_BASE`: endereço da tela de edição de tickets usado nos links de chamados semelhantes.
- `CORTEX_RUNTIME_PILOT_CONTROL=true`: torna o painel a fonte efetiva da lista de empresas do piloto. Este é o padrão dos arquivos Compose.
- `CORTEX_PILOT_TICKET_IDS` e `CORTEX_PILOT_ORGANIZATION_IDS`: mantidos somente para diagnóstico e execução direta do worker fora do controle do painel. Com o controle em tempo de execução ativo, esses valores não contornam a seleção feita na interface.
- `CORTEX_IMAGE`: imagem publicada, como `ghcr.io/lucass352/cortex:<commit>`.

`CORTEX_AUTONOTE_MODE` controla este worker:

| Valor | Comportamento |
| --- | --- |
| `disabled` | Recebe eventos, mas não consome a fila. |
| `dry_run` | Consulta, gera e guarda prévia no Cortex, sem PATCH. |
| `internal_note` | Publica a sugestão como ação interna e confirma o marcador no Movidesk. |

O `.env.server` define o **teto** permitido. O painel aplica as autorizações mais restritivas em tempo de execução. Para publicar, as quatro condições abaixo precisam ser verdadeiras ao mesmo tempo:

1. `CORTEX_AUTONOTE_MODE=internal_note` no servidor;
2. piloto ativo no painel;
3. pelo menos uma empresa selecionada no painel;
4. opção **Publicar nota interna (privada)** marcada.

Se o arquivo de estado estiver ausente, ilegível ou inconsistente, o worker rebaixa a execução para `dry_run`. A whitelist de contas do Playground serve apenas para simulações e nunca autoriza escrita no Movidesk. Alterar o teto do `.env.server` exige recriar os containers; selecionar empresas, pausar o piloto ou desmarcar a publicação privada tem efeito no worker sem redeploy.

## Subida no Docker da Alternativa

Restaurar o banco e os volumes da base atual antes do piloto; a imagem contém código, não os 30 mil chamados indexados. Ver também `DEPLOY_PORTAINER.md`.

Em um banco restaurado/existente, aplique uma vez a migração que cria e preenche o índice empresa → chamados:

```bash
docker compose --env-file .env.server exec -T postgres psql -U deskcortex -d deskcortex < infra/005_company_workbench.sql
```

Em volume novo ela é executada automaticamente pelo PostgreSQL na inicialização.

```bash
docker compose --env-file .env.server config --quiet
docker compose --env-file .env.server pull
docker compose --env-file .env.server up -d --wait
docker compose --env-file .env.server ps
docker compose --env-file .env.server logs --tail=80 note-worker
```

Não executar `docker compose config` sem `--quiet` em um terminal gravado: a saída completa contém variáveis expandidas. Em Portainer, carregar os valores de `.env.server` nas variáveis da Stack e usar `docker-compose.yml` com a imagem publicada.

Publicar apenas 80/443 do proxy. Caddy encaminha `/webhooks/movidesk/tickets/*` ao receptor e o painel ao serviço autenticado. O access log do receptor está desativado para não registrar o segredo da URL; configurar o proxy externo da empresa da mesma forma.

## Gatilho Movidesk

URL a cadastrar:

```text
https://SEU_DOMINIO/webhooks/movidesk/tickets/SEU_MOVIDESK_WEBHOOK_SECRET
```

Configurar gatilhos para abertura/atualização dos chamados do piloto, preferencialmente ação pública recebida do cliente. Não incluir o agente Cortex como originador. O worker também filtra pelo chamado consultado na API, independentemente do payload recebido.

Só há geração quando a última ação relevante for pública e do cliente. Se um técnico respondeu depois, o ticket foi encerrado/excluído ou a empresa não está autorizada, registra o motivo e não gera nota. Chamados abertos por um técnico em nome do cliente sem uma ação identificada como cliente ficam fora deste primeiro fluxo.

## Validação do piloto

1. Subir inicialmente com `dry_run`, selecionar somente a empresa de teste no painel e enviar uma mensagem em um chamado dela.
2. Em **Agente e políticas → Webhook e notas internas**, confirmar recebimento, worker ativo e abrir a prévia. Conferir assunto, contexto, fontes e ausência de dados de outros clientes.
3. Preencher o agente, trocar o teto para `internal_note` e recriar `app`, `webhook` e `note-worker`. No painel, manter somente a empresa de teste selecionada, ativar o piloto e marcar a publicação privada. Enviar uma nova mensagem no chamado de teste. Eventos de prévia já concluídos não são republicados em massa.
4. Conferir no Movidesk que a ação está interna, que as ações antigas continuam intactas e que não houve envio ao cliente. Reenviar o mesmo webhook e conferir que não surge outra nota.
5. Testar resposta humana durante a geração, erro do Gemini, print legível, print ilegível e nova pergunta depois de resolução.

O teste automatizado usa API simulada e cobre contrato PATCH, duplicação, autor do bot, mudança durante a geração, demora de consistência da API, timeout após gravação e falha de modelo. A confirmação real da API da conta é uma etapa necessária antes de expandir as empresas.

## Falhas e limites

- A API pode demorar a refletir uma ação. O worker espera antes de analisar um histórico anterior ao evento; são até cinco tentativas espaçadas em pelo menos 60 segundos.
- Se PATCH tiver resultado incerto, o worker busca o marcador no chamado e **não repete o PATCH automaticamente**. Se não conseguir confirmar, o estado fica `needs_review`. Isso evita duplicar notas após timeout ou reinício.
- Em caso de erro do Gemini ou da base, nenhuma frase de indisponibilidade é publicada como resposta ao cliente; o evento fica para nova tentativa/revisão.
- O intervalo padrão é sete segundos entre chamadas deste worker. Outras integrações e a carga em lote também consomem a quota da conta: evitar sincronização em massa simultânea durante o piloto. HTTP 429 fica registrado e é reagendado.
- O worker processa uma entrega por vez. O volume SQLite deve estar no mesmo host Docker, com uma réplica por serviço; não usar NFS/múltiplos hosts.
- Há releitura antes de gravar, mas a API não oferece aqui uma operação condicional atômica entre leitura e PATCH; uma ação humana no intervalo final ainda pode ocorrer.
- Prints são evidências, não causas comprovadas. Formatos não suportados, imagens excessivas/ilegíveis e mais de dois anexos ficam identificados para leitura humana. Texto da conversa enviado ao Gemini continua sujeito à política de dados da empresa.
- As notas identificadas por `CORTEX-AI-NOTE:` são ignoradas pela refinaria v2: uma sugestão não vira automaticamente solução confirmada da base.

Referências: [webhooks](https://atendimento.movidesk.com/kb/en/article/55928/webhook) e [API de tickets/ações](https://zenvia.movidesk.com/kb/en/article/510136/api-de-tickets?menuId=48970-132324-510136).
