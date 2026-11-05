"""Evidence-first intake for integration symptoms, before historical retrieval."""
import re

import mapped_replies
from conversation_context import concrete_evidence


REPLY = '''Olá! Vamos localizar o retorno da integração para identificar por que o pedido não chegou ao Master.

1. No painel Sincronizador do Master, confira os indicadores dos serviços e clique em Ocorrências, no canto inferior esquerdo.
2. Na tela Ocorrências de integrações, filtre a coluna Integração pelo integrador do pedido.
3. Selecione o período correspondente ao pedido, informe o número da venda ou pedido no campo de busca e clique em Procurar.
4. Copie o texto completo da coluna Ocorrência com Ctrl+C e cole aqui, ou envie um print legível. Com esse retorno podemos indicar o ajuste e como validar a integração.

Se não aparecer nenhuma ocorrência, informe o integrador, o número do pedido e o período pesquisado. Se o serviço correspondente estiver vermelho, informe também esse detalhe. As imagens são exemplos: use os dados do seu pedido.'''


def has_explicit_mapping_evidence(text):
    """True when the customer already supplied enough evidence to skip intake.

    The generic Occurrences recipe is useful only for an unknown integration
    failure.  A concrete de/para symptom must proceed to grounded retrieval;
    otherwise the intake response masks the very evidence needed to find the
    correct procedure.
    """
    from support_policy import evidence
    return evidence(text)['concrete_error']


def intake(question, contextual_query, history, category="", service=""):
    text = mapped_replies.normalized(question)
    classification = mapped_replies.normalized(f'{category} {service}')
    commercial = re.search(r'\b(?:comercial|contratar modulo novo)\b', classification)
    credentials = re.search(r'\b(?:api|token)\b', text)
    provisioning = re.search(r'\b(?:solicitar|solicito|liberar|obter|fornecer|gerar|criar)\b.{0,65}\b(?:acessos?|credenciais|token|chave)\b', text)
    failure = re.search(r'\b(?:erro|falha|timeout|expirad[oa]|invalid[oa]|401|403|negado)\b', text)
    if commercial and credentials and provisioning and not failure and not concrete_evidence(contextual_query):
        return {'reply': 'Olá! Recebemos a solicitação de acesso para a integração. '
                         'A equipe responsável pela implantação precisa validar a liberação da API e do token.',
                'mode': 'triage', 'sources': [], 'attachments': [], 'evidence_scope': 'access_provisioning'}
    if (mapped_replies.match(contextual_query) or concrete_evidence(contextual_query)
            or has_explicit_mapping_evidence(contextual_query)):
        return None
    symptom = re.search(r'\b(pedido|venda)\b.{0,100}\bnao (?:esta )?(?:caiu|cai|entrou|chegou|apareceu|desceu|desce|baixou|integrou|integrando|integra|foi integrado)', text)
    if not symptom:
        return None
    # An already attempted intake needs targeted investigation, not the same recipe.
    if any('Ocorrências de integrações' in m.get('content', '') for m in history):
        return None
    if re.search(r'ja (?:conferi|consultei|pesquisei)|nao (?:ha|tem|aparece|apareceu) (?:nenhuma )?ocorrencia|servico.{0,30}(?:vermelho|parado)', text):
        return None
    images, notices = mapped_replies.note_images({'images': [
        {'file': f'ocorrencias-{step}.png', 'after_step': step,
         'caption': f'Passo {step}: tela de referência. Integrador, datas, códigos e mensagens são exemplos.'}
        for step in (1, 2, 3)]})
    return {'reply': REPLY, 'mode': 'triage', 'sources': [], 'similar_tickets': [],
            'images': images, 'attachments': notices, 'evidence_scope': 'collect_error'}
