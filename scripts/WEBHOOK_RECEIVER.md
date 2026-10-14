# Receptor de webhooks Movidesk

## Estado atual

**Atualização 14/10/2026:** o receptor continua somente recebendo e enfileirando, para responder rapidamente ao Movidesk. O serviço separado `movidesk_note_worker.py` consome a fila, consulta o chamado completo e atualiza apenas esse ticket nas tabelas operacionais antes de qualquer análise. Essa sincronização incremental continua mesmo quando a geração automática de notas está pausada. A carga completa fica agendada para 00:00 no horário de Brasília e atua como reconciliação diária. O worker também pode publicar uma sugestão como ação interna. Configuração, deduplicação, ensaio e limites estão em [Piloto de notas internas](../docs/PILOTO_NOTAS_INTERNAS.md).

O serviço `movidesk_webhook:app` escuta somente em `127.0.0.1:8003` e recebe `POST /webhooks/movidesk/tickets/{secret}`. O segredo de URL é gerado e mantido em `.env` como `MOVIDESK_WEBHOOK_SECRET`. O serviço valida o segredo, limita o corpo a 2 MiB, exige JSON com `Id` numérico, deduplica pacotes iguais em `scripts/output/movidesk_webhooks.sqlite3` e responde `Ok` rapidamente. Não consulta a API, não gera resposta de IA e não escreve no Movidesk.

O painel local mostra contagem e os últimos IDs em **Agente e políticas → Recebimento Movidesk**. O receptor pode ser iniciado com:

```powershell
cd scripts
python -m uvicorn movidesk_webhook:app --host 127.0.0.1 --port 8003 --no-access-log
```

## Para cadastrar no Movidesk

É necessária uma **URL HTTPS pública e fixa** que encaminhe exclusivamente ao serviço na porta 8003, por exemplo `https://DOMINIO/webhooks/movidesk/tickets/SEGREDO`. `http://127.0.0.1:8003` não é acessível pelos servidores do Movidesk. O domínio, a publicação e o teste externo ainda dependem da infraestrutura escolhida. Não publique a porta 8001 do painel administrativo junto com o receptor. Redija os logs do proxy para não gravar o segredo contido no caminho.

No Movidesk, a configuração é em **Configurações → Automação → Gatilhos**; o perfil precisa permitir criar gatilhos. Configure o tipo Ticket, as condições e a ação de webhook com a URL. Comece pelo ticket e conta de teste. A página **Configurações → Automação → Webhook — log de execução** mostra tentativas e erros.

## Contrato e limites confirmados

- O webhook envia JSON via POST com `Id` do ticket, `Actions`, `WebhookEvents` e outros campos. Não há garantia documentada de ID de evento ou assinatura digital; o segredo de URL autentica a entrega até conhecermos outro mecanismo disponível no ambiente.
- A entrega é **ao menos uma vez**. A documentação informa até quatro tentativas adicionais, espaçadas em 30 segundos, e recomenda confirmação em até três segundos. Por isso o receptor apenas persiste e confirma; o processamento virá depois.
- O pacote pode não trazer a organização com a identificação necessária. O worker consulta o ticket individual pelo ID, verifica a empresa e a última mensagem e filtra pelos IDs configurados. Consultas de lista não incluem todas as coleções; a leitura também tenta `/tickets/past` quando necessário.
- O worker usa `PATCH /tickets?id=...`, enviando apenas uma nova ação `type: 1`, sem ID existente. Não envia as listas de clientes, status ou outros campos. O receptor continua sem escrita externa.
- Um gatilho amplo pode disparar novamente quando o bot alterar o ticket. O worker ignora as próprias ações e mantém uma chave por mensagem do cliente. Timeout de PATCH exige reconciliação do marcador, sem repetição cega da escrita.

Fontes oficiais: [Webhook Movidesk](https://atendimento.movidesk.com/kb/en/article/55928/webhook), [API de Tickets](https://zenvia.movidesk.com/kb/en/article/510136/api-de-tickets), [Gatilhos](https://atendimento.movidesk.com/kb/en/article/119441/gatilhos).
