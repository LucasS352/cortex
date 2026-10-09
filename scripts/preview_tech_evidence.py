"""Isolated UI verification with fictional tickets, no DB or AI calls.

Run: python scripts/preview_tech_evidence.py
Open: http://127.0.0.1:8001/qa/tech
Never use this harness as the application server.
"""
from contextlib import nullcontext
from pathlib import Path
import tempfile

import uvicorn
from fastapi.responses import HTMLResponse
import simulator_app
import tech_desk
from tech_evidence import cards

app = simulator_app.app
session = simulator_app.session_store.get_or_create("qa-fictional-ticket")
session.add_user_turn("Exemplo fictício: erro ao sincronizar pedido na integração")
session.add_assistant_turn("Conferir o vínculo do produto e repetir a sincronização após validar o cadastro.", evidence_classified=[{"node_id": 101}])
rows = [
    {"id": 101, "source_external_id": 900001, "title": "EXEMPLO FICTÍCIO — Pedido sem vínculo de produto", "modules": ["Ecommerce"], "solution": "Vínculo do SKU revisado no cadastro da integração."},
    {"id": 102, "source_external_id": 900002, "title": "EXEMPLO FICTÍCIO — Falha por credencial expirada", "modules": ["Ecommerce"], "solution": "O diagnóstico inicial não se aplicava. A credencial da integração havia expirado."},
]
tech_desk.connection = lambda: nullcontext(None)
def search_fixture(conn, query, provided_ids=(), offset=0):
    if query == "indisponivel":
        raise ValueError("Synthetic outage")
    matches = [] if query == "sem resultados" else [row for row in rows[1:] if not query.startswith('#') or str(row['source_external_id']) == query[1:]]
    return {"sources": cards(rows[:1], [101]), "items": cards(matches), "next_offset": None, "retrieval_status": "complete"}
tech_desk.retrieve = search_fixture
def detail_fixture(conn, node_id):
    row = next((r for r in rows if r["id"] == node_id), None)
    if not row:
        return None
    return {**cards([row])[0], "summary": {"solution": row["solution"]}, "original_status": "available",
            "status": "Resolvido (fictício)", "snapshot_at": "2026-09-09", "source_revision": "fixture-revision",
            "notice": "Dados fictícios exclusivos para teste da interface. Confira o texto original abaixo.",
            "actions": [{"id": 1, "author": "Técnico de exemplo", "created_at": "2026-09-01", "is_internal": False,
                         "text": row["solution"] + "\nEste é o texto original da ação de teste."}]}
tech_desk.detail = detail_fixture

@app.get('/qa/tech')
def preview():
    html = simulator_app.index().body.decode('utf-8')
    boot = "<script>currentClientSessionId='qa-fictional-ticket';switchTab('tech');loadLatestTechCase();</script>"
    return HTMLResponse(html.replace('</body>', boot + '</body>'))

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as tmp:
        tech_desk.REVIEW_DB = Path(tmp) / 'reviews.sqlite3'
        uvicorn.run(app, host='127.0.0.1', port=8001)
