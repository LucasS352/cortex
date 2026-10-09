"""
DeskCortex — Probe de Sondagem da API Movidesk
=================================================
Script leve que valida o token e coleta metadados da API.

Executa 5 testes sequenciais:
  1. Token Alive    → GET /tickets?$top=1&$select=id
  2. Services       → GET /services
  3. Custom Fields  → GET /customFields (tentativa, pode não existir como endpoint GET)
  4. Recent Ticket  → GET /tickets?$top=1 (com $expand completo)
  5. Past Ticket    → GET /tickets/past?$top=1 (com $expand completo)

Resultados são exibidos no terminal (rich) e salvos em scripts/output/.

Uso:
    python probe_api.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Forçar UTF-8 no console Windows para suporte a emojis
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich import print as rprint

# Importação do módulo de configuração local
sys.path.insert(0, str(Path(__file__).parent))
from config import (
    MOVIDESK_TOKEN,
    MOVIDESK_BASE_URL,
    ODATA_SELECT_FIELDS,
    ODATA_EXPAND,
    OUTPUT_DIR,
    is_turbo_window,
    get_rate_config,
)

console = Console(force_terminal=True)


def build_url(endpoint: str, params: dict | None = None) -> str:
    """Constrói URL completa com token e parâmetros OData."""
    base = f"{MOVIDESK_BASE_URL}{endpoint}?token={MOVIDESK_TOKEN}"
    if params:
        for key, value in params.items():
            base += f"&{key}={value}"
    return base


def save_json(filename: str, data) -> Path:
    """Salva dados como JSON formatado na pasta output/."""
    filepath = OUTPUT_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return filepath


def probe_token_alive(client: httpx.Client) -> dict:
    """Teste 1: Valida se o token está ativo com uma requisição mínima."""
    console.rule("[bold cyan]Teste 1: Token Alive")

    url = build_url("/tickets", {"$top": "1", "$select": "id"})
    start = time.perf_counter()
    resp = client.get(url)
    duration = (time.perf_counter() - start) * 1000

    result = {
        "test": "Token Alive",
        "endpoint": "/tickets?$top=1&$select=id",
        "status_code": resp.status_code,
        "duration_ms": round(duration),
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        result["tickets_returned"] = len(data) if isinstance(data, list) else "N/A"
        console.print(f"  ✅ Token válido! Status: {resp.status_code} | "
                       f"Tickets retornados: {result['tickets_returned']} | "
                       f"Tempo: {result['duration_ms']}ms")
    else:
        result["error"] = resp.text[:500]
        console.print(f"  ❌ Falha! Status: {resp.status_code}")
        console.print(f"  Resposta: {resp.text[:300]}")

    return result


def probe_services(client: httpx.Client) -> dict:
    """Teste 2: Lista serviços cadastrados no Movidesk."""
    console.rule("[bold cyan]Teste 2: Serviços (/services)")

    url = build_url("/services", {"$top": "100", "$select": "id,name,parentServiceId,isVisible"})
    start = time.perf_counter()
    resp = client.get(url)
    duration = (time.perf_counter() - start) * 1000

    result = {
        "test": "Services",
        "endpoint": "/services",
        "status_code": resp.status_code,
        "duration_ms": round(duration),
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        save_json("services.json", data)
        count = len(data) if isinstance(data, list) else "N/A"
        result["count"] = count

        console.print(f"  ✅ {count} serviços encontrados | Tempo: {result['duration_ms']}ms")
        console.print(f"  💾 Salvo em: {OUTPUT_DIR / 'services.json'}")

        # Exibir tabela dos serviços
        if isinstance(data, list) and len(data) > 0:
            table = Table(title="Serviços do Movidesk", show_lines=True)
            table.add_column("ID", style="cyan", width=8)
            table.add_column("Nome", style="white", width=50)
            table.add_column("Parent", style="dim", width=8)
            table.add_column("Visível", style="green", width=8)

            for svc in data[:30]:  # Mostrar até 30
                table.add_row(
                    str(svc.get("id", "")),
                    str(svc.get("name", "")),
                    str(svc.get("parentServiceId", "")),
                    "✅" if svc.get("isVisible") else "❌",
                )
            if len(data) > 30:
                table.add_row("...", f"(+{len(data) - 30} serviços)", "", "")
            console.print(table)
    else:
        result["error"] = resp.text[:500]
        console.print(f"  ❌ Falha! Status: {resp.status_code}")
        console.print(f"  Resposta: {resp.text[:300]}")

    return result


def probe_custom_fields(client: httpx.Client) -> dict:
    """Teste 3: Tenta listar campos personalizados.

    Nota: O endpoint /customFields pode não existir como GET direto.
    Os custom fields são tipicamente acessados via $expand nos tickets.
    Tentamos o endpoint direto e, se falhar, explicamos a alternativa.
    """
    console.rule("[bold cyan]Teste 3: Campos Personalizados")

    # Tentativa 1: endpoint direto
    url = build_url("/customFields", {"$top": "100"})
    start = time.perf_counter()
    resp = client.get(url)
    duration = (time.perf_counter() - start) * 1000

    result = {
        "test": "Custom Fields",
        "endpoint": "/customFields",
        "status_code": resp.status_code,
        "duration_ms": round(duration),
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        save_json("custom_fields.json", data)
        count = len(data) if isinstance(data, list) else "N/A"
        result["count"] = count

        console.print(f"  ✅ {count} campos personalizados encontrados | Tempo: {result['duration_ms']}ms")
        console.print(f"  💾 Salvo em: {OUTPUT_DIR / 'custom_fields.json'}")

        if isinstance(data, list) and len(data) > 0:
            table = Table(title="Campos Personalizados", show_lines=True)
            table.add_column("ID", style="cyan", width=8)
            table.add_column("Nome", style="white", width=40)
            table.add_column("Tipo", style="yellow", width=15)

            for field in data[:20]:
                table.add_row(
                    str(field.get("id", "")),
                    str(field.get("name", field.get("title", ""))),
                    str(field.get("type", field.get("fieldType", ""))),
                )
            console.print(table)
    else:
        console.print(f"  ⚠️  Endpoint /customFields retornou {resp.status_code}")
        console.print("  ℹ️  Os campos personalizados serão extraídos via $expand=customFieldValues nos tickets.")
        result["note"] = "Custom fields serão extraídos via $expand nos tickets"

    return result


def probe_recent_ticket(client: httpx.Client) -> dict:
    """Teste 4: Busca 1 ticket recente com payload completo ($expand)."""
    console.rule("[bold cyan]Teste 4: Amostra de Ticket Recente (/tickets)")

    url = build_url("/tickets", {
        "$top": "1",
        "$select": ODATA_SELECT_FIELDS,
        "$expand": ODATA_EXPAND,
        "$orderby": "lastUpdate desc",
    })
    start = time.perf_counter()
    resp = client.get(url)
    duration = (time.perf_counter() - start) * 1000

    result = {
        "test": "Recent Ticket Sample",
        "endpoint": "/tickets (recente)",
        "status_code": resp.status_code,
        "duration_ms": round(duration),
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        save_json("sample_ticket.json", data)

        if isinstance(data, list) and len(data) > 0:
            ticket = data[0]
            result["ticket_id"] = ticket.get("id")
            result["subject"] = ticket.get("subject", "")[:80]
            result["status"] = ticket.get("status")
            result["baseStatus"] = ticket.get("baseStatus")
            result["actionCount"] = ticket.get("actionCount", 0)
            result["has_actions"] = "actions" in ticket and len(ticket.get("actions", [])) > 0
            result["has_clients"] = "clients" in ticket and len(ticket.get("clients", [])) > 0
            result["has_customFields"] = "customFieldValues" in ticket and len(ticket.get("customFieldValues", [])) > 0
            result["payload_size_bytes"] = len(json.dumps(ticket, ensure_ascii=False))

            console.print(f"  ✅ Ticket #{ticket.get('id')} | Tempo: {result['duration_ms']}ms")
            console.print(f"  📋 Assunto: {result['subject']}")
            console.print(f"  📊 Status: {result['status']} ({result['baseStatus']})")
            console.print(f"  💬 Ações: {len(ticket.get('actions', []))} (actionCount={result['actionCount']})")
            console.print(f"  👥 Clientes: {'✅' if result['has_clients'] else '❌'} | "
                           f"Custom Fields: {'✅' if result['has_customFields'] else '❌'}")
            console.print(f"  📦 Tamanho do payload: {result['payload_size_bytes']:,} bytes")
            console.print(f"  💾 Salvo em: {OUTPUT_DIR / 'sample_ticket.json'}")

            # Mostrar preview do JSON (primeiros campos)
            preview = json.dumps(ticket, ensure_ascii=False, indent=2, default=str)
            if len(preview) > 2000:
                preview = preview[:2000] + "\n  ... (truncado)"
            syntax = Syntax(preview, "json", theme="monokai", line_numbers=False)
            console.print(Panel(syntax, title=f"Ticket #{ticket.get('id')} (preview)", border_style="green"))
        else:
            console.print("  ⚠️  Nenhum ticket retornado (base pode estar vazia nos últimos 90 dias)")
    else:
        result["error"] = resp.text[:500]
        console.print(f"  ❌ Falha! Status: {resp.status_code}")
        console.print(f"  Resposta: {resp.text[:300]}")

    return result


def probe_past_ticket(client: httpx.Client) -> dict:
    """Teste 5: Busca 1 ticket histórico com payload completo ($expand)."""
    console.rule("[bold cyan]Teste 5: Amostra de Ticket Histórico (/tickets/past)")

    url = build_url("/tickets/past", {
        "$top": "1",
        "$select": ODATA_SELECT_FIELDS,
        "$expand": ODATA_EXPAND,
    })
    start = time.perf_counter()
    resp = client.get(url)
    duration = (time.perf_counter() - start) * 1000

    result = {
        "test": "Past Ticket Sample",
        "endpoint": "/tickets/past (histórico)",
        "status_code": resp.status_code,
        "duration_ms": round(duration),
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        save_json("sample_past_ticket.json", data)

        if isinstance(data, list) and len(data) > 0:
            ticket = data[0]
            result["ticket_id"] = ticket.get("id")
            result["subject"] = ticket.get("subject", "")[:80]
            result["status"] = ticket.get("status")
            result["baseStatus"] = ticket.get("baseStatus")
            result["has_actions"] = "actions" in ticket and len(ticket.get("actions", [])) > 0
            result["has_clients"] = "clients" in ticket and len(ticket.get("clients", [])) > 0
            result["has_customFields"] = "customFieldValues" in ticket and len(ticket.get("customFieldValues", [])) > 0
            result["payload_size_bytes"] = len(json.dumps(ticket, ensure_ascii=False))

            console.print(f"  ✅ Ticket Histórico #{ticket.get('id')} | Tempo: {result['duration_ms']}ms")
            console.print(f"  📋 Assunto: {result['subject']}")
            console.print(f"  📊 Status: {result['status']} ({result['baseStatus']})")
            console.print(f"  💬 Ações: {len(ticket.get('actions', []))}")
            console.print(f"  👥 Clientes: {'✅' if result['has_clients'] else '❌'} | "
                           f"Custom Fields: {'✅' if result['has_customFields'] else '❌'}")
            console.print(f"  📦 Tamanho do payload: {result['payload_size_bytes']:,} bytes")
            console.print(f"  💾 Salvo em: {OUTPUT_DIR / 'sample_past_ticket.json'}")
        else:
            console.print("  ⚠️  Nenhum ticket histórico retornado")
    else:
        result["error"] = resp.text[:500]
        console.print(f"  ❌ Falha! Status: {resp.status_code}")
        console.print(f"  Resposta: {resp.text[:300]}")

    return result


def probe_ticket_count(client: httpx.Client) -> dict:
    """Teste Bônus: Tenta estimar o total de tickets via paginação."""
    console.rule("[bold cyan]Teste Bônus: Estimativa de Volume")

    # Pega últimos tickets para ver o range de IDs
    url = build_url("/tickets", {
        "$top": "1",
        "$select": "id",
        "$orderby": "id desc",
    })
    resp = client.get(url)

    result = {
        "test": "Volume Estimate",
        "success": resp.status_code == 200,
    }

    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            max_id = data[0].get("id", 0)
            result["max_ticket_id"] = max_id
            console.print(f"  📊 Maior ID de ticket recente: #{max_id}")
            console.print(f"  📐 Estimativa grosseira de volume: ~{max_id:,} tickets")
            console.print("     (IDs podem não ser sequenciais — volume real descoberto na ingestão)")

    return result


def main():
    """Executa todos os testes de sondagem da API Movidesk."""

    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Probe de Sondagem da API Movidesk [/]",
        border_style="blue",
    ))

    # Validação de configuração
    if not MOVIDESK_TOKEN:
        console.print("[bold red]❌ MOVIDESK_TOKEN não configurado no .env![/]")
        sys.exit(1)

    console.print(f"  🔑 Token: {MOVIDESK_TOKEN[:8]}...{MOVIDESK_TOKEN[-4:]}")
    console.print(f"  🌐 Base URL: {MOVIDESK_BASE_URL}")
    console.print(f"  ⏰ Janela: {'🚀 TURBO (noturno)' if is_turbo_window() else '🏢 Comercial'}")
    console.print(f"  📁 Output: {OUTPUT_DIR}")
    console.print()

    results = []

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # Teste 1: Token
        results.append(probe_token_alive(client))
        if not results[-1]["success"]:
            console.print("\n[bold red]⛔ Token inválido! Abortando demais testes.[/]")
            save_json("probe_results.json", results)
            sys.exit(1)
        time.sleep(1)

        # Teste 2: Serviços
        results.append(probe_services(client))
        time.sleep(1)

        # Teste 3: Campos Personalizados
        results.append(probe_custom_fields(client))
        time.sleep(1)

        # Teste 4: Ticket Recente
        results.append(probe_recent_ticket(client))
        time.sleep(1)

        # Teste 5: Ticket Histórico
        results.append(probe_past_ticket(client))
        time.sleep(1)

        # Bônus: Estimativa de volume
        results.append(probe_ticket_count(client))

    # Salvar todos os resultados
    save_json("probe_results.json", results)

    # Sumário final
    console.print()
    console.rule("[bold green]Resumo da Sondagem")
    summary_table = Table(show_lines=True)
    summary_table.add_column("#", style="cyan", width=3)
    summary_table.add_column("Teste", style="white", width=25)
    summary_table.add_column("Status", width=8)
    summary_table.add_column("HTTP", width=6)
    summary_table.add_column("Tempo", width=10)
    summary_table.add_column("Detalhes", style="dim", width=40)

    for i, r in enumerate(results, 1):
        status = "✅" if r.get("success") else "❌"
        http_code = str(r.get("status_code", "-"))
        duration = f"{r.get('duration_ms', '-')}ms" if "duration_ms" in r else "-"
        details = ""
        if "count" in r:
            details = f"{r['count']} itens"
        elif "ticket_id" in r:
            details = f"#{r['ticket_id']} | {r.get('payload_size_bytes', 0):,} bytes"
        elif "max_ticket_id" in r:
            details = f"Max ID: #{r['max_ticket_id']}"
        elif "note" in r:
            details = r["note"][:40]

        summary_table.add_row(str(i), r["test"], status, http_code, duration, details)

    console.print(summary_table)
    console.print(f"\n💾 Resultados completos salvos em: {OUTPUT_DIR / 'probe_results.json'}")
    console.print("[bold green]✅ Probe concluído![/]\n")


if __name__ == "__main__":
    main()
