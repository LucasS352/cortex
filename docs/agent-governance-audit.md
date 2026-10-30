# Central de agentes — auditoria e homologação

Branch: `codex/agent-governance`. Produção não foi alterada.

## Diagnóstico anterior à mudança

O chamado 132574, aberto pelo portal do cliente, não apareceu no log do gatilho `DEV - Cortex` no momento da abertura. A configuração desse gatilho exigia uma ação pública adicionada pelo cliente, enquanto o gatilho de triagem disparava na abertura. Foi criado um gatilho separado de abertura no Movidesk; o chamado 132575 confirmou o recebimento automático e a publicação de nota privada na versão já em produção. Esse teste valida o gatilho remoto e o fluxo atual, mas não homologa as mudanças desta branch. As capturas anteriores mostram 495 entregas e publicações concluídas, sem comprovar a causa de cada chamado ausente.

Hipóteses verificáveis no código: último autor não identificado como cliente, filtro de e-mail incompatível com o autor da última ação, empresa ambígua/ausente, chamado finalizado, automático desligado, teto do servidor limitado a prévia, worker sem atividade, mensagem já processada ou falha na atualização operacional/geração/publicação. Autorizar uma empresa isoladamente nunca basta. Não há evidência para escolher uma dessas hipóteses como causa do teste.

Lacunas confirmadas: não havia seleção de eventos/status; o worker serial não usava os limites de geração do painel; o caminho de sincronização sem geração escapava do tratamento de retentativas. Essas lacunas foram tratadas incrementalmente.

## Fluxo mapeado

1. `movidesk_webhook.receive_ticket`: valida segredo, tamanho e ID, persiste payload em SQLite e responde rapidamente. Falha de persistência retorna 503.
2. `enqueue_payload`: SHA-256 de payload canônico elimina reenvio idêntico.
3. `claim`: fila durável, lease de 600 segundos, máximo de tentativas e serialização global.
4. `effective_settings`: combina teto do ambiente com empresas, solicitantes, pausa e publicação do painel. Estado ausente/inválido falha fechado.
5. `process`: lê API atual, verifica consistência da ação anunciada e atualiza fotografia e base operacional PostgreSQL.
6. `eligible`: exige empresa externa inequívoca autorizada, ticket público não finalizado, última ação útil do cliente e solicitante permitido. Confirmações automáticas conhecidas e ações do bot são descartadas.
7. Regras novas: eventos/status globais e exceções restritivas por empresa. Compatibilidade preservada até ativação explícita.
8. `generate_note`: motor existente de busca, base canônica, evidências, anexos e guardrails. Admission control compartilhado com outras gerações.
9. Evidência insuficiente conserva minuta em revisão; não publica procedimento incerto.
10. Outbox e marcador por mensagem/ocorrência. Releitura de contexto, status e autorização antes do PATCH. Escrita exclusivamente `actions.type=1`.
11. Entrega ambígua exige reconciliação do marcador; não repete PATCH cegamente. Resultado, etapas e duração desde recebimento são registrados.

## Contrato de eventos e limites

O receptor existente recebe snapshots, não um tipo de evento normalizado garantido. A UI oferece apenas mensagem nova de cliente e transição de status observada. O evento de abertura é configurado em gatilho separado no Movidesk, como foi validado no chamado 132575; uma primeira fotografia não é rotulada artificialmente como criação. A transição de status compara a fotografia anterior à atual e exige duas observações. Mensagem nova compara identidade/conteúdo de ações. Atribuição isolada não é exibida como evento implementado.

Status selecionáveis provêm do mapeamento real `refined_tickets`, não de códigos inventados. Cadastros ainda não sincronizados não aparecem. Status finais continuam bloqueados.

O worker permanece serial (limite efetivo de simultaneidade 1), mesmo quando o teto configurado é maior. Admissão de geração respeita intervalo, janela de 60 segundos e concorrência compartilhados. O espaçamento entre chamadas da API Movidesk mantém o piso do servidor; não é o mesmo que intervalo entre gerações.

## Homologação e limitações explícitas

Testes isolados usam API falsa, banco temporário e gerador controlado, cobrindo criação, status inelegível, transição, duplicidade, empresa não autorizada/desabilitada, fila sem worker, falha de geração/publicação, contexto insuficiente e prévia sem escrita. Preservam os testes existentes de entrega incerta, mensagens novas e anexos.

Monitoramento lista as últimas 100 entregas locais; a consulta por chamado busca seu próprio recorte. Dados de empresa/status exibidos nos logs antigos vêm da última fotografia disponível, não de uma fotografia histórica por execução. Etapas antigas não registradas são apresentadas como “sem registro”, nunca como sucesso. O indicador de worker informa heartbeat recente, não uma garantia de disponibilidade futura. A elegibilidade é uma inspeção da última fotografia, não uma promessa de execução.

Antes de produção: homologar em ambiente de teste com o número do chamado original, conferir entregas do gatilho remoto, payload de criação, status sincronizados, volume compartilhado entre receptor/app/worker, assinatura do bot e confirmação de uma nota privada. Não ampliar empresas autorizadas nem habilitar regras globalmente sem aprovação. Não foi realizado deploy em produção.
