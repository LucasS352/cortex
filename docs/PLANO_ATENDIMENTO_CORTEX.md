# Cortex — revisão técnica e plano de evolução

Data: 07/10/2026. Escopo: código e banco locais, playground de atendimento e fluxos compartilhados. Este documento diferencia implementações verificadas de trabalho futuro. Não constitui liberação de respostas automáticas no Movidesk.

## Parecer sobre o relatório do Antigravity

**A recuperação melhorou, mas o relato de sucesso não comprova a correção do atendimento completo.**

| Afirmação | Conferência local |
| --- | --- |
| Exclusão de palavras conversacionais | Confirmada. A pergunta original resulta em `relatorio`, `vendas`, `produto`. |
| Índice `idx_kn_title_fts` | Confirmado no PostgreSQL. É um índice GIN de busca textual em português, não um índice de embeddings semânticos. Incluída migração reproduzível em `infra/004_knowledge_title_fts.sql`. |
| Timeout de 15 s | Confirmado na conexão administrativa. Aumentar o prazo não elimina a causa de consultas caras. |
| Busca em menos de 0,05 s | Não confirmado para a busca completa. Medições realizadas nesta revisão: aproximadamente 5,7–9 s. A etapa de texto completo consumiu 4,3–6,8 s; títulos consumiram cerca de 0,14–0,47 s por consulta. São amostras locais, não percentis de carga. |
| Resposta completa funcionando | A chamada ao vivo retornou resposta em aproximadamente 15,2 s, dos quais 9,2 s foram do modelo. O modelo respondeu; o resultado apresentou acentuação corrompida. |
| Artigo correto | Nó 1422, artigo oficial 310538, “Relatório Agrupado por Produto”. Os passos principais estão no texto original. |
| Demais referências | Nó 203 trata de markup. Nó 20252 corresponde ao chamado 92155. O chamado 109931 corresponde ao nó 12499 e trata de vendas por vendedor. Não são identificadores intercambiáveis. |
| Documentos suficientes para todo procedimento | Não. Nós 542 e 601 têm solução textual vazia; existência de artigo/vídeo no banco não equivale a procedimento extraído e revisado. |
| Caminho `T:\master\Relatorio` | Existe no artigo original. Não comprova que a unidade T existe no cliente, nem que executar o relatório salvará automaticamente o arquivo ali. |

Também apareceu entre os primeiros resultados um manual de erro de produto pai/filho. Ele não deve fornecer passos para uma dúvida sobre relatório de vendas. O modelo acrescentou uma localização presumida do asterisco (“coluna mais à esquerda”) não explicitada no texto consultado. Conferir imagens e não preencher detalhes por suposição.

A execução antiga que teria abortado em 9.758 ms não foi reproduzida retroativamente; a consulta OR abrangente e o custo atual do texto completo sustentam o risco de timeout, mas não permitem atestar cada número daquele relato.

## Alterações implementadas nesta revisão

- Contexto por problema dentro do chamado: mensagens, assunto, evidências e estado separados. O assunto original não contamina automaticamente os problemas seguintes.
- Novo problema fiscal sem evidência pede print ou texto completo e a operação envolvida. “Erro”, isoladamente, não é um log técnico.
- Mudança explícita, mudança de domínio, retomada identificável e dúvida ambígua têm transições distintas. Agradecimento e resolução permanecem anteriores à busca.
- Busca e prompt recebem apenas o histórico do problema ativo. A linha do tempo mantém os anteriores.
- Painel acompanha o assunto/especialista atual e diferencia resultados encontrados da fonte efetivamente usada na resposta.
- Fontes insuficientes não voltam a ser utilizáveis por terem texto longo. A referência principal não é trocada silenciosamente por outra mais fraca. A geração usa um procedimento aplicável por vez.
- Transporte do modelo via `curl.exe` usa UTF-8 explícito e envia o corpo por stdin, evitando acentos corrompidos e limites de linha de comando para prompts longos.
- Orientação do modelo restringe inferências de posições de botões e caminhos específicos de ambiente. Isso é uma restrição de geração, não prova formal de ausência de alucinações.
- Playground aceita imagem PNG/JPEG/WebP via clipe ou colagem. OCR local insere texto no editor para conferência antes do envio. Não diagnostica pela imagem nem inventa telas a partir de palavras detectadas. Em ambiente sem OCR, informa a indisponibilidade e pede o erro escrito.
- Backup `.env.bak*` passou a ser ignorado pelo Git; não houve leitura ou divulgação de credenciais.

## Fluxo de atendimento desejado

1. **Entender a mensagem atual:** é continuação, resposta à pergunta anterior, novo problema, retomada, agradecimento, sucesso ou pedido de humano?
2. **Selecionar o problema ativo:** identificar o objetivo do cliente, o módulo e o ambiente; preservar os demais problemas com seus próprios estados.
3. **Verificar o que falta:** aproveitar o erro já enviado; pedir uma ou duas informações que realmente discriminem a causa. Não repetir pergunta respondida.
4. **Buscar evidência aplicável:** erro, entidade, operação, integrador e versão quando informados. Distinguir documento oficial, caso histórico e investigação inconclusa.
5. **Analisar antes de orientar:** separar fatos, hipóteses e contradições; não considerar semelhança textual como certeza diagnóstica.
6. **Orientar um procedimento sustentado:** passos com fonte, pré-condições, responsável por executar e forma de conferir o resultado. Não prometer caminhos, menus ou efeitos ausentes da documentação.
7. **Validar:** perguntar pelo resultado quando necessário; interpretar “vou testar” como pendência, “deu certo” como resolução daquele problema e “outro erro” como nova investigação.
8. **Encaminhar quando necessário:** entregar ao técnico resumo útil, evidências, verificações realizadas e lacuna objetiva. Não dizer que uma transferência aconteceu sem o sistema efetivamente realizá-la.

## Plano de implementação do projeto

| Ordem | Entrega | Situação e aceite |
| --- | --- | --- |
| 1 | Estado por problema | Base implementada no playground. Próximo: persistir no servidor, suportar mais de 40 mensagens e permitir selecionar/retomar um problema na interface. Aceite: nenhum vazamento de evidência entre assuntos nos testes de conversa. |
| 2 | Planejador de diálogo estruturado | Pendente. Modelo propõe JSON com intenção, problema ativo, fatos e mensagens de origem, lacunas, próxima ação e justificativa. Validar schema e referências no servidor. Regras atuais ficam como contingência; não continuar acrescentando exceções para cada frase. |
| 3 | Perguntas diagnósticas por domínio | Coleta fiscal inicial implementada. Pendente: fluxos revisados de fiscal, integração, estoque, financeiro, relatórios e infraestrutura. Aceite: pergunta necessária e específica, sem repetir informação disponível. Dúvidas de uso não exigem um “erro” para serem respondidas. |
| 4 | Imagens e documentos | OCR local de prints implementado. Pendente: armazenamento por mensagem/problema, evidência visual com proveniência, leitura multimodal e implementação portável para Docker/Linux. OCR pode errar; não afirmar que inferências são texto observado. |
| 5 | Recuperação e cobertura | Reconciliação lexical elegível já implementada. Pendente: reduzir custo do texto completo, evitar transferir embeddings desnecessários, filtrar por intenção/métrica e melhorar consultas por significado. Preservar originais, revisões e exclusões. |
| 6 | Qualidade do acervo | Pendente por lote: transcrever vídeos, completar passos que estão apenas em imagens, identificar versões/ambiente e revisar procedimentos. Não promover automaticamente todos os chamados a solução. |
| 7 | Resposta e verificação | Restrições restauradas e mistura de fontes reduzida. Pendente: resposta estruturada com passos ligados às fontes; validação das afirmações de menus, parâmetros, caminhos e efeitos antes da publicação. Rejeitar/reformular resposta sem sustentação. |
| 8 | Unificação dos fluxos | Pendente: playground, chat, rascunho e síntese devem usar a mesma orquestração. Ainda há lógica legada; correções isoladas numa rota não provam conformidade das demais. |
| 9 | Disponibilidade e observabilidade | Transporte corrigido. Pendente: métricas por busca/modelo/OCR, tratamento separado de timeout e ausência real de fonte, limite de fila e recuperação do provedor. Exibir configuração e saúde recente como informações distintas. |
| 10 | Avaliação e piloto | Regressões automatizadas disponíveis; pendente corpus revisado por técnicos e avaliação ponta a ponta. Manter modo de sugestão/revisão humana até cumprir critérios definidos. |

### Contrato proposto para o planejador

`intent`, `issue_id`, `topic_relation`, `facts[{value,message_id}]`, `missing_fields`, `next_action`, `source_ids`, `procedure_steps[{text,source_id}]`, `expected_validation`, `requires_human`, `reason`.

O servidor verifica tipos, IDs e transições permitidas. O modelo não pode inventar fonte, alterar status externo, executar comandos ou marcar sucesso sem confirmação do cliente. Conteúdo de chamados, OCR e documentos é dado de entrada, não instrução privilegiada.

### Critérios propostos para liberar o piloto

- Corpus de pelo menos 100 conversas revisadas, com perguntas de uso e erros, reservado em parte para avaliação sem ajuste prévio.
- Cobrir mudança de assunto, dois problemas simultâneos, retomada, problema recorrente, resultado parcial, sarcasmo/negação, print ilegível, fonte incompleta, erro conhecido e desconhecido, timeout e ausência de modelo.
- Meta inicial: fonte aplicável entre os cinco primeiros resultados em pelo menos 90% dos casos com resposta documentada. Isso mede recuperação, não acerto de diagnóstico.
- Nenhum procedimento sem sustentação no conjunto de liberação; qualquer caso encontrado impede a publicação daquele fluxo até correção. Não interpretar zero falhas no conjunto como garantia universal.
- Meta de latência a validar: busca p95 abaixo de 3 s e resposta completa p95 abaixo de 20 s sob carga definida. As medições atuais não comprovam essas metas.
- Revisão humana dos primeiros atendimentos; feedback com causa da rejeição/correção e vínculo à versão do código/base/modelo. Não aprender automaticamente com qualquer conversa do cliente.
- Teste de reabertura/retomada e histórico persistente antes de integração real. Nenhum envio externo é autorizado por este documento.

## Como testar agora

Conferência pela API após as correções: a pergunta de relatório usou somente o
nó 1422 na geração, retornou português com acentuação correta e omitiu a promessa
de gravação na unidade T. Tempo observado: 14,46 s, sendo 5,554 s de busca e 8,902 s
de modelo. A mudança integração resolvida → erro fiscal iniciou o problema 2 e
pediu evidência em 12 ms, sem fontes antigas. Um print sintético enviado ao endpoint
de imagens foi transcrito como “Rejeição 539: Duplicidade de NF-e”, com confirmação
solicitada antes de usar o texto. Essas verificações são locais e não substituem
testes visuais/interativos nem um piloto com clientes.

1. Reproduzir integração com log, confirmar resolução, informar “agora estou com outro erro de nota fiscal”. Esperado: coleta fiscal, sem referência à descrição curta ou à Loja Integrada.
2. Enviar a rejeição escrita. Esperado: investigar somente o problema fiscal.
3. Usar o clipe ou colar um print; conferir o texto extraído antes de enviar. Esperado: transcrição literal ou aviso claro de falha.
4. Perguntar “Como faço para ver um relatório de quantas vendas eu tive por produto, tem isso no Master?”. Esperado: referência principal 1422/310538, português correto, sem passos de markup/produto pai e sem promessa de gravação na unidade T do cliente.
5. Agradecer sem testar, confirmar sucesso, informar falha persistente e retomar a integração anterior. Conferir estados e fontes em cada resposta.

Os testes locais verificam mecanismos e regressões. O projeto completo ainda depende das entregas pendentes acima, sobretudo planejamento semântico, memória persistente, verificação da resposta final e avaliação com atendimentos reais revisados.
