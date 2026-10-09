"""Export the official KB video work queue without treating videos as validated procedures."""
from __future__ import annotations

import json
import re
from pathlib import Path

import knowledge_curation as curation

OUT = Path(__file__).parent / "output" / "video_knowledge_inventory.json"


def build_inventory():
    curation.ensure_schema()
    with curation.connect(read_only=True) as conn:
        rows = conn.execute("""SELECT kn.id,kn.title,raw.external_id,raw.content_html,
            c.status,c.revision,c.solution_override,c.version_note
            FROM raw_kb_articles raw
            JOIN ticket_knowledge_nodes kn ON kn.source_type='OFFICIAL_KB'
                AND (kn.metadata->>'external_id')::bigint=raw.external_id
            LEFT JOIN knowledge_curation c ON c.node_id=kn.id
            WHERE raw.content_html LIKE '%%youtube.com/embed/%%'
            ORDER BY raw.external_id""").fetchall()
    result = []
    for row in rows:
        match = re.search(r'youtube\.com/embed/([A-Za-z0-9_-]{11})', row['content_html'] or '')
        if not match:
            continue
        frame_dir = Path(__file__).parent / 'output' / 'curation_media' / str(row['id'])
        frames = sorted(frame_dir.glob('frame-*.jpg')) if frame_dir.exists() else []
        error_file = frame_dir / 'extraction_error.json'
        error = json.loads(error_file.read_text(encoding='utf-8')) if error_file.exists() else None
        result.append({
            'node_id': row['id'], 'article_id': row['external_id'], 'title': row['title'],
            'video_id': match.group(1), 'video_url': f'https://www.youtube.com/watch?v={match.group(1)}',
            'status': row['status'] or 'not_started', 'revision': row['revision'] or 0,
            'has_step_by_step': bool((row['solution_override'] or '').strip()),
            'frame_count': len(frames), 'version_note': row['version_note'],
            'extraction_status': 'frames_ready' if frames else 'source_error' if error else 'not_started',
            'extraction_error': error['message'] if error else None,
        })
    return result


if __name__ == '__main__':
    items = build_inventory()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{len(items)} artigos, {len({x["video_id"] for x in items})} vídeos distintos, '
          f'{sum(x["has_step_by_step"] for x in items)} rascunhos com passos; {OUT}')
