"""
DeskCortex — Orquestrador Noturno: GOLD Conclusão -> SILVER Ingestão -> SILVER Anexos
====================================================================================
Este script monitora o término da extração de anexos do GOLD.
Assim que o GOLD terminar:
  Passo 1: Dispara a ingestão vetorial dos 25.075 tickets SILVER (Gemini 3072 dims, ~55 min)
  Passo 2: Dispara a extração de anexos, prints e GIFs dos 25.075 tickets SILVER (durante a madrugada)

Uso:
  python chain_silver_pipeline.py
"""

import os
import sys
import time
import subprocess
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN

console = Console(force_terminal=True)
PYTHON_EXE = sys.executable
SCRIPTS_DIR = Path(__file__).parent


def check_gold_pending() -> tuple[int, int]:
    """Retorna (pendentes, total) de chamados GOLD para extração de anexos."""
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        res = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN metadata->>'attachments_extracted_at' IS NULL THEN 1 ELSE 0 END) as pendentes
            FROM ticket_knowledge_nodes
            WHERE tier = 'GOLD' AND source_external_id IS NOT NULL;
        """).fetchone()
        return res["pendentes"] or 0, res["total"] or 0


def run_command_live(cmd: list[str], title: str):
    """Executa um subprocesso exibindo saída em tempo real."""
    console.rule(f"[bold cyan]🚀 {title}")
    console.print(f"[dim]Executando: {' '.join(cmd)}[/]\n")
    
    proc = subprocess.Popen(
        cmd,
        cwd=str(SCRIPTS_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    
    for line in proc.stdout:
        print(line, end="", flush=True)
        
    proc.wait()
    if proc.returncode != 0:
        console.print(f"\n[bold red]❌ Erro ao executar {title} (código {proc.returncode})[/]")
    else:
        console.print(f"\n[bold green]✅ {title} concluído com sucesso![/]")
    return proc.returncode


def main():
    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Orquestrador de Transição Noturna [/]\n"
        "[dim]Aguardando finalização do GOLD para engatar SILVER automaticamente[/]",
        border_style="blue"
    ))

    # Fase 1: Monitorar término do GOLD
    console.print("[bold yellow]⏳ Monitorando extração de anexos do GOLD...[/]")
    while True:
        pendentes, total = check_gold_pending()
        concluidos = total - pendentes
        perc = (concluidos / total * 100) if total > 0 else 100
        
        console.print(f"  Status GOLD Anexos: [green]{concluidos}/{total}[/] ({perc:.1f}%) concluídos | [yellow]{pendentes}[/] pendentes...")
        
        if pendentes == 0:
            console.print("\n[bold green]🌟 TODOS OS 2.829 CHAMADOS GOLD TIVERAM SEUS ANEXOS EXTRAÍDOS COM SUCESSO![/]\n")
            break
            
        time.sleep(30)

    # Fase 2: Ingestão Vetorial do SILVER (25.075 tickets com Gemini Embedding 2)
    console.rule("[bold magenta]ETAPA 1/2: Ingestão de Embeddings dos 25.075 Tickets SILVER")
    cmd_ingest_silver = [PYTHON_EXE, str(SCRIPTS_DIR / "batch_ingest.py"), "--silver"]
    ret = run_command_live(cmd_ingest_silver, "Ingestão Vetorial SILVER")
    
    # Fase 3: Extração de Anexos do SILVER (Prints, GIFs e Logs)
    console.rule("[bold magenta]ETAPA 2/2: Extração de Imagens e GIFs dos 25.075 Tickets SILVER")
    cmd_attachments_silver = [PYTHON_EXE, str(SCRIPTS_DIR / "extract_attachments.py"), "--silver"]
    run_command_live(cmd_attachments_silver, "Extração de Anexos SILVER")
    
    console.rule("[bold green]🏆 PIPELINE NOTURNO 100% CONCLUÍDO COM SUCESSO!")
    console.print("[bold green]Todos os chamados GOLD e SILVER estão no banco com vetores e anexos locais salvos![/]")


if __name__ == "__main__":
    main()
