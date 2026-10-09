"""Keep independent support issues separate inside the same conversation.

Reconstructed from message order, not client-supplied metadata. The initial
ticket subject only belongs to the first issue. The model and retrieval receive
messages belonging to the active issue; archived evidence stays in the timeline.
"""
import re
from dataclasses import dataclass, field

from conversation_outcome import classify_outcome
from knowledge_access import normalize, query_plan
from triage_router import route


def concrete_evidence(text):
    plan = query_plan(text)
    if any(plan[k] for k in ('signatures', 'identifiers', 'codes', 'objects')):
        return True
    value = normalize(text)
    return bool(re.search(r'http(?:/\d\.\d)?\s*[: -]?\s*[45]\d\d|\b(?:bad request|unauthorized|forbidden|exception|access violation|timeout)\b', value))


def reports_problem(text):
    return bool(re.search(r'\b(?:erro|falha|problema|rejeicao|travou|nao consigo|nao funciona|nao sobe|nao subiu|nao desce|nao desceu)\b', normalize(text)))


def topics(text):
    result = route('', text)
    found = {key for key, hits in result['signals'].items() if hits}
    # Registration/product terms occur across domains and do not on their own
    # make fiscal and integration problems the same investigation.
    specific = found - {'registration', 'products', 'sales'}
    return specific or found


@dataclass
class Issue:
    id: int
    subject: str
    modules: set = field(default_factory=set)
    messages: list = field(default_factory=list)
    status: str = 'open'


def active_context(subject, messages):
    issues = []
    active = None
    pending_topic = None
    transition = 'initial'
    for message in messages:
        if message.get('role') != 'client':
            if active:
                active.messages.append(message)
            continue
        text = message.get('content', '')
        value = normalize(text)
        outcome = classify_outcome(text)
        detected = topics(text)
        explicit_new = bool(re.search(r'\b(?:outr[oa] (?:erro|problema|duvida|assunto|coisa|pergunta|questao)|'
                                      r'novo (?:erro|problema|assunto)|nova duvida|mudando de assunto|aproveitando)\b', value))
        if explicit_new and not detected and pending_topic:
            detected = topics(pending_topic)
        resume = bool(re.search(r'\b(?:voltando|retomando|sobre o (?:erro|problema) anterior)\b', value))
        recurrence = bool(re.search(r'\b(?:voltou|novamente|mesmo erro|mesmo problema)\b', value))
        # Short references without a symptom may refer to the existing issue.
        # Ask for clarification instead of silently mixing disjoint domains.
        unrelated = bool(active and detected - {'registration','products','sales'}
                         and active.modules and detected.isdisjoint(active.modules))
        new = active is None or (outcome is None and (explicit_new or (unrelated and reports_problem(text))))
        transition = 'continue'
        if resume and detected:
            candidates = [issue for issue in issues if issue is not active and issue.modules & detected]
            if len(candidates) == 1:
                active = candidates[0]
                active.status = 'open'
                new = False
                transition = 'resume'
        if active and active.status == 'resolved' and outcome is None and not recurrence and not resume:
            new = True
        if new:
            first = not issues
            if active and explicit_new:
                prefix = re.split(r'\b(?:mas|agora|outro|outra)\b', text, maxsplit=1, flags=re.I)[0]
                if classify_outcome(prefix) == 'resolved': active.status = 'resolved'
            active = Issue(len(issues) + 1, subject if first else ((pending_topic + '\n' + text) if pending_topic else text)[:300],
                           topics(subject + ' ' + text) if first else detected)
            issues.append(active)
            pending_topic = None
            transition = 'initial' if first else 'new_issue'
        elif unrelated and outcome is None and not concrete_evidence(text) and transition != 'resume':
            transition = 'clarify_topic'
            pending_topic = text
        active.messages.append(message)
        if detected and transition != 'clarify_topic':
            active.modules |= detected
        if outcome == 'resolved':
            active.status = 'resolved'
        elif outcome is None and active.status == 'resolved':
            active.status = 'open'
            transition = 'reopened' if transition == 'continue' else transition
    if active is None:
        active = Issue(1, subject, topics(subject), list(messages))
        issues.append(active)
    return active, {
        'active_issue_id': active.id, 'transition': transition,
        'issues': [{'id': item.id, 'subject': item.subject, 'modules': sorted(item.modules), 'status': item.status}
                   for item in issues],
    }
