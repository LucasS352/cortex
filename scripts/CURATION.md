# Curadoria técnica — piloto NF-e / Safetech

Camada adicional no schema PostgreSQL `knowledge_curation`. O primeiro lote examina títulos de chamados e artigos do escopo NF-e/Safetech, além das referências explícitas das fichas. Não representa toda a base. Nenhum serviço externo ou modelo pago é chamado pelo pipeline.

## Consultar o resultado

Abra `output/knowledge-curation/index.html` no navegador. Funciona offline, com busca, filtros, trechos originais e links para o Help Desk. O arquivo é interno: inclui trechos de atendimentos. Não publicar como site público.

Para visualizar em localhost, a partir da raiz:

```powershell
python -m http.server 8769 --bind 127.0.0.1 --directory output/knowledge-curation
```

Abra http://127.0.0.1:8769/. Essa página é um snapshot; decisões são registradas pela CLI e uma nova exportação atualiza o HTML. Ainda não foi integrada ao atendimento automático existente.

## Construir e pesquisar

```powershell
python scripts/curation.py build --candidates 40
python scripts/curation.py search "qryXMLSTF ORA-00942 SFT_XML_NOTA_FISCAL_SAIDA"
python scripts/curation.py search "certificado digital" --approved-only
python scripts/curation.py export
```

Usa as credenciais existentes em `scripts/config.py`. Com múltiplos tenants, informar `--tenant UUID` antes do subcomando. Não combina tenants implicitamente. Migração: `deskcortex-api/migrations/004_knowledge_curation.sql`.

## O que é gerado

- `sources`: texto normalizado por ação/artigo, IDs, data, URL, hash, assinaturas e alertas. O original bruto permanece nas tabelas existentes.
- `cards`: ficha atual, estado e revisão baseada no conteúdo.
- `card_versions`: documento e citações de cada revisão, preservando os trechos anteriores.
- `reviews`: autor declarado, decisão, justificativa, data e revisão exata.
- `runs`: métricas de cada processamento.

O extrator é determinístico. Agrupa assinaturas explícitas (Oracle, rejeição, objetos SFT/SPT e algumas mensagens). Não usa similaridade como prova de mesma causa. Não há chamada de LLM em lote nesta fase. As seis sínteses iniciais foram preparadas pelo assistente e cada citação é conferida programaticamente contra as ações/artigos originais normalizados.

`curation_seeds.json` define as sínteses iniciais. Cada afirmação inclui uma referência e uma citação literal. Se a citação não existir, a construção falha antes de salvar as fichas. Alertas e limites de aplicação são critérios editoriais; não são trechos atribuídos às fontes.

Os grupos automáticos mostram até oito trechos por assinatura e guardam a lista completa de fontes associadas. A fila é limitada pelo argumento `--candidates` (até 500). Fontes sem assinatura ficam disponíveis no snapshot SQL, mas ainda não geram agrupamento. Vídeos sem transcrição recebem alerta; anexos e OCR ficam fora deste piloto.

## Revisão humana

As fichas começam como `draft`; classificações Gold/High preexistentes não equivalem a aprovação humana. O revisor deve abrir as evidências, verificar aplicabilidade/versão e conferir as limitações antes de aprovar.

Copie a chave e a revisão mostradas no final da ficha:

```powershell
python scripts/curation.py review --key safetech-xml-indisponivel --revision HASH_EXIBIDO --reviewer "Nome do técnico" --decision approved --note "Evidências e limites conferidos para o procedimento de consulta de XML."
```

Decisões aceitas: `approved`, `rejected`, `needs_review`. Autor e justificativa são obrigatórios. Identidade é autodeclarada na CLI local, sem autenticação corporativa. Uma revisão antiga é recusada. Aprovar relê fontes originais e verifica hashes/citações. Qualquer alteração no documento aprovado durante nova construção passa a `needs_review`. Conteúdo idêntico preserva revisão e aprovação. Grupos automáticos não podem ser aprovados como procedimento; primeiro escrever uma ficha específica em `curation_seeds.json` com trechos verificáveis.

Alterações nas fontes são detectadas na reconstrução e na tentativa de aprovação, não em tempo real. O HTML offline não se atualiza sozinho. Ao integrar futuramente ao atendimento, restringir a fichas aprovadas e revalidar a atualidade da fonte.

## Validação

```powershell
python -m unittest discover -s scripts -p test_curation.py -v
python scripts/evaluate_curation.py
```

O teste de integração usa os dados do piloto e testa aprovação/invalidação dentro de uma transação revertida. Não deixa aprovações fictícias. O relatório `output/knowledge-curation/evaluation.json` contém buscas de regressão, citações, idempotência, isolamento de tenant e bloqueios de revisão. As dez consultas são um conjunto pequeno conhecido, não uma estimativa de precisão geral. Para medir qualidade real, separar chamados não usados na construção e obter julgamento independente dos técnicos.

## Limites e próxima etapa

O artigo 123522 comprova normalização da integração sem ação da Alternativa; não documenta uma correção específica aplicada pela Safetech. O caso 128797 mostra `SPT_VW_FATURA_PEDIDO_ECOMMERCE`; não substituir silenciosamente por `SFT`. A presença de sinônimo Oracle não prova existência/permissão sobre o destino. Estes limites aparecem nas fichas.

Próxima etapa: técnicos revisarem as seis fichas e selecionarem os grupos prioritários. Expandir com transcrição/OCR, versionamento de procedimentos e avaliação independente. A integração com o RAG atual deve preservar a distinção entre documento aprovado, hipótese e estado real do cliente.
