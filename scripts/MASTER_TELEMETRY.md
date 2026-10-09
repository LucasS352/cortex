# Inventário diário do ERP Master

Implementado em 10/09/2026. O Master envia uma observação diária de cada instalação, tanto estável quanto BETA. O DeskCortex mantém histórico e estado atual por ambiente, preservando a origem de cada informação.

## Contrato para o desenvolvedor Delphi

`POST /api/v1/master/telemetry/config`, JSON UTF-8. O mesmo objeto também pode vir dentro de `{"status":"ok","info":{...}}`.

```json
{
  "empresa": "Empresa de exemplo",
  "usuario": "Supervisor",
  "versao": "v3.0 - 10/09/2026 08:03:18",
  "ticket": "Ticket-130004",
  "servidor": "192.0.2.10",
  "banco": "/firebird/exemplo.fdb",
  "datahora": "10/09/2026 09:00:00",
  "config": "[geral]\r\nn_bancos=1\r\n[conexao_1]\r\nservidor=192.0.2.10\r\nbanco=/firebird/exemplo.fdb\r\ndll=fbclient5.dll",
  "codigo_empresa": "empresa-001",
  "instalacao_id": "instalacao-001",
  "event_id": "identificador-unico-deste-envio",
  "timezone_offset": "-03:00"
}
```

Os oito campos do formato atual são aceitos. `ticket` ausente, vazio, só com espaços ou `null` indica versão estável; qualquer texto não vazio indica BETA, e o texto completo fica em `beta_ticket`. Isso é independente de existir a palavra BETA em `versao`. O ticket não abre um chamado novo e não comprova que o atendimento histórico desse protocolo contém a solução.

`codigo_empresa` e `instalacao_id` são opcionais por compatibilidade, mas necessários para identificação confiável ao longo de mudanças. São identificadores declarados, não credenciais. Sem eles, o cadastro é provisório e deriva de empresa/servidor/banco; não se deve usar nome da empresa como chave definitiva. Uma instalação já associada a um código de empresa não é transferida silenciosamente para outro código.

`event_id` identifica uma observação e deve permanecer igual nas retentativas, junto com `datahora` e conteúdo. Se omitido, o servidor calcula uma chave determinística a partir da observação sanitizada. Mesmo evento com outro conteúdo retorna `409`.

`datahora` usa `DD/MM/AAAA HH:MM:SS`; `timezone_offset` assume `-03:00` se omitido. A data contida em `versao` é a compilação local, sem conversão presumida de fuso. Ela nunca substitui a data da coleta. Relógio de coleta mais de 10 minutos no futuro impede a promoção automática ao estado atual, mas o envio é mantido no histórico.

Limites: INI com até 262.144 caracteres e corpo JSON até 2 MiB. Escapes JSON como `\u0020`, `\u005B` e `\r\n` são tratados pelo decoder JSON; não aplicar uma segunda decodificação de escapes, que danificaria caminhos Windows.

## Rotina diária no Master

1. Executar em tarefa secundária uma vez por instalação/dia, com pequeno atraso aleatório para distribuir a carga. Não executar uma vez por tela nem por login de cada operador.
2. Capturar o ambiente realmente ativo e os metadados correspondentes. Preencher `ticket` somente para o executável BETA; voltar a vazio quando retornar à versão estável.
3. Remover senhas, logins de banco, tokens e segredos no emissor. O receptor repete a filtragem e não grava o INI bruto.
4. Gerar `event_id` uma única vez e enviar por HTTPS ao coletor autenticado da organização. Não distribuir a chave administrativa global do DeskCortex dentro do executável.
5. Marcar a observação como entregue somente após `stored` ou `duplicate`. Em falha transitória, repetir a mesma observação com backoff; `422` exige corrigir o formato e `409` exige conferir identidade/evento. Evitar reenviar continuamente um payload inválido.
6. Após atualização, troca de BETA ou mudança importante, um envio adicional no mesmo dia é permitido. Usar nova observação com horário mais recente e novo `event_id`.

Esse agendamento pertence ao Master/coletor do desenvolvedor. Nenhuma automação do Codex foi criada e nenhum envio externo foi realizado.

## Respostas e leitura

`POST /api/v1/master/telemetry/config/validate` valida sem persistir e retorna versão, BETA, contagem de conexões, ocorrências que correspondem ao ambiente ativo e contadores de sanitização. Nunca retorna o INI bruto.

`POST /api/v1/master/telemetry/config` retorna `status` (`stored`/`duplicate`), `environment_id`, `snapshot_id`, `promoted`, `is_beta`, avisos e, em novos registros, campos alterados.

- `GET /api/v1/master/environments?beta_only=false&limit=50&offset=0`: inventário paginado, com sinalização de última coleta há mais de 36 horas.
- `GET /api/v1/master/environments/{id}/history?limit=20&offset=0`: envios e campos alterados.
- `GET /api/v1/master/environments/{id}/snapshots/{snapshot_id}`: configuração de diagnóstico sanitizada daquele envio.
- Interface local: `/ambientes`, também acessível pelo link **Ambientes Master** no simulador.

## Regras que evitam diagnósticos errados

- Seções repetidas, inclusive `[conexao_1]`, são preservadas por ocorrência. Não usar um parser que sobrescreva silenciosamente todos os blocos anteriores.
- O exemplo recebido tem 102 blocos de conexão; isso não significa 102 empresas identificadas ou 102 instalações ativas. Descrições de conexão não criam cadastros de empresas automaticamente.
- Versão/BETA/usuário são observações do ambiente ativo do JSON. Nunca propagar a versão do executável para todas as bases citadas no arquivo.
- A conexão ativa é localizada pela combinação exata normalizada servidor + banco. Zero ou múltiplas correspondências geram aviso; o servidor não escolhe um banco arbitrariamente.
- Chaves desconhecidas e valores sensíveis são omitidos. Chaves repetidas dentro do mesmo bloco usam o último valor e geram aviso. Comentários com `#` ou `;` são ignorados.
- Um envio atrasado não reverte a configuração atual. Um horário exatamente igual com conteúdo diferente permanece apenas no histórico, evitando escolher uma ordem arbitrária.
- Um novo dia com a mesma configuração ainda registra uma observação. Reenvio do mesmo evento não duplica histórico.
- As alterações são comparadas ao estado vigente antes da recepção, não a uma suposta sequência causal reconstruída. O histórico mantém horário de coleta e de recepção.

## Persistência e operação

Aplicar `deskcortex-api/migrations/003_master_environment_telemetry.sql` no PostgreSQL. A migração cria somente as tabelas `master_environments` e `master_environment_snapshots` e seus índices. O receptor usa transação e lock por ambiente, sem executar consultas nos Firebirds dos clientes.

O hash e a persistência usam somente dados sanitizados. Não há coluna de INI bruto. A API de validação também remove o conteúdo original dos erros de validação, que normalmente poderiam ecoar o payload sensível.

A implementação está no serviço FastAPI existente (`scripts/simulator_app.py`), por meio de `master_config.py` e `master_telemetry.py`. Não há outro parser concorrente no NestJS.

O simulador continua restrito à máquina local e ao único tenant da organização. Antes de receber diretamente pela internet, é necessário um coletor autenticado com identidade de instalação/empresa vinculada no servidor, HTTPS e limites de requisição. Identificadores enviados no JSON não autorizam acesso. Não expor o simulador por proxy como atalho para essa etapa.

## Testes

`python -m unittest discover -s scripts -p test_master_telemetry.py -v`

Com o PostgreSQL local ativo e a migração aplicada, definir `MASTER_TEST_POSTGRES=1` apenas no processo dos testes habilita verificações transacionais. Cada teste desfaz seus registros ao terminar. Fixtures são fictícias; o arquivo real fornecido pelo usuário não é armazenado como telemetria ativa durante a validação.
