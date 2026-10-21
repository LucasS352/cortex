"""
DeskCortex -- IngestManuals: Pipeline de Ingestao de Manuais Internos (Fase 2)
===============================================================================
Extrai texto + imagens de todos os arquivos da pasta Instrucoes_master,
vetoriza com Gemini Embedding e insere no banco com source_type=MANUAL.
Materiais brutos entram como nao revisados e com autoridade 70; somente a
curadoria humana pode promove-los a conhecimento canonico.

Tipos suportados: .docx .pdf .rtf .txt .gif .png .jpg
Ignorados: ~$*.docx (temp Word), .mp4, .xls, .xlsx

Uso:
  python ingest_manuals.py --dry-run --limit 5
  python ingest_manuals.py --all
  python ingest_manuals.py --category Nota_Fiscal
  python ingest_manuals.py --media-only
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path
from typing import Optional

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL

console = Console(force_terminal=True)

INSTRUCOES_DIR = Path(r"c:\Users\Lucas Souza\Desktop\movidesk_project\Instrucoes_master")
BATCH_SIZE = 25
MAX_CHARS_CHUNK = 1500
OVERLAP_CHARS = 150
MIN_CHUNK_CHARS = 80
AUTHORITY_SCORE = 70

SHORTCUT_RE = re.compile(
    r"\b((?:Ctrl|Alt|Shift)(?:\s*\+?\s*(?:Ctrl|Alt|Shift))*\s*\+?\s*(?:F\d{1,2}|[A-Za-z0-9]))\b",
    re.I,
)
SCREEN_PATH_RE = re.compile(
    r"(?:Acesse|V[áa]\s+em|Menu|Aba|Tela|M[óo]dulo)?[:\s]*"
    r"([A-Za-zÀ-ÿ0-9%()./_ -]+(?:\s*>{1,2}\s*[A-Za-zÀ-ÿ0-9%()./_ -]+)+)",
    re.I,
)
# ---------------------------------------------------------------------------
# Mapa de categorias por nome de arquivo / pasta
# ---------------------------------------------------------------------------

def classify_category(filepath: Path) -> str:
    parts = [p.lower() for p in filepath.parts]
    name = filepath.stem.lower()
    full = " ".join(parts) + " " + name
    rules = [
        (["site -", "anymarket", "vtex", "skyhub", "b2w", "magalu", "olist",
          "tray", "loja integrada", "moovin", "mercos", "wehub", "plugg", "pier8",
          "core", "ez", "jet", "bis2bis", "fullfilmet", "ecommerce", "marketplace"], "Ecommerce"),
        (["erros nota", "nota fiscal", "nfe", "nf-e", "nfce", "sat",
          "cfop", "cest", "ncm", "gnre", "tributac", "tributaca", "difal",
          "ipi", "icms", "csosn", "aliquota", "nota complement", "certificado",
          "danfe", "xml", "sefaz", "carta de correc", "inutilizar"], "Nota_Fiscal"),
        (["compras", "entrada de compra", "pedido de compra", "devolucao", "movimentacao", "devolucao"], "Compras"),
        (["financeiro", "boleto", "contas a pagar", "juros", "refaturamento",
          "conciliacao", "pagarme", "conta corrente", "despesas"], "Financeiro"),
        (["produtos", "cadastro de produto", "estoque", "inventario", "zerar estoque",
          "controle de estoque", "atualizacao de estoque", "lote", "fotos",
          "importar itens", "kit", "quais as formulas"], "Produtos_Estoque"),
        (["vendas", "venda", "gerenciamento", "caixa", "cancelamento", "troca"], "Vendas"),
        (["sql", "sqls", "codigos sql", "selects lucas", "buscas sql", "bd - sql",
          "table unknown", "retirando pedidos da fila"], "SQL_Consultas"),
        (["transportadora", "intelipost", "mandae", "correios", "meus correios",
          "mercado de envios", "plp", "etiqueta", "rastreio", "sigepweb",
          "declaracao de conteudo", "conhecimento de transporte"], "Transportadoras"),
        (["dashboard", "relat"], "Dashboard_Relatorios"),
        (["backup", "instalacao", "migracao", "servidor", "terminal firebird",
          "vpn", "acesso dalla", "nosso cloud", "log", "logar", "firebird",
          "validacao de backup", "infos backup"], "Infraestrutura"),
        (["mobile"], "Mobile"),
        (["api", "config ini", "parametros", "desabilitar ez", "sitef", "farma"], "Configuracoes"),
        (["cadastro", "clientes", "fornecedor", "fabricante", "tipo categoria",
          "especificacoes", "grupo de usuario", "unidade de estoque", "usuario"], "Cadastros"),
    ]
    for keywords, category in rules:
        if any(k in full for k in keywords):
            return category
    return "Geral"


# ---------------------------------------------------------------------------
# Helpers de texto
# ---------------------------------------------------------------------------

def _split_text(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks, current, current_len = [], [], 0
    for para in paragraphs:
        if current_len + len(para) > MAX_CHARS_CHUNK and current:
            chunk = "\n\n".join(current)
            if len(chunk) >= MIN_CHUNK_CHARS:
                chunks.append(chunk)
            current = current[-1:] + [para]
            current_len = sum(len(p) for p in current)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        chunk = "\n\n".join(current)
        if len(chunk) >= MIN_CHUNK_CHARS:
            chunks.append(chunk)
    return chunks

def extract_shortcuts(text: str) -> list[str]:
    found, seen = [], set()
    for m in SHORTCUT_RE.findall(text):
        sk = (m[0] if isinstance(m, tuple) else m).upper()
        sk = re.sub(r"\s*\+\s*", "+", sk.strip())
        sk = re.sub(r"\s+", "+", sk)
        sk = re.sub(r"^(CTRL|ALT|SHIFT)(F\d{1,2}|[A-Z0-9])$", r"\1+\2", sk)
        if sk not in seen:
            seen.add(sk); found.append(sk)
    return found

def extract_screen_path(text: str) -> Optional[str]:
    m = SCREEN_PATH_RE.search(text)
    if m:
        path = re.sub(r"\s*>{1,2}\s*", " > ", m.group(1).strip())
        return path if len(path) > 10 else None
    return None


def manual_source_id(source_file: str, kind: str, index: int) -> str:
    """Return a stable source id that fits the database VARCHAR(50).

    A document may generate multiple chunks and images.  Using only its file
    name made every item collide with the unique source_id constraint, so the
    first chunk was stored and the rest were silently discarded.
    """
    normalized = source_file.replace("\\", "/").casefold().encode("utf-8")
    file_hash = hashlib.sha256(normalized).hexdigest()[:16]
    kind_code = {"chunk": "C", "image": "I", "media": "M"}.get(kind, "X")
    return f"MANUAL:{file_hash}:{kind_code}{index:04d}"
# ---------------------------------------------------------------------------
# Extratores por tipo de arquivo
# ---------------------------------------------------------------------------

def extract_docx(filepath: Path) -> tuple[list[str], list[dict]]:
    try:
        from docx import Document
    except ImportError:
        console.print("[red]python-docx nao instalado: pip install python-docx[/red]")
        return [], []
    try:
        doc = Document(filepath)
    except Exception as e:
        console.print(f"[yellow]Nao foi possivel abrir {filepath.name}: {e}[/yellow]")
        return [], []

    chunks, current_section, current_heading, images_info = [], [], "", []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        is_heading = (
            para.style and para.style.name and
            ("Heading" in para.style.name or "Titulo" in para.style.name or
             (len(text) < 120 and para.runs and any(r.bold for r in para.runs if r.text.strip())))
        )
        if is_heading:
            if current_section:
                chunks.extend(_split_text(current_heading + "\n" + "\n".join(current_section)))
            current_heading, current_section = text, []
        else:
            current_section.append(text)
    if current_section:
        section_text = (current_heading + "\n" + "\n".join(current_section)) if current_heading else "\n".join(current_section)
        chunks.extend(_split_text(section_text))

    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                img_data = rel.target_part.blob
                img_name = Path(rel.target_part.partname).name
                images_info.append({
                    "name": img_name,
                    "data": base64.b64encode(img_data).decode("utf-8"),
                    "format": img_name.rsplit(".", 1)[-1].lower() if "." in img_name else "png",
                })
            except Exception:
                pass
    return chunks, images_info


def extract_pdf(filepath: Path) -> tuple[list[str], list[dict]]:
    try:
        import fitz
    except ImportError:
        console.print("[red]pymupdf nao instalado: pip install pymupdf[/red]")
        return [], []
    chunks, images_info = [], []
    try:
        doc = fitz.open(str(filepath))
        for page_num, page in enumerate(doc):
            text = page.get_text("text").strip()
            if text and len(text) > MIN_CHUNK_CHARS:
                chunks.extend(_split_text(f"[Pagina {page_num+1}]\n{text}"))
            for img_idx, img in enumerate(page.get_images(full=True)):
                try:
                    base_img = doc.extract_image(img[0])
                    images_info.append({
                        "name": f"p{page_num+1}_img{img_idx}.{base_img['ext']}",
                        "data": base64.b64encode(base_img["image"]).decode("utf-8"),
                        "format": base_img["ext"],
                    })
                except Exception:
                    pass
        doc.close()
    except Exception as e:
        console.print(f"[yellow]Erro PDF {filepath.name}: {e}[/yellow]")
    return chunks, images_info


def extract_rtf(filepath: Path) -> list[str]:
    try:
        from striprtf.striprtf import rtf_to_text
        raw = filepath.read_bytes().decode("latin-1", errors="replace")
        return _split_text(rtf_to_text(raw))
    except ImportError:
        console.print("[yellow]striprtf nao instalado, pulando RTF[/yellow]")
        return []
    except Exception as e:
        console.print(f"[yellow]Erro RTF {filepath.name}: {e}[/yellow]")
        return []


def extract_txt(filepath: Path) -> list[str]:
    try:
        return _split_text(filepath.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        console.print(f"[yellow]Erro TXT {filepath.name}: {e}[/yellow]")
        return []
# ---------------------------------------------------------------------------
# Gemini Vision: descrever GIFs e PNGs como conhecimento estruturado
# ---------------------------------------------------------------------------

def describe_image_with_vision(image_path: Path, context_name: str = "", category: str = "") -> Optional[dict]:
    try:
        img_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        ext = image_path.suffix.lower().lstrip(".")
        mime = {"gif":"image/gif","png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg"}.get(ext,"image/png")
    except Exception as e:
        console.print(f"[yellow]Nao foi possivel ler {image_path.name}: {e}[/yellow]")
        return None

    prompt = (
        "Voce e um especialista no ERP Master (Alternativa Sistemas). "
        f"Analise esta imagem do sistema. Nome: {image_path.name}. Contexto: {context_name or category}. "
        "Retorne APENAS JSON valido (sem markdown) com esta estrutura exata: "
        '{"title":"Nome descritivo","module":"Modulo do Master","screen":"Tela especifica",'
        '"objective":"O que o usuario faz","flow":["1. Passo","2. Passo"],'
        '"shortcuts":["F3"],"description":"Descricao completa da imagem"}'
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    body = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": img_b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }).encode("utf-8")

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                return json.loads(text)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5.0 * (attempt + 1)
                console.print(f"[yellow]Vision 429, aguardando {wait}s...[/yellow]")
                time.sleep(wait)
            else:
                console.print(f"[yellow]Vision HTTP {e.code} para {image_path.name}[/yellow]")
                break
        except Exception as e:
            console.print(f"[yellow]Vision erro {image_path.name}: {e}[/yellow]")
            break
    return None


# ---------------------------------------------------------------------------
# Embedding em batch
# ---------------------------------------------------------------------------

def batch_embed(texts: list[str]) -> list[Optional[list[float]]]:
    if not texts:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_EMBEDDING_MODEL}:batchEmbedContents?key={GEMINI_API_KEY}"
    payload = [
        {"model": GEMINI_EMBEDDING_MODEL,
         "content": {"parts": [{"text": t[:8000]}]},
         "taskType": "RETRIEVAL_DOCUMENT"}
        for t in texts
    ]
    body = json.dumps({"requests": payload}).encode("utf-8")
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [e["values"] for e in data.get("embeddings", [])]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5.0 * (2 ** attempt)
                console.print(f"[yellow]Embed 429, aguardando {wait:.0f}s (tentativa {attempt+1}/5)[/yellow]")
                time.sleep(wait)
            else:
                console.print(f"[red]Embed HTTP {e.code}[/red]")
                return [None] * len(texts)
        except Exception as e:
            console.print(f"[red]Embed erro: {e}[/red]")
            return [None] * len(texts)
    return [None] * len(texts)


# ---------------------------------------------------------------------------
# Insercao no banco
# ---------------------------------------------------------------------------

def insert_manual_chunk(conn, title: str, content_text: str, embedding: list[float],
                         category: str, source_file: str, shortcut_keys: list[str],
                         screen_path: Optional[str], media_path: Optional[str] = None,
                         media_type: Optional[str] = None, media_metadata: Optional[dict] = None,
                         source_id: Optional[str] = None, node_metadata: Optional[dict] = None) -> bool:
    vec_str = "[" + ",".join(str(float(v)) for v in embedding) + "]"
    modules_arr = [category] if category and category != "Geral" else None
    shortcuts_arr = shortcut_keys if shortcut_keys else None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ticket_knowledge_nodes (
                    source_type, source_id, title,
                    content_for_embedding, solution, product,
                    modules, tier, confidence, source_authority,
                    final_score, validation_evidence,
                    embedding, embedding_generated, embedding_model,
                    manual_category, shortcut_keys, screen_path,
                    media_path, media_type, media_metadata, metadata,
                    created_at
                ) VALUES (
                    'MANUAL', %s, %s,
                    %s, %s, 'Master ERP',
                    %s, 'MANUAL', 'MEDIUM', %s,
                    %s, ARRAY['RAW_INTERNAL_MANUAL_UNREVIEWED'],
                    %s::vector, TRUE, 'models/gemini-embedding-2',
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW()
                ) ON CONFLICT (source_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    content_for_embedding = EXCLUDED.content_for_embedding,
                    solution = EXCLUDED.solution,
                    modules = EXCLUDED.modules,
                    confidence = EXCLUDED.confidence,
                    source_authority = EXCLUDED.source_authority,
                    final_score = EXCLUDED.final_score,
                    validation_evidence = EXCLUDED.validation_evidence,
                    embedding = EXCLUDED.embedding,
                    embedding_generated = EXCLUDED.embedding_generated,
                    embedding_model = EXCLUDED.embedding_model,
                    manual_category = EXCLUDED.manual_category,
                    shortcut_keys = EXCLUDED.shortcut_keys,
                    screen_path = EXCLUDED.screen_path,
                    media_path = EXCLUDED.media_path,
                    media_type = EXCLUDED.media_type,
                    media_metadata = EXCLUDED.media_metadata,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
            """, (
                source_id or manual_source_id(source_file, "chunk", 1),
                (title or "")[:500],
                content_text[:8000],
                content_text[:8000],
                modules_arr,
                AUTHORITY_SCORE,
                AUTHORITY_SCORE,
                vec_str,
                category,
                shortcuts_arr,
                screen_path,
                media_path,
                media_type,
                json.dumps(media_metadata) if media_metadata else None,
                json.dumps(node_metadata or {"source_file": source_file, "review_status": "UNREVIEWED"}),
            ))
            return cur.rowcount == 1
    except Exception as e:
        conn.rollback()
        console.print(f"[red]INSERT erro: {e}[/red]")
        return False
# ---------------------------------------------------------------------------
# Descoberta de arquivos
# ---------------------------------------------------------------------------

SKIP_EXTENSIONS = {".mp4", ".xls", ".xlsx", ".doc"}
MEDIA_EXTENSIONS = {".gif", ".png", ".jpg", ".jpeg"}
TEXT_EXTENSIONS  = {".docx", ".pdf", ".rtf", ".txt"}

def _should_skip(f: Path) -> bool:
    if f.name.startswith("~$") or f.name.startswith("."):
        return True
    if f.suffix.lower() in SKIP_EXTENSIONS:
        return True
    try:
        if f.stat().st_size < 200:
            return True
    except Exception:
        return True
    return False

def discover_files(base_dir: Path, category_filter: str = "", media_only: bool = False) -> list[Path]:
    files = []
    allowed = MEDIA_EXTENSIONS if media_only else MEDIA_EXTENSIONS | TEXT_EXTENSIONS
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip(path):
            continue
        if path.suffix.lower() not in allowed:
            continue
        if category_filter:
            if classify_category(path).lower() != category_filter.lower():
                continue
        files.append(path)
    return files


# ---------------------------------------------------------------------------
# Processador principal por arquivo
# ---------------------------------------------------------------------------

def process_file(filepath: Path, conn, dry_run: bool = False, vision_enabled: bool = True) -> tuple[int, int]:
    category = classify_category(filepath)
    ext = filepath.suffix.lower()
    rel_path = str(filepath.relative_to(INSTRUCOES_DIR))
    inserted = skipped = 0

    # -- Imagens soltas (GIF / PNG) --
    if ext in MEDIA_EXTENSIONS:
        if not vision_enabled:
            return 0, 1
        meta = describe_image_with_vision(filepath, filepath.stem, category)
        if not meta:
            return 0, 1
        flow_text = "\n".join(meta.get("flow") or [])
        text = (
            f"MANUAL TECNICO -- {meta.get('title','')}\n"
            f"Modulo: {meta.get('module','')}\n"
            f"Tela: {meta.get('screen','')}\n"
            f"Objetivo: {meta.get('objective','')}\n"
            f"Fluxo:\n{flow_text}\n"
            f"Descricao: {meta.get('description','')}"
        )
        shortcuts = meta.get("shortcuts") or extract_shortcuts(text)
        if dry_run:
            console.print(f"  [dim][DRY RUN][/dim] MEDIA: {filepath.name} -> \"{meta.get('title','')}\"")
            return 1, 0
        embs = batch_embed([text])
        if embs and embs[0]:
            ok = insert_manual_chunk(conn, title=meta.get("title") or filepath.stem,
                content_text=text, embedding=embs[0], category=category,
                source_file=rel_path, shortcut_keys=shortcuts, screen_path=meta.get("screen"),
                media_path=rel_path, media_type=ext.lstrip("."), media_metadata=meta,
                source_id=manual_source_id(rel_path, "media", 1),
                node_metadata={"source_file": rel_path, "item_kind": "standalone_media", "review_status": "UNREVIEWED"})
            conn.commit()
            return (1, 0) if ok else (0, 1)
        return 0, 1

    # -- Documentos de texto --
    text_chunks, embedded_images = [], []
    if ext == ".docx":
        text_chunks, embedded_images = extract_docx(filepath)
    elif ext == ".pdf":
        text_chunks, embedded_images = extract_pdf(filepath)
    elif ext == ".rtf":
        text_chunks = extract_rtf(filepath)
    elif ext == ".txt":
        text_chunks = extract_txt(filepath)

    if dry_run:
        console.print(f"  [dim][DRY RUN][/dim] {filepath.name}: {len(text_chunks)} chunks, {len(embedded_images)} imgs")
        return len(text_chunks), 0

    for i in range(0, len(text_chunks), BATCH_SIZE):
        batch = text_chunks[i:i+BATCH_SIZE]
        embeddings = batch_embed(batch)
        time.sleep(0.4)
        for j, (chunk_text, emb) in enumerate(zip(batch, embeddings)):
            if not emb:
                skipped += 1
                continue
            chunk_number = i + j + 1
            ok = insert_manual_chunk(
                conn, title=f"{filepath.stem} (p{i+j+1})", content_text=chunk_text,
                embedding=emb, category=category, source_file=rel_path,
                shortcut_keys=extract_shortcuts(chunk_text),
                screen_path=extract_screen_path(chunk_text),
                source_id=manual_source_id(rel_path, "chunk", chunk_number),
                node_metadata={
                    "source_file": rel_path,
                    "item_kind": "text_chunk",
                    "chunk_index": chunk_number,
                    "chunk_total": len(text_chunks),
                    "review_status": "UNREVIEWED",
                })
            if ok: inserted += 1
            else: skipped += 1
        conn.commit()

    # -- Imagens embutidas no docx --
    if vision_enabled and embedded_images:
        for image_index, img_info in enumerate(embedded_images, start=1):
            try:
                tmp = Path(os.environ.get("TEMP","/tmp")) / f"dc_tmp_{img_info['name']}"
                tmp.write_bytes(base64.b64decode(img_info["data"]))
                meta = describe_image_with_vision(tmp, filepath.stem, category)
                tmp.unlink(missing_ok=True)
                if not meta:
                    continue
                flow_text = "\n".join(meta.get("flow") or [])
                text = (f"IMAGEM MANUAL: {filepath.stem}\n"
                        f"Modulo: {meta.get('module','')}\nTela: {meta.get('screen','')}\n"
                        f"Objetivo: {meta.get('objective','')}\nFluxo:\n{flow_text}\n"
                        f"Descricao: {meta.get('description','')}")
                embs = batch_embed([text])
                if embs and embs[0]:
                    ok = insert_manual_chunk(conn, title=meta.get("title") or f"Img {filepath.stem}",
                        content_text=text, embedding=embs[0], category=category,
                        source_file=rel_path, shortcut_keys=meta.get("shortcuts") or [],
                        screen_path=meta.get("screen"), media_type=img_info.get("format","png"),
                        media_metadata=meta,
                        source_id=manual_source_id(rel_path, "image", image_index),
                        node_metadata={
                            "source_file": rel_path,
                            "item_kind": "embedded_image",
                            "image_index": image_index,
                            "review_status": "UNREVIEWED",
                        })
                    conn.commit()
                    if ok: inserted += 1
            except Exception as e:
                console.print(f"[yellow]Img embutida {filepath.name}: {e}[/yellow]")

    return inserted, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DeskCortex -- Ingestao de Manuais Internos")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--category", type=str, default="")
    parser.add_argument("--media-only", action="store_true")
    parser.add_argument("--no-vision", action="store_true")
    args = parser.parse_args()

    if not args.all and not args.dry_run and args.limit == 0:
        console.print("[yellow]Use --all, --dry-run ou --limit N[/yellow]")
        parser.print_help()
        return

    console.print(Panel(
        "[bold cyan]DeskCortex -- Ingestao de Manuais (Fase 2)[/bold cyan]\n"
        f"Dir: [dim]{INSTRUCOES_DIR}[/dim]\n"
        f"Modo: {'[yellow]DRY RUN[/yellow]' if args.dry_run else '[green]REAL[/green]'} | "
        f"Vision: {'[red]OFF[/red]' if args.no_vision else '[green]ON[/green]'}",
        border_style="cyan"
    ))

    files = discover_files(INSTRUCOES_DIR, args.category, args.media_only)
    if args.limit:
        files = files[:args.limit]

    console.print(f"\n[bold]{len(files)} arquivos para processar[/bold]")
    cats: dict[str, int] = {}
    for f in files:
        c = classify_category(f); cats[c] = cats.get(c, 0) + 1
    t = Table(title="Arquivos por categoria", header_style="bold cyan")
    t.add_column("Categoria"); t.add_column("Qtd", justify="right")
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        t.add_row(c, str(n))
    console.print(t)

    total_ins = total_skp = total_err = 0
    try:
        with psycopg.connect(POSTGRES_DSN) as conn:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                          BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn(),
                          console=console) as progress:
                task = progress.add_task("Processando...", total=len(files))
                for filepath in files:
                    try:
                        progress.update(task, description=f"[cyan]{filepath.name[:45]}[/cyan]")
                        ins, skp = process_file(filepath, conn, args.dry_run, not args.no_vision)
                        total_ins += ins; total_skp += skp
                    except Exception as e:
                        console.print(f"[red]ERRO {filepath.name}: {e}[/red]")
                        total_err += 1
                    finally:
                        progress.advance(task)
    except psycopg.OperationalError as e:
        console.print(f"[red]Conexao PostgreSQL: {e}[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold green]Concluido![/bold green]\n"
        f"Inseridos: [green]{total_ins:,}[/green] | Ignorados: [yellow]{total_skp}[/yellow] | Erros: [red]{total_err}[/red]",
        border_style="green" if total_err == 0 else "yellow"
    ))

if __name__ == "__main__":
    main()
