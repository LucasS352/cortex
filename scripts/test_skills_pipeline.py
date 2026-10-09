"""
Teste de Integração E2E da Arquitetura de Skills (Fase 3)
=========================================================
Valida o pipeline de capacidades especializadas:
  VisionSkill -> InvestigationState -> FiscalSkill -> GroundedRAGSkill -> InvestigationSkill -> DialogueSkill
"""

import sys
sys.path.insert(0, "scripts")

from chat_session import ChatSession, ResolutionStatus
from skills.registry import get_global_registry
from skills.base_skill import Capability

def test_rejeicao_481_pipeline():
    print("=" * 70)
    print("TESTE E2E: Pipeline de Capacidades Especializadas — Rejeição 481")
    print("=" * 70)

    registry = get_global_registry()
    session = ChatSession(session_id="test_skill_481")
    state = session.state

    # 1. Entrada do usuário com imagem contendo ruído de OCR
    noisy_ocr_text = "Ocorreram os seguintes erros ao enviar a NFe: 8->481-Rejeição: Código Regime Tributário do emitente diuerge do cadastro na SEFAZ"
    session.add_user_turn("Estou tentando emitir a nota fiscal e deu esse erro na tela")

    print("\n[1] Executando Percepção Visual (VisionSkill)...")
    vision_res = registry.execute_single_capability(
        Capability.EXTRACT_ENTITIES_TEXT,
        state,
        context={"raw_text": noisy_ocr_text}
    )
    assert vision_res.success, f"VisionSkill falhou: {vision_res.errors}"
    entities = vision_res.data.get("entities", [])
    print(f"  -> Entidades estruturadas extraídas: {len(entities)}")
    for ent in entities:
        print(f"     * [{ent['type']}] = '{ent['value']}' (conf={ent['confidence']:.2f})")

    rejection_code = vision_res.data.get("rejection_code")
    assert rejection_code == "481", f"Esperava 481, obteve {rejection_code}"
    print(f"  -> Rejeição detectada: {rejection_code}")

    # Verificar se InvestigationState recebeu a evidência de FATO
    has_fato = any(ev.source == "sistema" and "481" in ev.content for ev in state.evidence)
    assert has_fato, "InvestigationState não recebeu a evidência FATO do sistema!"
    print("  -> InvestigationState atualizado com Evidência FATO (sistema).")

    # 2. Executando Domínio Fiscal (FiscalSkill)
    print("\n[2] Executando Domínio Fiscal (FiscalSkill)...")
    fiscal_res = registry.execute_single_capability(
        Capability.DIAGNOSE_FISCAL_REJECTION,
        state,
        context={"rejection_code": rejection_code}
    )
    assert fiscal_res.success, f"FiscalSkill falhou: {fiscal_res.errors}"
    diag = fiscal_res.data.get("rejection_diagnosis", {})
    print(f"  -> Diagnóstico estrutural: {diag.get('meta', {}).get('title')}")
    print(f"  -> Menu do Master: {diag.get('meta', {}).get('menu_path')}")

    # 3. Executando Conhecimento (GroundedRAGSkill)
    print("\n[3] Executando Conhecimento (GroundedRAGSkill)...")
    rag_res = registry.execute_single_capability(
        Capability.RETRIEVE_KNOWLEDGE,
        state,
        context={"rejection_code": rejection_code, "query_text": f"Rejeição {rejection_code} CRT divergente"}
    )
    assert rag_res.success, f"GroundedRAGSkill falhou: {rag_res.errors}"
    top_ranked = rag_res.data.get("top_ranked", [])
    print(f"  -> Chunks recuperados e rerankeados: {len(top_ranked)}")
    if top_ranked:
        print(f"  -> Top 1: [{top_ranked[0].source_type}] {top_ranked[0].title} (score={top_ranked[0].final_score:.3f})")

    # 4. Executando Raciocínio Investigativo & Trava T1 (InvestigationSkill)
    print("\n[4] Executando Raciocínio Investigativo (InvestigationSkill)...")
    inv_res = registry.execute_single_capability(
        Capability.EVALUATE_HYPOTHESES,
        state,
        context={"rejection_code": rejection_code}
    )
    assert inv_res.success, f"InvestigationSkill falhou: {inv_res.errors}"
    print(f"  -> Status da Investigação: {inv_res.data.get('status')}")
    print(f"  -> Causa Confirmada (Trava T1): {inv_res.data.get('confirmed_cause')}")
    print(f"  -> Pronto para Prescrever: {inv_res.data.get('ready_to_prescribe')}")
    assert inv_res.data.get("ready_to_prescribe") is True, "Trava T1 deveria ter confirmado a causa!"

    # 5. Executando Apresentação (DialogueSkill)
    print("\n[5] Executando Apresentação Humanizada (DialogueSkill)...")
    dial_res = registry.execute_single_capability(
        Capability.HUMANIZE_RESPONSE,
        state,
        context={
            "session": session,
            "media_refs": rag_res.data.get("media_refs", []),
            "shortcuts": rag_res.data.get("shortcuts", []),
            "user_message": "Estou tentando emitir a nota fiscal e deu esse erro na tela"
        }
    )
    assert dial_res.success, f"DialogueSkill falhou: {dial_res.errors}"
    reply = dial_res.data.get("reply", "")
    print(f"  -> Resposta do Romário:\n{reply}\n")

    # Guardrail Anti-Loop check: Não pode pedir print se imagem já foi enviada!
    assert "cola um print" not in reply.lower() and "manda um print" not in reply.lower(), \
        "Violação de Guardrail: Romário pediu print mesmo após imagem já ter sido analisada!"

    print("=" * 70)
    print("✅ TESTE E2E CONCLUÍDO COM 100% DE SUCESSO!")
    print("=" * 70)

if __name__ == "__main__":
    test_rejeicao_481_pipeline()
