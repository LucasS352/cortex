# Escopo obrigatório e autorização em lote

O agente atende chamados públicos elegíveis, ainda abertos, nos status operacionais (incluindo Novo, Triagem e Em atendimento), criados estritamente depois da liberação e da autorização da empresa. Status ou equipes relacionados a **Desenvolvimento** e **Comercial** são bloqueados. Outros filtros (solicitante, empresa, eventos, pausas individuais e modos do servidor) continuam obrigatórios. Listas antigas de status permitidos deixam de restringir essa política.

O servidor registra `automation_released_at` ao liberar a governança, ao habilitar a publicação automática ou ao confirmar uma instalação antiga sem data. Registra `company_authorized_at` ao adicionar uma empresa. Vale a data mais recente das duas. Salvar novamente sem mudar a liberação não altera a data; pausar e liberar globalmente cria uma nova liberação. Reativar um ticket individual não muda essas datas.

Chamados antigos não voltam a ser elegíveis ao receber uma mensagem ou mudar de status. Data ausente ou inválida bloqueia o processamento. O `createdDate` do Movidesk sem sufixo é interpretado como UTC, conforme a [documentação oficial](https://atendimento.movidesk.com/kb/pt-br/article/256/movidesk-ticket-api). Para configurações antigas, as datas de autorização e liberação são recuperadas das transições reais registradas na auditoria. Sem esse histórico, a confirmação administrativa continua necessária; nenhuma data é inventada. Os timestamps são internos: não podem ser definidos pelo cliente da API.

`automation_scope.evaluate` é executada antes de gerar a resposta e novamente na seção de publicação do worker, usando o estado atualizado. A regra vale também com regras opcionais desligadas e no acionamento manual do processamento. As prévias administrativas mostram os mesmos impedimentos.

## Autorizar todas

O botão consulta todas as empresas externas ativas do cadastro local atual, sem depender de busca ou paginação. A confirmação mostra quantas serão adicionadas. Um token do snapshot impede confirmar uma lista que mudou durante a operação. A ação é administrativa, auditada e preserva os demais controles. Empresas futuras precisam de nova autorização; não há autorização universal permanente.

Endpoints: `GET` e `POST /api/admin/pilot/companies/authorize-all`. A operação não agenda notas nem altera a pausa, os filtros de solicitante ou a permissão de publicar. Se a automação já estiver ativa, as autorizações adicionadas passam a valer para eventos que satisfaçam todas as condições acima.

## Atualização

Atualizar aplicação, webhook e note-worker para a mesma imagem. Depois, conferir as empresas e solicitantes autorizados e salvar as configurações para confirmar a data de liberação mostrada em Agente e políticas. A atualização por si só não libera chamados antigos.

Regressões incluem o chamado Novo seguido pelo recibo automático do Movidesk, permissões de outros status operacionais, bloqueio de Desenvolvimento/Comercial, recuperação auditada das datas e continuidade do bloqueio dos chamados anteriores à liberação. O painel de processamento consulta o estado automaticamente a cada 15 segundos enquanto estiver visível. Os testes usam dados isolados e não publicam notas reais.
