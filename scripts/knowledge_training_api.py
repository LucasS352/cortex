"""API do Ambiente de Treinamento do CORTEX (/treinamento). Protegida pelo web_auth.gate global."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

import knowledge_training as kt
from technician_workbench import actor

router = APIRouter(prefix='/api/treinamento', tags=['Treinamento da IA'])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        detail = json.loads(str(exc))
        raise HTTPException(409, {'message': f"Ticket com baixa chance de virar conhecimento ({detail['score']}/100). "
                                             'Confirme para forçar a análise.', **detail})
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


class Applicability(BaseModel):
    master_version: str | None = Field(default=None, max_length=40)
    ufs: list[str] = Field(default_factory=list, max_length=27)
    integrations: list[str] = Field(default_factory=list, max_length=20)
    tax_regime: str | None = Field(default=None, max_length=60)
    valid_from: str | None = Field(default=None, max_length=10)
    valid_until: str | None = Field(default=None, max_length=10)


class ProcedureImage(BaseModel):
    file: str = Field(min_length=1, max_length=100)
    caption: str = Field(min_length=3, max_length=300)
    after_step: int = Field(ge=1, le=100)


class Content(BaseModel):
    images: list[ProcedureImage] = Field(default_factory=list, max_length=10)
    title: str = Field(min_length=5, max_length=200)
    module_key: str
    subtopic_key: str = 'general'
    symptom: str = Field(min_length=5, max_length=6000)
    error_messages: list[str] = Field(default_factory=list, max_length=15)
    root_cause: str = Field(min_length=5, max_length=6000)
    procedure: str = Field(min_length=5, max_length=10000)
    prerequisites: str | None = Field(default=None, max_length=4000)
    expected_result: str | None = Field(default=None, max_length=4000)
    applicability: Applicability = Field(default_factory=Applicability)
    keywords: list[str] = Field(default_factory=list, max_length=30)


class NewCandidate(BaseModel):
    ticket_external_id: int = Field(ge=1)
    force: bool = False


class Decision(BaseModel):
    decision: Literal['new', 'revise', 'attach']
    reason: str = Field(min_length=5, max_length=1000)
    target_item_id: int | None = None
    content: Content | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


class Revision(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)
    content: Content


class Feedback(BaseModel):
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=1000)
    ticket_external_id: int | None = None


class ConflictAction(BaseModel):
    note: str = Field(min_length=5, max_length=1000)
    dismiss: bool = False


def _content(model: Content | None):
    if model is None:
        return {}
    data = model.model_dump()
    data['applicability'] = {k: v for k, v in data['applicability'].items() if v not in (None, [], '')}
    return data


@router.get('/overview')
def overview():
    return _guard(kt.overview)


@router.get('/sugestoes')
def suggestions(module: str | None = None, limit: int = Query(30, ge=1, le=100)):
    return _guard(kt.suggest_tickets, limit=limit, module=module)


@router.get('/candidatos')
def candidates(module: str | None = None, status: str = 'open'):
    statuses = ('pending', 'conflict') if status == 'open' else (status,)
    return _guard(kt.list_candidates, statuses, module)


@router.post('/candidatos')
def create_candidate(body: NewCandidate, request: Request):
    """Botão 'Transformar em Conhecimento'."""
    return _guard(kt.create_candidate, body.ticket_external_id, actor(request), body.force)


@router.get('/candidatos/{candidate_id}')
def candidate(candidate_id: int):
    return _guard(kt.get_candidate, candidate_id)


@router.post('/candidatos/{candidate_id}/aprovar')
def approve(candidate_id: int, body: Decision, request: Request):
    if body.decision != 'attach' and body.content is None:
        raise HTTPException(422, 'Envie o conteúdo revisado.')
    return _guard(kt.approve, candidate_id, body.decision, _content(body.content), actor(request), body.reason, body.target_item_id)


@router.post('/candidatos/{candidate_id}/rejeitar')
def reject(candidate_id: int, body: Reason, request: Request):
    _guard(kt.reject, candidate_id, actor(request), body.reason)
    return {'ok': True}


@router.get('/itens')
def items(module: str | None = None, subtopic: str | None = None, status: str = 'active', q: str | None = None):
    return _guard(kt.list_items, module, subtopic, status, (q or '').strip()[:120] or None)


@router.get('/itens/{item_id}')
def item(item_id: int):
    return _guard(kt.item_detail, item_id)


@router.post('/itens/{item_id}/revisar')
def revise(item_id: int, body: Revision, request: Request):
    return _guard(kt.revise_item, item_id, _content(body.content), actor(request), body.reason)


@router.post('/itens/{item_id}/inativar')
def deprecate(item_id: int, body: Reason, request: Request):
    _guard(kt.deprecate, item_id, actor(request), body.reason)
    return {'ok': True}


@router.post('/itens/{item_id}/feedback')
def feedback(item_id: int, body: Feedback, request: Request):
    return _guard(kt.feedback, item_id, body.rating, actor(request), body.comment, body.ticket_external_id)


@router.post('/conflitos/{conflict_id}')
def conflict(conflict_id: int, body: ConflictAction, request: Request):
    _guard(kt.resolve_conflict, conflict_id, actor(request), body.note, body.dismiss)
    return {'ok': True}


@router.get('/taxonomia')
def taxonomy():
    return _guard(kt.taxonomy_options)


class ModuleEdit(BaseModel):
    key: str = Field(min_length=2, max_length=50)
    label: str = Field(min_length=2, max_length=80)
    description: str = Field(default='', max_length=500)


class AnswerReview(BaseModel):
    action_id: str = Field(min_length=1, max_length=80)
    rating: Literal[-1, 1]
    module_key: str = Field(min_length=2, max_length=50)
    comment: str = Field(default='', max_length=1000)


@router.post('/taxonomia')
def save_module(body: ModuleEdit, request: Request):
    return _guard(kt.save_module, body.key, body.label, body.description, actor(request))


@router.get('/tickets/{ticket_id}/respostas')
def ticket_answers(ticket_id: int):
    return _guard(kt.ticket_answers, ticket_id)


@router.post('/tickets/{ticket_id}/avaliar')
def review_answer(ticket_id: int, body: AnswerReview, request: Request):
    return _guard(kt.review_answer, ticket_id, body.action_id, body.rating, body.module_key, body.comment, actor(request))


@router.get('/avaliacoes')
def answer_reviews():
    return _guard(kt.answer_reviews)


@router.get('/imagens')
def procedure_images():
    return kt.procedure_images()
