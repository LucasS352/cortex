"""Read-only, real-corpus retrieval and note presentation evaluation. No ticket writes."""
import json
import time
from pathlib import Path
import argparse
import html
import psycopg
from psycopg.rows import dict_row
from config import POSTGRES_DSN
from knowledge_access import search
from movidesk_note_worker import render_note
from technician_workbench import build_reply
from mapped_replies import match, note_images
from support_triage import intake

CASES = [
    ('produto', 'como faço pra criar um produto?', [207361,343261]),
    ('produto', 'como cadastrar produto no Master?', [207361,343261]),
    ('clientes', 'como cadastrar um cliente?', [207377]),
    ('clientes', 'quero criar um cliente', [207377]),
    ('fornecedores', 'como cadastrar fornecedor?', [207374]),
    ('estoque', 'como cadastrar unidade de estoque?', [207370]),
    ('grades', 'como cadastrar produtos e grades?', [207376]),
    ('kits', 'como cadastrar kits de produtos?', [207363,199959]),
    ('imagens', 'como cadastrar fotos de produtos?', [207362]),
    ('importacao', 'como importar cadastros de produtos?', [207368]),
    ('exportacao', 'como exportar cadastros de produtos?', [207366]),
    ('financeiro', 'como gerar boleto no fluxo de vendas?', [198734]),
    ('financeiro', 'como cadastrar contas a pagar avulsa?', [211377]),
    ('financeiro', 'como cadastrar plano de contas?', [444717]),
    ('estoque', 'como lançar inventário?', [211368]),
    ('estoque', 'como fazer transferência de estoque?', [212797]),
    ('pdv', 'como cancelar uma venda de balcão no pdv?', [212182]),
    ('integracao', 'Forma de pagamento Shopee PayLater não configurada', []),
    ('integracao', 'meu pedido não caiu no master', []),
    ('fiscal', 'IE do destinatário inválida', []),
    ('fiscal', 'Duplicidade de NF-e com diferença na Chave de Acesso', []),
    ('negativo', 'ORA-99999 SFT_OBJETO_NAO_DOCUMENTADO', []),
]


def run(generate=False, preview_path=None):
    results = []
    previews = []
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row,
                        options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        for category, question, expected in CASES:
            started = time.monotonic()
            recipe = match(question)
            triage = intake(question, question, [])
            rows = search(conn, question, top_k=5, maintain_index=False) if not recipe and not triage else []
            ids = [(r.get('metadata') or {}).get('external_id', r.get('source_external_id')) for r in rows]
            rank = next((i+1 for i, aid in enumerate(ids) if aid in expected), None)
            first = rows[0] if rows else {}
            response = triage
            if recipe:
                images, notices = note_images(recipe)
                response = {'reply': recipe['reviewed_reply'], 'images': images,
                            'similar_tickets': recipe['resolved_examples'], 'attachments': notices}
            elif first.get('applicable') and first.get('videos') and first.get('evidence_status') == 'video_reference_without_transcript':
                reply, _ = build_reply({}, '', [], [], rows, question, audience='customer_draft')
                response = {'reply': reply, 'videos': first['videos']}
            elif generate and rows:
                reply, meta = build_reply({}, '', [], [], rows, question, audience='customer_draft')
                response = {'reply': reply, 'generation': meta}
            if response and rows:
                response['videos'] = [v for source in rows if source.get('applicable') for v in source.get('videos', [])]
            note = render_note(response, 'evaluation') if response else ''
            previews.append('<section><h2>' + html.escape(question) + '</h2>' +
                            (note or '<p>Resposta ainda não gerada; revisar evidências recuperadas.</p>') + '</section>')
            results.append({'category': category, 'question': question, 'expected_articles': expected,
                'rank': rank, 'first_titles': [r['title'] for r in rows[:3]],
                'first_applicable': first.get('applicable'), 'reasons': first.get('applicability_reasons'),
                'mode': 'reviewed' if recipe else 'triage' if triage else 'video' if response else 'requires_generation' if rows else 'no_evidence',
                'images_rendered': note.count('<img '), 'videos_rendered': note.count('https://youtu.be/'),
                'source_ids': ids, 'reply_preview': response['reply'] if response else None,
                'milliseconds': round((time.monotonic()-started)*1000)})
    labeled = [r for r in results if r['expected_articles']]
    if preview_path:
        Path(preview_path).write_text('<!doctype html><meta charset="utf-8"><title>Cortex — simulações N1</title>'
            '<style>body{font:16px Arial;max-width:1000px;margin:30px auto;color:#24344a}section{border-top:2px solid #ccd;padding:24px 0}img{max-width:100%}</style>'
            '<h1>Simulações sobre o corpus local</h1><p>Prévia para revisão; nenhum chamado foi alterado.</p>' + ''.join(previews), encoding='utf-8')
    return {'scope': 'Local real corpus; read-only retrieval and note rendering. Optional model generation only when needed. No production delivery.',
            'generated_answers_enabled': generate, 'scenarios': len(results), 'retrieval_cases': len(labeled),
            'hit_at_5': sum(bool(r['rank']) for r in labeled)/len(labeled),
            'mrr_at_5': sum(1/r['rank'] if r['rank'] else 0 for r in labeled)/len(labeled),
            'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--preview')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    result = run(args.generate, args.preview)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'results'}, ensure_ascii=False) if args.summary else rendered)
