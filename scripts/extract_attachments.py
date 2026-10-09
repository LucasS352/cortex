"""
DeskCortex — Extrator Oficial de Anexos e Imagens/GIFs do Movidesk
==================================================================
1. Consulta a lista de anexos de cada chamado via:
   GET /tickets?$select=id&$expand=actions($select=id,attachments)
2. Baixa os binários reais (PNG, JPG, GIF, PDF, LOG, JSON, SQL) via:
   GET /storage/download?token={TOKEN}&id={HASH}
3. Salva os arquivos em scripts/output/attachments/{ticket_id}/
4. Atualiza o Knowledge Node correspondente no PostgreSQL:
   - metadata->'attachments' (lista com caminhos locais, nomes, tipos, tamanhos)
   - metadata->'has_images' (true/false)
   - metadata->'has_gifs' (true/false)
   - metadata->'image_count' (número de imagens/GIFs)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
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
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn, MofNCompleteColumn

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, MOVIDESK_TOKEN, MOVIDESK_BASE_URL

console = Console(force_terminal=True)

ATTACHMENTS_DIR = Path(__file__).parent / "output" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

# Formatos de Imagem e Animação
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ANIMATED_EXTENSIONS = {".gif"}
TECH_EXTENSIONS = {".pdf", ".log", ".sql", ".json", ".xml", ".txt", ".csv"}

# Nomes de arquivos de ruído para ignorar (avatares padrão, logos de assinatura de email)
IGNORED_FILENAMES = {
    "default-avatar.png", "avatar.png", "image001.png", "image002.png", 
    "logo.png", "facebook.png", "instagram.png", "linkedin.png"
}


def get_ticket_attachment_list(ticket_id: int, max_retries: int = 3) -> list[dict]:
    """Obtém os metadados e hashes de todos os anexos do ticket."""
    url = (
        f"{MOVIDESK_BASE_URL}/tickets"
        f"?token={MOVIDESK_TOKEN}"
        f"&id={ticket_id}"
        f"&$select=id"
        f"&$expand=actions($select=id,attachments)"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DeskCortex/1.0"})
    
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                actions = data.get("actions", [])
                attachments = []
                seen_hashes = set()
                
                for act in actions:
                    for att in act.get("attachments", []):
                        file_hash = att.get("path")
                        file_name = att.get("fileName") or "anexo"
                        
                        if not file_hash or file_hash in seen_hashes:
                            continue
                            
                        # Filtro de ruído óbvio (avatares)
                        if file_name.lower() in IGNORED_FILENAMES:
                            continue
                            
                        seen_hashes.add(file_hash)
                        att["action_id"] = act.get("id")
                        attachments.append(att)
                        
                return attachments
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** (attempt + 2))
            elif e.code == 404:
                return []
            else:
                time.sleep(1)
        except Exception:
            time.sleep(1)
            
    return []


def download_file_by_hash(file_hash: str, file_name: str, ticket_id: int) -> dict | None:
    """Baixa o binário do anexo a partir do endpoint /storage/download."""
    ticket_dir = ATTACHMENTS_DIR / str(ticket_id)
    ticket_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitizar nome de arquivo
    safe_name = "".join(c if c.isalnum() or c in ".-_ " else "_" for c in file_name).strip()
    ext = Path(safe_name).suffix.lower()
    
    # Prevenir sobreposição de nomes idênticos com hashes diferentes
    local_path = ticket_dir / safe_name
    if local_path.exists() and local_path.stat().st_size > 0:
        return {
            "file_name": safe_name,
            "local_path": str(local_path),
            "relative_path": f"attachments/{ticket_id}/{safe_name}",
            "extension": ext,
            "is_image": ext in IMAGE_EXTENSIONS,
            "is_gif": ext in ANIMATED_EXTENSIONS,
            "file_size": local_path.stat().st_size,
            "hash": file_hash
        }

    url = f"{MOVIDESK_BASE_URL}/storage/download?token={MOVIDESK_TOKEN}&id={file_hash}"
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCortex/1.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            
            # Se o arquivo for menor que 300 bytes e for png/gif, pode ser pixel de rastreio de email
            if len(data) < 300 and ext in IMAGE_EXTENSIONS:
                return None
                
            local_path.write_bytes(data)
            
            return {
                "file_name": safe_name,
                "local_path": str(local_path),
                "relative_path": f"attachments/{ticket_id}/{safe_name}",
                "extension": ext,
                "is_image": ext in IMAGE_EXTENSIONS,
                "is_gif": ext in ANIMATED_EXTENSIONS,
                "file_size": len(data),
                "hash": file_hash
            }
    except Exception as ex:
        return None


def update_node_attachments(conn, ticket_id: int, saved_files: list[dict]):
    """Atualiza o Knowledge Node com os metadados dos arquivos salvos."""
    images = [f for f in saved_files if f.get("is_image")]
    gifs = [f for f in saved_files if f.get("is_gif")]
    
    meta_update = {
        "has_attachments": len(saved_files) > 0,
        "total_attachments": len(saved_files),
        "has_images": len(images) > 0,
        "image_count": len(images),
        "has_gifs": len(gifs) > 0,
        "gif_count": len(gifs),
        "attachments": saved_files,
        "attachments_extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S")
    }
    
    conn.execute("""
        UPDATE ticket_knowledge_nodes
        SET metadata = metadata || %s::jsonb,
            updated_at = NOW()
        WHERE source_external_id = %s;
    """, (json.dumps(meta_update), ticket_id))
    conn.commit()


def process_one_ticket(ticket_id: int) -> dict:
    """Processa a extração e download de anexos de 1 ticket."""
    att_list = get_ticket_attachment_list(ticket_id)
    saved = []
    
    if att_list:
        for att in att_list:
            res = download_file_by_hash(att["path"], att.get("fileName", "arquivo"), ticket_id)
            if res:
                saved.append(res)
                
    return {
        "ticket_id": ticket_id,
        "att_list_len": len(att_list),
        "saved": saved
    }


def process_tier(conn, tier: str, limit: int = 0, workers: int = 4):
    """Extrai anexos para os tickets de um tier em paralelo."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    console.rule(f"[bold cyan]📎 Extraindo Anexos dos Tickets {tier} ({workers} workers paralelos)")
    
    query = f"""
        SELECT source_external_id, title 
        FROM ticket_knowledge_nodes
        WHERE tier = '{tier}' AND source_external_id IS NOT NULL
          AND metadata->>'attachments_extracted_at' IS NULL
        ORDER BY final_score DESC
    """
    if limit > 0:
        query += f" LIMIT {limit}"
        
    rows = conn.execute(query).fetchall()
    total = len(rows)
    
    if total == 0:
        console.print(f"  [green]✅ Todos os chamados {tier} já tiveram seus anexos verificados![/]")
        return {"tickets_verificados": 0, "tickets_com_anexos": 0, "total_arquivos": 0, "imagens": 0, "gifs": 0, "bytes_baixados": 0}
        
    console.print(f"Chamados pendentes para verificar anexos: [bold yellow]{total}[/]")
    
    stats = {
        "tickets_verificados": 0,
        "tickets_com_anexos": 0,
        "total_arquivos": 0,
        "imagens": 0,
        "gifs": 0,
        "bytes_baixados": 0
    }
    
    ticket_ids = [r["source_external_id"] for r in rows]
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=35),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"Anexos {tier}", total=total)
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(process_one_ticket, tid): tid for tid in ticket_ids}
            
            for future in as_completed(future_map):
                try:
                    res = future.result()
                    stats["tickets_verificados"] += 1
                    saved = res["saved"]
                    
                    if saved:
                        stats["tickets_com_anexos"] += 1
                        for f in saved:
                            stats["total_arquivos"] += 1
                            stats["bytes_baixados"] += f["file_size"]
                            if f["is_image"]: stats["imagens"] += 1
                            if f["is_gif"]: stats["gifs"] += 1
                            
                        update_node_attachments(conn, res["ticket_id"], saved)
                    else:
                        # Marcar como verificado mesmo se não tiver anexos
                        conn.execute("""
                            UPDATE ticket_knowledge_nodes
                            SET metadata = metadata || '{"has_attachments": false, "attachments_extracted_at": "%s"}'::jsonb,
                                updated_at = NOW()
                            WHERE source_external_id = %s;
                        """ % (time.strftime("%Y-%m-%dT%H:%M:%S"), res["ticket_id"]))
                        conn.commit()
                        
                except Exception as ex:
                    pass
                finally:
                    progress.advance(task)
                    
    return stats


def main():
    parser = argparse.ArgumentParser(description="DeskCortex — Extração Oficial de Anexos")
    parser.add_argument("--poc", action="store_true", help="Primeiros 20 chamados GOLD")
    parser.add_argument("--gold", action="store_true", help="Todos os 2.830 chamados GOLD")
    parser.add_argument("--silver", action="store_true", help="Todos os chamados SILVER")
    parser.add_argument("--ticket", type=int, help="Um chamado específico")
    parser.add_argument("--limit", type=int, default=0, help="Limite de chamados")
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold white on blue] DeskCortex — Extrator de Anexos, Imagens e GIFs [/]\n"
        f"[dim]Destino local: {ATTACHMENTS_DIR}[/]",
        border_style="blue"
    ))

    t0 = time.perf_counter()

    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        if args.ticket:
            att_list = get_ticket_attachment_list(args.ticket)
            console.print(f"Ticket #{args.ticket} tem {len(att_list)} anexos encontrados.")
            saved = []
            for att in att_list:
                res = download_file_by_hash(att["path"], att.get("fileName", "arquivo"), args.ticket)
                if res:
                    saved.append(res)
                    console.print(f"  -> Baixado: {res['file_name']} ({res['file_size']} bytes) [IMG: {res['is_image']}, GIF: {res['is_gif']}]")
            if saved:
                update_node_attachments(conn, args.ticket, saved)
                console.print(f"[bold green]✅ Metadados atualizados no Knowledge Node #{args.ticket}![/]")
            return

        limit = 20 if args.poc else args.limit
        
        if args.poc:
            stats = process_tier(conn, "GOLD", limit=20)
        elif args.gold:
            stats = process_tier(conn, "GOLD", limit=limit)
        elif args.silver:
            stats = process_tier(conn, "SILVER", limit=limit)
        else:
            # Default: POC
            stats = process_tier(conn, "GOLD", limit=20)

    elapsed = time.perf_counter() - t0
    
    console.rule("[bold green]🏁 Extração Concluída")
    tbl = Table(show_lines=True)
    tbl.add_column("Métrica", style="cyan")
    tbl.add_column("Valor", style="bold green", justify="right")
    tbl.add_row("Chamados verificados", str(stats["tickets_verificados"]))
    tbl.add_row("Chamados com anexos úteis", str(stats["tickets_com_anexos"]))
    tbl.add_row("Total de arquivos baixados", str(stats["total_arquivos"]))
    tbl.add_row("🖼️ Imagens (PNG/JPG)", str(stats["imagens"]))
    tbl.add_row("🎞️ GIFs (Animações)", str(stats["gifs"]))
    tbl.add_row("Volume baixado", f"{stats['bytes_baixados'] / 1024 / 1024:.2f} MB")
    tbl.add_row("Tempo total", f"{elapsed:.1f}s")
    console.print(tbl)


if __name__ == "__main__":
    main()
