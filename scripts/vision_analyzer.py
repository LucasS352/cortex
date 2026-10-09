"""
DeskCortex -- VisionAnalyzer: Motor de Visao Computacional para Suporte (Fase 2.5)
==================================================================================
Capacidade de inspecionar screenshots e capturas de tela enviadas por clientes
usando Gemini 3.6 Flash com capacidades multimodais nativas (Vision).

Extrai deterministicamente:
  - Codigos de erro tecnicos (ex: SKU_NOT_FOUND, 783, 539)
  - Mensagens textuais de alerta ou rejeicao
  - Modulo e tela visivel do ERP Master
  - Integracoes identificadas (AnyMarket, VTEX, Mercado Livre...)
  - Alimenta o InvestigationState com dados de source="sistema" e type=FATO
"""

from __future__ import annotations
import base64
from dataclasses import dataclass, field
import json
import re
from typing import Optional
import urllib.request

from config import GEMINI_API_KEY
from chat_session import ChatSession, Evidence, EvidenceType, ResolutionStatus


VISION_SYSTEM_PROMPT = """Voce e o modulo de Visao Computacional Especialista do DeskCortex para suporte ao ERP Master (Alternativa Sistemas).
Sua missao e inspecionar minuciosamente a imagem fornecida (captura de tela ou foto de monitor do ERP Master) e extrair informacoes cruciais para o diagnostico de suporte tecnico.

Responda EXCLUSIVAMENTE em formato JSON puro, sem crases markdown adicionais, no seguinte schema:
{
  "is_erp_screenshot": true,
  "error_codes": ["codigos de erro exatos, ex: SKU_NOT_FOUND, REJEICAO 783, 539"],
  "error_messages": ["mensagens ou alertas de erro legiveis na tela"],
  "module_detected": "nome do modulo (Ecommerce, Fiscal, Financeiro, Estoque, Vendas, Configuracoes)",
  "screen_path": "caminho ou nome da tela visivel (ex: Gerenciamento > Pedidos > Ocorrencias)",
  "integration_detected": "nome da integracao se visivel (AnyMarket, VTEX, Mercado Livre, etc)",
  "order_number": "numero de pedido visivel se houver",
  "fields_visible": {"campo_relevante": "valor_observado"},
  "summary_observation": "resumo objetivo em 1 ou 2 frases do que esta acontecendo na tela"
}

Se a imagem nao for uma tela do sistema ou nao tiver erros visiveis, preencha os campos com listas vazias e null, explicando em summary_observation."""


@dataclass
class VisionResult:
    is_erp_screenshot: bool = True
    error_codes: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    module_detected: Optional[str] = None
    screen_path: Optional[str] = None
    integration_detected: Optional[str] = None
    order_number: Optional[str] = None
    fields_visible: dict = field(default_factory=dict)
    summary_observation: str = ""
    raw_response: str = ""

    def has_errors(self) -> bool:
        return bool(self.error_codes or self.error_messages)


class VisionAnalyzer:
    """
    Analisador de imagens e prints de tela do ERP Master via Gemini 3.6 Flash.
    """

    def __init__(self, api_key: str = GEMINI_API_KEY):
        self.api_key = api_key

    def analyze_screenshot(self, image_bytes: bytes, mime_type: str = "image/png") -> VisionResult:
        """
        Inspeciona a imagem usando Gemini 3.6 Flash Vision e/ou OCR nativo offline (winocr).
        Garante diagnóstico 100% resiliente mesmo sem conexão ou com cotas esgotadas da API.
        """
        gemini_res: Optional[VisionResult] = None

        if self.api_key:
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={self.api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": VISION_SYSTEM_PROMPT},
                            {
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": b64_data
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 1024,
                    "responseMimeType": "application/json"
                }
            }

            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_parts = candidates[0].get("content", {}).get("parts", [])
                        raw_text = content_parts[0].get("text", "").strip()

                        clean_json = raw_text
                        if clean_json.startswith("```"):
                            clean_json = re.sub(r"^```(?:json)?\n?", "", clean_json)
                            clean_json = re.sub(r"\n?```$", "", clean_json)

                        parsed = json.loads(clean_json)
                        gemini_res = VisionResult(
                            is_erp_screenshot=parsed.get("is_erp_screenshot", True),
                            error_codes=parsed.get("error_codes") or [],
                            error_messages=parsed.get("error_messages") or [],
                            module_detected=parsed.get("module_detected"),
                            screen_path=parsed.get("screen_path"),
                            integration_detected=parsed.get("integration_detected"),
                            order_number=str(parsed["order_number"]) if parsed.get("order_number") else None,
                            fields_visible=parsed.get("fields_visible") or {},
                            summary_observation=parsed.get("summary_observation") or "",
                            raw_response=raw_text
                        )
            except Exception as e:
                print(f"[DeskCortex] Gemini Vision indisponível ({e}). Acionando OCR Local nativo...")

        # Se o Gemini obteve erros com sucesso, retornamos o resultado dele
        if gemini_res and gemini_res.has_errors():
            return gemini_res

        # Fallback para OCR Local nativo offline (winocr)
        local_res = self._analyze_with_local_ocr(image_bytes)

        if local_res.has_errors():
            return local_res

        # Se o Gemini trouxe algo não vazio (mesmo sem erros técnicos explícitos)
        if gemini_res:
            return gemini_res

        return local_res

    def _analyze_with_local_ocr(self, image_bytes: bytes) -> VisionResult:
        """
        Motor de OCR Local nativo offline (Windows winocr + PIL).
        Executa reconhecimento óptico de caracteres localmente com upscaling de alta precisão
        e extração determinística por expressões regulares para SEFAZ e ERP Master.
        """
        try:
            import io
            import unicodedata
            from PIL import Image
            import winocr

            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Upscale 2.0x para garantir legibilidade máxima de fontes do Windows/SEFAZ
            if img.width < 1800 or img.height < 1400:
                scale = 2.0
                ocr_img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
            else:
                ocr_img = img

            ocr_res = winocr.recognize_pil_sync(ocr_img, "pt")
            extracted_text = ocr_res.get("text", "")

            # Normalizar para ASCII minúsculo para busca sem acentos
            norm_text = unicodedata.normalize("NFKD", extracted_text).encode("ascii", "ignore").decode("utf-8").lower()

            error_codes: list[str] = []
            error_messages: list[str] = []
            module_detected: Optional[str] = None
            screen_path: Optional[str] = None
            integration_detected: Optional[str] = None
            order_number: Optional[str] = None

            # 1. Detecção de códigos numéricos de rejeição SEFAZ (ex: 8->481, Rejeição: 783, 539)
            sefaz_matches = re.findall(r"(?:->|\b)(\d{3})\s*[-:]\s*(?:rejei|rejeio|erro)", norm_text, re.I)
            sefaz_matches += re.findall(r"(?:rejeicao|rejeio|rejecao)\s*[:\-]?\s*(\d{3})", norm_text, re.I)
            for sm in set(sefaz_matches):
                if sm not in error_codes:
                    error_codes.append(sm)

            # 2. Diagnóstico de Rejeição CFOP x CSOSN
            if "cfop" in norm_text and ("csosn" in norm_text or "sosn" in norm_text or "permitido" in norm_text):
                if "CFOP_CSOSN_INCOMPATIVEL" not in error_codes:
                    error_codes.append("CFOP_CSOSN_INCOMPATIVEL")
                msg_cfop = "Rejeição: CFOP não permitido para o CSOSN informado"
                if msg_cfop not in error_messages:
                    error_messages.append(msg_cfop)
                module_detected = "Fiscal"
                screen_path = "Cadastros > Produtos / Tributação > Natureza de Operação (CFOP e CSOSN)"

            # 3. Diagnóstico de IE do Destinatário Inválida (SEFAZ / Master)
            elif "destinatario invalida" in norm_text or re.search(r"\bie\b.*(?:destinatario|invalida)", norm_text) or "destmatario invalida" in norm_text:
                if "IE_DESTINATARIO_INVALIDA" not in error_codes:
                    error_codes.append("IE_DESTINATARIO_INVALIDA")
                msg_ie = "IE do destinatario invalida"
                if msg_ie not in error_messages:
                    error_messages.append(msg_ie)
                module_detected = "Fiscal"
                screen_path = "Cadastros > Clientes / Fornecedores > Inscrição Estadual"

            # 4. Diagnóstico de Rejeição 481 (Regime Tributário diverge da SEFAZ)
            elif re.search(r"regime\s*tribut.*diverg", norm_text) or "481" in error_codes or re.search(r"481.*regime", norm_text):
                if "481" not in error_codes:
                    error_codes.append("481")
                msg_481 = "Rejeição 481: Código Regime Tributário do emitente diverge do cadastro na SEFAZ"
                if msg_481 not in error_messages:
                    error_messages.append(msg_481)
                module_detected = "Fiscal"
                screen_path = "Configurações > Empresa / Filiais > Parâmetros Fiscais (CRT)"

            # 5. Diagnóstico de Rejeição 783 (CFOP interestadual)
            elif re.search(r"cfop.*interestadual", norm_text) or "783" in error_codes:
                if "783" not in error_codes:
                    error_codes.append("783")
                error_messages.append("Rejeição 783: CFOP de operação interestadual para consumidor final")
                module_detected = "Fiscal"
                screen_path = "Vendas > Faturamento > NF-e"

            # 6. Diagnóstico de Rejeição 539 / 204 (Duplicidade)
            elif re.search(r"duplicidade.*nfe", norm_text) or "539" in error_codes or "204" in error_codes:
                if "539" not in error_codes and "204" not in error_codes:
                    error_codes.append("539")
                error_messages.append("Rejeição 539/204: Duplicidade de NF-e com diferença na Chave de Acesso")
                module_detected = "Fiscal"
                screen_path = "Vendas > Faturamento > NF-e (Consultar na SEFAZ)"

            # 7. Diagnóstico de Erro 1824 / vDesc (Desconto negativo)
            elif "vdesc" in norm_text or "atomic type" in norm_text or "desconto negativo" in norm_text:
                if "1824" not in error_codes:
                    error_codes.append("1824")
                error_messages.append("Erro 1824 SEFAZ: Element vDesc - Desconto negativo não permitido na NF-e")
                module_detected = "Fiscal"
                screen_path = "Vendas > Faturamento > Descontos da Venda"

            # 8. Diagnóstico de DigestValue
            elif "digestvalue" in norm_text or "digest value" in norm_text:
                if "DIGEST_VALUE" not in error_codes:
                    error_codes.append("DIGEST_VALUE")
                error_messages.append("DigestValue do documento não confere")
                module_detected = "Fiscal"
                screen_path = "Vendas > Faturamento > NF-e"

            # 9. Captura Universal e Limpa de Frases de Rejeição/Erro SEFAZ no texto (ex: "Rejeição: ...")
            rejeicao_matches = re.findall(r'(?:rejei[cç][aã]o|erro)\s*[:\-]\s*([^\[\(\r\n:]{6,90})', norm_text, re.I)
            for rm in rejeicao_matches:
                clean_rm = re.sub(r'\s+', ' ', rm).strip()
                clean_rm = re.sub(r'\b(tnttem|nitem|item)\b.*$', '', clean_rm, flags=re.I).strip()
                clean_rm = re.sub(r'\b(nio|nao)\b', 'não', clean_rm, flags=re.I)
                clean_rm = re.sub(r'\b(ok|fechar|cancelar|clique|fechar|opcoes)\b', '', clean_rm, flags=re.I).strip()
                if len(clean_rm) >= 8 and clean_rm not in error_messages:
                    error_messages.append(clean_rm)
                    if not module_detected:
                        module_detected = "Fiscal"

            # 9. Erros de E-commerce / Integração
            if "sku_not_found" in norm_text or re.search(r"produto\s*n[aã]o\s*(encontrado|localizado|cadastrado)", norm_text):
                error_codes.append("SKU_NOT_FOUND")
                error_messages.append("Produto/SKU do anúncio não cadastrado no ERP Master")
                module_detected = "Ecommerce"
                screen_path = "Configurações > E-commerce > De-para de Produtos"

            if re.search(r"forma\s*de\s*pagamento", norm_text) and re.search(r"(n[aã]o\s*vinculad|n[aã]o\s*relacionad|pendente|erro)", norm_text):
                error_codes.append("FORMA_PAGAMENTO_NAO_VINCULADA")
                error_messages.append("Forma de pagamento não associada no De-para")
                module_detected = "Ecommerce"
                screen_path = "Configurações > E-commerce > De-para de Formas de Pagamento"

            if re.search(r"(transportadora|metodo\s*de\s*envio|frete)", norm_text) and re.search(r"(n[aã]o\s*vinculad|n[aã]o\s*relacionad|pendente|erro)", norm_text):
                error_codes.append("TRANSPORTADORA_NAO_VINCULADA")
                error_messages.append("Transportadora não associada no De-para")
                module_detected = "Ecommerce"
                screen_path = "Configurações > E-commerce > De-para de Transportadoras"

            # 6. Detecção de Canais e Integrações
            for hub in ["anymarket", "vtex", "shopee", "mercado livre", "magalu", "shein", "amazon", "skyhub", "olist"]:
                if hub in norm_text:
                    integration_detected = hub.title()
                    break

            # 7. Módulo padrão Fiscal se mencionar termos fiscais
            if any(term in norm_text for term in ["nfe", "nota fiscal", "sefaz", "danfe", "emitente", "faturamento"]):
                if not module_detected:
                    module_detected = "Fiscal"
                if not screen_path:
                    screen_path = "Vendas > Faturamento > NF-e"

            # 8. Extração de número de pedido
            order_m = re.search(r"(?:pedido|ordem|venda|numero)\s*[:#]?\s*(\d{4,12})", norm_text)
            if order_m:
                order_number = order_m.group(1)

            obs = f"OCR Local (Windows): Texto extraído com sucesso ({len(extracted_text)} caracteres)."
            if error_codes:
                obs += f" Erros técnicos detectados: {', '.join(error_codes)}."

            return VisionResult(
                is_erp_screenshot=True,
                error_codes=error_codes,
                error_messages=error_messages,
                module_detected=module_detected,
                screen_path=screen_path,
                integration_detected=integration_detected,
                order_number=order_number,
                fields_visible={"ocr_raw": extracted_text[:400]},
                summary_observation=obs,
                raw_response=extracted_text
            )

        except Exception as ocr_err:
            print(f"[DeskCortex] Erro no OCR Local nativo: {ocr_err}")
            return VisionResult(
                is_erp_screenshot=False,
                summary_observation=f"Falha ao executar OCR: {ocr_err}"
            )

    def feed_to_triage(self, vision_result: VisionResult, session: ChatSession) -> list[str]:
        """
        Converte o resultado visual em evidencias tecnicas (source='sistema', type=FATO)
        e alimenta o InvestigationState.
        Retorna lista de fatos extraidos para exibicao ao cliente.
        """
        state = session.state
        extracted_facts = []

        # 1. Modulo detectado
        if vision_result.module_detected and not state.module:
            state.module = vision_result.module_detected
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Modulo identificado visualmente na tela: {vision_result.module_detected}"
            )
            extracted_facts.append(f"Modulo: {vision_result.module_detected}")

        # 2. Tela visivel
        if vision_result.screen_path:
            state.add_evidence(
                source="sistema",
                type=EvidenceType.EVIDENCIA,
                content=f"Tela visualizada: {vision_result.screen_path}"
            )
            extracted_facts.append(f"Tela: {vision_result.screen_path}")

        # 3. Integracao detectada
        if vision_result.integration_detected and not state.integration:
            state.integration = vision_result.integration_detected
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Integracao identificada na imagem: {vision_result.integration_detected}"
            )
            extracted_facts.append(f"Integracao: {vision_result.integration_detected}")

        # 4. Numero de pedido visivel
        if vision_result.order_number and not state.entity:
            state.entity = {"type": "order", "id": str(vision_result.order_number)}
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Pedido #{vision_result.order_number} identificado visualmente na tela"
            )
            extracted_facts.append(f"Pedido: #{vision_result.order_number}")
            state.missing_information = [m for m in state.missing_information if m != "numero_pedido"]

        # 5. Codigos de erro encontrados
        all_errors = vision_result.error_codes + vision_result.error_messages
        for err in all_errors:
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Erro tecnico detectado na tela: {err}"
            )
            extracted_facts.append(f"Erro detectado: {err}")

        # 6. Remover 'ocorrencias_do_pedido' ou 'mensagem_erro_exata' se capturados
        if vision_result.has_errors():
            state.missing_information = [
                m for m in state.missing_information
                if m not in ("ocorrencias_do_pedido", "mensagem_erro_exata")
            ]

        return extracted_facts
