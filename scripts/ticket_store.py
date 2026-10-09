# coding: utf-8
"""
DeskCortex — Ticket Store (Movidesk Triagem e Portal do Cliente)
================================================================
Gerenciamento unificado de chamados do Movidesk:
- Suporta tickets criados pelo Portal do Cliente (/cliente)
- Suporta tickets ingeridos automaticamente pelo ERP Master (/api/v1/master/ticket)
- Fornece a Triagem ("Todos os tickets") completa e idêntica ao Movidesk real (/tecnico)
- Armazena Notas Internas da IA e Ações Públicas
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

TICKETS_FILE = Path(__file__).parent / "output" / "tickets.json"

INITIAL_SEED_TICKETS = [
    {
        "id": 130063,
        "type": 2,
        "type_label": "Solicitação",
        "subject": "Configuração e ativação da Empresa 11 para integração Full Magalu",
        "description": "Precisamos ativar a filial 11 no sincronizador para iniciar o envio de catálogo e faturamento via Magalu Entregas Full.",
        "opened_at": "10/09/2026 13:40",
        "resolved_at": "",
        "client": {
            "name": "STENIO CARLOS",
            "organization": "D1 Fitness - Diego",
            "email": "stenio@d1fitness.com.br",
            "phone": "(11) 98765-4321"
        },
        "responsible": "Renan Mitsuo",
        "category": "Solicitação de Serviço",
        "service": "Configuração / Integração",
        "urgency": "Normal",
        "urgency_color": "#eab308",
        "status": "Novo",
        "master_env": {
            "version": "12.4.1",
            "is_beta": False,
            "server": "192.168.1.100 | Firebird 3.0",
            "integrations": ["Magalu Full", "Mercado Livre"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Solicitação de ativação de filial para canal de marketplace. Requer verificação de token na plataforma e permissões na tabela EMPRESAS do ERP Master.",
            "possible_resolution": "1. Acessar Configurações > Filiais > Empresa 11 e verificar CNPJ cadastrado.\n2. No módulo Integrações > Marketplace > Magalu, cadastrar credenciais de autenticação da filial 11.\n3. Reiniciar serviço do sincronizador na máquina servidora para carregar a nova thread.",
            "confidence": "94%",
            "screen_path": "Integrações > Marketplace > Magalu",
            "shortcuts": ["F7 - Parâmetros", "Ctrl+F - Pesquisar Filial"],
            "references": ["Manual de Integração Magalu Full (Artigo #8402)"]
        },
        "actions": [
            {
                "id": 1,
                "type": "internal",
                "author": "DeskCortex I.A. (Triagem Automática)",
                "created_at": "10/09/2026 13:40",
                "content": "Triagem automática concluída. Diagnóstico preliminar e rota no ERP Master disponibilizados para o especialista."
            }
        ]
    },
    {
        "id": 130060,
        "type": 2,
        "type_label": "Análise",
        "subject": "IMPORTAR FOTO E EXPORTAR PLANILHA",
        "description": "Ao tentar importar a planilha de fotos em massa no cadastro de produtos, o sistema apresenta travamento e não carrega a pré-visualização.",
        "opened_at": "10/09/2026 13:12",
        "resolved_at": "",
        "client": {
            "name": "Bruno Silveira",
            "organization": "AR15 Sport",
            "email": "bruno@ar15sport.com.br",
            "phone": "(11) 97777-1234"
        },
        "responsible": "Luiz Fernando",
        "category": "Análise Técnica",
        "service": "Estoque / Produtos",
        "urgency": "Normal",
        "urgency_color": "#eab308",
        "status": "SUP - Triagem",
        "master_env": {
            "version": "12.4.2",
            "is_beta": True,
            "server": "192.168.0.17 | Firebird 3.0",
            "integrations": ["Mercado Livre", "Shopee", "Nuvemshop"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Falha no parser de colunas da planilha XLSX no módulo de carga em lote de produtos. A imagem referenciada precisa estar em formato URL válida ou caminho local acessível.",
            "possible_resolution": "1. Acessar Estoque > Produtos > Importação em Lote.\n2. Certificar-se de que a coluna de fotos está formatada como Texto simples.\n3. Executar com permissão de Administrador para gravação no diretório de cache C:\\Master\\FotosTemp.",
            "confidence": "91%",
            "screen_path": "Estoque > Produtos > Importação em Lote",
            "shortcuts": ["Ctrl+I - Importar", "F2 - Salvar"],
            "references": ["Manual Oficial: Importação e Exportação de Imagens em Lote (Artigo #5120)"]
        },
        "actions": [
            {
                "id": 1,
                "type": "internal",
                "author": "DeskCortex I.A. (Triagem Automática)",
                "created_at": "10/09/2026 13:12",
                "content": "Triagem automática concluída. Verificada presença de versão BETA 12.4.2 no cliente."
            }
        ]
    },
    {
        "id": 130059,
        "type": 2,
        "type_label": "Análise",
        "subject": "SUP - SNC - Baixar Pedidos Sincronizador Wehub",
        "description": "Pedidos do Wehub não estão descendo para o faturamento no ERP Master desde as 09:00.",
        "opened_at": "10/09/2026 12:50",
        "resolved_at": "10/09/2026 13:15",
        "client": {
            "name": "Nathalia Martins",
            "organization": "WR ELETRONICS",
            "email": "nathalia@wreletronics.com.br",
            "phone": "(19) 98888-5555"
        },
        "responsible": "Marília Gabriela",
        "category": "Análise Técnica",
        "service": "Sincronizador / Ecommerce",
        "urgency": "Alta",
        "urgency_color": "#8b5cf6",
        "status": "Resolvido",
        "master_env": {
            "version": "12.3.9",
            "is_beta": False,
            "server": "10.0.0.15 | SQL Server",
            "integrations": ["Wehub", "Mercado Livre"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Token de autorização da API Wehub expirado no sincronizador. Causou erro HTTP 401 nas chamadas de busca de pedidos pendentes.",
            "possible_resolution": "1. Acessar Sincronizador Wehub > Configurações > Tokens.\n2. Clicar em Renovar Chave de Integração.\n3. Clicar em Forçar Download de Pedidos e conferir na tela de Vendas.",
            "confidence": "98%",
            "screen_path": "Sincronizador Wehub > Tokens de Acesso",
            "shortcuts": ["F5 - Atualizar"],
            "references": ["Manual Sincronizador Wehub - Renovação de Token (Artigo #7712)"]
        },
        "actions": [
            {
                "id": 1,
                "type": "internal",
                "author": "DeskCortex I.A. (Triagem Automática)",
                "created_at": "10/09/2026 12:50",
                "content": "Triagem concluída. Causa identificada: Token Wehub expirado."
            },
            {
                "id": 2,
                "type": "public",
                "author": "Marília Gabriela",
                "created_at": "10/09/2026 13:15",
                "content": "Olá Nathalia! Identificamos que o token de comunicação com o Wehub havia expirado. Realizamos a renovação e os pedidos pendentes já desceram para a sua tela de faturamento."
            }
        ]
    },
    {
        "id": 130058,
        "type": 2,
        "type_label": "Análise",
        "subject": "Emissão de Etiquetas",
        "description": "Impressora térmica Zebra ZD220 cortando margem direita das etiquetas de envio Mercado Envios.",
        "opened_at": "10/09/2026 12:30",
        "resolved_at": "10/09/2026 13:00",
        "client": {
            "name": "Claudia",
            "organization": "Cikala Comércio",
            "email": "claudia@cikala.com.br",
            "phone": "(11) 96666-3333"
        },
        "responsible": "Luiz Fernando",
        "category": "Análise Técnica",
        "service": "Expedição / Etiquetas",
        "urgency": "Normal",
        "urgency_color": "#eab308",
        "status": "Resolvido",
        "master_env": {
            "version": "12.4.0",
            "is_beta": False,
            "server": "192.168.1.50",
            "integrations": ["Mercado Livre"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Resolução DPI descalibrada no driver de impressão do Windows (203 DPI vs 300 DPI).",
            "possible_resolution": "Ajustar formato da etiqueta no driver da impressora para 100x150mm com margens zeradas.",
            "confidence": "95%",
            "screen_path": "Expedição > Impressão de Etiquetas Zebra",
            "shortcuts": ["Ctrl+P"],
            "references": ["Configuração de Impressoras Térmicas Zebra (Artigo #3011)"]
        },
        "actions": []
    },
    {
        "id": 130055,
        "type": 2,
        "type_label": "Análise",
        "subject": "Erro ao transmitir vendas",
        "description": "Ao tentar transmitir o lote de notas fiscais, retorna Rejeição 481 - Código Regime Tributário divergente.",
        "opened_at": "10/09/2026 12:00",
        "resolved_at": "",
        "client": {
            "name": "João",
            "organization": "Tubarão Center",
            "email": "joao@tubaraocenter.com.br",
            "phone": "(13) 99123-4567"
        },
        "responsible": "Luiz Fernando",
        "category": "Análise Técnica",
        "service": "Fiscal / NF-e",
        "urgency": "Alta",
        "urgency_color": "#8b5cf6",
        "status": "SUP - Triagem",
        "master_env": {
            "version": "12.4.2",
            "is_beta": False,
            "server": "192.168.0.10 | Firebird 3.0",
            "integrations": ["Shopee", "Mercado Livre"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Rejeição SEFAZ 481: O CRT informado no cadastro da empresa emissora difere do cadastrado na base cadastral da SEFAZ Estadual.",
            "possible_resolution": "1. Acessar Configurações > Empresa > Parâmetros Fiscais.\n2. Verificar campo Regime Tributário: alterar conforme consulta CCC/Sintegra.\n3. Reabrir a NF-e e clicar em Retransmitir [F9].",
            "confidence": "99%",
            "screen_path": "Configurações > Empresa > Parâmetros Fiscais",
            "shortcuts": ["F9 - Transmitir", "Ctrl+E - Editar Empresa"],
            "references": ["Manual Oficial SEFAZ - Rejeição 481 (Artigo #4810)"]
        },
        "actions": []
    },
    {
        "id": 130054,
        "type": 2,
        "type_label": "Análise",
        "subject": "SUP - ETQ - Limite máximo de requisições alcançado",
        "description": "Erro ao gerar etiquetas: Limite de chamadas por minuto excedido na API da transportadora.",
        "opened_at": "10/09/2026 11:45",
        "resolved_at": "",
        "client": {
            "name": "Vitor Hugo",
            "organization": "Cikala Comércio",
            "email": "vitor@cikala.com.br",
            "phone": "(11) 96666-4444"
        },
        "responsible": "Lucas Souza",
        "category": "Análise Técnica",
        "service": "Expedição",
        "urgency": "Normal",
        "urgency_color": "#eab308",
        "status": "Em atendimento",
        "master_env": {
            "version": "12.4.1",
            "is_beta": False,
            "server": "192.168.1.50",
            "integrations": ["Mercado Livre", "Shopee"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Rate limit da API de etiquetas acionado. Recomendado aguardar 2 minutos e processar em lotes menores de 20 unidades.",
            "possible_resolution": "Fracionar o envio de etiquetas em blocos de até 20 notas fiscais.",
            "confidence": "90%",
            "screen_path": "Expedição > Emissão de Etiquetas em Bloco",
            "shortcuts": ["Ctrl+L - Selecionar Bloco"],
            "references": ["Boas Práticas de Expedição em Massa (Artigo #6110)"]
        },
        "actions": []
    },
    {
        "id": 130052,
        "type": 2,
        "type_label": "Solicitação",
        "subject": "Master não abre",
        "description": "Servidor reiniciou após queda de energia e os terminais apresentam Erro 10060 - Conexão Recusada.",
        "opened_at": "10/09/2026 11:15",
        "resolved_at": "",
        "client": {
            "name": "Joaquim",
            "organization": "Miligrama Farmácia",
            "email": "joaquim@miligrama.com.br",
            "phone": "(41) 99888-7777"
        },
        "responsible": "Suporte",
        "category": "Solicitação de Serviço",
        "service": "Infraestrutura / Banco",
        "urgency": "Crítica",
        "urgency_color": "#ef4444",
        "status": "Novo",
        "master_env": {
            "version": "12.3.8",
            "is_beta": False,
            "server": "192.168.0.5 | Firebird 3.0",
            "integrations": []
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Erro 10060 indica que o serviço Firebird Guardian / Server não subiu automaticamente após o reboot da máquina servidora.",
            "possible_resolution": "1. Acessar o servidor físico 192.168.0.5.\n2. Abrir services.msc.\n3. Localizar Firebird Server - DefaultInstance, clicar com botão direito e selecionar Iniciar.",
            "confidence": "99%",
            "screen_path": "Windows > Services.msc > Firebird Server",
            "shortcuts": ["Win+R > services.msc"],
            "references": ["Manual de Infraestrutura: Erro 10060 Firebird (Artigo #1006)"]
        },
        "actions": []
    },
    {
        "id": 130051,
        "type": 2,
        "type_label": "Análise",
        "subject": "SUP - ERR - Erro de Nota Fiscal - Numero de série",
        "description": "Ao tentar emitir NF-e, retorna duplicidade de número ou série não cadastrada para a filial ativa.",
        "opened_at": "10/09/2026 11:00",
        "resolved_at": "10/09/2026 11:40",
        "client": {
            "name": "Nathalia Martins",
            "organization": "WR ELETRONICS",
            "email": "nathalia@wreletronics.com.br",
            "phone": "(19) 98888-5555"
        },
        "responsible": "Renan Mitsuo",
        "category": "Análise Técnica",
        "service": "Fiscal / NF-e",
        "urgency": "Alta",
        "urgency_color": "#8b5cf6",
        "status": "Resolvido",
        "master_env": {
            "version": "12.3.9",
            "is_beta": False,
            "server": "10.0.0.15 | SQL Server",
            "integrations": ["Wehub"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Série da NF-e não sincronizada no parâmetro da empresa após atualização de certificado digital.",
            "possible_resolution": "Ajustar numeração da série ativa em Configurações > Empresa > Séries Fiscais.",
            "confidence": "96%",
            "screen_path": "Configurações > Empresa > Séries Fiscais",
            "shortcuts": ["Ctrl+S"],
            "references": ["Controle de Numeração de Séries NF-e (Artigo #2099)"]
        },
        "actions": []
    },
    {
        "id": 130050,
        "type": 2,
        "type_label": "Dúvida",
        "subject": "SUP - DUV - Atualização de NCM",
        "description": "Como atualizar a tabela IBPT e NCMs em lote no ERP Master para vigência do novo trimestre?",
        "opened_at": "10/09/2026 10:45",
        "resolved_at": "",
        "client": {
            "name": "Luciana",
            "organization": "Tubarão Center",
            "email": "luciana@tubaraocenter.com.br",
            "phone": "(13) 99123-9999"
        },
        "responsible": "Ingrid Brito",
        "category": "Dúvida",
        "service": "Fiscal / Cadastros",
        "urgency": "Normal",
        "urgency_color": "#eab308",
        "status": "Novo",
        "master_env": {
            "version": "12.4.2",
            "is_beta": False,
            "server": "192.168.0.10 | Firebird 3.0",
            "integrations": ["Mercado Livre"]
        },
        "attachments": [],
        "ai_internal_note": {
            "diagnosis": "Rotina periódica de importação da tabela de alíquotas do IBPT.",
            "possible_resolution": "1. Baixar o arquivo CSV atualizado no portal De Olho No Imposto (IBPT).\n2. No Master, acessar Fiscal > Tabelas Auxiliares > Importar Tabela IBPT.\n3. Selecionar o arquivo e clicar em Processar.",
            "confidence": "97%",
            "screen_path": "Fiscal > Tabelas Auxiliares > Importar Tabela IBPT",
            "shortcuts": ["F8 - Tabelas"],
            "references": ["Manual de Atualização IBPT e NCM (Artigo #1820)"]
        },
        "actions": []
    }
]


class TicketStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._tickets: Dict[int, dict] = {}
        self._load()

    def _load(self):
        with self._lock:
            if TICKETS_FILE.exists():
                try:
                    data = json.loads(TICKETS_FILE.read_text(encoding="utf-8"))
                    for t in data:
                        self._tickets[t["id"]] = t
                    return
                except Exception as e:
                    print(f"[TicketStore] Erro ao carregar tickets: {e}")

            for t in INITIAL_SEED_TICKETS:
                self._tickets[t["id"]] = t
            self._save()

    def _save(self):
        with self._lock:
            try:
                TICKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tickets_list = list(self._tickets.values())
                tickets_list.sort(key=lambda x: x.get("id", 0), reverse=True)
                TICKETS_FILE.write_text(json.dumps(tickets_list, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                print(f"[TicketStore] Erro ao salvar tickets: {e}")

    def list_all(self, filter_q: Optional[str] = None) -> List[dict]:
        with self._lock:
            items = list(self._tickets.values())
            items.sort(key=lambda x: x.get("id", 0), reverse=True)
            if not filter_q:
                return items

            q = filter_q.lower().strip()
            res = []
            for t in items:
                text_corpus = f"{t.get('id')} {t.get('subject', '')} {t.get('status', '')} {t.get('responsible', '')} {t.get('client', {}).get('name', '')} {t.get('client', {}).get('organization', '')}".lower()
                if q in text_corpus:
                    res.append(t)
            return res

    def get(self, ticket_id: int) -> Optional[dict]:
        with self._lock:
            return self._tickets.get(ticket_id)

    def get_by_client(self, client_name: str = "", organization: str = "", email: str = "") -> List[dict]:
        with self._lock:
            items = list(self._tickets.values())
            items.sort(key=lambda x: x.get("id", 0), reverse=True)
            res = []
            c_name = client_name.lower().strip() if client_name else ""
            c_org = organization.lower().strip() if organization else ""
            c_email = email.lower().strip() if email else ""

            for t in items:
                client_info = t.get("client", {})
                t_name = client_info.get("name", "").lower()
                t_org = client_info.get("organization", "").lower()
                t_email = client_info.get("email", "").lower()

                matched = False
                if c_name and (c_name in t_name or t_name in c_name):
                    matched = True
                elif c_org and (c_org in t_org or t_org in c_org):
                    matched = True
                elif c_email and (c_email in t_email or t_email in c_email):
                    matched = True

                if matched:
                    sanitized = dict(t)
                    sanitized.pop("ai_internal_note", None)
                    sanitized["actions"] = [a for a in sanitized.get("actions", []) if a.get("type") == "public"]
                    res.append(sanitized)
            return res

    def next_ticket_id(self) -> int:
        with self._lock:
            if not self._tickets:
                return 130064
            return max(self._tickets.keys()) + 1

    def create(self, data: dict) -> dict:
        with self._lock:
            tid = data.get("id") or self.next_ticket_id()
            data["id"] = tid
            if "opened_at" not in data:
                data["opened_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            if "status" not in data:
                data["status"] = "SUP - Triagem"
            if "type" not in data:
                data["type"] = 2
            if "type_label" not in data:
                data["type_label"] = "Análise"
            if "actions" not in data:
                data["actions"] = []

            if data.get("ai_internal_note"):
                note = data["ai_internal_note"]
                ai_content = note.get("internal_action_html")
                if not ai_content:
                    ai_content = f"**Diagnóstico Preliminar DeskCortex**:\n{note.get('diagnosis', '')}\n\n**Resolução Proposta**:\n{note.get('possible_resolution', '')}\n\n*Confiança: {note.get('confidence', '95%')} | Rota: {note.get('screen_path', 'N/A')}*"
                ai_action = {
                    "id": len(data["actions"]) + 1,
                    "type": "internal",
                    "author": "DeskCortex I.A. (Triagem Automática)",
                    "created_at": data["opened_at"],
                    "content": ai_content
                }
                data["actions"].insert(0, ai_action)

            self._tickets[tid] = data
            self._save()
            return data

    def add_action(self, ticket_id: int, action_type: str, author: str, content: str, time_spent: str = "15 min", new_status: Optional[str] = None) -> Optional[dict]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None

            now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            action_id = len(ticket.get("actions", [])) + 1
            action_item = {
                "id": action_id,
                "type": action_type,
                "author": author,
                "created_at": now_str,
                "content": content,
                "time_spent": time_spent
            }
            if "actions" not in ticket:
                ticket["actions"] = []
            ticket["actions"].append(action_item)

            if new_status:
                ticket["status"] = new_status
                if new_status == "Resolvido":
                    ticket["resolved_at"] = now_str

            self._save()
            return ticket

    def update_similar_feedback(
        self,
        ticket_id: int,
        node_id: int,
        is_helpful: bool,
        comment: str = "",
        technician: str = "Suporte"
    ) -> Optional[dict]:
        """
        Registra o feedback do técnico sobre um ticket/artigo semelhante.
        - Marca o nó como útil (is_helpful) no array similar_refs do ai_internal_note.
        - Se is_helpful=True, eleva a confiança da triagem automaticamente.
        - Se houver comentário, insere nota interna automática no histórico de ações.
        """
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return None

            now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            ai_note = ticket.setdefault("ai_internal_note", {})

            # Atualizar feedback no similar_refs
            similar_refs = ai_note.get("similar_refs", [])
            ref_updated = False
            for ref in similar_refs:
                if ref.get("node_id") == node_id:
                    ref["is_helpful"] = is_helpful
                    if comment:
                        ref["technician_comment"] = comment
                        ref["commented_by"] = technician
                        ref["commented_at"] = now_str
                    ref_updated = True
                    break

            if not ref_updated:
                # Adiciona referência inline se não encontrada (compatibilidade retroativa)
                new_ref = {"node_id": node_id, "is_helpful": is_helpful}
                if comment:
                    new_ref["technician_comment"] = comment
                    new_ref["commented_by"] = technician
                    new_ref["commented_at"] = now_str
                similar_refs.append(new_ref)

            ai_note["similar_refs"] = similar_refs

            # Elevar confiança se marcou como útil
            if is_helpful:
                current_conf = ai_note.get("confidence", "88%")
                try:
                    pct_val = int(str(current_conf).replace("%", "").strip())
                except Exception:
                    pct_val = 88
                new_conf = min(99, pct_val + 2) if pct_val < 98 else 99
                ai_note["confidence"] = f"{new_conf}%"
                ai_note["validated_by_technician"] = True
                # Atualizar badge de confiança no internal_action_html se existir
                html = ai_note.get("internal_action_html", "")
                if html and current_conf in html:
                    ai_note["internal_action_html"] = html.replace(
                        f"{pct_val}% Confiança", f"{new_conf}% Confiança ✓ Validado"
                    )

            # Registrar nota interna automática no histórico se houver comentário
            if comment:
                action_id = len(ticket.get("actions", [])) + 1
                comment_html = (
                    f'<div class="space-y-1">'
                    f'<div class="flex items-center gap-2 text-emerald-700 font-bold text-xs">'
                    f'<i data-lucide="check-circle" class="w-4 h-4 text-emerald-600"></i>'
                    f'Ticket Semelhante Validado como Referência — DeskCortex'
                    f'</div>'
                    f'<p class="text-xs text-slate-700 leading-relaxed">'
                    f'<b>Nó KB #{node_id}</b> marcado como útil para este chamado.<br>'
                    f'<b>Observação do técnico:</b> {comment}'
                    f'</p>'
                    f'</div>'
                )
                ticket.setdefault("actions", []).append({
                    "id": action_id,
                    "type": "internal",
                    "author": f"{technician} (via DeskCortex Similar)",
                    "created_at": now_str,
                    "content": comment_html,
                    "time_spent": "—"
                })

            self._save()
            return ticket


ticket_store = TicketStore()
