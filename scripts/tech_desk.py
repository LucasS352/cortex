"""Internal technician routes; isolated from model generation and client payloads."""
import json
import logging
import sqlite3
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from chat_session import session_store, ResolutionStatus
from config import POSTGRES_DSN
from tech_evidence import retrieve, detail, latest_references, revision

router = APIRouter(prefix="/api/tech")
logger = logging.getLogger(__name__)
REVIEW_DB = Path(__file__).parent / "output" / "tech_reviews.sqlite3"


def connection():
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=4,
                           options="-c statement_timeout=5000 -c default_transaction_read_only=on")


def session_or_404(session_id):
    session = session_store.get(session_id)
    if not session or session.is_expired():
        raise HTTPException(404, "Sessão não encontrada ou expirada.")
    return session


@router.get("/references/{session_id}")
def references(session_id: str, q: str = Query("", max_length=2000), offset: int = Query(0, ge=0, le=10000)):
    session = session_or_404(session_id)
    original = next((t.content for t in session.turns if t.role == "user"), "")
    query = q.strip() or original[:2000]
    try:
        with connection() as conn:
            return {**retrieve(conn, query, latest_references(session), offset), "revision": revision(session)}
    except (psycopg.Error, ValueError):
        logger.warning("Base de evidências indisponível; revisão manual continua disponível.")
        raise HTTPException(503, "Não foi possível consultar o histórico. Tente novamente; seu rascunho foi preservado.")


@router.get("/evidence/{node_id}")
def evidence(node_id: int):
    if node_id <= 0:
        raise HTTPException(422, "Referência inválida.")
    try:
        with connection() as conn:
            result = detail(conn, node_id)
    except (psycopg.Error, ValueError):
        raise HTTPException(503, "Histórico indisponível no momento.")
    if result is None:
        raise HTTPException(404, "Referência não encontrada.")
    return result


class ReviewRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=16, max_length=64)
    final_response: str = Field(min_length=1, max_length=20000)
    selected_evidence_ids: list[int] = Field(default_factory=list, max_length=30)
    decision: Literal["approved", "edited", "manual"]
    correction_reason: str = Field(default="", max_length=4000)


def review_connection():
    REVIEW_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REVIEW_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS reviews (
        request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, revision TEXT NOT NULL,
        request_json TEXT NOT NULL, original_response TEXT NOT NULL, evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(session_id, revision))""")
    return conn


@router.post("/review")
def review(req: ReviewRequest):
    session = session_or_404(req.session_id)
    body = json.dumps(req.model_dump(), sort_keys=True, ensure_ascii=False)
    if not req.final_response.strip():
        raise HTTPException(422, "Escreva a resposta revisada.")
    if req.decision == "edited" and not req.correction_reason.strip():
        raise HTTPException(422, "Informe o motivo da correção para a curadoria.")
    conn = review_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT request_json FROM reviews WHERE request_id = ?", (req.request_id,)).fetchone()
        if prior:
            if prior["request_json"] != body:
                raise HTTPException(409, "Esta chave já foi usada para outra revisão.")
            return {"status": "saved", "request_id": req.request_id, "delivery_status": "not_sent"}
        if revision(session) != req.expected_revision:
            raise HTTPException(409, "O atendimento mudou. Recarregue o caso e confira a nova pré-resposta.")
        if conn.execute("SELECT 1 FROM reviews WHERE session_id = ? AND revision = ?", (req.session_id, req.expected_revision)).fetchone():
            raise HTTPException(409, "Esta versão já foi revisada por outro atendimento.")
        snapshots = []
        if req.selected_evidence_ids:
            try:
                with connection() as db:
                    for node_id in dict.fromkeys(req.selected_evidence_ids):
                        item = detail(db, node_id)
                        if not item:
                            raise HTTPException(422, "Uma referência selecionada não está mais disponível.")
                        snapshots.append({"node_id": node_id, "source_revision": item["source_revision"], "summary": item["summary"]})
            except (psycopg.Error, ValueError):
                raise HTTPException(503, "Não foi possível validar as referências selecionadas. Tente novamente.")
        original = next((t.content for t in reversed(session.turns) if t.role == "assistant"), "")
        if req.decision == "approved" and req.final_response != original:
            raise HTTPException(422, "Resposta alterada deve ser registrada como editada.")
        if revision(session) != req.expected_revision:
            raise HTTPException(409, "O atendimento mudou durante a revisão. Recarregue o caso.")
        conn.execute("INSERT INTO reviews(request_id,session_id,revision,request_json,original_response,evidence_json) VALUES(?,?,?,?,?,?)",
                     (req.request_id, req.session_id, req.expected_revision, body, original, json.dumps(snapshots, ensure_ascii=False)))
        conn.commit()
        # Approval of text is not confirmation of resolution, nor external delivery.
        session.state.resolution_status = ResolutionStatus.PRESCRIBING
        return {"status": "saved", "request_id": req.request_id, "delivery_status": "not_sent"}
    finally:
        conn.close()
