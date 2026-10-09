# Avaliação interna do agente

O painel local (`http://127.0.0.1:8001/admin`) oferece, em **Desempenho > Testar resposta**, dois fluxos:

1. **Amostra de avaliação:** “Montar amostra” seleciona chamados reais com descrição e histórico técnico. A solução histórica é apenas referência. Um revisor marca os assuntos corretos segundo a taxonomia de `knowledge_curation.py` e descreve os critérios de uma boa resposta. A taxa de acerto do assunto principal usa somente casos revisados; casos sem rótulo não entram no denominador. A seleção e os rótulos ficam na base SQLite local de operações.
2. **Teste de resposta:** o número do chamado é opcional. Ao informar um número, “Carregar da base local” preenche o assunto e o contexto. A geração valida o vínculo, guarda `ticket_id`, módulos sugeridos e IDs das fontes no histórico de execução, e mostra o Raio-X no painel. Uma simulação sem número fica explicitamente sem vínculo.

O roteador sugere até três assuntos e permite cruzamento entre módulos. A sugestão não é diagnóstico nem autorização de resposta. Fontes prováveis continuam exigindo revisão humana. Nenhum destes fluxos publica notas no Movidesk ou responde ao cliente.

## Próximas decisões antes de um piloto

- Revisar casos rotulados, respostas esperadas e situações em que o agente deve pedir mais dados.
- Medir acerto de assunto, cobertura de assuntos secundários, aprovação da resposta e correções por tipo de fonte; guardar versões do roteador e da política para comparação.
- Definir IDs imutáveis das organizações de teste, autenticação do painel fora do localhost, chave de pausa, deduplicação e auditoria de escrita.
- Validar em ambiente controlado o contrato PATCH para inserir ação interna em chamado existente; POST `/tickets` cria chamado novo.
