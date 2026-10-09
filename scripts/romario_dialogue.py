"""
DeskCortex — RomarioDialogueEngine: Motor de Diálogo Humanizado do Romário
==========================================================================
Simula com máxima fidelidade e empatia o atendimento de um Analista de
Suporte Técnico Sênior da Alternativa Sistemas (Romário).

Comportamento:
  - 100% empático, conversacional, fluido e acolhedor (humano para humano).
  - Nunca soa como um robô, questionário mecânico ou URA.
  - Entende dúvidas de usabilidade ("como faço para ver isso?", "onde olho?").
  - Compreende causalidade técnica (ex: se o pedido não consta na AnyMarket,
    o ERP Master não tem como importar; o problema está entre o marketplace e o hub).
  - Orienta telas, menus e atalhos reais (F3, Gerenciamento > Pedidos, De-para).
  - Convida ativamente o cliente a colar prints com Ctrl+V.
  - Varia dinamicamente a linguagem para não parecer repetitivo.
"""

from __future__ import annotations
import re
import random
from typing import Optional, Any
from chat_session import ChatSession, InvestigationState, EvidenceType, ResolutionStatus


def _extract_field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


class RomarioDialogueEngine:
    """Motor de diálogo humanizado e contextual para o Romário."""

    @staticmethod
    def is_confusion_or_how_to(msg: str) -> bool:
        """Detecta se o cliente tem dúvida de processo, navegação, cadastro ou onde clicar."""
        m = msg.lower()
        patterns = [
            r"como\s+(que\s+)?(eu\s+)?fa[çc]o\s+(pra|para)\s+(ver|olhar|achar|consultar|saber|cadastrar|cadastro|fazer|emitir|gerar|imprimir|configurar|acessar|criar|lancar|cancelar)",
            r"como\s+(que\s+)?(eu\s+)?(vejo|olho|acho|consulto|sei|cadastro|cadastra|cadastrar|emito|emite|emitir|gero|gera|gerar|imprimo|imprime|imprimir|configuro|configura|configurar|acesso|acessa|acessar|crio|cria|criar|lanco|lan[çc]a|lan[çc]ar)",
            r"onde\s+(que\s+)?(eu\s+)?(olho|vejo|acho|fica|consulto|cadastro|cadastrar|faco|fazer|emito|emitir|configuro|configurar)",
            r"onde\s+[eé]\s+isso",
            r"onde\s+fica",
            r"passo\s+a\s+passo",
            r"n[aã]o\s+sei\s+(onde|como)\s+(olhar|ver|achar|consultar|fazer|cadastrar)",
            r"n[aã]o\s+sei\s+(ver|olhar|onde|como)",
            r"como\s+assim",
            r"n[aã]o\s+entendi",
            r"^\?+$",
            r"como\s+(cadastro|cadastra|cadastrar|emitir|emite|emito|gerar|gera|gero|imprimir|imprime|imprimo|lancar|lanca|lanco|configurar|configura|configuro|criar|cria|crio)",
            r"(queria|quero|gostaria\s+de|preciso)\s+(saber\s+como|cadastrar|emitir|gerar|criar|fazer)",
            r"como\s+funciona",
            r"qual\s+[eé]\s+o\s+caminho",
            r"qual\s+o\s+passo",
            r"com\s+uma\s+d[uú]vida",
            r"estou\s+com\s+d[uú]vida",
            r"tenho\s+uma\s+d[uú]vida",
            r"d[uú]vida\s+de\s+como",
            r"ajuda\s+para\s+(cadastrar|emitir|gerar|criar)",
        ]
        return any(re.search(p, m) for p in patterns)

    @staticmethod
    def is_not_in_hub(msg: str) -> bool:
        """Detecta se o cliente informou que o pedido não chegou na AnyMarket ou integrador."""
        m = msg.lower()
        return (
            (any(neg in m for neg in ["nao", "nem", "sem"]) and
             any(hub in m for hub in ["anymarket", "painel", "integrador", "hub", "integracao"]) and
             any(v in m for v in ["aparece", "esta", "ta", "te", "tem", "consta", "chegou", "subiu", "foi", "vejo", "achei", "encontro"]))
            or "nao te na anymarket" in m
            or "nao ta na anymarket" in m
            or "nem na anymarket" in m
            or "nao consta no painel" in m
            or "nao tem no painel" in m
        )

    @staticmethod
    def is_thanks_or_closing(msg: str) -> bool:
        """Detecta agradecimentos ou finalizações informais."""
        m = msg.lower()
        patterns = [
            r"\b(valeu|obrigad[oa]|show|blz|beleza|perfeito|fechou|vou\s+olhar|vou\s+testar|combinado)\b"
        ]
        return any(re.search(p, m) for p in patterns) and len(m.split()) <= 8

    @staticmethod
    def is_human_handoff_requested(msg: str) -> bool:
        """Detecta se o cliente pediu para falar com um humano/atendente."""
        m = msg.lower()
        patterns = [
            r"\b(humano|atendente|atendimento\s+humano|pessoa|operador|suporte\s+humano|falar\s+com\s+algu[eé]m|falar\s+com\s+humano|chamar\s+atendente|chamar\s+humano|passa\s+pro\s+suporte|passar\s+pro\s+suporte|transferir|transfere|transfira|transbordo|mesa\s+do\s+t[eé]cnico|falar\s+com\s+gente|suporte\s+t[eé]cnico)\b"
        ]
        return any(re.search(p, m) for p in patterns)

    @staticmethod
    def is_error_report(msg: str) -> bool:
        """Detecta se a mensagem relata um erro, falha, rejeição ou travamento técnico."""
        m = msg.lower()
        error_terms = [
            "erro", "rejei", "rejeição", "rejeicao", "falha", "travou", "travando",
            "bug", "problema", "nao consigo", "não consigo", "recusad", "nao autoriza",
            "não autoriza", "nao emite", "não emite", "nao salva", "não salva",
            "nao deixa", "não deixa", "violacao", "violação", "exception", "deu erro",
            "nao funciona", "não funciona", "ta dando erro", "tá dando erro",
            "rejeitado", "rejeitada"
        ]
        return any(t in m for t in error_terms)

    @staticmethod
    def has_specific_error_content(msg: str) -> bool:
        """Verifica se o usuário já forneceu detalhes específicos do erro (código, mensagem técnica, log)."""
        m = msg.lower()
        patterns = [
            r"\b(10060|10061|10054|204|481|783|539|694|629|388|1824|20419)\b",
            r"(timed?\s*out|timeout|tempo\s+esgotado)",
            r"(http\s*[:\-]?\s*\d{3})",
            r"(erro\s+interno|internal\s+error)",
            r"(?:rejei[cç][aã]o|cstat)\s*[:\-]?\s*(\d{3,5})",
            r"(\d{3,5})\s*[-:]\s*(?:rejei[cç][aã]o|cstat)",
            r"(viola[cç][aã]o|violation|sql\s*error|firebird|deadlock|access\s*violation)",
            r"(ocorreram\s+os\s+seguintes\s+erros)",
            r"(https?://\S+fazenda\S+)",
            r"(cst\s*\d+|csosn\s*\d+|cfop\s*\d+)",
            r"(valor\s+do\s+produto\s+difere)",
            r"(desconto\s+(?:maior|superior|100%))",
            r"(incompat[ií]vel|desonerad[oa]|mininclusive|regime\s+tribut[aá]rio)",
        ]
        if any(re.search(p, m) for p in patterns):
            return True
        return len(msg.split()) >= 10

    @classmethod
    def generate_response(
        cls,
        session: ChatSession,
        state: InvestigationState,
        triage_next_question: Optional[str] = None,
        top_candidates: Optional[list[dict]] = None
    ) -> str:
        """
        Gera uma resposta altamente humana, acolhedora e precisa tecnicamente
        baseada na evolução real da conversa.
        """
        user_turns = [t.content for t in session.turns if t.role == "user"]
        last_turn = session.turns[-1] if session.turns else None
        last_user_msg = last_turn.content if last_turn else ""
        turn_count = len(user_turns)
        combined_user_text = " ".join(user_turns)

        from conversation_outcome import classify_outcome, RESOLVED_REPLY, ACKNOWLEDGED_REPLY
        outcome = classify_outcome(user_turns[-1] if user_turns else '')
        if outcome:
            return RESOLVED_REPLY if outcome == 'resolved' else ACKNOWLEDGED_REPLY

        # Identificar se o usuário de fato enviou print / imagem
        user_has_sent_image = any(
            "[screenshot" in t.content.lower() or "[captura" in t.content.lower() or (hasattr(t, "media_refs") and t.media_refs)
            for t in session.turns if t.role == "user"
        ) or any(
            e.source == "sistema" and any(k in e.content.lower() for k in ["visualmente", "captura de tela", "screenshot", "imagem"])
            for e in state.evidence
        )

        # ---------------------------------------------------------------------
        # 0. CLIENTE PEDINDO PARA FALAR COM HUMANO / ATENDENTE
        # ---------------------------------------------------------------------
        if cls.is_human_handoff_requested(last_user_msg):
            return (
                "Entendido perfeitamente! Já estou direcionando o seu atendimento para um de nossos especialistas humanos "
                "da equipe de suporte da Alternativa Sistemas. 👤\n\n"
                "Todo o histórico da nossa conversa e as informações que você passou já foram salvos e enviados diretamente para a Mesa do Técnico "
                "para que o analista continue o seu atendimento sem você precisar repetir nada.\n\n"
                "👉 Clique no botão abaixo para acessar a **Mesa do Técnico** agora mesmo!"
            )

        # Error handling must use retrieved evidence, not the old keyword
        # templates below (which asserted causes and invented screen paths).
        if cls.is_error_report(combined_user_text) or (state.intent and 'erro' in state.intent):
            from triage_synthesizer import TriageSynthesizer
            from html import unescape
            if not top_candidates and not cls.has_specific_error_content(combined_user_text):
                return ('Para investigar, preciso da mensagem completa apresentada pelo sistema '
                        'e da tela ou integração em que ela aparece.')
            result = TriageSynthesizer.synthesize('', combined_user_text, search_results=top_candidates)
            text = re.sub(r'</(?:p|li)>', '\n\n', result['client_ready_response'])
            return unescape(re.sub(r'<[^>]+>', '', text)).strip()

        # ---------------------------------------------------------------------
        # 1. CLIENTE COM DÚVIDA DE COMO FAZER / NAVEGAÇÃO / TUTORIAIS EM VÍDEO (HOW-TO)
        # ---------------------------------------------------------------------
        # Se o cliente relatou erro/falha ou o intent é de erro, NUNCA tratar como how-to/tutorial!
        is_error = cls.is_error_report(last_user_msg) or (bool(state.intent) and "erro" in state.intent)
        if not is_error:
            try:
                from tutorial_catalog import find_matching_tutorial
                matched_tut = find_matching_tutorial(last_user_msg) or find_matching_tutorial(combined_user_text)
                if matched_tut:
                    return matched_tut.guide_text
            except Exception:
                pass

            if cls.is_confusion_or_how_to(last_user_msg) or cls.is_confusion_or_how_to(combined_user_text):
                # A. Cadastro de Produtos / Estoque
                if any(w in last_user_msg.lower() for w in ["produto", "produtos", "prroduto", "item", "itens", "mercadoria"]) and any(w in last_user_msg.lower() for w in ["cadastr", "inclu", "cria", "novo", "fazer", "saber"]):
                    return (
                        "Com certeza, parceiro! Cadastrar um produto no ERP Master é super simples e rápido: 📦\n\n"
                        "**Passo a passo no Master:**\n"
                        "1. No menu superior, acesse: **Cadastros > Estoque / Produtos** (ou utilize o atalho de pesquisa rápida **F3**).\n"
                        "2. Clique no botão **Novo** (ou pressione a tecla **Insert** no teclado).\n"
                        "3. Preencha as informações cadastrais básicas:\n"
                        "   • **Código / Referência:** Código interno ou SKU do item.\n"
                        "   • **Descrição:** Nome comercial completo do produto.\n"
                        "   • **Unidade:** Unidade de medida (ex: UN, PC, CX, KG).\n"
                        "   • **Preço de Venda:** Valor para tabelas de venda e faturamento.\n"
                        "4. Na aba **Fiscal / Tributação**, preencha os dados tributários:\n"
                        "   • **NCM:** Código de 8 dígitos da classificação fiscal.\n"
                        "   • **Origem:** (ex: 0 - Nacional, 1 - Importação direta).\n"
                        "   • **CST / CSOSN:** Conforme o enquadramento fiscal da sua empresa.\n"
                        "5. Clique no botão **Salvar** (ou atalho **F4**).\n\n"
                        "Pronto! O produto já fica ativo no sistema para vendas, estoque e emissão de notas fiscais. "
                        "Quer ajuda com algum campo específico do cadastro ou sobre importação por planilha?"
                    )

                # B. Cadastro de Clientes e Fornecedores
                if any(w in last_user_msg.lower() for w in ["cliente", "clientes", "fornecedor", "fornecedores"]) and any(w in last_user_msg.lower() for w in ["cadastr", "inclu", "cria", "novo"]):
                    return (
                        "Tranquilo, vamos lá! Para cadastrar cliente ou fornecedor no ERP Master: 👥\n\n"
                        "1. No menu superior, acesse: **Cadastros > Clientes e Fornecedores**.\n"
                        "2. Clique em **Novo** (ou aperte a tecla **Insert**).\n"
                        "3. Escolha o tipo: **Pessoa Física (CPF)** ou **Pessoa Jurídica (CNPJ)**.\n"
                        "4. Preencha Razão Social / Nome, Nome Fantasia, Endereço com CEP e Telefone.\n"
                        "5. Se for empresa contribuinte, preencha a **Inscrição Estadual (IE)**.\n"
                        "6. Clique em **Salvar** (ou atalho **F4**).\n\n"
                        "Já fica liberado para faturar pedidos desse cliente! Precisa de mais algum detalhe?"
                    )

                # C. Emissão de Nota Fiscal
                if any(w in last_user_msg.lower() for w in ["nota", "nf", "nfe", "danfe"]) and any(w in last_user_msg.lower() for w in ["emitir", "emite", "gerar", "fazer", "transmitir", "como"]):
                    return (
                        "Para emitir uma NF-e no ERP Master é bem prático: 🧾\n\n"
                        "1. Se a venda já estiver em pedido: acesse **Gerenciamento > Pedidos**, localize a venda e clique no botão **Faturar**.\n"
                        "2. Ou vá direto no menu: **Vendas > Faturamento > NF-e** e clique em **Nova Nota**.\n"
                        "3. Selecione o destinatário e confira os itens da nota.\n"
                        "4. Confira o **CFOP** e a **Natureza de Operação** na aba Tributação.\n"
                        "5. Clique em **Validar / Transmitir** para enviar à SEFAZ.\n\n"
                        "Assim que a SEFAZ autorizar, o sistema já libera a impressão da DANFE e envio do XML. Me avisa se deu certo aí!"
                    )

                # D. Onde ver CRT da empresa
                if state.intent == "erro_nota_fiscal" or state.confirmed_cause in ("regime_tributario_divergente", "cfop_incorreto", "duplicidade_nfe"):
                    return (
                        "Tranquilo, sem crise! Vou te guiar certinho onde você olha isso no Master:\n\n"
                        "**1. Para conferir o CRT da sua empresa:**\n"
                        "• No menu superior do ERP Master, acesse: **Configurações > Empresa / Filiais**.\n"
                        "• Selecione a sua empresa/filial emitente e clique no botão **Alterar**.\n"
                        "• Na aba **Parâmetros Fiscais**, veja o campo **Regime Tributário (CRT)** (se está 1 - Simples Nacional ou 3 - Regime Normal).\n\n"
                        "**2. Para consultar a SEFAZ oficial:**\n"
                        "• Acesse o site do **CCC (Cadastro Centralizado de Contribuintes)** da SEFAZ ou Sintegra e consulte o seu CNPJ para ver se na Receita a empresa está como Simples ou Normal.\n\n"
                        "Ajuste no Master para ficar igual à SEFAZ, clique em Salvar e retransmita a nota em **Vendas > Faturamento > NF-e**!"
                    )

                # E. Onde ver pedidos na AnyMarket / Ocorrências no Master
                if state.intent == "pedido_nao_integrado":
                    return (
                        "Tranquilo, sem crise! Vou te guiar certinho onde você olha isso:\n\n"
                        "**Se você estiver no ERP Master:**\n"
                        "1. Acesse o menu superior: **Gerenciamento > Pedidos**.\n"
                        "2. Pode apertar a tecla de atalho **F3** no teclado para abrir a pesquisa rápida (dá para pesquisar pelo nome do cliente ou número da venda).\n"
                        "3. Ao abrir o pedido, clique na aba **Ocorrências** (fica lá em cima à direita).\n"
                        "4. Veja o que está escrito na coluna **Erro**.\n\n"
                        "**Ou se você tem acesso ao portal da AnyMarket:**\n"
                        "1. Faça login no painel web da AnyMarket.\n"
                        "2. Vá no menu lateral em **Pedidos** e busque pelo número da venda do canal (ex: Shopee ou Mercado Livre).\n\n"
                        "Me conta o que você encontrou por lá!"
                    )

                # F. Se houver manuais da base de conhecimento (top_candidates)
                if top_candidates:
                    best = top_candidates[0]
                    content = _extract_field(best, "content_text") or _extract_field(best, "solution") or _extract_field(best, "diagnosis") or ""
                    screen = _extract_field(best, "screen_path") or _extract_field(best, "manual_category") or ""
                    shortcuts = _extract_field(best, "shortcut_keys") or []
                    shortcuts_str = f" | Atalhos: {', '.join(shortcuts)}" if shortcuts else ""
                    clean_lines = [l.strip() for l in content.split("\n") if l.strip() and not l.startswith("Modulo:") and not l.startswith("Tela:")]
                    excerpt = "\n".join(clean_lines[:8])
                    return (
                        f"Consultei o procedimento oficial no manual do ERP Master: 📖\n\n"
                        f"{f'📍 **Onde acessar no Master:** {screen}{shortcuts_str}\n\n' if screen else ''}"
                        f"{excerpt[:700]}\n\n"
                        f"Ficou alguma dúvida sobre como executar esse procedimento?"
                    )

        # ---------------------------------------------------------------------
        # 2. CLIENTE REVELA QUE O PEDIDO NÃO ESTÁ NA ANYMARKET
        # ---------------------------------------------------------------------
        if cls.is_not_in_hub(last_user_msg):
            return (
                "Ahhh, show de bola! Você acabou de matar a charada! 🎯\n\n"
                "Pode ficar totalmente tranquilo: **o ERP Master não tem culpa nenhuma nesse caso**.\n\n"
                "O fluxo das vendas de e-commerce funciona exatamente nessa ordem:\n"
                "1. **Canal de Venda** (Shopee, Mercado Livre, etc.)\n"
                "2. ➡️ **Hub Integrador** (AnyMarket)\n"
                "3. ➡️ **ERP Master**\n\n"
                "Como o pedido **ainda não consta no painel da AnyMarket**, o Master simplesmente não tem como puxar essa venda para dentro, pois ele depende do integrador despachar o pedido.\n\n"
                "**O que costuma acontecer na prática:**\n"
                "• O pagamento ainda está em análise de crédito ou antifraude no marketplace.\n"
                "• Ou o canal de venda (Shopee/ML) está com alguma fila momentânea de envio para o integrador.\n\n"
                "**O que você deve fazer agora:**\n"
                "Dá uma olhada direto no painel do canal onde a venda ocorreu (Shopee ou Mercado Livre) e veja se o status do pedido já consta como **'Aprovado'** ou **'Pronto para envio'**.\n\n"
                "Assim que o canal liberar, a AnyMarket recebe e o Master importa automaticamente em poucos minutos! Quer que eu fique de olho junto com você?"
            )

        # ---------------------------------------------------------------------
        # 3. CLIENTE AGRADECENDO OU FINALIZANDO ("valeu", "vou ver aqui", etc.)
        # ---------------------------------------------------------------------
        if cls.is_thanks_or_closing(last_user_msg):
            closing_options = [
                "Maravilha! Fico por aqui no seu aguardo. Testa com calma e qualquer dúvida é só me chamar que a gente vê na hora, combinado? Boa sorte aí!",
                "Show de bola! Qualquer coisa dá um grito aqui. Fico na torcida para dar tudo certo!",
                "Tamo junto! Vai lá e me avisa qualquer novidade. Um abraço e bom trabalho!",
            ]
            return random.choice(closing_options)

        # ---------------------------------------------------------------------
        # 3.5 CAUSA CONFIRMADA: ERRO 10060 / TIMEOUT SEFAZ / HTTP 500
        # ---------------------------------------------------------------------
        is_10060 = (
            (state.confirmed_cause and any(k in state.confirmed_cause for k in ["10060", "timeout", "instabilidade_sefaz"]))
            or any(p in last_user_msg.lower() for p in ["10060", "timed out", "timeout", "tempo esgotado", "http: 500", "http 500", "nfeautorizacao"])
            or any(ev.source == "sistema" and any(k in ev.content.lower() for k in ["10060", "timed out", "timeout"]) for ev in state.evidence)
        )
        if is_10060:
            intro = "Identifiquei perfeitamente na sua captura de tela!" if user_has_sent_image else "Identifiquei perfeitamente o seu caso!"
            return (
                f"{intro} Esse é o clássico **Erro Interno 10060: Connection timed out (Erro HTTP: 500)** ao contatar os servidores da SEFAZ. 🌐⚡\n\n"
                "**O que está acontecendo:**\n"
                "O ERP Master tentou transmitir a nota para o webservice da Fazenda, mas o servidor da SEFAZ demorou para responder e a conexão expirou (timeout). "
                "Isso ocorre devido a instabilidade, sobrecarga ou fila de processamento nos servidores da própria SEFAZ (neste caso, o portal da SEFAZ-SP).\n\n"
                "**Como proceder no ERP Master para resolver:**\n"
                "1. **Não fique clicando em reenviar repetidas vezes seguidas:** Isso é essencial para evitar o erro de duplicidade (Rejeição 539/204), pois a SEFAZ pode autorizar a nota na fila e demorar para devolver o comprovante ao sistema.\n"
                "2. **Aguarde de 3 a 5 minutos** para a SEFAZ desengargalar a fila de processamento.\n"
                "3. **Consulte antes de reenviar:** No Master, vá em **Vendas > Faturamento > NF-e**, clique com o botão direito sobre a nota rejeitada e escolha **Consultar na SEFAZ** (ou Sincronizar Protocolo). Se a Fazenda já tiver processado, o Master baixa o protocolo e a nota fica Autorizada automaticamente!\n"
                "4. **Precisa liberar o cliente agora no caixa (NFC-e)?**\n"
                "   • Se estiver com o cliente na loja e não puder aguardar a SEFAZ voltar, ative a **Emissão em Contingência Offline (NFC-e)** no PDV para imprimir a nota na hora e liberar o cliente. Assim que a SEFAZ restabelecer a conexão, o sistema transmite os lotes pendentes automaticamente!\n\n"
                "Aguarde uns minutinhos e clique em **Consultar na SEFAZ**. Me avisa aqui se ela já autorizou!"
            )

        # ---------------------------------------------------------------------
        # 4. CAUSA CONFIRMADA: REJEIÇÃO 481 - REGIME TRIBUTÁRIO DIVERGENTE (SEFAZ)
        # ---------------------------------------------------------------------
        is_481 = (
            (state.confirmed_cause and ("481" in state.confirmed_cause or "regime_tributario" in state.confirmed_cause or "regime tributário" in state.confirmed_cause.lower()))
            or any(p in last_user_msg.lower() for p in ["481", "regime tributario", "regime tributário", "crt"])
            or any(ev.source == "sistema" and "481" in ev.content for ev in state.evidence)
        )
        if is_481:
            intro = "Identifiquei perfeitamente na sua captura de tela!" if user_has_sent_image else "Identifiquei perfeitamente o seu caso!"
            return (
                f"{intro} Esse é o erro da **Rejeição 481 da SEFAZ** (Código de Regime Tributário do emitente diverge do cadastro na SEFAZ). 📑\n\n"
                "**O que aconteceu:**\n"
                "O Regime Tributário (CRT) configurado no cadastro da sua empresa no ERP Master não bate com o regime oficial ativo na base da SEFAZ (por exemplo: o Master está configurado como Simples Nacional, mas na SEFAZ a empresa desenquadrou para Regime Normal/Lucro Presumido, ou vice-versa).\n\n"
                "**Passo a passo definitivo para corrigir no ERP Master:**\n"
                "1. **Consulte a SEFAZ:** Acesse o portal do **CCC (Cadastro Centralizado de Contribuinte)** da SEFAZ ou Sintegra, consulte seu CNPJ e veja qual é o Regime Tributário ativo na Receita/SEFAZ (1 - Simples Nacional ou 3 - Regime Normal).\n"
                "2. **Acesse a Empresa no Master:** No menu superior do Master, acesse: **Configurações > Empresa / Filiais** (ou **Cadastros > Empresas**).\n"
                "3. Selecione a sua empresa/filial emitente e clique no botão **Alterar**.\n"
                "4. Na aba **Parâmetros Fiscais** (ou **Dados Fiscais**), localize o campo **Regime Tributário (CRT)** e altere para o código correspondente à SEFAZ:\n"
                "   • **1 - Simples Nacional** (se a empresa estiver enquadrada no Simples)\n"
                "   • **3 - Regime Normal** (se a empresa estiver no Lucro Presumido ou Real)\n"
                "5. Clique em **Salvar**.\n"
                "6. Agora vá na tela de faturamento em **Vendas > Faturamento > NF-e**, selecione a nota fiscal rejeitada e clique em **Reenviar / Transmitir**.\n\n"
                "Faz esse ajuste no cadastro da filial e retransmite a nota. Me confirma aqui se ela foi autorizada com sucesso!"
            )

        # ---------------------------------------------------------------------
        # 4.5 CAUSA CONFIRMADA: REJEIÇÃO 629 - VALOR DO PRODUTO DIFERE (DESCONTO)
        # ---------------------------------------------------------------------
        is_629 = (
            (state.confirmed_cause and ("629" in state.confirmed_cause or "valor_produto" in state.confirmed_cause))
            or any("629" in t.content for t in session.turns if t.role == "user")
            or any("valor do produto difere" in t.content.lower() for t in session.turns if t.role == "user")
            or any(ev.source in ("sistema", "usuario") and "629" in ev.content for ev in state.evidence)
        )
        if is_629:
            intro = "Identifiquei perfeitamente na sua captura de tela!" if user_has_sent_image else "Identifiquei perfeitamente o seu caso!"
            return (
                f"{intro} Esse erro é a **Rejeição 629 da SEFAZ**: *'Valor do Produto difere do produto Valor Unitário de Comercialização e Quantidade Comercial'*. 🧾\n\n"
                "**O que está acontecendo:**\n"
                "No ERP Master, esse erro ocorre quando o **desconto aplicado em algum item da nota é maior do que o valor do próprio produto** (ou seja, o desconto está ultrapassando 100%), gerando divergência matemática na validação da SEFAZ.\n\n"
                "**Passo a passo para corrigir no ERP Master:**\n"
                "1. Abra a nota fiscal rejeitada em **Vendas > Faturamento > NF-e** (ou abra o pedido em **Gerenciamento > Pedidos**).\n"
                "2. Vá até a aba **Itens da Nota**.\n"
                "3. Localize o produto que possui desconto informado e verifique os valores:\n"
                "   • O valor de desconto **não pode ser superior ao valor do item**.\n"
                "   • Se for uma bonificação/brinde de valor zero, ajuste o desconto para exatamente 100% (sem ultrapassar) e utilize a Natureza de Operação/CFOP de bonificação correspondente.\n"
                "4. Ajuste o valor ou recalcule o total da nota.\n"
                "5. Clique em **Salvar** e retransmita a nota fiscal clicando em **Reenviar / Transmitir**.\n\n"
                "Faz essa conferência no desconto dos itens e manda transmitir. Me avisa aqui se autorizou!"
            )

        # ---------------------------------------------------------------------
        # 5. CAUSA CONFIRMADA: REJEIÇÃO 783 - CFOP INCORRETO INTERESTADUAL
        # ---------------------------------------------------------------------
        is_783 = (
            (state.confirmed_cause and ("783" in state.confirmed_cause or "cfop" in state.confirmed_cause.lower()))
            or any(p in last_user_msg.lower() for p in ["783", "cfop", "interestadual"])
            or any(ev.source == "sistema" and "783" in ev.content for ev in state.evidence)
        )
        if is_783:
            return (
                "Na mosca! Esse é o caso da **Rejeição 783 da SEFAZ** (CFOP de operação interestadual para consumidor final não permitida). 🧾\n\n"
                "**O que aconteceu:**\n"
                "A nota fiscal é destinada a outro Estado, porém foi utilizado um CFOP que a SEFAZ não aceita para consumidor final não contribuinte (por exemplo, usar 6.102 em vez de 6.108).\n\n"
                "**Como resolver no ERP Master:**\n"
                "1. Abra a nota fiscal rejeitada em **Vendas > Faturamento > NF-e**.\n"
                "2. No campo **CFOP**, informe o código de operação interestadual para consumidor final adequado (geralmente **6.108** para venda a não contribuinte).\n"
                "3. Confira se o cadastro do destinatário está com 'Consumidor Final' marcado como Sim.\n"
                "4. Salve e clique em **Reenviar / Transmitir** a NF-e.\n\n"
                "Manda retransmitir e me confirma se autorizou!"
            )

        # ---------------------------------------------------------------------
        # 6. CAUSA CONFIRMADA: REJEIÇÃO 539/204 - DUPLICIDADE DE NF-E
        # ---------------------------------------------------------------------
        is_duplicidade = (
            (state.confirmed_cause and any(c in state.confirmed_cause for c in ["539", "204", "duplicidade"]))
            or any(p in last_user_msg.lower() for p in ["539", "204", "duplicidade"])
            or any(ev.source == "sistema" and any(c in ev.content for c in ["539", "204"]) for ev in state.evidence)
        )
        if is_duplicidade:
            return (
                "Identificado com clareza! Trata-se da **Rejeição 539/204 da SEFAZ** (Duplicidade de NF-e). 📋\n\n"
                "**O que aconteceu:**\n"
                "Uma nota fiscal com essa mesma numeração e série já foi autorizada pela SEFAZ anteriormente. O Master tentou enviar novamente e a SEFAZ barrou para evitar nota duplicada.\n\n"
                "**Como resolver no ERP Master:**\n"
                "1. Acesse: **Vendas > Faturamento > NF-e**.\n"
                "2. Localize a nota rejeitada, clique com o botão direito sobre ela e escolha **Consultar na SEFAZ** (ou Sincronizar Protocolo).\n"
                "3. O Master vai consultar a SEFAZ, baixar o protocolo de autorização existente e mudar o status da nota para **Autorizada** automaticamente.\n"
                "4. Pronto! A partir daí é só imprimir a DANFE normalmente.\n\n"
                "Clica em Consultar na SEFAZ e me avisa se a nota ficou verde/autorizada!"
            )

        # ---------------------------------------------------------------------
        # 7. CAUSA CONFIRMADA: FORMA DE PAGAMENTO NÃO VINCULADA
        # ---------------------------------------------------------------------
        is_pagamento = (
            (state.confirmed_cause and ("forma_pagamento" in state.confirmed_cause or "pagamento" in state.confirmed_cause.lower()))
            or any(p in last_user_msg.lower() for p in ["forma de pagamento", "condicao de pagamento", "pagamento nao"])
            or any(ev.source == "sistema" and "PAGAMENTO" in ev.content for ev in state.evidence)
        )
        if is_pagamento:
            return (
                "Diagnóstico na mosca! Esse é um dos casos mais comuns e bem fácil de resolver. 🚀\n\n"
                "O Master bloqueou a importação porque não sabe para qual forma de pagamento interna ele deve direcionar a venda que veio desse canal.\n\n"
                "**Passo a passo para ajustar de vez:**\n"
                "1. No Master, acesse: **Configurações > E-commerce > De-para de Formas de Pagamento** (ou clique direto em resolver na aba Ocorrências do pedido).\n"
                "2. Localize a forma de pagamento que veio do canal (ex: Cartão de Crédito Shopee, Boleto ML).\n"
                "3. Selecione a forma e a carteira financeira correspondente no seu ERP Master.\n"
                "4. Clique em **Salvar**.\n"
                "5. Volte na ocorrência do pedido e clique em **Reprocessar**.\n\n"
                "Assim que reprocessar, o pedido já entra faturando normalmente! Me avisa se deu certo aí."
            )

        # ---------------------------------------------------------------------
        # 8. CAUSA CONFIRMADA: TRANSPORTADORA NÃO VINCULADA
        # ---------------------------------------------------------------------
        if state.confirmed_cause == "transportadora_nao_vinculada" or any(
            p in last_user_msg.lower() for p in ["transportadora", "frete", "metodo de envio", "envio nao"]
        ):
            return (
                "Identificado com precisão! O pedido travou por falta de correlação de frete. 🚚\n\n"
                "Isso acontece quando o marketplace usa um método de envio que ainda não foi mapeado no seu cadastro.\n\n"
                "**Como resolver no ERP Master:**\n"
                "1. Acesse: **Configurações > E-commerce > De-para de Transportadoras**.\n"
                "2. Encontre o nome da transportadora/método enviado pelo canal (ex: Mercado Envios, Shopee Xpress).\n"
                "3. Associe com a transportadora correspondente já cadastrada no seu Master.\n"
                "4. Salve e clique em **Reprocessar** na ocorrência do pedido.\n\n"
                "Faz esse teste rápido aí e me confirma se o pedido foi importado!"
            )

        # ---------------------------------------------------------------------
        # 9. CAUSA CONFIRMADA: SKU NÃO CADASTRADO NO MASTER
        # ---------------------------------------------------------------------
        if state.confirmed_cause == "sku_nao_cadastrado" or any(
            p in last_user_msg.lower() for p in ["sku_not_found", "produto nao localizado", "sku nao"]
        ):
            return (
                "Pegamos o ponto! O erro é produto/SKU não localizado. 📦\n\n"
                "O anúncio do marketplace está com um código que o ERP Master não encontrou no cadastro de produtos.\n\n"
                "**Passo a passo:**\n"
                "1. Verifique o código SKU do anúncio no canal de venda ou na AnyMarket.\n"
                "2. No Master, vá em **Cadastros > Produtos** e certifique-se de que o produto possui exatamente o mesmo código de referência ou código de barras.\n"
                "3. Se o código for diferente, configure a correlação no de-para de produtos em **Configurações > E-commerce > De-para de Produtos**.\n"
                "4. Após salvar, mande **Reprocessar** a ocorrência.\n\n"
                "Consegue dar uma olhadinha no código do produto para conferirmos?"
            )

        # ---------------------------------------------------------------------
        # 10. TRIAGEM DE ERRO ESPECÍFICO (SEM CÓDIGO OU CAUSA CONFIRMADA)
        # ---------------------------------------------------------------------
        has_error_in_history = (
            cls.has_specific_error_content(last_user_msg)
            or cls.has_specific_error_content(combined_user_text)
            or any(
                ev.source in ("usuario", "sistema")
                and any(k in ev.content.lower() for k in ["rejeicao", "rejeição", "erro", "timeout", "629", "481", "783", "539", "difere", "desconto"])
                for ev in state.evidence
            )
        )

        if state.intent == "pedido_nao_integrado" and not state.confirmed_cause and not state.entity:
            return (
                "Opa, tudo bom? Sou o Romário, do suporte técnico da Alternativa Sistemas. Pode ficar tranquilo que vamos ver isso agora mesmo! 👋\n\n"
                "Para a gente começar a rastrear o que aconteceu:\n"
                "• Esse pedido veio de qual canal de venda? (Mercado Livre, Shopee, VTEX, Magalu...)\n"
                "• Vocês utilizam a **AnyMarket** ou outro integrador para receber esses pedidos?\n\n"
                "Se você tiver o número do pedido ou preferir colar um print com **Ctrl+V**, manda aqui que a gente já adianta!"
            )
        elif state.intent == "erro_nota_fiscal" and not state.confirmed_cause:
            if not user_has_sent_image and not has_error_in_history:
                return (
                    "Fala parceiro, sou o Romário do suporte! Vamos destravar essa nota fiscal agora mesmo. 🧾\n\n"
                    "Me conta: qual foi a mensagem ou código de rejeição que a SEFAZ retornou? (Por exemplo: Rejeição 481, 783, 539, 204, certificado...)\n\n"
                    "Se quiser tirar um print da tela com o erro e colar aqui com **Ctrl+V**, eu já bato o olho e te falo o caminho exato para corrigir!"
                )
        elif state.intent == "problema_boleto" and not state.confirmed_cause:
            if not user_has_sent_image and not cls.has_specific_error_content(last_user_msg):
                return (
                    "Olá! Sou o Romário do suporte. Vamos dar uma olhada nesse boleto para você emitir sem dores de cabeça! 💵\n\n"
                    "Me fala uma coisa: o erro acontece logo ao tentar gerar ou na hora de imprimir o boleto? E qual é o banco configurado (Bradesco, Itaú, Caixa...)?\n\n"
                    "Pode me passar os detalhes ou colar um print aqui no chat!"
                )

        if turn_count <= 1:
            has_error_mention = any(w in last_user_msg.lower() for w in ["erro", "rejei", "falha", "travou", "bug", "problema", "nao consigo", "não consigo"])
            if has_error_mention:
                if not user_has_sent_image and not has_error_in_history:
                    return (
                        "Fala parceiro, sou o Romário do suporte técnico da Alternativa Sistemas! Estou aqui para te ajudar. 👋\n\n"
                        "Me conta qual foi a mensagem ou código de erro que apareceu na tela? Se preferir mandar uma captura com **Ctrl+V**, já bato o olho para te orientar!"
                    )
            else:
                if top_candidates:
                    best = top_candidates[0]
                    sol = _extract_field(best, "solution") or _extract_field(best, "content_text") or _extract_field(best, "diagnosis") or ""
                    screen = _extract_field(best, "screen_path") or _extract_field(best, "manual_category") or ""
                    shortcuts = _extract_field(best, "shortcut_keys") or []
                    shortcuts_str = f" | Atalhos: {', '.join(shortcuts)}" if shortcuts else ""
                    if sol and len(sol.strip()) > 30:
                        clean_lines = [l.strip() for l in sol.split("\n") if l.strip() and not l.startswith("Modulo:") and not l.startswith("Tela:")]
                        excerpt = "\n".join(clean_lines[:6])
                        return (
                            f"Opa, tudo bem? Sou o Romário da Alternativa Sistemas! Com certeza, consultei o procedimento no manual do ERP Master: 📖\n\n"
                            f"{f'📍 **Onde acessar no Master:** {screen}{shortcuts_str}\n\n' if screen else ''}"
                            f"{excerpt[:650]}\n\n"
                            f"Faz esse teste aí no seu Master e me avisa se deu certo ou se ficou alguma dúvida!"
                        )
                return (
                    "Opa, tudo bem? Sou o Romário do suporte técnico da Alternativa Sistemas. Estou aqui para te ajudar! 🤝\n\n"
                    "Me conta: o que você precisa fazer ou qual dúvida você gostaria de tirar no Master que eu te passo o caminho certinho!"
                )

        # ---------------------------------------------------------------------
        # 11. RESPOSTA INTERMEDIÁRIA HUMANIZADA COM BASE NO ESTADO
        # ---------------------------------------------------------------------
        if state.entity and state.entity.get("id") and "ocorrencias_do_pedido" in state.missing_information:
            order_id = state.entity.get("id")
            sugestao_envio = "Me digite a mensagem de erro que você encontrar por lá que eu já te passo o clique exato!" if user_has_sent_image else "Se achar mais fácil, aperta a tecla **Print Screen** e dá um **Ctrl+V** aqui no chat que eu mesmo leio para você!"
            return (
                f"Show, anotei aqui o pedido **#{order_id}**! 🔎\n\n"
                "Agora, para irmos direto na causa raiz:\n"
                "Dá uma olhada no Master em **Gerenciamento > Pedidos > pesquise o pedido (F3) > aba Ocorrências**.\n"
                f"O que aparece escrito na coluna **Erro**? (Geralmente é algo como *'forma de pagamento não vinculada'*, *'transportadora não relacionada'* ou *'produto não encontrado'*).\n\n"
                f"{sugestao_envio}"
            )

        past_assistant_replies = [t.content for t in session.turns if t.role == "assistant"]
        already_asked_module = any("qual tela ou módulo você está acessando" in r or "qual tela" in r.lower() for r in past_assistant_replies)
        already_asked_error = any("qual foi a mensagem ou código de erro" in r.lower() for r in past_assistant_replies)

        # Se temos uma correspondência de alta relevância no manual oficial ou KB, entregamos a solução diretamente!
        if top_candidates:
            best = top_candidates[0]
            best_type = str(_extract_field(best, "source_type") or _extract_field(best, "knowledge_type") or "")
            score = float(_extract_field(best, "final_score") or 0.0)
            if best_type in ("MANUAL", "OFFICIAL_KB", "KB") or score >= 1.15:
                sol = _extract_field(best, "solution") or _extract_field(best, "content_text") or _extract_field(best, "diagnosis") or ""
                screen = _extract_field(best, "screen_path") or _extract_field(best, "manual_category") or ""
                shortcuts = _extract_field(best, "shortcut_keys") or []
                shortcuts_str = f" | Atalhos: {', '.join(shortcuts)}" if shortcuts else ""
                clean_lines = [l.strip() for l in sol.split("\n") if l.strip() and not l.startswith("Modulo:") and not l.startswith("Tela:")]
                excerpt = "\n".join(clean_lines[:8])
                return (
                    f"Identifiquei o procedimento oficial no manual do ERP Master: 📖\n\n"
                    f"{f'📍 **Onde acessar no Master:** {screen}{shortcuts_str}\n\n' if screen else ''}"
                    f"{excerpt[:750]}\n\n"
                    f"Faz esse teste aí no seu Master e me avisa se deu certo ou se ficou alguma dúvida!"
                )

        if triage_next_question:
            # Trava anti-repetição: se já fez essa exata pergunta antes, não repete
            if any(triage_next_question.strip() in r for r in past_assistant_replies):
                return (
                    "Compreendi perfeitamente! Como ainda precisamos dessa confirmação técnica e para não te fazer perder tempo repetindo perguntas, "
                    "estou transferindo o seu atendimento agora mesmo para um especialista humano da nossa equipe de suporte da Alternativa Sistemas. 👤\n\n"
                    "O histórico completo do seu chamado já está gravado e aberto na **Mesa do Técnico** para continuarmos o atendimento imediatamente!\n\n"
                    "👉 Clique no botão abaixo para ir para a **Mesa do Técnico**:"
                )
            return (
                f"Entendido! Para a gente fechar o diagnóstico com precisão:\n\n"
                f"{triage_next_question}\n\n"
                f"Se tiver qualquer dúvida de como consultar isso no sistema, é só me falar que eu te explico na hora!"
            )

        if top_candidates:
            best = top_candidates[0]
            sol = _extract_field(best, "solution") or _extract_field(best, "content_text") or _extract_field(best, "diagnosis") or ""
            screen = _extract_field(best, "screen_path") or _extract_field(best, "manual_category") or ""
            shortcuts = _extract_field(best, "shortcut_keys") or []
            shortcuts_str = f" | Atalhos: {', '.join(shortcuts)}" if shortcuts else ""
            clean_lines = [l.strip() for l in sol.split("\n") if l.strip() and not l.startswith("Modulo:") and not l.startswith("Tela:")]
            excerpt = "\n".join(clean_lines[:6])
            return (
                f"Pelo que você me descreveu, consultei o procedimento no manual do ERP Master: 📖\n\n"
                f"{f'📍 **Onde acessar no Master:** {screen}{shortcuts_str}\n\n' if screen else ''}"
                f"{excerpt[:600]}\n\n"
                f"Faz esse teste aí no seu Master e me avisa se deu certo ou se ficou alguma dúvida!"
            )

        if user_has_sent_image:
            if already_asked_error or turn_count >= 2:
                return (
                    "Recebi a sua imagem! Para resolvermos essa pendência sem você perder tempo, "
                    "estou transferindo o atendimento com a captura anexada diretamente para um especialista humano do nosso suporte. 👤\n\n"
                    "👉 Clique no botão abaixo para acompanhar na **Mesa do Técnico**:"
                )
            return (
                "Recebi a sua captura de tela! Para eu te orientar no clique exato do Master, "
                "consegue me digitar qual foi a mensagem ou código de erro que apareceu na tela? "
                "Ou me conta qual o número da nota/pedido que puxo as orientações aqui para você."
            )

        # ---------------------------------------------------------------------
        # 12. FALLBACK FINAL: GUARDRAIL ANTI-LOOP & TRANSBORDO HUMANO
        # ---------------------------------------------------------------------
        if already_asked_module or turn_count >= 2:
            return (
                "Compreendi perfeitamente a sua solicitação! Como não localizei o procedimento exato para esse caso na minha base de conhecimento, "
                "não vou te fazer perder tempo nem te deixar esperando em loop: estou transferindo o seu atendimento "
                "agora mesmo para um especialista humano da nossa equipe de suporte da Alternativa Sistemas. 👤\n\n"
                "Todo o histórico da nossa conversa e os dados que você passou já estão salvos e disponíveis na **Mesa do Técnico** "
                "para que o analista continue o atendimento de forma ágil e precisa!\n\n"
                "👉 Clique no botão abaixo para ir para a **Mesa do Técnico**:"
            )

        return (
            "Entendi o que você precisa! Me conta qual tela ou módulo você está acessando no Master agora "
            "(por exemplo: Cadastros, Vendas, Estoque ou Financeiro) para eu te passar o passo a passo exato do que preencher."
        )
