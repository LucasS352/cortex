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

## Execução do plano de suporte confiável — 21/10/2026

Esta seção registra a implementação baseada no plano e no prompt de execução
fornecidos nesta data. Não autoriza implantação nem publicação de procedimentos.

### Entregas de código

| Fase | Implementação e limite |
| --- | --- |
| Decisão | `support_policy.py` centraliza evidências, estados `PROCEDIMENTO_CONFIRMADO`, `INVESTIGAR`, `ENCAMINHAR`, força da evidência, procedimento/versionamento e pendências. Uma tentativa malsucedida exige revisão do próximo passo. Notas, assistente do técnico, rascunhos, síntese e simulador aplicam o contrato. |
| Fonte principal | A busca prioriza versões canônicas vigentes e aplicáveis. Históricos GOLD/SILVER e documentos sem aprovação continuam como apoio; não autorizam passos operacionais. Procedimentos já revisados no repositório são uma transição explícita até a aprovação canônica. |
| Rastreabilidade | Notas e cartões incluem identidade da fonte, versão, aprovação e proveniência. A publicação canônica registra referências de manuais, chamados e analistas no índice, além do histórico editorial. |
| Empresa | Chamados da mesma empresa são buscados primeiro; ativos aparecem somente como recorrência, sem resolução. Chamados resolvidos precisam ser documentados, qualificados e compatíveis. Referências ficam na nota interna. |
| Curadoria | A tela dedicada do técnico existente foi preservada. O administrador pode devolver uma proposta para ajuste com auditoria; o técnico recebe o motivo. Publicação e nova versão continuam dependendo de decisão administrativa. |
| Importação | `knowledge_staging.py` reúne os cards locais e exporta seis procedimentos iniciais para prévia. O administrador revisa conteúdo, fonte e imagens antes de aprovar. Hashes impedem aprovar conteúdo/imagem alterados; a mesma aprovação é idempotente. Revisar um item cria versão nova. |
| Segurança de saída | Bloqueios de comandos SQL de alteração e de navegação sem fonte aprovada. Esses controles são verificações objetivas parciais, sem equivalência a uma validação semântica completa. Rascunhos não aceitam que o navegador certifique a própria fonte. |
| Regressão | `docs/support-regressions.json` contém contratos fixos por domínio, separados da avaliação de qualidade real. CI executa as regressões, a política, a importação e o contrato do replay. |
| Métricas | A aba Qualidade do suporte apresenta avaliações humanas, prazo de aprovação, cobertura, versões com avaliações negativas, fontes selecionadas e termos técnicos que exigem investigação. A telemetria registra apenas identificadores/termos estruturados; não guarda pergunta, resposta, credencial ou nome de cliente. |

### Conteúdo preparado para revisão

`python scripts/knowledge_staging.py` exporta para
`output/support_audit/reviewed_procedures.json` os casos de AnyMarket/Carrier,
pagamento, produto sem relacionamento, NF-e 539, IE inválida e Produtos por
Fabricante/Fornecedor. O último usa o manual local com validação humana de
19/10/2026. A mesma aba lê os três cards existentes em
`output/master_screens/staging_cards.json`.

Há sobreposição entre os dois cards de AnyMarket: o administrador deve revisar
essa relação e selecionar um procedimento existente quando apropriado. Não
publicar ambos automaticamente. Causa, versões, pré-requisitos e resultado
esperado que estejam incompletos precisam ser preenchidos na revisão.

Nenhum desses exports publica, altera `cortex_kb` ou solicita embeddings. A
aprovação administrativa utiliza a transação de versões/auditoria já existente;
sem vetor, a versão publicada continua recuperável por busca textual.

### Avaliação real reproduzível

O corpus local observado contém 106.710 chamados refinados e 29.982 nós. A
amostra inicial congelada tem 50 chamados, sendo 40 de desenvolvimento e 10 reservados.
Abrange AnyMarket, Mercado Livre, Tray, NF-e, NFC-e, GNRE, API, financeiro,
estoque, casos complexos e casos sem solução suficiente.

A auditoria das entradas encontrou 31 casos impróprios para as métricas
principais: 12 primeiras mensagens públicas tardias, 12 registros retrospectivos
que já incluem desfecho e 7 chamados sem abertura pública utilizável. Esses
registros e as respostas anteriormente geradas foram preservados, mas excluídos
das métricas de qualidade. Não remover a resolução de um registro retrospectivo
e fingir que ele representa uma abertura fiel.

Foi congelada uma extensão de 30 chamados distintos, três por domínio em dez
domínios, após inspeção automática de 99 candidatos. O filtro exige relato de
problema/pergunta na primeira mensagem pública, diferença máxima de cinco
minutos para a criação do chamado e ausência dos marcadores de registro
retrospectivo. A extensão usa 15 casos de desenvolvimento e 15 reservados e
permanece separada da amostra inicial. Não houve ajuste do conteúdo do CORTEX
para acertar os desfechos desses exemplos.

- Manifesto privado: `output/support_audit/manifest.json`.
- SHA-256 dos casos: `496c208f4e8332965388038a373a6b9f4120d27bb0bc3a99643e15dd7672a52d`.
- Referência Git anterior: `052249b2915761e85d4f11324904d0a08dc3019e`.
- Desenvolvimento: `output/support_audit/final-development/`.
- Conjunto reservado: usar `output/support_audit/final-holdout/` somente após
  congelar as correções de comportamento.
- Extensão: `output/support_audit/extension-manifest.json` e `clean-extension/`.
- Verificação final reservada: `output/support_audit/final-verified-holdout/`.
- Reteste de falha do provedor: `output/support_audit/final-retest/`.

```powershell
python -m unittest discover -s scripts -p 'test_*.py'
$env:CORTEX_TEST_DATABASE='1'
python -m unittest discover -s scripts -p 'test_training_database.py'
Remove-Item Env:\CORTEX_TEST_DATABASE

python scripts/evaluate_real_support.py --limit 40 --label nova-comparacao --compare 052249b2915761e85d4f11324904d0a08dc3019e --review --workers 2
python scripts/evaluate_real_support.py --split holdout --allow-holdout --limit 10 --label novo-holdout --review --workers 2
python scripts/select_support_audit_extension.py --limit 30
python scripts/evaluate_real_support.py --manifest output/support_audit/extension-manifest.json --split all --allow-holdout --limit 30 --label nova-extensao --compare 052249b2915761e85d4f11324904d0a08dc3019e --review --workers 2
```

Não reutilizar os nomes de uma execução: o avaliador preserva relatórios
existentes. O conjunto reservado não pode orientar correções; se houver nova
correção após examiná-lo, separar outro conjunto independente antes de alegar
validação final.

O replay usa o gerador real da nota sem cliente de entrega Movidesk. Reconstrói
a entrada somente com a primeira mensagem pública elegível, sem copiar o
assunto, status, campos finais ou resolução do chamado. O assunto simulado é
derivado dessa mensagem inicial. O relatório registra quando o import não
permite confirmar quem escreveu a abertura.

A sessão temporária exclui o chamado avaliado, fontes que o referenciam,
conhecimento sem origem reconhecida, fontes posteriores/sem data utilizável e
perfis atuais da empresa. Chamados de apoio precisam estar resolvidos e sem
atualização posterior ao corte. O conhecimento é reconstruído conforme as
datas das fontes; não existe uma cópia integral de cada versão histórica do
índice. Essa limitação deve acompanhar qualquer interpretação dos resultados.

Somente objetos TEMP são criados para isolar/cachear o corpus. Durante a
recuperação e geração, a transação é `REPEATABLE READ, READ ONLY`. Os vetores
textuais são cacheados na cópia temporária para reduzir o custo da repetição;
nenhum índice ou tabela público é alterado. O replay não busca anexos remotos;
omissões ficam registradas no caso. Não confundir um caso sem anexo disponível
com uma simulação completa da percepção visual.

Cada caso registra a entrada, resposta, busca, fontes, decisão, tempos SQL e
latência, solução histórica separada, verificações objetivas e campos de revisão
humana. A crítica adicional por modelo usa rubrica de dez critérios e continua
identificada como automática e preliminar. Revisão humana começa pendente;
nenhuma taxa humana pode ser apresentada com denominador zero.

Na primeira rodada, a crítica automática compartilhava o disjuntor do gerador;
dois timeouts do avaliador afetaram a disponibilidade da simulação seguinte.
O avaliador foi corrigido para encerrar todas as gerações antes de iniciar a
crítica, em processos próprios. As duas falhas de geração foram retestadas;
o registro original continua preservado, inclusive quando a entrada foi excluída
pela auditoria de contaminação. Essa é uma correção da medição, não uma melhoria
inventada da inteligência do CORTEX.

As críticas ausentes/inválidas podem ser repetidas por
`retry_support_reviews.py`, em um processo novo por crítica e com limite de 60
segundos apenas para o avaliador. O prazo de 25 segundos do CORTEX permanece
inalterado. As notas válidas do avaliador e as respostas do CORTEX não são
reescritas para melhorar a taxa. As tentativas anteriores ficam registradas.
Na divergência entre avaliador, resposta e solução histórica, exigir validação
técnica; não presumir que o rótulo do modelo é verdade.

`audit_support_openings.py` requalifica os registros depois da execução, inclusive
quando o hash do contexto não corresponde mais à fonte conferida. Em seguida,
`sanitize_support_audit.py` mascara identidades conhecidas, contatos, credenciais,
chaves fiscais e caminhos de links privados nos campos exportados. Os hashes de
entrada continuam referindo-se ao conteúdo anterior à máscara adicional.
`summarize_support_audit.py` reúne casos únicos, pares comparáveis, retestes e
divergências anteriores sem contar tentativas repetidas como chamados novos.

Falha do gerador, da consulta ou do avaliador não vira aprovação técnica. Na
comparação antes/depois, usar os mesmos IDs e denominadores explícitos. Taxa de
recuperação aplicável e ausência de SQL detectado não equivalem a acerto de
diagnóstico ou garantia de segurança. Pastas de sondagem e execuções interrompidas
são diagnósticas; não somar suas tentativas às métricas finais.

Os relatórios ficam em `output/`, ignorado pelo Git. A máscara automática de
identidades/credenciais não substitui revisão de privacidade antes de compartilhar
os arquivos fora do ambiente corporativo.

### Implantação e rollback

1. Revisar o diff e executar a suíte e os dois testes transacionais. A validação
   observada nesta rodada passou em 352 testes da suíte (19 condicionais
   ignorados); os dois testes de banco passaram separadamente, com rollback.
2. Conferir o relatório consolidado e submeter os casos/fontes à revisão de
   técnicos. Validar também as resoluções históricas usadas como comparação.
3. Antes de qualquer publicação, conferir backup/restauração e a migração
   idempotente `infra/006_canonical_knowledge.sql`. O banco observado ainda não
   tem todas as colunas opcionais mais recentes; a leitura de métricas suporta
   essa versão sem aplicar DDL. Não executar migrações de produção nesta revisão.
4. Com o schema conferido em homologação, aprovar os seis procedimentos iniciais
   na prévia, com causa, escopo, pré-requisitos, prints e resultado esperado
   completos. Essa publicação ainda depende da autorização do responsável e não
   foi feita por esta execução.
5. Empacotar uma revisão identificável. Manter respostas públicas e automação
   desabilitadas; começar por sugestões internas revisadas em piloto restrito,
   somente após autorização final de implantação.
6. Preservar o volume de `scripts/output/`, incluindo a telemetria SQLite. Eventos
   medem seleção/geração de sugestões e incluem o canal de execução; não medem
   resolução confirmada nem envio público.
7. Se houver regressão, pausar o piloto e restaurar a imagem/configuração anterior.
   Para conhecimento, inativar o item ou publicar nova versão com o conteúdo
   anterior aprovado, preservando a auditoria. Não apagar versões históricas.

### Bloqueadores de liberação

Na inspeção inicial não havia itens canônicos publicados. A publicação dos
procedimentos e a validação humana continuam pendentes por exigência do prompt.
Dados antigos omitem autoria e, em alguns casos, anexos; não há snapshots
históricos completos do índice. A estabilidade do provedor e a latência precisam
ser avaliadas com os denominadores reais do relatório. Não liberar atendimento
autônomo com base apenas na suíte ou na crítica automática.

**Classificação de implantação: NÃO APTO PARA IMPLANTAÇÃO enquanto esses
bloqueadores não forem atendidos.** A evolução de código e os mecanismos de
avaliação podem estar prontos para revisão sem que a qualidade operacional tenha
sido comprovada.

### Evidências consolidadas desta execução

- 80 chamados reais distintos selecionados; gerador executado em 73 e resposta
  capturada em 72, incluindo execuções posteriormente excluídas por contaminação.
- 46 respostas elegíveis nas métricas principais, com as dez dimensões da rubrica
  preenchidas por avaliação automática. As 34 exclusões compreendem os 31 casos
  iniciais descritos acima e três contextos cujo hash mudou na conferência posterior.
- 11 pares elegíveis e comparáveis antes/depois. Tentativas e retestes não contam
  como chamados novos.
- Na resposta final dos 46 elegíveis: 44 decisões de investigação e duas de
  encaminhamento, sem procedimento confirmado. Isso evidencia falta de cobertura
  aprovada no cenário histórico, não comprovação de resolução autônoma.
- A crítica automática classificou 15 como corretas, 30 como parciais e uma para
  revisão técnica. São rótulos preliminares, com zero revisões humanas.
- Duas divergências de execuções anteriores — arquitetura da integração de frete
  e evidência sobre bloqueio de vendas por estoque — permanecem consultáveis. Um
  reteste diferente não foi apresentado como correção estável ou validação da
  solução histórica.
- Seis prévias homologadas no material do projeto passaram pela validação
  estrutural do importador. Conferência final: zero itens, versões e candidatos
  persistidos em `cortex_kb`; nenhuma publicação foi feita.

Relatório privado: `output/support_audit/final-report.md` e `final-report.json`.
Cada caso aponta para sua execução completa, incluindo a marca de exclusão das
métricas quando aplicável. O snapshot revisável de código está em
`output/support_audit/source-snapshot/`, com diff, cópias das alterações e hashes.
Esses diretórios continuam ignorados pelo Git.
