"""
DeskCortex -- ChatSession: Estado de Investigacao Multi-Turn (Fase 2)
======================================================================
Dataclasses que representam uma sessao de chat investigativo.

Separacao fundamental (deliberada):
  - ChatSession  : historico de mensagens (memoria da conversa)
  - InvestigationState : mapa causal do problema (estado da investigacao)
  - Evidence     : distingue Fato / Evidencia / Hipotese / Confirmacao / Conclusao
  - Hypothesis   : causa possivel com confidence score (0.0 a 1.0)

Trava T1: confidence > 0.80 sozinho NAO confirma causa.
Precisa de: confidence alta + evidencia compativel + ausencia de conflito.

Trava T2: o Manual e fonte de interpretacao, NAO evidencia do caso.
O manual explica o que SKU_NOT_FOUND significa.
O cliente confirmando que viu SKU_NOT_FOUND e a evidencia.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid
import threading


# -------------------------------------------------------------------------
# Enums de dominio
# -------------------------------------------------------------------------

class EvidenceType(str, Enum):
    FATO         = "fato"           # informacao confirmada pelo usuario ou sistema
    EVIDENCIA    = "evidencia"      # dado tecnico observado (log, tela, erro)
    HIPOTESE     = "hipotese"       # inferencia do modelo (nao confirmada)
    CONFIRMACAO  = "confirmacao"    # hipotese validada por evidencia adicional
    CONCLUSAO    = "conclusao"      # causa raiz definitivamente confirmada


class ResolutionStatus(str, Enum):
    OPEN         = "open"
    INVESTIGATING = "investigating"
    PRESCRIBING  = "prescribing"    # causa confirmada, prescrevendo solucao
    RESOLVED     = "resolved"
    ESCALATED    = "escalated"      # precisa de intervencao humana


class RiskLevel(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"     # ex: dados financeiros, NF autorizada com erro


# -------------------------------------------------------------------------
# Evidence: bloco atomico de conhecimento sobre o caso
# -------------------------------------------------------------------------

@dataclass
class Evidence:
    """
    Representa um pedaco de informacao sobre o caso atual.

    source:
      "usuario"  -- cliente relatou
      "sistema"  -- dado tecnico (log, erro, API)
      "manual"   -- o que o manual diz sobre esse tipo de situacao (fonte de interpretacao)

    IMPORTANTE (Trava T2):
      type="fato" so quando confirmado pelo usuario ou sistema.
      O manual NUNCA e source="manual" + type="fato" -- ele e sempre
      source="manual" + type="evidencia" (fornece contexto de interpretacao).
    """
    source: str                  # "usuario", "sistema", "manual"
    type: EvidenceType
    content: str
    confidence: float = 1.0      # 0.0 a 1.0 (relevante para hipoteses)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.source == "manual" and self.type == EvidenceType.FATO:
            raise ValueError(
                "Violacao da Trava T2: O manual e fonte de interpretacao "
                "e nao pode ser classificado como FATO."
            )

    @classmethod
    def from_dict(cls, data: dict) -> Evidence:
        ev = cls(
            source=data["source"],
            type=EvidenceType(data["type"]),
            content=data["content"],
            confidence=data.get("confidence", 1.0)
        )
        if "created_at" in data:
            if isinstance(data["created_at"], str):
                ev.created_at = datetime.fromisoformat(data["created_at"])
            else:
                ev.created_at = data["created_at"]
        return ev

    def is_fact(self) -> bool:
        return self.type in (EvidenceType.FATO, EvidenceType.CONFIRMACAO, EvidenceType.CONCLUSAO)

    def conflicts_with(self, other: Evidence) -> bool:
        """Heuristica simples -- pode ser expandida."""
        return False


# -------------------------------------------------------------------------
# Hypothesis: causa possivel com rastreabilidade
# -------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """
    Uma causa possivel para o problema investigado.

    confidence: probabilidade estimada (0.0 a 1.0)
    supporting_evidence: lista de conteudos de Evidence que suportam esta hipotese
    conflicting_evidence: lista de conteudos que contradizem esta hipotese

    Confirmacao (Trava T1):
      Uma hipotese so se torna causa confirmada quando:
      1. confidence >= 0.80 E
      2. Existe ao menos 1 Evidence compativel (tipo FATO ou CONFIRMACAO) E
      3. Nenhuma Evidence conflitante relevante existe
    """
    cause: str
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)

    def is_confirmable(self, evidence_list: list[Evidence]) -> bool:
        """
        Trava T1: confidence alta + evidencia compativel + sem conflito.
        """
        if self.confidence < 0.80:
            return False
        has_supporting = any(
            (e.is_fact() or (e.type == EvidenceType.EVIDENCIA and e.source == "sistema"))
            and any(s.lower() in e.content.lower() for s in self.supporting_evidence)
            for e in evidence_list
        )
        has_conflict = any(
            e.is_fact() and any(c.lower() in e.content.lower() for c in self.conflicting_evidence)
            for e in evidence_list
        )
        return has_supporting and not has_conflict

    @classmethod
    def from_dict(cls, data: dict) -> Hypothesis:
        return cls(
            cause=data["cause"],
            confidence=data["confidence"],
            supporting_evidence=data.get("supporting", []) if "supporting" in data else data.get("supporting_evidence", []),
            conflicting_evidence=data.get("conflicting", []) if "conflicting" in data else data.get("conflicting_evidence", [])
        )


# -------------------------------------------------------------------------
# InvestigationState: mapa causal estruturado do problema
# -------------------------------------------------------------------------

@dataclass
class InvestigationState:
    """
    Estado completo da investigacao -- vai alem da memoria da conversa.
    Representa o que o analista SABE sobre o problema, nao apenas o que foi dito.
    """
    intent: Optional[str]           = None   # "pedido_nao_integrado", "erro_nf", etc.
    product: str                    = "Master"
    module: Optional[str]           = None   # "Ecommerce", "Financeiro", "Nota_Fiscal"
    integration: Optional[str]      = None   # "AnyMarket", "VTEX", "B2W", "SKYHUB"
    entity: Optional[dict]          = None   # {"type": "order", "id": "123456"}
    symptoms: list[str]             = field(default_factory=list)
    evidence: list[Evidence]        = field(default_factory=list)
    missing_information: list[str]  = field(default_factory=list)
    hypotheses: list[Hypothesis]    = field(default_factory=list)
    eliminated_hypotheses: list[dict] = field(default_factory=list)
    investigation_step: int         = 0
    confirmed_cause: Optional[str]  = None
    recommended_action: Optional[str] = None
    risk_level: RiskLevel           = RiskLevel.LOW
    resolution_status: ResolutionStatus = ResolutionStatus.OPEN

    def add_evidence(self, source: str, type: EvidenceType, content: str, confidence: float = 1.0):
        self.evidence.append(Evidence(source=source, type=type, content=content, confidence=confidence))

    def add_hypothesis(self, cause: str, confidence: float, supporting: list[str] = None, conflicting: list[str] = None):
        for h in self.hypotheses:
            if h.cause == cause:
                h.confidence = max(h.confidence, confidence)
                if supporting:
                    h.supporting_evidence.extend(s for s in supporting if s not in h.supporting_evidence)
                if conflicting:
                    h.conflicting_evidence.extend(c for c in conflicting if c not in h.conflicting_evidence)
                return
        self.hypotheses.append(Hypothesis(
            cause=cause,
            confidence=confidence,
            supporting_evidence=supporting or [],
            conflicting_evidence=conflicting or [],
        ))

    def eliminate_hypothesis(self, cause: str, reason: str):
        self.hypotheses = [h for h in self.hypotheses if h.cause != cause]
        self.eliminated_hypotheses.append({"cause": cause, "reason": reason})

    def top_hypothesis(self) -> Optional[Hypothesis]:
        if not self.hypotheses:
            return None
        return max(self.hypotheses, key=lambda h: h.confidence)

    def check_confirmation(self) -> Optional[str]:
        """
        Trava T1: verifica se alguma hipotese pode ser confirmada como causa raiz.
        Retorna o nome da causa se confirmavel, None caso contrario.
        """
        for hyp in sorted(self.hypotheses, key=lambda h: h.confidence, reverse=True):
            if hyp.is_confirmable(self.evidence):
                return hyp.cause
        return None

    def to_prompt_context(self) -> str:
        """Serializa o estado para inclusao no prompt do LLM."""
        lines = [
            f"INTENT: {self.intent or 'desconhecido'}",
            f"MODULO: {self.module or 'nao identificado'}",
            f"INTEGRACAO: {self.integration or 'nao identificada'}",
            f"STATUS: {self.resolution_status.value}",
            f"RISCO: {self.risk_level.value}",
            f"ETAPA: {self.investigation_step}",
        ]
        if self.entity:
            lines.append(f"ENTIDADE: {self.entity.get('type','?')} #{self.entity.get('id','?')}")
        if self.symptoms:
            lines.append("SINTOMAS: " + "; ".join(self.symptoms))
        if self.evidence:
            lines.append("EVIDENCIAS:")
            for ev in self.evidence:
                lines.append(f"  [{ev.type.value.upper()}] ({ev.source}) {ev.content}")
        if self.missing_information:
            lines.append("INFORMACOES FALTANDO: " + "; ".join(self.missing_information))
        if self.hypotheses:
            lines.append("HIPOTESES (nao confirmadas ate prova em contrario):")
            for h in sorted(self.hypotheses, key=lambda x: x.confidence, reverse=True):
                lines.append(f"  - {h.cause} (confidence={h.confidence:.0%})")
        if self.eliminated_hypotheses:
            lines.append("HIPOTESES ELIMINADAS:")
            for eh in self.eliminated_hypotheses:
                lines.append(f"  - {eh['cause']} (Motivo: {eh['reason']})")
        if self.confirmed_cause:
            lines.append(f"CAUSA CONFIRMADA: {self.confirmed_cause}")
        if self.recommended_action:
            lines.append(f"ACAO RECOMENDADA: {self.recommended_action}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "product": self.product,
            "module": self.module,
            "integration": self.integration,
            "entity": self.entity,
            "symptoms": self.symptoms,
            "evidence": [
                {"source": e.source, "type": e.type.value, "content": e.content, "confidence": e.confidence}
                for e in self.evidence
            ],
            "missing_information": self.missing_information,
            "hypotheses": [
                {"cause": h.cause, "confidence": h.confidence,
                 "supporting": h.supporting_evidence,
                 "conflicting": h.conflicting_evidence}
                for h in self.hypotheses
            ],
            "eliminated_hypotheses": self.eliminated_hypotheses,
            "investigation_step": self.investigation_step,
            "confirmed_cause": self.confirmed_cause,
            "recommended_action": self.recommended_action,
            "risk_level": self.risk_level.value,
            "resolution_status": self.resolution_status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InvestigationState:
        state = cls()
        state.intent = data.get("intent")
        state.product = data.get("product", "Master")
        state.module = data.get("module")
        state.integration = data.get("integration")
        state.entity = data.get("entity")
        state.symptoms = data.get("symptoms", [])
        state.evidence = [Evidence.from_dict(e) for e in data.get("evidence", [])]
        state.missing_information = data.get("missing_information", [])
        state.hypotheses = [Hypothesis.from_dict(h) for h in data.get("hypotheses", [])]
        state.eliminated_hypotheses = data.get("eliminated_hypotheses", [])
        state.investigation_step = data.get("investigation_step", 0)
        state.confirmed_cause = data.get("confirmed_cause")
        state.recommended_action = data.get("recommended_action")
        state.risk_level = RiskLevel(data.get("risk_level", RiskLevel.LOW.value))
        state.resolution_status = ResolutionStatus(data.get("resolution_status", ResolutionStatus.OPEN.value))
        return state


# -------------------------------------------------------------------------
# Turn: um par mensagem/resposta com referencias de midia
# -------------------------------------------------------------------------

@dataclass
class MediaRef:
    path: str           # caminho relativo ao arquivo GIF/PNG
    media_type: str     # "gif", "png"
    title: str = ""
    module: str = ""
    screen: str = ""


@dataclass
class Turn:
    role: str                              # "user" | "assistant"
    content: str
    media_refs: list[MediaRef]             = field(default_factory=list)
    shortcuts: list[str]                   = field(default_factory=list)  # ["Ctrl+R", "F3"]
    screen_path: Optional[str]             = None   # "Gerenciamento > Pedidos > Ocorrencias"
    evidence_classified: list[dict]        = field(default_factory=list)
    created_at: datetime                   = field(default_factory=lambda: datetime.now(timezone.utc))


# -------------------------------------------------------------------------
# ChatSession: sessao completa de atendimento
# -------------------------------------------------------------------------

@dataclass
class ChatSession:
    """
    Sessao de atendimento investigativo.

    Separacao clara:
      turns  -> memoria da conversa (o que foi dito)
      state  -> mapa causal (o que sabemos sobre o problema)
    """
    session_id: str                        = field(default_factory=lambda: str(uuid.uuid4()))
    turns: list[Turn]                      = field(default_factory=list)
    # Internal notes must never enter state.to_dict() or the client LLM context.
    internal_notes: list[str]              = field(default_factory=list)
    client_ticket: dict                    = field(default_factory=dict)
    state: InvestigationState              = field(default_factory=InvestigationState)
    created_at: datetime                   = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime                = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_user_turn(self, content: str) -> Turn:
        t = Turn(role="user", content=content)
        self.turns.append(t)
        self.last_activity = datetime.now(timezone.utc)
        return t

    def add_assistant_turn(self, content: str, media_refs: list[MediaRef] = None,
                           shortcuts: list[str] = None, screen_path: str = None,
                           evidence_classified: list[dict] = None) -> Turn:
        t = Turn(
            role="assistant",
            content=content,
            media_refs=media_refs or [],
            shortcuts=shortcuts or [],
            screen_path=screen_path,
            evidence_classified=evidence_classified or [],
        )
        self.turns.append(t)
        self.last_activity = datetime.now(timezone.utc)
        return t

    def conversation_history_for_llm(self) -> list[dict]:
        """Formato compativel com Gemini (role/parts)."""
        msgs = []
        for turn in self.turns:
            role = "user" if turn.role == "user" else "model"
            msgs.append({"role": role, "parts": [{"text": turn.content}]})
        return msgs

    def is_expired(self, ttl_seconds: int = 7200) -> bool:
        delta = datetime.now(timezone.utc) - self.last_activity
        return delta.total_seconds() > ttl_seconds

    def to_response_dict(self, last_assistant_turn: Turn) -> dict:
        return {
            "session_id": self.session_id,
            "reply": last_assistant_turn.content,
            "media_refs": [
                {"path": m.path, "type": m.media_type, "title": m.title,
                 "module": m.module, "screen": m.screen}
                for m in last_assistant_turn.media_refs
            ],
            "shortcuts": last_assistant_turn.shortcuts,
            "screen_path": last_assistant_turn.screen_path,
            "evidence_classified": last_assistant_turn.evidence_classified,
            "investigation_state": self.state.to_dict(),
        }

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "investigation_state": self.state.to_dict(),
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChatSession:
        cs = cls(session_id=data["session_id"])
        if "investigation_state" in data:
            cs.state = InvestigationState.from_dict(data["investigation_state"])
        if "created_at" in data:
            cs.created_at = datetime.fromisoformat(data["created_at"]) if isinstance(data["created_at"], str) else data["created_at"]
        if "last_activity" in data:
            cs.last_activity = datetime.fromisoformat(data["last_activity"]) if isinstance(data["last_activity"], str) else data["last_activity"]
        return cs


# -------------------------------------------------------------------------
# SessionStore: armazenamento em memoria com TTL
# -------------------------------------------------------------------------

class SessionStore:
    """
    Armazenamento em memoria de sessoes ativas.
    TTL padrao: 2 horas de inatividade.
    Nota: para producao, substituir por Redis (Fase 3).
    """

    def __init__(self, ttl_seconds: int = 7200, max_sessions: int = 5000):
        self._sessions: dict[str, ChatSession] = {}
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.RLock()

    def get_or_create(self, session_id: str) -> ChatSession:
        with self._lock:
            if session_id and len(session_id) > 64:
                raise ValueError("session_id must be max 64 characters")
            self._evict_expired()
            if session_id and session_id in self._sessions:
                return self._sessions[session_id]
            if len(self._sessions) >= self.max_sessions:
                oldest_sid = min(self._sessions.keys(), key=lambda sid: self._sessions[sid].last_activity)
                self._sessions.pop(oldest_sid, None)
            session = ChatSession(session_id=session_id or str(uuid.uuid4()))
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> Optional[ChatSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def save(self, session: ChatSession):
        with self._lock:
            if session.session_id and len(session.session_id) > 64:
                raise ValueError("session_id must be max 64 characters")
            if session.session_id not in self._sessions and len(self._sessions) >= self.max_sessions:
                oldest_sid = min(self._sessions.keys(), key=lambda sid: self._sessions[sid].last_activity)
                self._sessions.pop(oldest_sid, None)
            self._sessions[session.session_id] = session

    def _evict_expired(self):
        expired = [sid for sid, s in self._sessions.items() if s.is_expired(self.ttl_seconds)]
        for sid in expired:
            self._sessions.pop(sid, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def client_tickets(self) -> list[dict]:
        with self._lock:
            self._evict_expired()
            return [{**s.client_ticket, 'session_id': s.session_id,
                     'created_at': s.created_at.isoformat(),
                     'last_activity': s.last_activity.isoformat()}
                    for s in reversed(list(self._sessions.values())) if s.client_ticket]


# Instancia global (importada pelo simulator_app e triage_engine)
session_store = SessionStore()
