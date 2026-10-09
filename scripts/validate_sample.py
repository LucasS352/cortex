"""
DeskCortex — Validador de Amostra de Calibração (500 Tickets)
===============================================================
Permite inspecionar detalhadamente a amostra estratificada de calibração:
  - 100 GOLD_frontier (Score 55-65)
  - 100 SILVER_frontier (Score 35-45)
  - 100 REVIEW_frontier (PRs reprovados e diagnósticos)
  - 100 IGNORE_frontier (Ruído e inatividade)
  - 100 RANDOM_control (Amostra de controle geral)

Gera um relatório visual no console e exporta um arquivo HTML navegável para validação humana.

Uso:
    python validate_sample.py --html
    python validate_sample.py --tier GOLD_frontier --count 10
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

# Forçar UTF-8 no Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config import OUTPUT_DIR

console = Console(force_terminal=True)


def load_calibration_sample():
    sample_file = OUTPUT_DIR / "calibration_sample_500.json"
    if not sample_file.exists():
        console.print(f"[bold red]❌ Arquivo {sample_file} não encontrado![/]")
        sys.exit(1)
    with open(sample_file, "r", encoding="utf-8") as f:
        return json.load(f)


def inspect_tier(tier_name: str, count: int = 10):
    sample_data = load_calibration_sample()
    tickets = sample_data.get(tier_name, [])

    if not tickets:
        console.print(f"[bold red]❌ Grupo {tier_name} vazio ou não encontrado![/]")
        return

    console.rule(f"[bold cyan]Inspecionando {min(count, len(tickets))} tickets de: {tier_name}")

    for i, t in enumerate(tickets[:count], 1):
        ext_id = t["external_id"]
        subj = t["subject"]
        serv = t["service_full"]
        tech = t["technical_score"]
        sol = t["solution_score"]
        val = t["validation_score"]
        final = t["final_score"]
        tier = t["quality_tier"]
        ev_lvl = t.get("evidence_level", "NONE")
        block = t.get("hard_block_reason")

        prob = t.get("problem_statement") or "N/A"
        sol_text = t.get("solution_statement") or "N/A"

        content = (
            f"[bold yellow]Assunto:[/] {subj}\n"
            f"[bold yellow]Serviço:[/] {serv}\n"
            f"[bold yellow]Scores:[/] Tech: {tech} | Sol: {sol} | Val: {val} | Final: [bold green]{final}[/] | Tier: [bold cyan]{tier}[/]\n"
            f"[bold yellow]Evidência:[/] {ev_lvl} | [bold yellow]Hard Block:[/] {block or 'Nenhum'}\n\n"
            f"[bold cyan]── Relato do Cliente (Problema):──[/]\n{prob[:300]}...\n\n"
            f"[bold green]── Ação do Suporte (Solução):──[/]\n{sol_text[:300]}..."
        )

        console.print(Panel(content, title=f"#{ext_id} (Amostra {i}/{count})", border_style="blue"))


def generate_html_viewer():
    """Gera um visualizador HTML interativo para validar os 500 tickets no navegador."""
    sample_data = load_calibration_sample()
    html_file = OUTPUT_DIR / "calibration_viewer_500.html"

    html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>DeskCortex — Validador de Calibração (500 Chamados)</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }}
        h1 {{ color: #38bdf8; text-align: center; margin-bottom: 5px; }}
        .subtitle {{ text-align: center; color: #94a3b8; margin-bottom: 25px; }}
        .tabs {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; }}
        .tab-btn {{ background: #1e293b; border: 1px solid #334155; color: #f8fafc; padding: 10px 20px; border-radius: 8px; cursor: pointer; font-weight: bold; }}
        .tab-btn.active {{ background: #2563eb; border-color: #3b82f6; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 15px; margin-bottom: 15px; }}
        .badge {{ padding: 3px 8px; border-radius: 5px; font-weight: bold; font-size: 12px; }}
        .badge-gold {{ background: #854d0e; color: #fef08a; }}
        .badge-silver {{ background: #475569; color: #e2e8f0; }}
        .badge-review {{ background: #713f12; color: #fed7aa; }}
        .badge-ignore {{ background: #7f1d1d; color: #fecaca; }}
        .field {{ margin-bottom: 8px; }}
        .field-label {{ font-weight: bold; color: #38bdf8; }}
        .box-problem {{ background: #0f172a; border-left: 4px solid #f59e0b; padding: 10px; border-radius: 4px; margin-top: 5px; white-space: pre-wrap; font-size: 13px; }}
        .box-solution {{ background: #0f172a; border-left: 4px solid #10b981; padding: 10px; border-radius: 4px; margin-top: 5px; white-space: pre-wrap; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>DeskCortex — Validador de Calibração (500 Chamados)</h1>
    <div class="subtitle">Amostra Estratificada com Casos de Fronteira e Controle Aleatório para Auditoria Humana</div>

    <div class="tabs">
        <button class="tab-btn active" onclick="showTab('GOLD_frontier')">🌟 GOLD Fronteira (100)</button>
        <button class="tab-btn" onclick="showTab('SILVER_frontier')">🥈 SILVER Fronteira (100)</button>
        <button class="tab-btn" onclick="showTab('REVIEW_frontier')">🔴 REVIEW / Bloqueios (100)</button>
        <button class="tab-btn" onclick="showTab('IGNORE_frontier')">🚫 IGNORE Fronteira (100)</button>
        <button class="tab-btn" onclick="showTab('RANDOM_control')">🎲 RANDOM Controle (100)</button>
    </div>
"""

    for group_name, tickets in sample_data.items():
        is_active = "active" if group_name == "GOLD_frontier" else ""
        html_content += f'<div id="{group_name}" class="tab-content {is_active}">'

        for i, t in enumerate(tickets, 1):
            tier = t.get("quality_tier", "REVIEW")
            badge_class = f"badge-{tier.lower()}"
            ext_id = t["external_id"]
            subj = html.escape(str(t.get("subject", "")))
            serv = html.escape(str(t.get("service_full", "")))
            tech = t.get("technical_score", 0)
            sol = t.get("solution_score", 0)
            val = t.get("validation_score", 0)
            final = t.get("final_score", 0)
            ev = t.get("evidence_level", "NONE")
            block = t.get("hard_block_reason") or "Nenhum"
            prob = html.escape(str(t.get("problem_statement", "")))
            sol_text = html.escape(str(t.get("solution_statement", "")))

            html_content += f"""
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <span style="font-size:16px; font-weight:bold; color:#f8fafc;">#{ext_id} — {subj}</span>
                    <div>
                        <span class="badge {badge_class}">{tier} (Score: {final})</span>
                        <span class="badge" style="background:#334155;">Evidência: {ev}</span>
                        {f'<span class="badge" style="background:#ef4444;">Bloqueio: {block}</span>' if block != 'Nenhum' else ''}
                    </div>
                </div>
                <div class="field"><span class="field-label">Serviço:</span> {serv} | <span class="field-label">Scores:</span> Tech: {tech} | Solução: {sol} | Validação: {val}</div>
                <div class="field-label" style="color:#f59e0b;">Relato do Cliente (Problema):</div>
                <div class="box-problem">{prob}</div>
                <div class="field-label" style="color:#10b981; margin-top:10px;">Ação / Conclusão do Suporte (Solução):</div>
                <div class="box-solution">{sol_text}</div>
            </div>
            """

        html_content += "</div>"

    html_content += """
    <script>
        function showTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            event.target.classList.add('active');
        }
    </script>
</body>
</html>
"""

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    console.print(f"🌐 Visualizador HTML interativo gerado em: [bold green]{html_file}[/]")


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Validador de Calibração")
    parser.add_argument("--tier", type=str, default="GOLD_frontier", help="Grupo para inspecionar")
    parser.add_argument("--count", type=int, default=5, help="Quantidade de tickets a exibir")
    parser.add_argument("--html", action="store_true", help="Gerar visualizador HTML")
    args = parser.parse_args()

    inspect_tier(args.tier, args.count)
    if args.html or True:
        generate_html_viewer()


if __name__ == "__main__":
    main()
