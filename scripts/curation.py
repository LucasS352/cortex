"""Evidence-preserving NF-e/Safetech pilot. No external model/API calls.

Run: python scripts/curation.py build | search QUERY | export | review ...
Raw records remain source-of-truth; automated clusters are review candidates.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from config import POSTGRES_DSN, PROJECT_ROOT

ROOT = Path(__file__).parent
OUT = PROJECT_ROOT / "output" / "knowledge-curation"
SEEDS = ROOT / "curation_seeds.json"
SCHEMA = PROJECT_ROOT / "deskcortex-api" / "migrations" / "004_knowledge_curation.sql"
SCOPE = r"safetech|safatech|\mnf-?e\M|\mnfc-?e\M|nota[s]? fiscal|rejei|sefaz|danfe"
STOP = set("a o as os de da do das dos e em um uma para por no na nos nas com como que eu meu minha esta estou erro problema fazer fica nao ao pelo pela mais esse essa tem esta la".split())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def norm(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def clean(text):
    # Only strip recognizable HTML tags: preserve SQL < > and XML tag names.
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", text or "")
    text = re.sub(r"(?i)</?(?:p|div|br|span|strong|b|a|li|ul|ol|table|tr|td|img)\b[^>]*>", "\n", text)
    text = html.unescape(text).replace("\xa0", " ").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def technical(text):
    # Keep complete segments separately; cleaning is only for extraction/search.
    text = re.split(r"Obrigado por entrar em contato|Você já\s*assistiu|(?m:^__\s*$)", text, flags=re.I)[0]
    return text.strip()


def signatures(text):
    t = norm(text)
    codes = set(re.findall(r"\bORA-\d{5}\b", text.upper()))
    codes |= {"SEFAZ:" + n for n in re.findall(r"(?:rejei[cç][aã]o\s*[:\-]?\s*|\b)(\d{3})\s*[-:]\s*rejei[cç][aã]o", text, re.I)}
    codes |= {"SEFAZ:" + n for n in re.findall(r"rejei[cç][aã]o\s*[:\-]?\s*(\d{3})\b", text, re.I)}
    objects = set(re.findall(r"\bS[FP]T_[A-Z0-9_]+\b", text.upper()))
    phrases = []
    for phrase in ("classe nao registrada", "connection timed out", "duplicidade", "certificado digital", "grupo de icms para a uf de destino"):
        if phrase in t:
            phrases.append(phrase)
    return {"codes": sorted(codes), "objects": sorted(objects), "phrases": phrases}


def risk_flags(text):
    flags = []
    t = norm(text)
    if re.search(r"\b(update|delete|drop|truncate|alter table|grant)\b", t):
        flags.append("contains_database_mutation")
    if re.search(r"icms|cfop|cst\b|csosn|isenta|aliquota|difal", t):
        flags.append("fiscal_review")
    if "00000000000" in t or "desconto 100%" in t:
        flags.append("unsafe_generalization_review")
    if re.search(r"capicom|regasm|framework 3.5", t):
        flags.append("legacy_components")
    if "sistema atualizado" in t or "correção do dev" in text.lower():
        flags.append("check_release_details")
    return flags


def make_source(tenant, kind, external_id, title, segments, date):
    useful = "\n".join(technical(s["text"]) for s in segments)
    url = (f"https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{external_id}" if kind == "ticket"
           else f"https://helpdesk.alternativasistemas.com.br/kb/article/{external_id}")
    return dict(tenant_id=str(tenant), source_key=f"{kind}:{external_id}", source_type=kind,
                external_id=external_id, title=title, url=url, source_date=str(date or ""),
                segments=segments, content_hash=digest([title, segments]),
                signatures=signatures(title + "\n" + useful), flags=risk_flags(useful))


def load_sources(conn, tenant):
    seeds = json.loads(SEEDS.read_text(encoding="utf-8"))
    seed_ids = [int(c["source"].split(":")[1]) for s in seeds for c in s["claims"] if c["source"].startswith("ticket:")]
    sql = """SELECT external_id, payload, created_date FROM raw_tickets
        WHERE tenant_id=%s AND ((payload->>'subject') ~* %s OR external_id=ANY(%s)) ORDER BY external_id"""
    sources = {}
    with conn.cursor(name="curation_tickets") as cur:
        cur.execute(sql, (tenant, SCOPE, seed_ids))
        for row in cur:
            p = row["payload"]
            segments = []
            for i, a in enumerate(p.get("actions") or []):
                if a.get("isDeleted"):
                    continue
                body = clean(a.get("description") or "")
                if body:
                    segments.append({"id": f"action:{a.get('id', i)}", "text": body,
                                     "date": a.get("createdDate"), "status": a.get("status")})
            s = make_source(tenant, "ticket", row["external_id"], p.get("subject") or "", segments, row["created_date"])
            sources[s["source_key"]] = s
    for row in conn.execute("""SELECT external_id,title,content_text,content_html,updated_at_source
          FROM raw_kb_articles WHERE tenant_id=%s AND (title ~* %s OR external_id=ANY(%s)) ORDER BY external_id""",
          (tenant, SCOPE, [int(c["source"].split(":")[1]) for s in seeds for c in s["claims"] if c["source"].startswith("kb:")])):
        text = clean(row["content_text"] or row["content_html"] or "")
        s = make_source(tenant, "kb", row["external_id"], row["title"], [{"id": "article", "text": text}], row["updated_at_source"])
        if len(technical(text)) < 200 and "iframe" in (row["content_html"] or ""):
            s["flags"].append("video_without_transcript")
        sources[s["source_key"]] = s
    return sources


def locate(source, quote):
    for seg in source["segments"]:
        start = seg["text"].find(quote)
        if start >= 0:
            return {"source": source["source_key"], "source_hash": source["content_hash"],
                    "segment": seg["id"], "start": start, "end": start + len(quote), "quote": quote,
                    "source_date": source["source_date"], "url": source["url"], "title": source["title"]}
    raise ValueError(f"Evidence not found: {source['source_key']} / {quote}")


def curated_cards(sources):
    cards = []
    for seed in json.loads(SEEDS.read_text(encoding="utf-8")):
        card = dict(seed, kind="curated", review_requirement="technical_review", evidence_level="source_linked_not_human_validated")
        card["claims"] = []
        for claim in seed["claims"]:
            evidence = locate(sources[claim["source"]], claim["quote"])
            card["claims"].append({"field": claim["field"], "text": claim["text"], "evidence": evidence})
        keys = {c["evidence"]["source"] for c in card["claims"]}
        card["flags"] = sorted({f for k in keys for f in sources[k]["flags"]})
        card["source_count"] = len(keys)
        cards.append(card)
    return cards


def candidate_cards(sources, limit=40):
    groups = defaultdict(list)
    for source in sources.values():
        sig = source["signatures"]
        module = "Safetech" if "safetech" in norm(source["title"]) or sig["objects"] else "NF-e"
        # Codes alone are insufficient for Oracle: retain object identity.
        labels = sig["objects"] or sig["codes"] or sig["phrases"]
        for label in labels:
            groups[(module, label)].append(source)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    cards = []
    for (module, label), members in ordered[:limit]:
        members.sort(key=lambda s: (s["source_type"] != "kb", -s["external_id"]))
        evidence = []
        # One excerpt per source, deduplicate exact text without erasing source IDs.
        seen = set()
        for source in members:
            needle = label.removeprefix("SEFAZ:")
            for seg in source["segments"]:
                body = technical(seg["text"])
                pos = norm(body).find(norm(needle))
                if pos < 0:
                    continue
                quote = body[max(0, pos - 160):pos + 400]
                if quote and quote not in seen:
                    evidence.append({"field": "source_excerpt", "text": "Trecho para revisão; não estabelece causa ou correção geral.",
                                     "evidence": locate(source, quote)})
                    seen.add(quote)
                break
            if len(evidence) >= 8:
                break
        if not evidence:
            continue
        cards.append(dict(key="candidate-" + digest([module, label])[:16], title=f"Revisar: {label}",
                          module=module, kind="candidate", aliases=[label],
                          summary=f"Agrupamento por assinatura em {len(members)} fontes. Casos podem ter causas diferentes.",
                          source_count=len(members), source_keys=[s["source_key"] for s in members],
                          claims=evidence, flags=sorted({f for s in members for f in s["flags"]}),
                          evidence_level="unreviewed_cluster", review_requirement="write_specific_case_before_approval",
                          limitations=["Assinatura compartilhada não comprova mesma causa ou mesma solução.",
                                        "Textos de origem podem conter dados internos e orientações específicas de um cliente."], related=[]))
    return cards, len(groups)


def validate_card(card, sources):
    for claim in card["claims"]:
        ev = claim["evidence"]
        src = sources[ev["source"]]
        segment = next(s for s in src["segments"] if s["id"] == ev["segment"])
        if src["content_hash"] != ev["source_hash"] or segment["text"][ev["start"]:ev["end"]] != ev["quote"]:
            raise ValueError(f"Broken evidence: {card['key']}")


def save_card(conn, tenant, card):
    revision = digest(card)
    conn.execute("""INSERT INTO knowledge_curation.cards(tenant_id,card_key,title,module,kind,revision,document)
         VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,card_key) DO UPDATE SET
         title=excluded.title,module=excluded.module,kind=excluded.kind,revision=excluded.revision,document=excluded.document,
         status=CASE WHEN knowledge_curation.cards.status='draft' THEN 'draft' ELSE 'needs_review' END,updated_at=now()
         WHERE knowledge_curation.cards.revision<>excluded.revision""",
         (tenant, card["key"], card["title"], card["module"], card["kind"], revision, Jsonb(card)))
    conn.execute("""INSERT INTO knowledge_curation.card_versions(tenant_id,card_key,revision,document)
        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (tenant, card["key"], revision, Jsonb(card)))


def build(conn, tenant, limit):
    sources = load_sources(conn, tenant)
    cards = curated_cards(sources)
    candidates, groups = candidate_cards(sources, limit)
    cards += candidates
    for card in cards:
        validate_card(card, sources)
    for s in sources.values():
        conn.execute("""INSERT INTO knowledge_curation.sources
            (tenant_id,source_key,title,source_type,external_id,url,source_date,content_hash,segments,signatures,flags)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,source_key) DO UPDATE SET
            title=excluded.title,source_date=excluded.source_date,content_hash=excluded.content_hash,
            segments=excluded.segments,signatures=excluded.signatures,flags=excluded.flags,refreshed_at=now()
            WHERE knowledge_curation.sources.content_hash<>excluded.content_hash OR knowledge_curation.sources.flags<>excluded.flags""",
            (tenant,s["source_key"],s["title"],s["source_type"],s["external_id"],s["url"],s["source_date"],s["content_hash"],Jsonb(s["segments"]),Jsonb(s["signatures"]),Jsonb(s["flags"])))
    for card in cards:
        save_card(conn, tenant, card)
    # Previously reviewed cards outside today's candidate cutoff must also go stale.
    for row in conn.execute("SELECT card_key,document FROM knowledge_curation.cards WHERE tenant_id=%s AND status='approved'", (tenant,)).fetchall():
        if any(c["evidence"]["source"] not in sources or
               c["evidence"]["source_hash"] != sources[c["evidence"]["source"]]["content_hash"]
               for c in row["document"]["claims"]):
            conn.execute("UPDATE knowledge_curation.cards SET status='needs_review' WHERE tenant_id=%s AND card_key=%s", (tenant,row["card_key"]))
    metrics = dict(sources=len(sources), tickets=sum(s["source_type"]=="ticket" for s in sources.values()),
                   kb=sum(s["source_type"]=="kb" for s in sources.values()), signature_groups=groups,
                   curated_cards=len(cards)-len(candidates), candidate_cards=len(candidates),
                   verified_quotes=sum(len(c["claims"]) for c in cards),
                   flags=dict(Counter(f for s in sources.values() for f in s["flags"])),
                   coverage="NF-e/Safetech selected by title plus explicit seed references; not full corpus",
                   generated_at=datetime.now(timezone.utc).isoformat())
    conn.execute("INSERT INTO knowledge_curation.runs(tenant_id,metrics) VALUES(%s,%s)", (tenant,Jsonb(metrics)))
    return metrics


def all_cards(conn, tenant):
    return [dict(r["document"], status=r["status"], revision=r["revision"]) for r in conn.execute(
        "SELECT document,status,revision FROM knowledge_curation.cards WHERE tenant_id=%s ORDER BY kind DESC,title", (tenant,))]


def search(cards, query, approved_only=False):
    words = set(re.findall(r"[a-z0-9_]+", norm(query))) - STOP
    ranked = []
    for card in cards:
        if approved_only and card["status"] != "approved":
            continue
        title = norm(card["title"] + " " + " ".join(card["aliases"]))
        body = norm(card["summary"] + " " + " ".join(c["text"] + " " + c["evidence"]["quote"] for c in card["claims"]))
        score = sum(5 if w in title else 1 if w in body else 0 for w in words)
        score += sum(20 for a in card["aliases"] if len(a)>5 and norm(a) in norm(query))
        if score:
            score += 4 if card["kind"] == "curated" else 0
            ranked.append((score,card))
    return sorted(ranked, key=lambda x: (-x[0],x[1]["key"]))


def review(conn, tenant, key, revision, reviewer, decision, note):
    if not reviewer.strip() or not note.strip():
        raise ValueError("Reviewer and justification are required")
    row = conn.execute("SELECT * FROM knowledge_curation.cards WHERE tenant_id=%s AND card_key=%s FOR UPDATE", (tenant,key)).fetchone()
    if not row or row["revision"] != revision:
        raise ValueError("Ficha inexistente ou revisão desatualizada; reabra a ficha")
    if decision == "approved" and row["kind"] != "curated":
        raise ValueError("Agrupamentos automáticos precisam ser convertidos em ficha específica antes de aprovação")
    # Verify against current ORIGINAL records, not only the cached snapshots.
    source_keys = {c["evidence"]["source"] for c in row["document"]["claims"]}
    sources = load_sources(conn, tenant) if decision == "approved" else {}
    if decision == "approved":
        if not source_keys <= sources.keys():
            raise ValueError("Fonte removida ou fora do escopo; reconstrua o piloto")
        validate_card(row["document"], sources)
    conn.execute("INSERT INTO knowledge_curation.reviews(tenant_id,card_key,revision,reviewer,decision,note) VALUES(%s,%s,%s,%s,%s,%s)",
                 (tenant,key,revision,reviewer,decision,note))
    conn.execute("UPDATE knowledge_curation.cards SET status=%s,updated_at=now() WHERE tenant_id=%s AND card_key=%s", (decision,tenant,key))


def export(conn, tenant):
    OUT.mkdir(parents=True, exist_ok=True)
    cards = all_cards(conn,tenant)
    run = conn.execute("SELECT metrics FROM knowledge_curation.runs WHERE tenant_id=%s ORDER BY id DESC LIMIT 1", (tenant,)).fetchone()
    payload = dict(tenant_id=str(tenant),metrics=run["metrics"] if run else {},cards=cards)
    # Safe inside script tag, including untrusted source contents.
    encoded = json.dumps(payload,ensure_ascii=False).replace("<","\\u003c").replace("\u2028","\\u2028").replace("\u2029","\\u2029")
    page = (ROOT / "curation_view.html").read_text(encoding="utf-8").replace("__CURATION_DATA__",encoded)
    (OUT / "index.html").write_text(page,encoding="utf-8")
    (OUT / "catalog.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    return str(OUT / "index.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant",help="Tenant UUID; required if more than one tenant exists")
    sub = parser.add_subparsers(dest="command",required=True)
    b = sub.add_parser("build"); b.add_argument("--candidates",type=int,default=40)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--approved-only",action="store_true")
    sub.add_parser("export")
    r = sub.add_parser("review")
    for arg in ("key","revision","reviewer","note"):
        r.add_argument("--"+arg,required=True)
    r.add_argument("--decision",choices=["approved","rejected","needs_review"],required=True)
    args = parser.parse_args()
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row,options="-c statement_timeout=120000") as conn:
        tenants = [str(r["tenant_id"]) for r in conn.execute("SELECT DISTINCT tenant_id FROM raw_tickets")]
        tenant = args.tenant or (tenants[0] if len(tenants)==1 else None)
        if tenant not in tenants:
            parser.error("Specify a valid --tenant; implicit cross-tenant access is not supported")
        if args.command == "build":
            if not 0 <= args.candidates <= 500:
                parser.error("--candidates must be between 0 and 500")
            conn.execute(SCHEMA.read_text(encoding="utf-8"))
            print(json.dumps(build(conn,tenant,args.candidates),ensure_ascii=False,indent=2))
            print(export(conn,tenant))
        elif args.command == "export":
            print(export(conn,tenant))
        elif args.command == "search":
            for score,card in search(all_cards(conn,tenant),args.query,args.approved_only)[:5]:
                print(json.dumps(dict(key=card["key"],title=card["title"],score=score,status=card["status"],summary=card["summary"]),ensure_ascii=False))
        else:
            review(conn,tenant,args.key,args.revision,args.reviewer,args.decision,args.note)
            print(export(conn,tenant))


if __name__ == "__main__":
    main()
