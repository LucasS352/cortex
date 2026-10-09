"""
DeskCortex — Ingestão Completa de Metadados e Base de Conhecimento Movidesk
=============================================================================
Extrai e persiste todas as entidades auxiliares do Movidesk:
  1. 📚 Base de Conhecimento (1.452 artigos com HTML e texto completo)
  2. 🏢 Serviços (Catálogo completo de serviços e árvores hierárquicas)
  3. 👥 Pessoas / Clientes / Contatos / Técnicos (/persons)
  4. ⭐ Pesquisas de Satisfação (/survey/responses e /survey/questions)

Uso:
    python ingest_all_metadata.py
    python ingest_all_metadata.py --only-kb
    python ingest_all_metadata.py --only-services
    python ingest_all_metadata.py --only-persons
    python ingest_all_metadata.py --only-surveys
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Forçar UTF-8 no console Windows para emojis
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    MOVIDESK_TOKEN,
    MOVIDESK_BASE_URL,
    POSTGRES_DSN,
    MAX_RETRIES,
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
)

console = Console(force_terminal=True)


def get_db_connection() -> psycopg.Connection:
    """Cria conexão com PostgreSQL."""
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row)


def get_tenant_id(conn: psycopg.Connection) -> str:
    """Retorna o tenant_id da Alternativa Sistemas."""
    row = conn.execute("SELECT id FROM tenants WHERE slug = 'alternativa' LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("Tenant Alternativa não encontrado!")
    return str(row["id"])


# ---------------------------------------------------------------------------
# 1. Ingestão de Serviços (/services)
# ---------------------------------------------------------------------------
def ingest_services(conn: psycopg.Connection, tenant_id: str) -> int:
    console.rule("[bold cyan]1. Ingestão de Serviços (/services)")
    url = f"{MOVIDESK_BASE_URL}/services?token={MOVIDESK_TOKEN}&$top=1000"

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        if resp.status_code != 200:
            console.print(f"[bold red]❌ Erro ao buscar serviços: HTTP {resp.status_code}[/]")
            return 0

        services = resp.json()
        if not isinstance(services, list):
            return 0

        count = 0
        for svc in services:
            svc_id = svc.get("id")
            if svc_id is None:
                continue

            conn.execute("""
                INSERT INTO raw_services (tenant_id, external_id, name, parent_service_id, is_visible, payload)
                VALUES (%(tenant_id)s, %(external_id)s, %(name)s, %(parent_id)s, %(is_visible)s, %(payload)s)
                ON CONFLICT (tenant_id, external_id)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    parent_service_id = EXCLUDED.parent_service_id,
                    is_visible = EXCLUDED.is_visible,
                    payload = EXCLUDED.payload,
                    ingested_at = NOW()
            """, {
                "tenant_id": tenant_id,
                "external_id": svc_id,
                "name": svc.get("name"),
                "parent_id": svc.get("parentServiceId"),
                "is_visible": bool(svc.get("isVisible", True)),
                "payload": json.dumps(svc, ensure_ascii=False, default=str),
            })
            count += 1

        conn.commit()
        console.print(f"  ✅ {count} serviços inseridos/atualizados com sucesso!")
        return count


# ---------------------------------------------------------------------------
# 2. Ingestão da Base de Conhecimento (Artigos)
# ---------------------------------------------------------------------------
async def fetch_article_detail(client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
                               article_id: int) -> dict | None:
    """Busca o conteúdo completo de um artigo pelo ID."""
    url = f"{MOVIDESK_BASE_URL}/article/{article_id}?token={MOVIDESK_TOKEN}"
    async with semaphore:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    await asyncio.sleep(retry_after + 1)
                    continue
                elif resp.status_code >= 500:
                    await asyncio.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
                    continue
                else:
                    return None
            except Exception:
                await asyncio.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
                continue
    return None


async def ingest_kb_articles(tenant_id: str) -> int:
    console.rule("[bold cyan]2. Ingestão da Base de Conhecimento (/kb/article + /article/:id)")

    page_size = 50
    page = 1
    all_summary_items = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Passo 1: Listar todos os artigos disponíveis
        console.print("  🔍 Varrendo catálogo de artigos da Base de Conhecimento...")
        while True:
            url = f"{MOVIDESK_BASE_URL}/kb/article?token={MOVIDESK_TOKEN}&page={page}&pageSize={page_size}"
            resp = await client.get(url)
            if resp.status_code != 200:
                break

            data = resp.json()
            items = data.get("items", [])
            if not items:
                break

            all_summary_items.extend(items)
            total_size = data.get("totalSize", len(all_summary_items))
            console.print(f"    📦 Página {page} | {len(items)} artigos listados ({len(all_summary_items)}/{total_size})")

            if len(all_summary_items) >= total_size:
                break
            page += 1

        total_articles = len(all_summary_items)
        console.print(f"  📚 Total de artigos encontrados: {total_articles}")

        # Passo 2: Buscar detalhes completos de cada artigo em paralelo (8 workers)
        console.print("  🚀 Baixando conteúdo completo dos artigos em modo turbo (8 workers)...")
        semaphore = asyncio.Semaphore(8)

        conn = get_db_connection()
        count = 0
        batch_size = 20

        for i in range(0, total_articles, batch_size):
            batch = all_summary_items[i:i + batch_size]
            tasks = [fetch_article_detail(client, semaphore, item["id"]) for item in batch]
            results = await asyncio.gather(*tasks)

            for art in results:
                if not art or not isinstance(art, dict):
                    continue

                art_id = art.get("id")
                if art_id is None:
                    continue

                conn.execute("""
                    INSERT INTO raw_kb_articles (
                        tenant_id, external_id, title, summary, categories, tags,
                        content_html, content_text, payload, created_at_source, updated_at_source
                    ) VALUES (
                        %(tenant_id)s, %(external_id)s, %(title)s, %(summary)s,
                        %(categories)s, %(tags)s, %(content_html)s, %(content_text)s,
                        %(payload)s, %(created_at)s, %(updated_at)s
                    )
                    ON CONFLICT (tenant_id, external_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        categories = EXCLUDED.categories,
                        tags = EXCLUDED.tags,
                        content_html = EXCLUDED.content_html,
                        content_text = EXCLUDED.content_text,
                        payload = EXCLUDED.payload,
                        updated_at_source = EXCLUDED.updated_at_source,
                        ingested_at = NOW()
                """, {
                    "tenant_id": tenant_id,
                    "external_id": art_id,
                    "title": art.get("title"),
                    "summary": art.get("summary"),
                    "categories": json.dumps(art.get("categories", []), ensure_ascii=False),
                    "tags": json.dumps(art.get("tags", []), ensure_ascii=False),
                    "content_html": art.get("contentHtml"),
                    "content_text": art.get("contentText"),
                    "payload": json.dumps(art, ensure_ascii=False, default=str),
                    "created_at": art.get("createdDate"),
                    "updated_at": art.get("updatedDate"),
                })
                count += 1

            conn.commit()
            console.print(f"    💾 Progresso: {count}/{total_articles} artigos salvos no PostgreSQL")

        conn.close()
        console.print(f"  ✅ {count} artigos da Base de Conhecimento ingeridos com sucesso!")
        return count


# ---------------------------------------------------------------------------
# 3. Ingestão de Pessoas / Clientes / Técnicos (/persons)
# ---------------------------------------------------------------------------
def ingest_persons(conn: psycopg.Connection, tenant_id: str, max_pages: int = 0) -> int:
    console.rule("[bold cyan]3. Ingestão de Pessoas e Organizações (/persons)")

    top = 100
    skip = 0
    total_persons = 0
    page = 0

    with httpx.Client(timeout=30.0) as client:
        while True:
            if max_pages > 0 and page >= max_pages:
                break

            url = (
                f"{MOVIDESK_BASE_URL}/persons?token={MOVIDESK_TOKEN}"
                f"&$top={top}&$skip={skip}"
                f"&$select=id,codeReferenceAdditional,isActive,personType,profileType,"
                f"accessProfile,businessName,businessBranch,corporateName,cpfCnpj,"
                f"userName,accountEmail,role,bossId,bossName,classification,createdDate,"
                f"changedDate,addresses,contacts,emails,teams,relationships"
            )

            resp = client.get(url)
            if resp.status_code != 200:
                console.print(f"  ❌ Erro ao buscar persons em skip={skip}: HTTP {resp.status_code}")
                break

            persons = resp.json()
            if not isinstance(persons, list) or not persons:
                console.print(f"  🏁 Fim dos registros de pessoas (skip={skip})")
                break

            count = 0
            for p in persons:
                p_id = str(p.get("id"))
                if not p_id:
                    continue

                conn.execute("""
                    INSERT INTO raw_persons (
                        tenant_id, external_id, business_name, cpf_cnpj,
                        person_type, profile_type, email, phone, payload
                    ) VALUES (
                        %(tenant_id)s, %(external_id)s, %(business_name)s, %(cpf_cnpj)s,
                        %(person_type)s, %(profile_type)s, %(email)s, %(phone)s, %(payload)s
                    )
                    ON CONFLICT (tenant_id, external_id)
                    DO UPDATE SET
                        business_name = EXCLUDED.business_name,
                        cpf_cnpj = EXCLUDED.cpf_cnpj,
                        person_type = EXCLUDED.person_type,
                        profile_type = EXCLUDED.profile_type,
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        payload = EXCLUDED.payload,
                        ingested_at = NOW()
                """, {
                    "tenant_id": tenant_id,
                    "external_id": p_id,
                    "business_name": p.get("businessName") or p.get("corporateName"),
                    "cpf_cnpj": p.get("cpfCnpj"),
                    "person_type": p.get("personType"),
                    "profile_type": p.get("profileType"),
                    "email": p.get("accountEmail") or (p.get("emails", [{}])[0].get("email") if p.get("emails") else None),
                    "phone": p.get("contacts", [{}])[0].get("contact") if p.get("contacts") else None,
                    "payload": json.dumps(p, ensure_ascii=False, default=str),
                })
                count += 1

            conn.commit()
            total_persons += count
            page += 1
            console.print(f"    📦 Página {page:>3} | skip={skip:>6} | {count:>3} pessoas salvas | Total: {total_persons:,}")

            if len(persons) < top:
                break

            skip += top
            time.sleep(0.2)  # Janela turbo

    console.print(f"  ✅ {total_persons} pessoas/organizações ingeridas com sucesso!")
    return total_persons


# ---------------------------------------------------------------------------
# 4. Ingestão de Pesquisas de Satisfação (/survey/responses)
# ---------------------------------------------------------------------------
def ingest_surveys(conn: psycopg.Connection, tenant_id: str) -> int:
    console.rule("[bold cyan]4. Ingestão de Pesquisas de Satisfação (/survey/responses)")

    limit = 100
    starting_after = None
    total_surveys = 0
    page = 0

    with httpx.Client(timeout=30.0) as client:
        while True:
            url = f"{MOVIDESK_BASE_URL}/survey/responses?token={MOVIDESK_TOKEN}&limit={limit}"
            if starting_after:
                url += f"&startingAfter={starting_after}"

            resp = client.get(url)
            if resp.status_code != 200:
                console.print(f"  ❌ Erro ao buscar surveys: HTTP {resp.status_code}")
                break

            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if not items:
                break

            count = 0
            for s in items:
                s_id = str(s.get("id"))
                if not s_id:
                    continue

                conn.execute("""
                    INSERT INTO raw_surveys (
                        tenant_id, external_id, ticket_id, client_id,
                        question_id, value, commentary, payload
                    ) VALUES (
                        %(tenant_id)s, %(external_id)s, %(ticket_id)s, %(client_id)s,
                        %(question_id)s, %(value)s, %(commentary)s, %(payload)s
                    )
                    ON CONFLICT (tenant_id, external_id)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        commentary = EXCLUDED.commentary,
                        payload = EXCLUDED.payload,
                        ingested_at = NOW()
                """, {
                    "tenant_id": tenant_id,
                    "external_id": s_id,
                    "ticket_id": s.get("ticketId"),
                    "client_id": str(s.get("clientId", "")),
                    "question_id": str(s.get("questionId", "")),
                    "value": s.get("value"),
                    "commentary": s.get("commentary"),
                    "payload": json.dumps(s, ensure_ascii=False, default=str),
                })
                count += 1

            conn.commit()
            total_surveys += count
            page += 1
            starting_after = items[-1].get("id")
            console.print(f"    ⭐ Página {page:>3} | {count:>3} avaliações salvas | Total: {total_surveys:,}")

            if isinstance(data, dict) and not data.get("hasMore", False):
                break
            if len(items) < limit:
                break

            time.sleep(0.1)

    console.print(f"  ✅ {total_surveys} avaliações de satisfação ingeridas com sucesso!")
    return total_surveys


# ---------------------------------------------------------------------------
# Execução Principal
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Ingestão Completa de Metadados e KB Movidesk")
    parser.add_argument("--only-kb", action="store_true", help="Ingerir apenas Base de Conhecimento")
    parser.add_argument("--only-services", action="store_true", help="Ingerir apenas Serviços")
    parser.add_argument("--only-persons", action="store_true", help="Ingerir apenas Pessoas/Organizações")
    parser.add_argument("--only-surveys", action="store_true", help="Ingerir apenas Pesquisas de Satisfação")
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Ingestão Completa de Metadados & KB Movidesk [/]",
        border_style="blue",
    ))

    with get_db_connection() as conn:
        tenant_id = get_tenant_id(conn)
        console.print(f"  🏢 Tenant: Alternativa Sistemas ({tenant_id})\n")

        run_all = not (args.only_kb or args.only_services or args.only_persons or args.only_surveys)

        # 1. Serviços
        if run_all or args.only_services:
            ingest_services(conn, tenant_id)

        # 2. Base de Conhecimento
        if run_all or args.only_kb:
            asyncio.run(ingest_kb_articles(tenant_id))

        # 3. Pessoas / Clientes
        if run_all or args.only_persons:
            ingest_persons(conn, tenant_id)

        # 4. Pesquisas de Satisfação
        if run_all or args.only_surveys:
            ingest_surveys(conn, tenant_id)

    # Resumo final
    with get_db_connection() as conn:
        console.print()
        console.rule("[bold green]Resumo Final do Banco de Dados (DeskCortex)")

        summary = Table(show_lines=True)
        summary.add_column("Entidade", style="cyan", width=30)
        summary.add_column("Tabela PostgreSQL", style="white", width=25)
        summary.add_column("Total de Registros", style="green", justify="right", width=20)

        tables = [
            ("Base de Conhecimento (Artigos)", "raw_kb_articles"),
            ("Catálogo de Serviços", "raw_services"),
            ("Pessoas / Clientes / Contatos", "raw_persons"),
            ("Pesquisas de Satisfação", "raw_surveys"),
            ("Tickets / Chamados de Suporte", "raw_tickets"),
        ]

        for label, tbl in tables:
            count = conn.execute(f"SELECT COUNT(*) as c FROM {tbl}").fetchone()["c"]
            summary.add_row(label, tbl, f"{count:,}")

        console.print(summary)
        console.print("\n[bold green]✅ Ingestão de metadados e Base de Conhecimento concluída![/]\n")


if __name__ == "__main__":
    main()
