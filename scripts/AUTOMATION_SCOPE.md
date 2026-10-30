# Escopo obrigatório e autorização em lote

O agente atende somente chamados públicos elegíveis, ainda abertos, no status **Triagem**, criados estritamente depois da liberação e da autorização da empresa. Outros filtros (solicitante, empresa, regras, pausas individuais e modos do servidor) continuam obrigatórios.

O servidor registra `automation_released_at` ao liberar a governança, ao habilitar a publicação automática ou ao confirmar uma instalação antiga sem data. Registra `company_authorized_at` ao adicionar uma empresa. Vale a data mais recente das duas. Salvar novamente sem mudar a liberação não altera a data; pausar e liberar globalmente cria uma nova liberação. Reativar um ticket individual não muda essas datas.

Chamados antigos não voltam a ser elegíveis ao receber uma mensagem ou entrar em Triagem. Data ausente, sem fuso ou inválida bloqueia o processamento. Instalações antigas sem datas permanecem bloqueadas até a confirmação administrativa. Os timestamps são internos: não podem ser definidos pelo cliente da API.

`automation_scope.evaluate` é executada antes de gerar a resposta e novamente na seção de publicação do worker, usando o estado atualizado. A regra vale também com regras opcionais desligadas e no acionamento manual do processamento. As prévias administrativas mostram os mesmos impedimentos.

## Autorizar todas

O botão consulta todas as empresas externas ativas do cadastro local atual, sem depender de busca ou paginação. A confirmação mostra quantas serão adicionadas. Um token do snapshot impede confirmar uma lista que mudou durante a operação. A ação é administrativa, auditada e preserva os demais controles. Empresas futuras precisam de nova autorização; não há autorização universal permanente.

Endpoints: `GET` e `POST /api/admin/pilot/companies/authorize-all`. A operação não agenda notas nem altera a pausa, os filtros de solicitante ou a permissão de publicar. Se a automação já estiver ativa, as autorizações adicionadas passam a valer para eventos que satisfaçam todas as condições acima.

## Atualização

Atualizar aplicação, webhook e note-worker para a mesma imagem. Depois, conferir as empresas e solicitantes autorizados e salvar as configurações para confirmar a data de liberação mostrada em Agente e políticas. A atualização por si só não libera chamados antigos.

Verificações: 72 testes de escopo, autorização em lote, piloto, diagnóstico, regras e worker passaram. Também foi conferida a interface no navegador, incluindo confirmação e cancelamento da autorização em lote. Os testes usam dados isolados e não publicam notas reais.
