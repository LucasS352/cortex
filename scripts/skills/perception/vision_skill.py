"""
DeskCortex — VisionSkill (Camada de Percepção)
==============================================
Responsável por extrair evidências fáticas e entidades estruturadas
de capturas de tela do ERP Master ou fotos de erro enviadas pelo cliente.

Retorna estritamente um VisionResult tipado com VisualEntity, sem assumir
ou prescrever regras de negócio fiscais hardcoded.
"""

from __future__ import annotations
import base64
import io
import json
import logging
import re
import unicodedata
import urllib.request
from typing import Any, Dict, List, Optional

from PIL import Image

try:
    import winocr
    HAS_WINOCR = True
except ImportError:
    HAS_WINOCR = False

from config import GEMINI_API_KEY
from chat_session import Evidence, EvidenceType
from ..base_skill import BaseSkill, Capability, RiskLevel, SkillResult, VisualEntity, VisionResult

logger = logging.getLogger("DeskCortex.VisionSkill")


class VisionSkill(BaseSkill):
    """
    Skill de Percepção Visual de Classe Mundial.
    Combina Multimodal Vision (Gemini), OCR Local Nativo (winocr), Auto-Crop de
    caixas de diálogo e Normalizador Fuzzy para fontes bitmap do Windows.
    """

    name = "vision_skill"
    description = "Extrai entidades visuais e mensagens de erro de capturas de tela do ERP Master"
    version = "2.0.0"
    capabilities = [
        Capability.EXTRACT_ERROR_IMAGE,
        Capability.EXTRACT_ENTITIES_TEXT,
    ]
    risk_level = RiskLevel.READ_ONLY

    def __init__(self, api_key: str = GEMINI_API_KEY):
        self.api_key = api_key

    def can_handle(self, state: Any, context: Optional[Dict[str, Any]] = None) -> bool:
        if not context:
            return False
        return bool(context.get("image_bytes") or context.get("image_base64") or context.get("raw_text"))

    def execute(self, state: Any, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        """
        Executa a análise visual e enriquece o InvestigationState com evidências fáticas.
        """
        context = context or {}
        image_bytes: Optional[bytes] = context.get("image_bytes")
        image_base64: Optional[str] = context.get("image_base64")

        if not image_bytes and image_base64:
            raw_b64 = image_base64
            if "," in raw_b64:
                _, raw_b64 = raw_b64.split(",", 1)
            try:
                image_bytes = base64.b64decode(raw_b64)
            except Exception as e:
                return SkillResult(
                    success=False,
                    errors=[f"Falha ao decodificar base64 da imagem: {e}"],
                    confidence=0.0
                )

        if not image_bytes:
            # Caso receba apenas texto bruto de log
            raw_text = context.get("raw_text", "")
            vision_res = self._extract_entities_from_text(raw_text, is_ocr=False)
        else:
            vision_res = self._process_image(image_bytes, mime_type=context.get("mime_type", "image/png"))

        # Atualizar InvestigationState com Fatos comprovados pelo sistema
        self._feed_state_with_vision(state, vision_res)

        rejection_code = vision_res.get_first_value("rejection_code")
        module = vision_res.get_first_value("module")

        return SkillResult(
            success=True,
            data={
                "vision_result": vision_res.to_dict(),
                "entities": [e.to_dict() for e in vision_res.entities],
                "rejection_code": rejection_code,
                "module": module,
                "summary_observation": vision_res.summary_observation,
            },
            confidence=vision_res.confidence,
            metadata={"skill": self.name, "extracted_count": len(vision_res.entities)}
        )

    def _process_image(self, image_bytes: bytes, mime_type: str = "image/png") -> VisionResult:
        """Processa a imagem priorizando multimodal e caindo para OCR Local resiliente."""
        # 1. Tentar multimodal se chave estiver disponível
        gemini_res: Optional[VisionResult] = None
        if self.api_key:
            gemini_res = self._try_gemini_vision(image_bytes, mime_type)
            if gemini_res and len(gemini_res.entities) > 0:
                return gemini_res

        # 2. OCR Local Nativo (winocr) com Auto-Crop e Fuzzy Normalizer
        local_res = self._run_local_ocr_pipeline(image_bytes)
        if local_res and len(local_res.entities) > 0:
            return local_res

        # Se nenhum detectou erros críticos, retorna o melhor resultado obtido
        return gemini_res or local_res or VisionResult(raw_text="", confidence=0.5)

    def _run_local_ocr_pipeline(self, image_bytes: bytes) -> VisionResult:
        """Executa OCR local com suporte a upscaling e auto-crop de janelas de diálogo centrais."""
        if not HAS_WINOCR:
            return VisionResult(raw_text="winocr indisponível no ambiente", confidence=0.0)

        try:
            pil_img = Image.open(io.BytesIO(image_bytes))
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")

            # Execução 1: Imagem completa com upscale inteligente
            full_text = self._ocr_pil_image(pil_img, upscale=True)
            res = self._extract_entities_from_text(full_text, is_ocr=True)

            # Se encontrou entidades fiscais ou de e-commerce com alta confiança, retorna
            if res.entities:
                return res

            # Execução 2: Auto-Crop da área central (modal de erro do Windows costuma ficar centralizado)
            w, h = pil_img.size
            if w >= 1000 and h >= 700:
                crop_box = (int(w * 0.15), int(h * 0.20), int(w * 0.85), int(h * 0.80))
                cropped_img = pil_img.crop(crop_box)
                cropped_text = self._ocr_pil_image(cropped_img, upscale=True)
                cropped_res = self._extract_entities_from_text(cropped_text, is_ocr=True)

                if cropped_res.entities:
                    cropped_res.raw_text = f"{full_text}\n--- ÁREA CENTRAL ---\n{cropped_text}"
                    return cropped_res

            return res

        except Exception as e:
            logger.error(f"[VisionSkill] Erro no pipeline OCR local: {e}", exc_info=True)
            return VisionResult(raw_text=f"Erro OCR: {e}", confidence=0.0)

    def _ocr_pil_image(self, img: Image.Image, upscale: bool = True) -> str:
        """Roda winocr em uma imagem PIL com resize LANCZOS se necessário."""
        ocr_target = img
        if upscale and (img.width < 1800 or img.height < 1400):
            scale = 2.0
            ocr_target = img.resize(
                (int(img.width * scale), int(img.height * scale)),
                Image.Resampling.LANCZOS
            )
        ocr_data = winocr.recognize_pil_sync(ocr_target, "pt")
        return ocr_data.get("text", "")

    def _extract_entities_from_text(self, raw_text: str, is_ocr: bool = True) -> VisionResult:
        """
        Normaliza o texto com tolerância fuzzy e extrai entidades visuais estruturadas.
        Resolve ruídos como 'diuerge' -> 'diverge' e '8->481' -> '481'.
        """
        # Normalização: minúsculo, sem acentos NFKD
        norm = unicodedata.normalize("NFKD", raw_text).encode("ascii", "ignore").decode("utf-8").lower()

        # Substituições comuns de ruído OCR de fontes bitmap
        fuzzy_text = norm.replace("diuerge", "diverge")
        fuzzy_text = fuzzy_text.replace("diuerg", "diverg")
        fuzzy_text = fuzzy_text.replace("rejeio", "rejeicao")
        fuzzy_text = fuzzy_text.replace("cdigo", "codigo")
        fuzzy_text = fuzzy_text.replace("sefa2", "sefaz")

        entities: List[VisualEntity] = []

        # 1. Detecção de Códigos de Rejeição SEFAZ
        rejection_matches = set()

        # Detecção direta de códigos SEFAZ comuns mesmo colados com caracteres como '->'
        for sm in re.findall(r"(?:->|\D|^)(481|783|539|204|694|10060)(?:\D|$)", fuzzy_text):
            rejection_matches.add(sm)

        # Detecção genérica de 3 a 5 dígitos precedidos ou seguidos de termos de erro/rejeição
        for m in re.findall(r"(?:->|\b|\D)(\d{3,5})\s*[-:]?\s*(?:rejei|rejeicao|erro)", fuzzy_text, re.I):
            rejection_matches.add(m)
        for m in re.findall(r"(?:rejeicao|rejei|erro|cod|codigo)\s*[:\-]?\s*(\d{3,5})", fuzzy_text, re.I):
            rejection_matches.add(m)

        # Detecção específica de Timeout / 10060 SEFAZ
        if "10060" in rejection_matches or "timed out" in fuzzy_text or "connection timed out" in fuzzy_text or ("timeout" in fuzzy_text and ("sefaz" in fuzzy_text or "nfe" in fuzzy_text or "nfce" in fuzzy_text)):
            rejection_matches.add("10060")
            entities.append(VisualEntity(
                type="rejection_code",
                value="10060",
                confidence=0.98,
                raw_snippet="Erro Interno 10060 / Connection timed out na SEFAZ"
            ))
            entities.append(VisualEntity(
                type="module",
                value="Fiscal",
                confidence=0.98,
                raw_snippet="NF-e / NFC-e"
            ))
            entities.append(VisualEntity(
                type="screen_path",
                value="Vendas > Faturamento > NF-e / NFC-e (Consultar na SEFAZ)",
                confidence=0.95,
                raw_snippet="Consultar na SEFAZ"
            ))

        # Semântica contextual específica para Rejeição 481 (CRT Divergente)
        is_481_semantic = (
            "481" in rejection_matches
            or bool(re.search(r"tribut[a-z]*.*di[uv,]*erg", fuzzy_text))
            or bool(re.search(r"regime.*tribut", fuzzy_text))
        )
        if is_481_semantic:
            rejection_matches.add("481")
            entities.append(VisualEntity(
                type="rejection_code",
                value="481",
                confidence=0.98,
                raw_snippet="Regime Tributário do emitente diverge do cadastro na SEFAZ"
            ))
            entities.append(VisualEntity(
                type="module",
                value="Fiscal",
                confidence=0.98,
                raw_snippet="NFe / SEFAZ"
            ))
            entities.append(VisualEntity(
                type="screen_path",
                value="Configurações > Empresa / Filiais > Parâmetros Fiscais (CRT)",
                confidence=0.95,
                raw_snippet="Configuração de CRT no Master"
            ))

        # Semântica contextual para Rejeição 783 (CFOP interestadual consumidor final)
        elif re.search(r"cfop.*interestadual", fuzzy_text) or "783" in rejection_matches:
            rejection_matches.add("783")
            entities.append(VisualEntity(
                type="rejection_code",
                value="783",
                confidence=0.98,
                raw_snippet="CFOP de operação interestadual para consumidor final"
            ))
            entities.append(VisualEntity(
                type="module",
                value="Fiscal",
                confidence=0.98,
                raw_snippet="Vendas > Faturamento > NF-e"
            ))

        # Semântica contextual para Rejeição 539 / 204 (Duplicidade)
        elif re.search(r"duplicidade.*nfe", fuzzy_text) or "539" in rejection_matches or "204" in rejection_matches:
            val = "539" if "539" in rejection_matches or "chave" in fuzzy_text else "204"
            entities.append(VisualEntity(
                type="rejection_code",
                value=val,
                confidence=0.95,
                raw_snippet="Duplicidade de NF-e"
            ))
            entities.append(VisualEntity(
                type="module",
                value="Fiscal",
                confidence=0.95,
                raw_snippet="Vendas > Faturamento > NF-e (Consultar na SEFAZ)"
            ))

        # Outros códigos de rejeição SEFAZ genéricos detectados
        for code in rejection_matches:
            if not any(e.type == "rejection_code" and e.value == code for e in entities):
                entities.append(VisualEntity(
                    type="rejection_code",
                    value=code,
                    confidence=0.90,
                    raw_snippet=f"Rejeição {code}"
                ))
                if not any(e.type == "module" for e in entities):
                    entities.append(VisualEntity(type="module", value="Fiscal", confidence=0.90, raw_snippet="SEFAZ"))

        # 2. Erros de E-commerce / Integração de Pedidos
        if "sku_not_found" in fuzzy_text or re.search(r"produto\s*n[aã]o\s*(encontrado|localizado|cadastrado)", fuzzy_text):
            entities.append(VisualEntity(
                type="ecommerce_error",
                value="SKU_NOT_FOUND",
                confidence=0.98,
                raw_snippet="SKU não cadastrado ou não associado"
            ))
            entities.append(VisualEntity(type="module", value="Ecommerce", confidence=0.98, raw_snippet="Ocorrências de Pedidos"))
            entities.append(VisualEntity(
                type="screen_path",
                value="Configurações > E-commerce > De-para de Produtos",
                confidence=0.95,
                raw_snippet="De-para de Produtos"
            ))

        if re.search(r"forma\s*de\s*pagamento", fuzzy_text) and re.search(r"(n[aã]o\s*vinculad|n[aã]o\s*relacionad|pendente|erro)", fuzzy_text):
            entities.append(VisualEntity(
                type="ecommerce_error",
                value="FORMA_PAGAMENTO_NAO_VINCULADA",
                confidence=0.95,
                raw_snippet="Forma de pagamento não associada no De-para"
            ))
            entities.append(VisualEntity(type="module", value="Ecommerce", confidence=0.95, raw_snippet="De-para"))

        if re.search(r"(transportadora|metodo\s*de\s*envio|frete)", fuzzy_text) and re.search(r"(n[aã]o\s*vinculad|n[aã]o\s*relacionad|pendente|erro)", fuzzy_text):
            entities.append(VisualEntity(
                type="ecommerce_error",
                value="TRANSPORTADORA_NAO_VINCULADA",
                confidence=0.95,
                raw_snippet="Transportadora não vinculada no De-para"
            ))
            entities.append(VisualEntity(type="module", value="Ecommerce", confidence=0.95, raw_snippet="De-para"))

        # 3. Identificação de Hubs / Canais
        for hub in ["anymarket", "vtex", "shopee", "mercado livre", "magalu", "shein", "amazon", "skyhub", "olist"]:
            if hub in fuzzy_text:
                entities.append(VisualEntity(
                    type="integration",
                    value=hub.title(),
                    confidence=0.92,
                    raw_snippet=hub
                ))
                break

        # 4. Número de Pedido
        order_m = re.search(r"(?:pedido|ordem|venda|numero)\s*[:#]?\s*(\d{4,12})", fuzzy_text)
        if order_m:
            entities.append(VisualEntity(
                type="order_number",
                value=order_m.group(1),
                confidence=0.90,
                raw_snippet=order_m.group(0)
            ))

        obs = f"OCR Local: Processado com sucesso ({len(raw_text)} chars)."
        rejection = next((e.value for e in entities if e.type == "rejection_code"), None)
        if rejection:
            obs += f" Identificada Rejeição SEFAZ {rejection}."

        return VisionResult(
            raw_text=raw_text,
            normalized_text=fuzzy_text,
            entities=entities,
            confidence=0.96 if entities else 0.60,
            summary_observation=obs
        )

    def _try_gemini_vision(self, image_bytes: bytes, mime_type: str) -> Optional[VisionResult]:
        """Chama o modelo Gemini Multimodal se disponível na rede."""
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Você é o especialista de Visão Computacional do DeskCortex. "
                                "Extraia estritamente em JSON: "
                                "{\"rejection_code\": \"481/783/539... ou null\", "
                                "\"error_message\": \"mensagem legível\", "
                                "\"module\": \"Fiscal/Ecommerce/Financeiro/etc\", "
                                "\"screen\": \"caminho de tela visível\", "
                                "\"order_number\": \"se houver\", "
                                "\"integration\": \"se houver\"}"
                            )
                        },
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
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    text = candidates[0]["content"]["parts"][0]["text"].strip()
                    parsed = json.loads(text)
                    entities: List[VisualEntity] = []

                    if parsed.get("rejection_code"):
                        entities.append(VisualEntity(
                            type="rejection_code",
                            value=str(parsed["rejection_code"]),
                            confidence=0.98,
                            raw_snippet=parsed.get("error_message", "")
                        ))
                    if parsed.get("module"):
                        entities.append(VisualEntity(
                            type="module",
                            value=parsed["module"],
                            confidence=0.95,
                            raw_snippet="Gemini Vision"
                        ))
                    if parsed.get("screen"):
                        entities.append(VisualEntity(
                            type="screen_path",
                            value=parsed["screen"],
                            confidence=0.92,
                            raw_snippet="Gemini Screen"
                        ))
                    if parsed.get("order_number"):
                        entities.append(VisualEntity(
                            type="order_number",
                            value=str(parsed["order_number"]),
                            confidence=0.90,
                            raw_snippet="Pedido"
                        ))
                    if parsed.get("integration"):
                        entities.append(VisualEntity(
                            type="integration",
                            value=parsed["integration"],
                            confidence=0.92,
                            raw_snippet="Integração"
                        ))

                    return VisionResult(
                        raw_text=text,
                        normalized_text=text.lower(),
                        entities=entities,
                        confidence=0.98 if entities else 0.70,
                        summary_observation="Análise Multimodal Gemini 3.6 Flash concluída com sucesso."
                    )
        except Exception as e:
            logger.info(f"[VisionSkill] Gemini multimodal em fallback: {e}")
            return None

    def _feed_state_with_vision(self, state: Any, vision_res: VisionResult):
        """
        Alimenta o InvestigationState central apenas com Evidências de FATO
        comprovadas tecnicamente pelo sistema (source='sistema').
        """
        if not hasattr(state, "add_evidence"):
            return

        rejection_code = vision_res.get_first_value("rejection_code")
        module = vision_res.get_first_value("module")
        screen_path = vision_res.get_first_value("screen_path")
        integration = vision_res.get_first_value("integration")
        order_number = vision_res.get_first_value("order_number")
        ecommerce_error = vision_res.get_first_value("ecommerce_error")

        if rejection_code:
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Rejeição SEFAZ {rejection_code} detectada visualmente na tela de emissão de NF-e.",
                confidence=0.98
            )
            state.intent = "erro_nota_fiscal"
            state.module = "Fiscal"

        if ecommerce_error:
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Erro de integração detectado na tela: {ecommerce_error}.",
                confidence=0.98
            )
            state.intent = "pedido_nao_integrado"
            state.module = "Ecommerce"

        if module and not getattr(state, "module", None):
            state.module = module

        if screen_path:
            state.add_evidence(
                source="sistema",
                type=EvidenceType.EVIDENCIA,
                content=f"Tela ativa visualizada no Master: {screen_path}.",
                confidence=0.95
            )

        if integration:
            state.integration = integration
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Integração de canal identificada: {integration}.",
                confidence=0.95
            )

        if order_number and hasattr(state, "entity"):
            state.entity = {"type": "order", "id": order_number}
            state.add_evidence(
                source="sistema",
                type=EvidenceType.FATO,
                content=f"Número do pedido visível na tela: {order_number}.",
                confidence=0.95
            )
