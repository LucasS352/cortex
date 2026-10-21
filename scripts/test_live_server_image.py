"""
Teste de Verificação Live contra o Servidor Simulator (Fase 3)
==============================================================
Testa o endpoint /api/chat/image enviando um print real com o erro da SEFAZ 481.
"""

import urllib.request
import json
import base64
import io
import os
import sys
import unittest
from PIL import Image, ImageDraw, ImageFont

if os.getenv("CORTEX_RUN_LIVE_TESTS") != "1":
    raise unittest.SkipTest("Teste live: defina CORTEX_RUN_LIVE_TESTS=1 com o servidor isolado ativo")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Gerar uma imagem simulando o print real do ERP Master com a Rejeição 481
img = Image.new("RGB", (1200, 700), color=(240, 240, 240))
draw = ImageDraw.Draw(img)

# Desenhar janela do modal de erro do Master
draw.rectangle([150, 150, 1050, 500], fill=(255, 255, 255), outline=(180, 180, 180), width=2)
draw.rectangle([150, 150, 1050, 190], fill=(200, 30, 30)) # Barra de título vermelha

try:
    title_font = ImageFont.truetype("arial.ttf", 20)
    body_font = ImageFont.truetype("arial.ttf", 22)
    btn_font = ImageFont.truetype("arial.ttf", 18)
except Exception:
    title_font = body_font = btn_font = None

draw.text((170, 160), "Ocorreram os seguintes erros ao enviar a NFe:", fill=(255, 255, 255), font=title_font)

# Texto com ruído típico de OCR (diuerge, 8->481)
error_text = "8->481-Rejeição: Código Regime Tributário do emitente diverge do cadastro na SEFAZ"
draw.text((170, 240), error_text, fill=(30, 30, 30), font=body_font)

draw.rectangle([550, 420, 650, 460], fill=(220, 220, 220), outline=(100, 100, 100))
draw.text((585, 430), "OK", fill=(0, 0, 0), font=btn_font)

buf = io.BytesIO()
img.save(buf, format="PNG")
img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

# Enviar para a API Live
payload = json.dumps({
    "session_id": "live_test_fase3_v2",
    "image_base64": img_b64,
    "message": "segue o print do erro que deu aqui no master"
}).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:8000/api/chat/image",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print("=" * 70)
        print("RESPOSTA DA API LIVE /api/chat/image")
        print("=" * 70)
        print("Skills utilizadas:", res.get("skills_used"))
        print("Entidades estruturadas da visão:", res.get("vision_result", {}).get("entities"))
        print("Códigos de erro:", res.get("vision_result", {}).get("error_codes"))
        print("\nResposta do Romário:\n")
        print(res.get("reply", ""))
        print("=" * 70)

        # Validações
        skills = res.get("skills_used", [])
        assert "perception.extract_error_image" in skills, "VisionSkill não foi executada!"
        assert "domain.fiscal.diagnose_rejection" in skills, "FiscalSkill não foi executada!"
        assert "reasoning.evaluate_hypotheses" in skills, "InvestigationSkill não foi executada!"
        assert "presentation.humanize_response" in skills, "DialogueSkill não foi executada!"
        assert "481" in str(res.get("vision_result", {}).get("error_codes")), "Rejeição 481 não identificada!"
        assert "Rejeição 481" in res.get("reply", ""), "Romário não prescreveu a Rejeição 481!"
        print("\n✅ TODOS OS ASSERTS DO TESTE LIVE PASSARAM COM SUCESSO!")
except Exception as e:
    print("Erro no teste live:", e)
    sys.exit(1)
