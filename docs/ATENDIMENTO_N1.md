# Atendimento N1 com evidências

O worker do Movidesk lê anexos antes de decidir a próxima etapa.

- Pedido não caiu/chegou/integrou, sem erro específico: orienta a busca em Ocorrências com três prints, antes da consulta de históricos.
- Erro já informado em texto ou extraído do print: pula a coleta inicial e consulta procedimentos aplicáveis.
- Triagem já enviada ou cliente já pesquisou sem retorno: prossegue com investigação contextual, sem repetir automaticamente o roteiro completo.
- Procedimento revisado: preserva passos, imagens e referências mapeadas.
- Demais procedimentos: o modelo recebe instrução de passos numerados, ajuste condicional e validação; sua qualidade depende das fontes recuperadas.
- Referências internas: até três casos resolvidos/fechados, com solução documentada, classificação GOLD/SILVER, sem bloqueio e correspondentes ao problema. A busca inclui outras empresas do mesmo tenant. Referências de outras empresas não são apresentadas como histórico do cliente.
- Menos de três referências: informa a lacuna na nota interna. Sem fonte aplicável, mantém investigação e registra diagnóstico pendente.

Os prints incorporados são material revisado. Não há geração automática de prints para telas sem material. O fluxo não garante cobertura de todo N1; casos sem evidência suficiente continuam exigindo análise humana. Não publica resposta pública automaticamente.

Validação: `python -m unittest test_support_triage test_mapped_replies test_movidesk_note_worker test_technician_workbench test_conversation_context`.
