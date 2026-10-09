"""
DeskCortex — Auditoria Estatística Profunda da Base de Dados (105k Tickets)
=============================================================================
Fotografia estatística completa e de altíssima performance:
  1. Panorama geral das tabelas
  2. Distribuição por ano (2015 a 2026)
  3. Distribuição por status
  4. Profundidade das ações
  5. Termos técnicos do ERP Master
  6. Padrões de ruído vs soluções confirmadas

Uso:
    python audit_dataset.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Forçar UTF-8 no Windows
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
from config import POSTGRES_DSN, OUTPUT_DIR

console = Console(force_terminal=True)


def get_db_connection() -> psycopg.Connection:
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row)


def run_audit():
    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Auditoria Estatística da Base Bruta (105k Tickets) [/]",
        border_style="blue",
    ))

    report = {}
    t_start = time.perf_counter()

    with get_db_connection() as conn:
        total_tickets_row = conn.execute("SELECT COUNT(*) as c FROM raw_tickets").fetchone()
        total_tickets = total_tickets_row["c"]

        # -------------------------------------------------------------------
        # 1. Panorama Geral das Tabelas
        # -------------------------------------------------------------------
        console.rule("[bold cyan]1. Panorama Geral do Banco de Dados")
        tbl_query = """
            SELECT 'raw_tickets' as tabela, 'Chamados / Tickets de Suporte' as descricao, COUNT(*) as total,
                   pg_size_pretty(pg_total_relation_size('raw_tickets')) as tamanho
            FROM raw_tickets
            UNION ALL
            SELECT 'raw_kb_articles', 'Artigos Base de Conhecimento', COUNT(*), pg_size_pretty(pg_total_relation_size('raw_kb_articles')) FROM raw_kb_articles
            UNION ALL
            SELECT 'raw_persons', 'Pessoas / Clientes / Empresas', COUNT(*), pg_size_pretty(pg_total_relation_size('raw_persons')) FROM raw_persons
            UNION ALL
            SELECT 'raw_services', 'Catálogo de Serviços', COUNT(*), pg_size_pretty(pg_total_relation_size('raw_services')) FROM raw_services
            UNION ALL
            SELECT 'raw_surveys', 'Pesquisas de Satisfação', COUNT(*), pg_size_pretty(pg_total_relation_size('raw_surveys')) FROM raw_surveys;
        """
        rows = conn.execute(tbl_query).fetchall()
        report["overview"] = rows

        tbl = Table(show_lines=True)
        tbl.add_column("Tabela", style="cyan", width=22)
        tbl.add_column("Descrição", style="white", width=35)
        tbl.add_column("Total de Registros", style="bold green", justify="right", width=20)
        tbl.add_column("Tamanho em Disco", style="yellow", justify="right", width=18)
        for r in rows:
            tbl.add_row(r["tabela"], r["descricao"], f"{r['total']:,}", r["tamanho"])
        console.print(tbl)

        # -------------------------------------------------------------------
        # 2. Distribuição Temporal (2015 a 2026)
        # -------------------------------------------------------------------
        console.rule("[bold cyan]2. Distribuição de Chamados por Ano")
        year_query = """
            SELECT 
                EXTRACT(YEAR FROM (payload->>'createdDate')::timestamp)::int as ano,
                COUNT(*) as tickets,
                ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM raw_tickets), 2) as percentual
            FROM raw_tickets
            WHERE payload->>'createdDate' IS NOT NULL
            GROUP BY 1
            ORDER BY 1;
        """
        years = conn.execute(year_query).fetchall()
        report["years"] = years

        ytbl = Table(show_lines=True)
        ytbl.add_column("Ano", style="cyan", justify="center", width=15)
        ytbl.add_column("Total de Chamados", style="bold green", justify="right", width=25)
        ytbl.add_column("Percentual", style="yellow", justify="right", width=20)
        for y in years:
            ytbl.add_row(str(y["ano"]), f"{y['tickets']:,}", f"{y['percentual']}%")
        console.print(ytbl)

        # -------------------------------------------------------------------
        # 3. Distribuição de Status
        # -------------------------------------------------------------------
        console.rule("[bold cyan]3. Distribuição por Status Base")
        status_query = """
            SELECT 
                COALESCE(payload->>'baseStatus', 'Sem Status') as base_status,
                COUNT(*) as total,
                ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM raw_tickets), 2) as percentual
            FROM raw_tickets
            GROUP BY 1
            ORDER BY 2 DESC;
        """
        statuses = conn.execute(status_query).fetchall()
        report["statuses"] = statuses

        stbl = Table(show_lines=True)
        stbl.add_column("Status Base", style="cyan", width=25)
        stbl.add_column("Total", style="bold green", justify="right", width=20)
        stbl.add_column("Percentual", style="yellow", justify="right", width=20)
        for s in statuses:
            stbl.add_row(s["base_status"], f"{s['total']:,}", f"{s['percentual']}%")
        console.print(stbl)

        # -------------------------------------------------------------------
        # 4. Profundidade das Ações (Conversas)
        # -------------------------------------------------------------------
        console.rule("[bold cyan]4. Análise de Profundidade das Ações (Conversas)")
        action_query = """
            SELECT 
                CASE 
                    WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) = 0 THEN '0 ações (Vazio / Sem interação)'
                    WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) = 1 THEN '1 ação (Apenas relato inicial / sem resposta)'
                    WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) BETWEEN 2 AND 5 THEN '2 a 5 ações (Resolução rápida / direta)'
                    WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) BETWEEN 6 AND 15 THEN '6 a 15 ações (Atendimento técnico padrão)'
                    ELSE '> 15 ações (Incidente complexo / projeto longo)'
                END as faixa_acoes,
                COUNT(*) as total_tickets,
                ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM raw_tickets), 2) as percentual
            FROM raw_tickets
            GROUP BY 1
            ORDER BY total_tickets DESC;
        """
        actions = conn.execute(action_query).fetchall()
        report["actions_depth"] = actions

        atbl = Table(show_lines=True)
        atbl.add_column("Faixa de Ações", style="cyan", width=45)
        atbl.add_column("Total de Chamados", style="bold green", justify="right", width=20)
        atbl.add_column("Percentual", style="yellow", justify="right", width=15)
        for a in actions:
            atbl.add_row(a["faixa_acoes"], f"{a['total_tickets']:,}", f"{a['percentual']}%")
        console.print(atbl)

        # -------------------------------------------------------------------
        # 5. Frequência de Termos Técnicos do ERP Master (Single Pass Rápido)
        # -------------------------------------------------------------------
        console.rule("[bold cyan]5. Frequência de Termos Técnicos no Assunto / Módulos")
        tech_terms_defs = [
            ("Integração", "SINC", "sinc"),
            ("Integração", "Anymarket", "anymarket"),
            ("Integração", "Mercado Livre", "mercado livre"),
            ("Integração", "Shopee", "shopee"),
            ("Integração", "Nuvemshop", "nuvemshop"),
            ("Integração", "Sigep / Correios", "sigep"),
            ("Fiscal", "NF-e / Nota Fiscal", "nfe"),
            ("Fiscal", "NFC-e / Cupom", "nfce"),
            ("Fiscal", "Rejeição / Erro Sefaz", "rejei"),
            ("Fiscal", "CSOSN / CFOP", "csosn"),
            ("Fiscal", "SPED / Sintegra", "sped"),
            ("Fiscal", "Boleto / CNAB", "boleto"),
            ("Sistema / DB", "Estoque / Saldo", "estoque"),
            ("Sistema / DB", "Venda / Pedido", "venda"),
            ("Sistema / DB", "Firebird / Banco", "firebird"),
            ("Sistema / DB", "Erro 10061 (Conexão)", "10061"),
            ("Sistema / DB", "Access Violation", "access violation"),
            ("Desenvolvimento", "Melhoria (MEL)", "mel -"),
            ("Desenvolvimento", "Erro (ERR)", "err -"),
            ("Desenvolvimento", "Projeto (PRO)", "pro -"),
        ]

        term_cols = []
        for i, (cat, label, term) in enumerate(tech_terms_defs):
            term_cols.append(f"COUNT(CASE WHEN LOWER(payload->>'subject') LIKE '%{term}%' THEN 1 END) as t_{i}")

        fast_terms_query = f"SELECT {', '.join(term_cols)} FROM raw_tickets;"
        t_row = conn.execute(fast_terms_query).fetchone()

        tech_results = []
        ttbl = Table(show_lines=True)
        ttbl.add_column("Categoria", style="cyan", width=20)
        ttbl.add_column("Módulo / Conceito", style="white", width=30)
        ttbl.add_column("Chamados no Assunto", style="bold green", justify="right", width=22)
        ttbl.add_column("% da Base", style="yellow", justify="right", width=15)

        for i, (cat, label, term) in enumerate(tech_terms_defs):
            cnt = t_row[f"t_{i}"]
            pct = round((cnt * 100.0) / total_tickets, 2)
            tech_results.append({"categoria": cat, "termo": label, "total": cnt, "percentual": pct})
            ttbl.add_row(cat, label, f"{cnt:,}", f"{pct}%")

        report["tech_terms"] = tech_results
        console.print(ttbl)

        # -------------------------------------------------------------------
        # 6. Detecção Heurística Preliminar (Qualidade vs Ruído)
        # -------------------------------------------------------------------
        console.rule("[bold cyan]6. Detecção Heurística Preliminar (Qualidade vs Ruído)")
        quality_query = """
            SELECT 
                COUNT(CASE WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) >= 2 AND payload->>'baseStatus' = 'Closed' THEN 1 END) as fechados_com_conversa,
                COUNT(CASE WHEN jsonb_array_length(COALESCE(payload->'actions', '[]'::jsonb)) <= 1 THEN 1 END) as sem_conversa_tecnica,
                COUNT(CASE WHEN payload->>'baseStatus' = 'Canceled' OR LOWER(payload->>'subject') LIKE '%teste%' THEN 1 END) as cancelados_testes,
                COUNT(CASE WHEN payload->>'baseStatus' = 'InAttendance' THEN 1 END) as em_atendimento
            FROM raw_tickets;
        """
        q_row = conn.execute(quality_query).fetchone()

        q_items = [
            ("Chamados Fechados com Conversa Técnica (>= 2 ações)", q_row["fechados_com_conversa"], round(q_row["fechados_com_conversa"]*100.0/total_tickets, 2)),
            ("Chamados Vazios / Sem Resposta (<= 1 ação)", q_row["sem_conversa_tecnica"], round(q_row["sem_conversa_tecnica"]*100.0/total_tickets, 2)),
            ("Cancelados / Testes / Duplicados", q_row["cancelados_testes"], round(q_row["cancelados_testes"]*100.0/total_tickets, 2)),
            ("Chamados Abertos / Em Atendimento Recente", q_row["em_atendimento"], round(q_row["em_atendimento"]*100.0/total_tickets, 2)),
        ]
        report["quality_heuristics"] = q_items

        qtbl = Table(show_lines=True)
        qtbl.add_column("Padrão Heurístico", style="cyan", width=55)
        qtbl.add_column("Total de Chamados", style="bold green", justify="right", width=20)
        qtbl.add_column("% da Base", style="yellow", justify="right", width=15)
        for label, cnt, pct in q_items:
            qtbl.add_row(label, f"{cnt:,}", f"{pct}%")
        console.print(qtbl)

    t_total = time.perf_counter() - t_start
    console.print(f"\n⏱️ Auditoria concluída em [bold yellow]{t_total:.2f} segundos[/]!")

    # Salvar JSON com o relatório completo
    output_path = OUTPUT_DIR / "audit_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    console.print(f"💾 Relatório salvo em: [bold green]{output_path}[/]\n")


if __name__ == "__main__":
    run_audit()
