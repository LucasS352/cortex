"""Evidence-bound synthesis: no source-free procedures or confidence percentages."""
from html import escape
import re
from knowledge_access import applicability, assessment, quality


class TriageSynthesizer:
    @staticmethod
    def clean_raw_solution(raw_text):
        text = re.split(r'obrigado por entrar|voc[eê] j[aá]|atenciosamente|at\.te|__', raw_text or '', flags=re.I)[0]
        return text.strip()

    @classmethod
    def synthesize(cls, subject, description, error_code=None, requester_name='Cliente',
                   company_name='Empresa', search_results=None, master_env=None):
        query = f'{description}\n{subject}'
        sources = []
        for row in search_results or []:
            item = dict(row)
            if not item.get('answer_use'):
                item.update(quality(row))
            item.update(applicability(row, query))
            sources.append(item)
        decision = assessment(sources)
        first_name = escape((requester_name or 'Cliente').split()[0])
        steps, shortcuts, screen = [], [], ''
        if not decision['can_support_procedure']:
            diagnosis = 'Evidência insuficiente para confirmar a causa e orientar alterações.'
            reply = ('Recebi as informações do chamado. As referências disponíveis precisam de conferência '
                     'técnica antes de indicar uma correção para este caso.')
        else:
            source = sources[0]
            diagnosis = 'Referência correspondente encontrada; causa no cliente ainda precisa de validação.'
            # Imported conversations may contain another customer's details.
            # Only official or editorially reviewed wording can be quoted as
            # a procedure without a separate synthesis/review step.
            reviewed = source.get('source_type') in ('OFFICIAL_KB','KB') or (source.get('metadata') or {}).get('reviewed_summary')
            if reviewed:
                procedure = cls.clean_raw_solution(source.get('solution') or source.get('content_text'))
                steps = [procedure] if procedure else []
                reply = 'A referência correspondente registra o procedimento abaixo. Confirme a aplicabilidade ao seu ambiente antes de executá-lo.'
                screen = source.get('screen_path') or ''
                shortcuts = source.get('shortcut_keys') or []
            else:
                reply = 'Localizei um histórico relacionado. O procedimento precisa de revisão técnica antes de ser aplicado ao seu ambiente.'
        confidence = 'Não estimada'
        steps_html = ''.join(f'<li>{escape(s)}</li>' for s in steps)
        client_html = f'<p>Olá, {first_name}!</p><p>{escape(reply)}</p>' + (f'<ol>{steps_html}</ol>' if steps else '')
        internal_html = f'<p>{escape(diagnosis)}</p><p>Confiança diagnóstica: não estimada.</p>'
        if steps:
            internal_html += f'<ol>{steps_html}</ol>'
        return dict(diagnosis=diagnosis, procedure_steps=steps, screen_path=screen, shortcuts=shortcuts,
                    confidence=confidence, internal_action_html=internal_html,
                    client_ready_response=client_html, needs_human=True, evidence_assessment=decision)
