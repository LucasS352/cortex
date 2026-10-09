"""Recognize customer outcomes before consulting old diagnostic evidence."""
import re
import unicodedata

RESOLVED_REPLY = (
    "Que bom que deu certo! Obrigado pelo retorno. "
    "Se precisar de mais alguma ajuda, é só responder neste chamado ou entrar em contato conosco."
)
ACKNOWLEDGED_REPLY = (
    "Por nada! Fico à disposição. Após testar, pode informar o resultado aqui no chamado. "
    "Se precisar de ajuda, é só entrar em contato."
)


def classify_outcome(message):
    text = ''.join(c for c in unicodedata.normalize('NFKD', message.lower())
                   if not unicodedata.combining(c))
    text = re.sub(r'\s+', ' ', text).strip()
    # Do not interpret quoted logs, questions, partial results or a new issue
    # as confirmation that the customer's whole request was resolved.
    if len(text) > 600 or re.search(r'[?"“”]|http/|\b(?:mas|porem|entretanto|so que|parcialmente|apenas|alguns|algumas)\b', text):
        return None
    # These phrases negate the error, not the success.
    remaining = re.sub(r'\b(?:nao (?:aparece|apareceu|ocorre|ocorreu|deu) mais (?:o )?erro|sem erros?)\b', '', text)
    if re.fullmatch(r'(?:muito )?(?:obrigad[oa][!,. ]*)?(?:ainda )?(?:vou testar|nao testei)[!,. ]*(?:obrigad[oa][!,. ]*)?', text):
        return 'acknowledged'
    if re.search(r'\b(?:nao|nunca|ainda|continua|continuou|persiste|persistiu|voltou|outro erro|outra duvida)\b',
                 re.sub(r'\bvoltou a funcionar\b', '', remaining)):
        # A promise to test is an acknowledgement, never a resolution.
        if not re.fullmatch(r'(?:obrigad[oa][!,. ]*)?(?:ainda )?nao testei[!,. ]*(?:obrigad[oa][!,. ]*)?', text):
            return None
        return 'acknowledged'
    if re.search(r'\b(?:vou|irei|vamos|preciso|falta)\s+(?:testar|verificar|conferir)|\b(?:se|quando|tomara|espero|talvez|acho)\b', text):
        if re.search(r'\b(?:vou|irei) testar\b', text) and not re.search(r'\b(?:erro|falha|problema|duvida)\b', remaining):
            return 'acknowledged'
        return None
    # Tolerate the common typo "deu ceerto" without fuzzy matching error text.
    success = re.search(r'\b(?:deu ce{1,2}rto|funcionou|(?:esta|ta|agora) funcionando|'
                        r'(?:(?:o )?(?:erro|problema) )?(?:foi |esta |ficou )?resolvid[oa]|consegui resolver|'
                        r'voltou a funcionar|sincronizou com sucesso|tudo certo)\b', remaining)
    if success:
        # An explicit new error wins over a success phrase in the same turn.
        residual = remaining[:success.start()] + remaining[success.end():]
        residual = re.sub(r'\bobrigad[oa](?: pela ajuda| pelo suporte| pelo retorno)?\b', '', residual)
        if re.search(r'\b(?:erro|falha|travou|pendente|duvida|ajuda|como|onde|preciso|quero|gostaria|pode|so)\b', residual):
            return None
        return 'resolved'
    polite = re.sub(r'\b(?:muito obrigad[oa]|obrigad[oa](?: pela ajuda| pelo suporte| pelo retorno)?|'
                    r'valeu|perfeito|beleza|ok|otimo|show|bom dia|boa tarde|boa noite|ate mais)\b', '', text)
    if text and not re.sub(r'[\s,!.;:]+', '', polite):
        return 'acknowledged'
    return None
