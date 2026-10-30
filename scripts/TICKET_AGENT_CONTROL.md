# Pausa por chamado, desempenho da curadoria e observações internas

Implementação de 30/10/2026. A validação descrita aqui é local e com dados fictícios; nenhuma nota foi enviada ao Movidesk por estes testes.

## Controle individual

Na página `/treinamento/tecnico`, o técnico carrega o ticket com seu PIN existente. O botão **Pausar agente neste chamado** aparece no topo, junto ao perfil. Após pausar, a página mostra **Agente pausado neste chamado** e oferece **Reativar agente**.

`PUT /api/treinamento/tecnico/tickets/{id}/agente` recebe `{"paused":true}` ou `false`. Cada alteração valida novamente o PIN pelo mecanismo já usado pelo editor, a origem da requisição e o ticket local. Alterar o número ou o PIN na tela exige carregar o chamado novamente. Nenhum PIN é gravado na auditoria da pausa.

O estado e a auditoria ficam nas tabelas SQLite `ticket_agent_state` e `ticket_agent_audit`, no arquivo existente `scripts/output/movidesk_webhooks.sqlite3`. São registrados ticket, estado, nome do técnico e instante da alteração. O mesmo volume `cortex_output` é compartilhado pela aplicação e pelo worker no Compose existente.

O worker continua sincronizando o histórico. Consulta a pausa antes da geração e novamente na seção protegida imediatamente antes do PATCH. Uma trava de arquivo por ticket, compartilhada entre processos, ordena a confirmação da pausa e a publicação. Uma análise que termina após uma pausa confirmada não pode enviar sua nota. Um PATCH que já começou não pode ser desfeito: a confirmação da pausa espera a seção de envio terminar. A trava não mantém uma transação de escrita SQLite aberta durante a chamada de rede.

A reativação remove somente a suspensão do ticket. Não habilita empresas, modos de publicação ou regras globais e não reenvia eventos já descartados. Novos webhooks não removem a suspensão. Os demais tickets permanecem independentes.

**Atualização:** aplicação e note-worker precisam executar esta mesma revisão. Atualizar só a interface não protege contra um worker antigo. Os arquivos de estado e de trava precisam permanecer no volume compartilhado. Não há alteração de senha, empresa ou política global nesta entrega.

## Curadoria: causa e mudanças

O endpoint administrativo de respostas técnicas declarava `refresh=false`, mas chamava uma função cujo padrão era `refresh_live=True`. Ao abrir uma revisão, o frontend voltava a chamar esse endpoint mesmo quando o candidato já continha o histórico rico sanitizado. Isso colocava uma nova consulta ao Movidesk no caminho de abertura do editor.

Agora o endpoint respeita `refresh=false`; a atualização explícita continua disponível. O editor reutiliza o snapshot rico e a ordem dos prints vinculados ao candidato. Propostas técnicas continuam usando o snapshot submetido pelo técnico. Nenhuma mudança foi feita nas regras de aprovação, rejeição, publicação ou classificação.

Taxonomia, indicadores e lista são consultados em paralelo. O catálogo de imagens é carregado somente ao abrir os editores que precisam dele. A revisão consulta suas dependências em paralelo e mostra uma mensagem de carregamento/erro, evitando a página branca. A fila usa páginas de 50 registros, com filtros e ordenação estável; busca 51 resumos para identificar a próxima página. A API permanece compatível com chamadas sem paginação, limitadas a 100 resumos.

### Medições

Na base local sem candidatos pendentes: taxonomia aquecida 18–19 ms, indicadores 37–39 ms, consulta resumida da fila 18–24 ms, catálogo de imagens 4–7 ms. Essas leituras não indicaram gargalo de índice no conjunto local. A revisão real de produção não foi medida: não havia candidato equivalente local e a base de evidências local não satisfaz a restrição de organização única.

Comparação reproduzível no navegador contra a revisão `dfd22de`, com os mesmos dados fictícios, três execuções e latência controlada de 50 ms por leitura e 800 ms no endpoint que atualizava o ticket:

| Operação | Antes (mediana) | Depois (mediana) | Redução |
|---|---:|---:|---:|
| Abrir fila | 555 ms | 447 ms | 19% |
| Abrir revisão | 1.445 ms | 321 ms | 78% |

São medições do teste controlado, não estimativas garantidas para produção. As chamadas iniciais caíram de quatro para três em ambos os fluxos. Não foram adicionados índices sem evidência de benefício. O relatório de cada nova execução fica em `scripts/output/training-qa/timings.json`.

## Formatação

`render_note` conserva exatamente o HTML da sugestão ao cliente, imagens, fontes e tutoriais anteriores ao bloco interno. O rodapé recebe separador visual, espaçamento compacto e `font-size:9pt` em estilos inline nos parágrafos, listas, links e rótulos. Textos, marcadores, URLs e referências são preservados. Não há alteração na geração ou na decisão da IA.

As regras valem para novas notas renderizadas; notas já publicadas não são editadas. A validação de renderização foi feita no navegador, com equivalência de 9 pt a 12 px. Não foi criada nota em produção para testar a sanitização do Movidesk.

## Verificações

```powershell
cd scripts
python -m unittest test_ticket_agent_control test_curation_loading test_movidesk_note_worker test_movidesk_webhook test_training_workflow test_curation_publication -q
cd ..
python scripts/verify_training_ui.py --baseline-ref dfd22de
```

110 testes passaram, incluindo PIN incorreto, origem inválida, pausa durante a geração, ordenação entre pausa e envio, persistência em outro processo, auditoria, isolamento de tickets, reativação em modo sem publicação e repetição de webhooks. Os testes existentes de curadoria e publicação foram executados junto às regressões novas.

A conferência no navegador valida pausa/reativação, recarregamento, mudança de ticket, paginação, abertura da revisão, ausência de erros JavaScript e fonte do rodapé. Prévia e capturas ficam em `scripts/output/training-qa/`. O teste automatizado usa exemplos fictícios e nunca acessa o Movidesk.
