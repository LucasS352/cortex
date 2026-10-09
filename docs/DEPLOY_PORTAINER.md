# Implantação do Cortex no Portainer

## O que está no Git

O repositório contém o código, o `Dockerfile`, a Stack `compose.portainer.yaml`, o esquema SQL sem dados e o receptor Movidesk. **Não contém** `.env`, banco de dados, chamados, anexos nem os manuais originais. A Stack cria uma base vazia com as tabelas corretas; o histórico e o RAG exigem restauração separada.

O modo de produção exige login para toda a aplicação. O webhook é um serviço separado, acessível apenas no caminho secreto HTTPS. O serviço `note-worker` gera sugestões e, em modo `internal_note`, publica uma ação interna no mesmo chamado. A Stack não envia respostas públicas ao cliente. Veja [Piloto de notas internas](PILOTO_NOTAS_INTERNAS.md) para configuração do `.env.server` e validação.

## Antes de subir

1. Criar um repositório **privado** no GitHub e enviar o branch `main` após conferir o conteúdo do commit. O workflow `.github/workflows/cortex-image.yml` publica `ghcr.io/<organização>/<repositório>:latest` e uma tag por commit.
2. Apontar o DNS de `CORTEX_DOMAIN` ao servidor e liberar as portas 80 e 443. O Caddy usa essas portas para obter e renovar HTTPS. Se já houver um proxy na máquina, adaptar a Stack para encaminhar o painel ao serviço `app:8001` e somente `/webhooks/movidesk/tickets/*` ao serviço `webhook:8003`.
3. No servidor, verificar se o Portainer usa **Docker Standalone**. No Git Registry/Registries, configurar acesso à imagem GHCR privada se o pacote não for público. O token deve ter permissão de leitura de pacotes. Mantenha também o repositório privado.
4. Gerar a senha do administrador fora do chat e guardar os três valores impressos por `python scripts/create_admin_hash.py`. O script pede a senha sem ecoá-la e gera o hash da senha, a chave da sessão e o segredo do webhook.

O checkout local foi inicializado em Git, mas não possui identidade de commit nem remoto configurados. No computador de quem fará o primeiro push, configure `git config user.name` e `git config user.email` com a identidade real, crie o commit e vincule o remoto do repositório privado da empresa. Não adicione arquivos ignorados nem use `git add -f` para `.env`, `output` ou `Instrucoes_master`.

## Stack de Git no Portainer

Em **Stacks > Add stack > Git repository**:

- URL: URL do repositório privado; configure a credencial Git no Portainer.
- Compose path: `compose.portainer.yaml`.
- Habilite **relative path volumes**, pois o SQL e o Caddyfile são arquivos relativos do repositório.
- Preencha as variáveis de `.env.example` na seção **Environment variables** da Stack. Use segredos reais; não salve `.env` no Git.
- `CORTEX_IMAGE` deve apontar para a imagem GHCR publicada, preferencialmente a tag do commit implantado.
- Um único serviço `app` e um único `webhook`: o agendador local da sincronização não foi feito para múltiplas réplicas.

O arquivo da Stack usa `image`, sem `build`. [O Portainer informa que build em Compose pode falhar em ambientes Docker remotos](https://docs.portainer.io/faqs/known-issues/docker-compose-files-including-build-steps-fail); por isso a imagem é criada no GitHub Actions e publicada no GHCR.

Após o deploy, acesse `https://<CORTEX_DOMAIN>/admin`. O Movidesk recebe a URL `https://<CORTEX_DOMAIN>/webhooks/movidesk/tickets/<MOVIDESK_WEBHOOK_SECRET>`. Configure o gatilho só depois de HTTPS, DNS e login validados. O endpoint recebe e armazena eventos; o `note-worker` faz o processamento e o PATCH interno conforme o teto `CORTEX_AUTONOTE_MODE` e as empresas autorizadas no painel.

## Dados e mídia

O banco novo tem as 18 tabelas atuais e um registro inicial da Alternativa, mas não inclui o histórico existente. Para migrar o ambiente atual, faça um backup PostgreSQL completo fora do Git. Um exemplo no host atual é:

```bash
docker exec deskcortex-postgres pg_dump -U deskcortex -d deskcortex -Fc > cortex-backup.dump
```

Transfira o arquivo por canal interno protegido e restaure no banco de destino em uma janela de manutenção, com `app` e `webhook` parados. Antes de qualquer restauração, mantenha uma cópia do volume do destino. Verifique a contagem de tabelas e de nós de conhecimento após a restauração. O procedimento exato de `pg_restore` depende de a base de destino estar vazia ou já inicializada; **não restaure sobre dados em uso sem ensaio prévio**.

Os volumes `cortex_output` e `manual_media` também começam vazios. O primeiro guarda anexos, mídias curadas, auditoria local, configurações do agente e recibos do webhook. Copie esses diretórios separadamente do computador atual para os volumes persistentes do servidor; o backup SQL não os inclui. O material de `Instrucoes_master` pertence a `manual_media` e deve ser transferido fora do Git. Sem esses arquivos, referências a imagens e manuais antigos não aparecerão mesmo com o banco restaurado.

## Teste local antes do Portainer

Preencha as variáveis em um `.env` local (ignorado pelo Git) e execute:

```bash
docker compose config --quiet
docker compose up --build -d
```

Para verificar: `docker compose ps`, `docker compose logs --tail=100 app`, abrir a URL HTTPS e testar login, leitura de chamados, fontes e atualização manual. Faça o teste de webhook com uma conta de teste antes de habilitar o gatilho geral.

## Cuidados operacionais

- Faça backup regular de `pgdata`, `cortex_output` e `manual_media`; os volumes sobrevivem ao redeploy, mas não substituem backup.
- `CORTEX_AUTONOTE_MODE` no `.env.server` é o teto de permissão do servidor. A lista efetiva do piloto é administrada em **Agente e políticas → Piloto & Sandbox**. Sem empresa selecionada, piloto ativo e a opção de nota privada marcada, o worker opera sem publicar. As contas do Playground não autorizam escrita no Movidesk.
- Não expor PostgreSQL, serviço `app` ou serviço `webhook` diretamente à internet. Só Caddy publica portas.
- Os segredos do Portainer devem ser acessíveis apenas aos administradores da Stack. Rotacione o token Movidesk e os segredos se forem expostos.
