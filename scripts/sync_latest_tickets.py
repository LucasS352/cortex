"""
DeskCortex — Sincronizador de Chamados Recentes do Movidesk
===========================================================
Sincroniza os chamados mais recentes criados no Movidesk, refina-os,
gera vetores semânticos no PostgreSQL e valida a integração do chamado #129713.

Uso:
    python sync_latest_tickets.py
"""

import json
import os
import re
import sys
import time
import urllib.request
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
from config import POSTGRES_DSN, MOVIDESK_TOKEN, MOVIDESK_BASE_URL, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL
from refine_tickets_v2 import clean_html, strip_signature, extract_modules_and_errors, compute_scores_v2
from batch_ingest import call_batch_embed, compose_ticket_text


console = Console(force_terminal=True)
ATTACHMENTS_DIR = Path(__file__).parent / "output" / "attachments"


def fetch_ticket_from_movidesk(ticket_id: int) -> dict | None:
    """Busca um ticket completo com ações, anexos e clientes via API Movidesk."""
    url = f"{MOVIDESK_BASE_URL}/tickets?token={MOVIDESK_TOKEN}&id={ticket_id}&$expand=actions($expand=attachments),clients,owner"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DeskCortex/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"Erro HTTP {e.code} ao buscar ticket #{ticket_id}: {e.reason}")
        return None
    except Exception as e:
        print(f"Erro ao buscar ticket #{ticket_id}: {e}")
        return None


def smart_extract_problem_solution(subject: str, substantive_actions: list[str]) -> tuple[str, str, str]:
    """
    Extrai problema, narrativa e solução. Se houver apenas 1 ação com 'Resolução:',
    divide inteligentemente o relato e a solução técnica.
    """
    if not substantive_actions:
        return subject, "", ""

    if len(substantive_actions) == 1:
        text = substantive_actions[0]
        # Verificar se tem marcador explícito de resolução
        sol_markers = [
            r"(?i)(?:Ações realizadas para Resolução|Resolução|Como foi resolvido|Solução):\s*",
            r"(?i)(?:Realizamos a troca|Foi realizado|Ajustado|Corrigido)",
        ]
        for marker in sol_markers:
            match = re.search(marker, text)
            if match:
                problem = text[:match.start()].strip() or subject
                solution = text[match.start():].strip()
                return problem, "", solution
        return text, "", text

    problem = substantive_actions[0]
    narrative = "\n---\n".join(substantive_actions[1:-1]) if len(substantive_actions) > 2 else ""
    solution = substantive_actions[-1]
    return problem, narrative, solution


def sync_recent():
    console.print(Panel.fit(
        "[bold white on blue] DeskCortex — Sincronização Incremental de Chamados Recentes [/]\n"
        "[dim]Buscando atualizações desde o último checkpoint no Movidesk...[/]",
        border_style="blue"
    ))

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        tenant_row = conn.execute("SELECT id FROM tenants LIMIT 1;").fetchone()
        tenant_id = tenant_row["id"]

        max_ext_row = conn.execute("SELECT MAX(external_id) as max_id FROM raw_tickets;").fetchone()
        last_id = max_ext_row["max_id"] or 129639
        console.print(f"  📌 Último chamado no banco: [bold cyan]#{last_id}[/]")

        # Sondar até onde vai a numeração no Movidesk
        console.print("  🔍 Sondando limite superior de chamados no Movidesk...")
        upper_limit = last_id
        for probe_id in range(last_id + 1, last_id + 200):
            t = fetch_ticket_from_movidesk(probe_id)
            if t:
                upper_limit = probe_id
            elif probe_id > upper_limit + 5:
                # 5 404 consecutivos = fim da fila
                break

        console.print(f"  🎯 Range a sincronizar: [bold green]#{last_id + 1} até #{upper_limit}[/] (Total: {upper_limit - last_id} IDs)")

        new_tickets = []
        for tid in range(last_id + 1, upper_limit + 1):
            ticket_payload = fetch_ticket_from_movidesk(tid)
            if not ticket_payload:
                continue

            console.print(f"   📥 Baixado #{tid}: [slate-300]{ticket_payload.get('subject', '')[:45]}[/]")

            # 1. Inserir em raw_tickets
            created_date = ticket_payload.get("createdDate")
            raw_res = conn.execute("""
                INSERT INTO raw_tickets (tenant_id, source_provider, external_id, payload, created_date)
                VALUES (%s, 'movidesk', %s, %s::jsonb, %s)
                ON CONFLICT (tenant_id, source_provider, external_id) 
                DO UPDATE SET payload = EXCLUDED.payload, created_date = EXCLUDED.created_date
                RETURNING id;
            """, (tenant_id, tid, json.dumps(ticket_payload), created_date)).fetchone()
            raw_id = raw_res["id"]

            # 2. Refinaria e Scoring
            subject = ticket_payload.get("subject") or "Sem assunto"
            base_status = ticket_payload.get("baseStatus") or "Unknown"
            actions = ticket_payload.get("actions", [])
            substantive_actions = []
            for act in actions:
                if act.get("isDeleted"):
                    continue
                d = strip_signature(clean_html(act.get("description") or ""))
                if d and len(d) >= 10:
                    substantive_actions.append(d)

            prob, narr, sol = smart_extract_problem_solution(subject, substantive_actions)
            full_text = f"{subject}\n{prob}\n{narr}\n{sol}"
            modules, error_codes = extract_modules_and_errors(full_text)

            # Extrair anexos tradicionais e prints inline de htmlDescription
            inline_images = []
            seen_hashes = set()
            for act in actions:
                for att in act.get("attachments", []):
                    fn = att.get("fileName", "")
                    is_img = bool(re.search(r'\.(png|jpg|jpeg|gif|webp)$', fn, re.I))
                    path = att.get("path", "")
                    m = re.search(r'movidesk-files/([A-Fa-f0-9]{32})', path)
                    h = m.group(1) if m else None
                    if h and h not in seen_hashes:
                        seen_hashes.add(h)
                        inline_images.append({
                            "file_name": fn,
                            "relative_path": f"api/image/{h}",
                            "is_image": is_img,
                            "is_gif": fn.lower().endswith(".gif"),
                            "hash": h
                        })
                html_desc = act.get("htmlDescription", "") or ""
                hashes = re.findall(r'https?://[^"\']*movidesk-files/([A-Fa-f0-9]{32})', html_desc)
                for h in hashes:
                    if h not in seen_hashes and h != "F9953E4A25BEA6539E5BB0BF1D7BAEFF":
                        seen_hashes.add(h)
                        inline_images.append({
                            "file_name": f"print_{len(inline_images)+1}.png",
                            "relative_path": f"api/image/{h}",
                            "is_image": True,
                            "is_gif": False,
                            "hash": h
                        })

            (tech_score, sol_score, val_score, noise_score, final_score,

             tier, evidence_level, hard_block_reason, is_blocked_gold, evidence_data) = compute_scores_v2(
                subject, prob, narr, sol, base_status, len(actions), substantive_actions, modules, error_codes
            )

            # Salvar em refined_tickets
            refined_res = conn.execute("""
                INSERT INTO refined_tickets (
                    tenant_id, raw_ticket_id, external_id, subject, service_full,
                    service_first_level, category, urgency, status, base_status,
                    client_business_name, created_date, total_actions,
                    agent_actions_count, client_actions_count, problem_statement,
                    technical_narrative, solution_statement, detected_modules,
                    detected_error_codes, technical_score, solution_score,
                    validation_score, noise_score, final_score, quality_tier,
                    evidence_level, hard_block_reason, is_blocked_gold, evidence_data
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s::jsonb,
                    %s::jsonb, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (tenant_id, external_id) DO UPDATE SET
                    final_score = EXCLUDED.final_score,
                    quality_tier = EXCLUDED.quality_tier,
                    problem_statement = EXCLUDED.problem_statement,
                    solution_statement = EXCLUDED.solution_statement
                RETURNING id;
            """, (
                tenant_id, raw_id, tid, subject[:350], None,
                None, ticket_payload.get("category"), ticket_payload.get("urgency"), ticket_payload.get("status"), base_status,
                (ticket_payload.get("clients", [{}])[0].get("businessName") if ticket_payload.get("clients") else None),
                created_date, len(actions),
                max(0, len(substantive_actions) - 1), 1 if substantive_actions else 0,
                prob, narr, sol,
                json.dumps(modules, ensure_ascii=False), json.dumps(error_codes, ensure_ascii=False),
                tech_score, sol_score, val_score, noise_score, final_score, tier,
                evidence_level, hard_block_reason, is_blocked_gold, json.dumps(evidence_data, ensure_ascii=False)
            )).fetchone()
            refined_id = refined_res["id"]

            new_tickets.append({
                "raw_id": raw_id,
                "refined_id": refined_id,
                "external_id": tid,
                "subject": subject,
                "tier": tier,
                "score": final_score,
                "problem": prob,
                "narrative": narr,
                "solution": sol,
                "problem_statement": prob,
                "technical_narrative": narr,
                "solution_statement": sol,
                "detected_modules": modules,
                "detected_error_codes": error_codes,
                "modules": modules,
                "error_codes": error_codes,
                "evidence_data": evidence_data,
                "inline_images": inline_images,
                "payload": ticket_payload
            })

        conn.commit()

        console.print(f"\n[bold green]✅ {len(new_tickets)} novos chamados sincronizados e refinados com sucesso![/]\n")

        # 3. Gerar Vetores (Knowledge Nodes) para os tickets úteis ou especificamente o 129713
        to_ingest = [t for t in new_tickets if t["tier"] in ("GOLD", "SILVER") or t["external_id"] == 129713]
        console.print(f"🧠 Gerando Embeddings para {len(to_ingest)} chamados qualificados...")

        if to_ingest:
            texts = [compose_ticket_text(t) for t in to_ingest]
            embeddings = call_batch_embed(texts)

            for t, emb, txt in zip(to_ingest, embeddings, texts):
                src_id = f"ticket-{t['external_id']}"
                tier_val = t["tier"] if t["tier"] in ("GOLD", "SILVER") else "SILVER"
                source_type = "VALIDATED_TICKET" if tier_val == "GOLD" else "PROBABLE_TICKET"
                authority = 85 if tier_val == "GOLD" else 60
                confidence_str = "HIGH" if tier_val == "GOLD" else "MEDIUM"

                imgs = t.get("inline_images", [])
                meta = {
                    "tenant_id": str(tenant_id),
                    "raw_ticket_id": str(t["raw_id"]),
                    "refined_ticket_id": str(t["refined_id"]),
                    "has_attachments": len(imgs) > 0,
                    "has_images": any(img.get("is_image") for img in imgs),
                    "attachments": imgs,
                    "total_attachments": len(imgs),
                    "image_count": sum(1 for img in imgs if img.get("is_image")),
                    "synced_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }


                vec_str = "[" + ",".join(str(float(v)) for v in emb) + "]" if emb else None

                conn.execute("""
                    INSERT INTO ticket_knowledge_nodes (
                        source_type, source_id, source_external_id,
                        title, content_for_embedding,
                        problem, diagnosis, solution, result,
                        product, modules, detected_error_codes, version_reference,
                        tier, confidence, technical_score, solution_score,
                        validation_score, final_score,
                        validation_evidence, hard_block_reason, source_authority,
                        metadata, embedding, embedding_generated, embedding_model
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, NULL,
                        'Master ERP', %s, %s, NULL,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s::vector, %s, 'gemini-embedding-2'
                    )
                    ON CONFLICT (source_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        embedding_generated = EXCLUDED.embedding_generated,
                        solution = EXCLUDED.solution,
                        problem = EXCLUDED.problem,
                        final_score = EXCLUDED.final_score,
                        tier = EXCLUDED.tier,
                        updated_at = NOW();
                """, (
                    source_type, src_id, t["external_id"],
                    t["subject"][:350], txt,
                    t["problem"], t.get("narrative", ""), t["solution"],
                    t["modules"] if t["modules"] else None,
                    t["error_codes"] if t["error_codes"] else None,
                    tier_val, confidence_str,
                    t.get("technical_score", 0), t.get("solution_score", 0),
                    t.get("validation_score", 0), t["score"],
                    t["evidence_data"].get("evidence_tags") if t["evidence_data"].get("evidence_tags") else None,
                    t.get("hard_block_reason"), authority,
                    json.dumps(meta), vec_str, emb is not None
                ))
            conn.commit()
            console.print(f"[bold green]✨ {len(to_ingest)} Knowledge Nodes vetorizados e gravados no PostgreSQL![/]")


        # 4. Auditoria Especial do Chamado #129713
        t129713 = conn.execute("""
            SELECT kn.source_external_id, kn.title, kn.tier, kn.confidence, kn.final_score,
                   kn.modules, kn.detected_error_codes, kn.problem, kn.solution,
                   kn.embedding_generated
            FROM ticket_knowledge_nodes kn
            WHERE kn.source_external_id = 129713;
        """).fetchone()

        console.rule("[bold magenta]🌟 AUDITORIA ESPECIAL DO CHAMADO #129713")
        if t129713:
            console.print(Panel(
                f"[bold green]CHAMADO INTEGRADO COM SUCESSO NO DESKCORTEX![/]\n\n"
                f"• [bold white]ID:[/] #{t129713['source_external_id']}\n"
                f"• [bold white]Título:[/] {t129713['title']}\n"
                f"• [bold white]Qualidade (Tier):[/] [bold yellow]{t129713['tier']}[/] (Score: {t129713['final_score']})\n"
                f"• [bold white]Módulos:[/] {t129713['modules']}\n"
                f"• [bold white]Códigos de Erro:[/] [rose]{t129713['detected_error_codes']}[/]\n"
                f"• [bold white]Vetorizado no pgvector (3072 dims):[/] {'✅ SIM' if t129713['embedding_generated'] else '❌ NÃO'}\n\n"
                f"[bold cyan]⚠️ Problema Extraído:[/]\n{t129713['problem'][:250]}...\n\n"
                f"[bold emerald]✅ Solução Registrada:[/]\n{t129713['solution'][:250]}...",
                title="Validação Oficial #129713",
                border_style="green"
            ))
        else:
            console.print("[bold red]Chamado #129713 não encontrado nos knowledge nodes.[/]")


if __name__ == "__main__":
    sync_recent()
