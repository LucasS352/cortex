"""
DeskCortex — Teste Escalonado de Rate Limit da API Movidesk
==============================================================
Testa a capacidade real da API em diferentes níveis de concorrência.

Executa 4 rodadas progressivas:
  Rodada 1: 1 worker  → 10 requests sequenciais
  Rodada 2: 2 workers → 20 requests concorrentes
  Rodada 3: 4 workers → 20 requests concorrentes
  Rodada 4: 8 workers → 20 requests concorrentes

Para cada rodada mede:
  - Tempo total
  - Requests por minuto reais
  - Quantidade de 429 (rate limit)
  - Latência média por request

Uso:
    python test_rate_limit.py
"""

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
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

sys.path.insert(0, str(Path(__file__).parent))
from config import MOVIDESK_TOKEN, MOVIDESK_BASE_URL

console = Console(force_terminal=True)


@dataclass
class RoundResult:
    workers: int
    total_requests: int
    successful: int = 0
    rate_limited: int = 0
    errors: int = 0
    total_time_seconds: float = 0.0
    latencies_ms: list = field(default_factory=list)

    @property
    def requests_per_minute(self) -> float:
        if self.total_time_seconds < 0.1:
            return 0
        return (self.successful / self.total_time_seconds) * 60

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def safe(self) -> bool:
        return self.rate_limited == 0


async def do_request(client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
                      result: RoundResult, request_num: int):
    """Executa uma request mínima (GET /tickets?$top=1&$select=id)."""
    url = (
        f"{MOVIDESK_BASE_URL}/tickets"
        f"?token={MOVIDESK_TOKEN}"
        f"&$top=1&$select=id"
    )
    async with semaphore:
        start = time.perf_counter()
        try:
            resp = await client.get(url)
            latency = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                result.successful += 1
                result.latencies_ms.append(latency)
            elif resp.status_code == 429:
                result.rate_limited += 1
                console.print(f"    ⚠️  429 na request #{request_num}")
            else:
                result.errors += 1
                console.print(f"    ❌ HTTP {resp.status_code} na request #{request_num}")

        except Exception as e:
            result.errors += 1
            console.print(f"    ❌ Erro na request #{request_num}: {e}")


async def run_round(workers: int, total_requests: int) -> RoundResult:
    """Executa uma rodada de testes com N workers concorrentes."""
    result = RoundResult(workers=workers, total_requests=total_requests)
    semaphore = asyncio.Semaphore(workers)

    console.print(f"\n  🏁 Rodada: {workers} worker(s) × {total_requests} requests")

    start = time.perf_counter()

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            do_request(client, semaphore, result, i + 1)
            for i in range(total_requests)
        ]
        await asyncio.gather(*tasks)

    result.total_time_seconds = time.perf_counter() - start

    status = "✅ SEGURO" if result.safe else "⚠️  RATE LIMITED"
    console.print(
        f"    {status} | "
        f"{result.successful}/{total_requests} OK | "
        f"{result.rate_limited} 429s | "
        f"{result.requests_per_minute:.0f} req/min | "
        f"Lat média: {result.avg_latency_ms:.0f}ms | "
        f"Tempo: {result.total_time_seconds:.1f}s"
    )

    return result


async def run_all_tests():
    """Executa todas as rodadas de teste de rate limit."""

    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Teste de Rate Limit Escalonado [/]",
        border_style="blue",
    ))
    console.print(f"  🔑 Token: {MOVIDESK_TOKEN[:8]}...{MOVIDESK_TOKEN[-4:]}")
    console.print(f"  🌐 Base URL: {MOVIDESK_BASE_URL}")
    console.print()

    rounds_config = [
        (1, 10),    # 1 worker,  10 requests
        (2, 20),    # 2 workers, 20 requests
        (4, 20),    # 4 workers, 20 requests
        (8, 20),    # 8 workers, 20 requests
    ]

    results: list[RoundResult] = []

    for workers, requests in rounds_config:
        result = await run_round(workers, requests)
        results.append(result)

        # Se tomou 429, avisa e espera um pouco antes da próxima rodada
        if result.rate_limited > 0:
            console.print("    ⏳ Aguardando 70s para resetar o rate limit antes da próxima rodada...")
            await asyncio.sleep(70)
        else:
            # Pausa curta entre rodadas
            await asyncio.sleep(5)

    # Tabela de resultados
    console.print()
    console.rule("[bold green]Resultados do Teste de Rate Limit")

    table = Table(show_lines=True, title="Throughput por Nível de Concorrência")
    table.add_column("Workers", style="cyan", justify="center", width=10)
    table.add_column("Requests", justify="center", width=10)
    table.add_column("OK", style="green", justify="center", width=6)
    table.add_column("429s", justify="center", width=6)
    table.add_column("Erros", justify="center", width=6)
    table.add_column("Req/min", style="bold", justify="center", width=10)
    table.add_column("Lat Média", justify="center", width=12)
    table.add_column("Tempo", justify="center", width=10)
    table.add_column("Status", justify="center", width=15)

    max_safe_workers = 1  # Default mínimo

    for r in results:
        status_style = "green" if r.safe else "red"
        status_text = "✅ SEGURO" if r.safe else "⚠️ LIMITADO"

        if r.safe:
            max_safe_workers = r.workers

        table.add_row(
            str(r.workers),
            str(r.total_requests),
            str(r.successful),
            str(r.rate_limited) if r.rate_limited > 0 else "-",
            str(r.errors) if r.errors > 0 else "-",
            f"{r.requests_per_minute:.0f}",
            f"{r.avg_latency_ms:.0f}ms",
            f"{r.total_time_seconds:.1f}s",
            f"[{status_style}]{status_text}[/]",
        )

    console.print(table)

    # Recomendação
    console.print()
    console.print(f"[bold green]📊 Recomendação: use no máximo {max_safe_workers} worker(s) "
                   f"concorrentes para evitar rate limiting.[/]")

    if max_safe_workers >= 4:
        est_time = (300_000 / (max_safe_workers * 100 * 60 / 6.7))  # minutos estimados
        console.print(f"   ⏱ Tempo estimado para 300k tickets: ~{est_time:.0f} minutos")
    else:
        est_time = 300_000 / 1000  # ~1000 tickets/min no modo comercial
        console.print(f"   ⏱ Tempo estimado para 300k tickets: ~{est_time:.0f} minutos "
                       f"(~{est_time / 60:.1f} horas)")

    console.print(f"\n💡 Atualize RATE_TURBO.max_concurrent em config.py para {max_safe_workers}")


def main():
    if not MOVIDESK_TOKEN:
        console.print("[bold red]❌ MOVIDESK_TOKEN não configurado no .env![/]")
        sys.exit(1)

    asyncio.run(run_all_tests())


if __name__ == "__main__":
    main()
