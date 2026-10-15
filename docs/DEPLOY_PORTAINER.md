# Implantação do Cortex no Portainer

## O que está no Git

O repositório contém o código, o `Dockerfile`, a Stack `docker-compose.yml`, o esquema SQL sem dados e o receptor Movidesk. `docker-compose.yaml` e `compose.portainer.yml` são cópias compatíveis. **Não contém** `.env`, banco de dados, chamados, anexos nem os manuais originais. A Stack cria uma base vazia com as tabelas corretas; o histórico e o RAG exigem restauração separada.

Esta Stack exige o login da Alternativa para telas, APIs e arquivos da aplicação. `/login` e `/healthz` são públicos; o healthcheck só informa disponibilidade do banco/esquema. O webhook é um serviço separado, protegido por seu próprio segredo. O serviço `note-worker` gera sugestões e, em modo `internal_note`, publica uma ação interna no mesmo chamado. A Stack não envia respostas públicas ao cliente. Veja [Piloto de notas internas](PILOTO_NOTAS_INTERNAS.md) para configuração do `.env.server` e validação.

## Antes de subir

1. Publicar as alterações em `main` no repositório `LucasS352/cortex` e aguardar o workflow **Build Cortex image** terminar. Ele testa autenticação, Compose e webhook, depois publica `ghcr.io/lucass352/cortex:latest` e uma tag por commit. Atualizar só o YAML não atualiza o código dentro da imagem.
2. Para acesso pelo IP informado, usar `CORTEX_PUBLIC_URL=http://179.236.68.24` e liberar a porta 80. O Caddy recebe essa mesma URL; não é necessário preencher `CORTEX_DOMAIN` nem `CORTEX_ALLOWED_HOSTS`. Para HTTPS com domínio, usar `https://seu-dominio`, apontar o DNS e liberar 80/443. HTTP exige login, mas não criptografa o tráfego; o cookie usa `Secure` quando a URL é HTTPS. Se já houver um proxy na máquina, adaptar a Stack para encaminhar o painel ao serviço `app:8001` e somente `/webhooks/movidesk/tickets/*` ao serviço `webhook:8003`. Uma porta externa diferente exige ajustar também o mapeamento do Caddy.
3. No servidor, verificar se o Portainer usa **Docker Standalone**. No Git Registry/Registries, configurar acesso à imagem GHCR privada se o pacote não for público. O token deve ter permissão de leitura de pacotes. Mantenha também o repositório privado.
4. Gerar a senha do administrador fora do chat e guardar os valores impressos por `python scripts/create_admin_hash.py`. O script pede a senha sem ecoá-la e gera o hash, a chave da sessão e segredos independentes para os webhooks Movidesk e Master.

Não adicione arquivos ignorados nem use `git add -f` para `.env`, `output` ou `Instrucoes_master`.

## Corrigir a Stack existente sem recriar o banco

Em **Stacks → cortex → Edit stack settings**, mantenha o repositório e `refs/heads/main`, com Compose path `docker-compose.yml`. Nas variáveis de ambiente, configure:

| Variável | Valor |
| --- | --- |
| `CORTEX_PUBLIC_URL` | `http://179.236.68.24` |
| `CORTEX_ADMIN_USERNAME` | `alternativa` |
| `CORTEX_ADMIN_PASSWORD_HASH` | Hash gerado por `python scripts/create_admin_hash.py` |
| `CORTEX_SESSION_SECRET` | Chave aleatória gerada pelo mesmo script |
| `MOVIDESK_WEBHOOK_SECRET` | Segredo atual se já configurado; caso contrário, um novo segredo gerado |
| `MASTER_CONFIG_WEBHOOK_SECRET` | Segredo exclusivo gerado para o envio diário do `config.ini` |
| `POSTGRES_PASSWORD` | Na Stack existente, omitir ou usar `cortex_secret`; o volume foi criado pelo fallback anterior |
| `CORTEX_IMAGE` | `ghcr.io/lucass352/cortex:<commit corrigido>` ou `:latest` com re-pull |

Preserve também as chaves Movidesk/Gemini e configurações do piloto existentes. Não copie a senha aleatória de um `.env.server` novo para `POSTGRES_PASSWORD`: mudar a variável não altera a senha no volume já inicializado e faria a aplicação perder a conexão. A atualização mantém `cortex_secret` quando essa variável não estiver preenchida, igual à Stack que criou o volume atual.

Cole o hash no campo de valor do Portainer com os `$` originais, sem aspas extras. Em arquivo `.env.server`, use aspas simples para preservar os `$`. O Compose bloqueia a implantação se faltarem credenciais, em vez de abrir o painel sem login. `CORTEX_AUTH_REQUIRED=false` no Portainer não desativa a proteção desta Stack.

Se o acesso já tiver sido preparado localmente, copie as quatro variáveis de `.env.server.access` para a Stack: usuário, hash da senha, chave de sessão e URL pública. Esse arquivo é privado e ignorado pelo Git; publicar o código não transfere as credenciais. Adicione também as variáveis Movidesk/Gemini e `MASTER_CONFIG_WEBHOOK_SECRET` do `.env.server`, cada uma em seu próprio campo no Portainer. Não copie comentários, aspas externas nem os textos `&#x20;`. A senha é configurada como hash scrypt, com mínimo de 8 caracteres.

Para gerar um único arquivo pronto para a opção **Load variables from .env file** do Portainer, execute `python scripts/prepare_portainer_env.py` e importe `.env.server.portainer`. O arquivo combina as configurações locais com o hash do login e corrige `POSTGRES_PASSWORD` para o volume existente. Os valores são gravados sem aspas porque o Portainer preserva aspas como parte do valor importado. Ele é ignorado pelo Git e não deve ser commitado.

Depois que a imagem corrigida estiver publicada, use **Pull and redeploy**, solicitando nova busca da imagem se usar `latest`. Preserve os volumes e o nome da Stack. Abra `http://179.236.68.24/admin`: deve redirecionar ao login. Não use a porta 9443, que pertence ao Portainer.

As duas falhas corrigidas eram: `/healthz` retornava 404 quando a autenticação estava desativada, e o Compose interpolava a variável do Caddy antes de iniciar o container. O YAML agora usa `{$$CORTEX_DOMAIN}`; dentro do Caddyfile o resultado correto é `{$CORTEX_DOMAIN}`.

## Stack de Git no Portainer

Em **Stacks > Add stack > Git repository**:

- URL: URL do repositório privado; configure a credencial Git no Portainer.
- Compose path: `docker-compose.yml`.
- Habilite **relative path volumes**, pois os arquivos SQL são relativos ao repositório. O Caddyfile é criado dentro do container, sem bind mount do host.
- Preencha as variáveis de `.env.example` na seção **Environment variables** da Stack. Use segredos reais; não salve `.env` no Git.
- `CORTEX_IMAGE` deve apontar para a imagem GHCR publicada, preferencialmente a tag do commit implantado.
- Um único serviço `app` e um único `webhook`: o agendador local da sincronização não foi feito para múltiplas réplicas.

O arquivo da Stack usa `image`, sem `build`. [O Portainer informa que build em Compose pode falhar em ambientes Docker remotos](https://docs.portainer.io/faqs/known-issues/docker-compose-files-including-build-steps-fail); por isso a imagem é criada no GitHub Actions e publicada no GHCR.

Após o deploy, acesse `<CORTEX_PUBLIC_URL>/admin`. O Movidesk recebe a URL `<CORTEX_PUBLIC_URL>/webhooks/movidesk/tickets/<MOVIDESK_WEBHOOK_SECRET>` e o Master recebe `<CORTEX_PUBLIC_URL>/webhooks/master/config/<MASTER_CONFIG_WEBHOOK_SECRET>`. Valide o login e o transporte escolhido antes de configurar os disparos. O endpoint Master sanitiza o INI antes da persistência e deduplica as retentativas por `event_id`.

## Dados e mídia

O banco novo tem as 18 tabelas atuais e um registro inicial da Alternativa, mas não inclui o histórico existente. Para migrar o ambiente atual, faça um backup PostgreSQL completo fora do Git. Um exemplo no host atual é:

```bash
docker exec deskcortex-postgres pg_dump -U deskcortex -d deskcortex -Fc > cortex-backup.dump
```

Transfira o arquivo por canal interno protegido e restaure no banco de destino em uma janela de manutenção, com `app` e `webhook` parados. Antes de qualquer restauração, mantenha uma cópia do volume do destino. Verifique a contagem de tabelas e de nós de conhecimento após a restauração. O procedimento exato de `pg_restore` depende de a base de destino estar vazia ou já inicializada; **não restaure sobre dados em uso sem ensaio prévio**.

Os volumes `cortex_output` e `manual_media` também começam vazios. O primeiro guarda anexos, mídias curadas, auditoria local, configurações do agente e recibos do webhook. Copie esses diretórios separadamente do computador atual para os volumes persistentes do servidor; o backup SQL não os inclui. O material de `Instrucoes_master` pertence a `manual_media` e deve ser transferido fora do Git. Sem esses arquivos, referências a imagens e manuais antigos não aparecerão mesmo com o banco restaurado.

## Teste local antes do Portainer

Preencha as variáveis em `.env.server` (ignorado pelo Git) e execute:

```bash
docker compose --env-file .env.server config --quiet
docker compose --env-file .env.server pull
docker compose --env-file .env.server up -d --wait
```

Para verificar: `docker compose --env-file .env.server ps`, logs de `app` e `caddy`, abrir a URL configurada e testar login, leitura de chamados, fontes e logout. `/healthz` deve responder 200; falha real do banco/esquema retorna 503. Sem sessão, uma página retorna ao login e uma API retorna 401. Faça o teste de webhook com uma conta de teste antes de habilitar o gatilho geral.

## Cuidados operacionais

- Faça backup regular de `pgdata`, `cortex_output` e `manual_media`; os volumes sobrevivem ao redeploy, mas não substituem backup.
- `CORTEX_AUTONOTE_MODE` no `.env.server` é o teto de permissão do servidor. A lista efetiva do piloto é administrada em **Agente e políticas → Piloto & Sandbox**. A publicação exige, ao mesmo tempo, empresa selecionada, e-mail do solicitante autorizado, piloto ativo e a opção de nota privada marcada. As contas do Playground não autorizam escrita no Movidesk.
- Não expor PostgreSQL, serviço `app` ou serviço `webhook` diretamente à internet. Só Caddy publica portas.
- Os segredos do Portainer devem ser acessíveis apenas aos administradores da Stack. Rotacione o token Movidesk e os segredos se forem expostos.
