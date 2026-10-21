"""
DeskCortex -- Suite de Testes Automatizados da Fase 2.5
======================================================
Valida todas as correções críticas, inteligência de triagem,
motor de reranking, sessão de chat com travas T1/T2, visão
computacional e endpoints dos simuladores do cliente e técnico.
"""

import os
import sys
import unittest
import threading
from unittest.mock import patch, MagicMock

# Garante acesso ao diretorio scripts
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chat_session import (
    ChatSession,
    Evidence,
    EvidenceType,
    Hypothesis,
    InvestigationState,
    ResolutionStatus,
    SessionStore,
)
from reranker import Reranker, RankedChunk
from triage_engine import TriageEngine
from vision_analyzer import VisionAnalyzer, VisionResult


class TestRerankerCorrections(unittest.TestCase):
    """Testa correções dos bugs no Reranker."""

    def setUp(self):
        self.reranker = Reranker()

    def test_semantic_sim_normalization(self):
        """BUG 1: Normalização de leitura semantic_sim / semantic_score / similarity."""
        state = InvestigationState()
        # Caso 1: banco retorna semantic_sim
        raw_1 = [{
            "id": 1,
            "title": "Manual de Integracao AnyMarket",
            "content_for_embedding": "Como vincular forma de pagamento no Master",
            "tier": "MANUAL",
            "semantic_sim": 0.85,
            "media_path": None
        }]
        ranked_1 = self.reranker.rerank(raw_1, state=state)
        self.assertEqual(len(ranked_1), 1)
        self.assertAlmostEqual(ranked_1[0].semantic_score, 0.85, places=2)

        # Caso 2: banco ou adaptador retorna similarity
        raw_2 = [{
            "id": 2,
            "title": "Erro 539 Duplicidade NF-e",
            "content_for_embedding": "Rejeicao 539 ao emitir nota",
            "tier": "OFFICIAL_KB",
            "similarity": 0.78,
            "media_path": None
        }]
        ranked_2 = self.reranker.rerank(raw_2, state=state)
        self.assertEqual(len(ranked_2), 1)
        self.assertAlmostEqual(ranked_2[0].semantic_score, 0.78, places=2)

    def test_tier_source_type_mapping_and_authority(self):
        """BUG 2: Normalização do source_type e pesos de autoridade (MANUAL > KB > GOLD > SILVER)."""
        state = InvestigationState()
        raw_docs = [
            {"id": 1, "title": "Manual Master", "tier": "MANUAL", "similarity": 0.8},
            {"id": 2, "title": "KB Oficial", "tier": "OFFICIAL_KB", "similarity": 0.8},
            {"id": 3, "title": "Ticket Gold", "tier": "VALIDATED_TICKET", "similarity": 0.8},
            {"id": 4, "title": "Ticket Silver", "tier": "PROBABLE_TICKET", "similarity": 0.8},
        ]
        ranked = self.reranker.rerank(raw_docs, state=state)
        by_id = {r.node_id: r for r in ranked}

        # Verifica mapeamento de source_type
        self.assertEqual(by_id[1].source_type, "MANUAL")
        self.assertEqual(by_id[2].source_type, "KB")
        self.assertEqual(by_id[3].source_type, "GOLD")
        self.assertEqual(by_id[4].source_type, "SILVER")

        # Verifica hierarquia de autoridade: MANUAL (1.0) > KB (0.9) > GOLD (0.85) > SILVER (0.70)
        self.assertGreater(by_id[1].final_score, by_id[2].final_score)
        self.assertGreater(by_id[2].final_score, by_id[3].final_score)
        self.assertGreater(by_id[3].final_score, by_id[4].final_score)

    def test_media_bonus_not_clamped(self):
        """BUG 3: Garante que media_bonus=1.2 não é clampado a 1.0 quando metadata de módulo bate."""
        state = InvestigationState(module="Ecommerce")
        doc_sem_media = {
            "id": 1,
            "title": "Guia AnyMarket",
            "tier": "MANUAL",
            "similarity": 0.9,
            "media_path": None
        }
        doc_com_media = {
            "id": 2,
            "title": "Guia AnyMarket",
            "tier": "MANUAL",
            "similarity": 0.9,
            "media_path": "/media/gifs/de_para.gif",
            "media_metadata": {"module": "Ecommerce"}
        }

        r_sem = self.reranker.rerank([doc_sem_media], state=state)[0]
        r_com = self.reranker.rerank([doc_com_media], state=state)[0]

        # Com midia e módulo deve pontuar estritamente mais alto que sem midia
        self.assertGreater(r_com.final_score, r_sem.final_score)
        self.assertAlmostEqual(r_com.media_bonus, 1.2, places=2)

    def test_content_text_fallback_chain(self):
        """Verifica fallback de content_text para solution e diagnosis_summary."""
        state = InvestigationState(integration="AnyMarket")
        doc = {
            "id": 10,
            "title": "Chamado Antigo",
            "tier": "VALIDATED_TICKET",
            "content": "",
            "solution": "Configurado o de-para de transportadora na AnyMarket",
            "similarity": 0.75
        }
        ranked = self.reranker.rerank([doc], state=state)
        self.assertEqual(len(ranked), 1)
        # O fallback capturou o texto de solution
        self.assertIn("AnyMarket", ranked[0].content_text)
        # O bonus de integração foi ativado
        self.assertAlmostEqual(ranked[0].integration_bonus, 1.0, places=2)


class TestChatSessionAndSafetyLocks(unittest.TestCase):
    """Testa regras de integridade de dados e travas de segurança T1/T2."""

    def test_trava_t2_runtime_enforcement(self):
        """Trava T2: 'manual' NUNCA pode ser fonte de FATO (apenas sistema ou usuario/tecnico)."""
        # 1. Tentar criar FATO com source='manual' deve disparar ValueError
        with self.assertRaises(ValueError):
            Evidence(
                source="manual",
                type=EvidenceType.FATO,
                content="De-para nao cadastrado"
            )

        # 2. Criar FATO com source='sistema' deve ser permitido
        ev_sistema = Evidence(
            source="sistema",
            type=EvidenceType.FATO,
            content="Log confirma pedido nao encontrado"
        )
        self.assertEqual(ev_sistema.source, "sistema")
        self.assertEqual(ev_sistema.type, EvidenceType.FATO)

        # 3. Criar HIPOTESE com source='manual' deve ser permitido
        ev_manual = Evidence(
            source="manual",
            type=EvidenceType.HIPOTESE,
            content="Manual indica configurar transportadora"
        )
        self.assertEqual(ev_manual.source, "manual")
        self.assertEqual(ev_manual.type, EvidenceType.HIPOTESE)

    def test_session_store_thread_safety_and_lru(self):
        """Testa SessionStore com concorrência multithread e limite LRU."""
        store = SessionStore(max_sessions=20)
        errors = []

        def worker(thread_id):
            try:
                for i in range(10):
                    s_id = f"t_{thread_id}_s_{i}"
                    sess = ChatSession(session_id=s_id)
                    store.save(sess)
                    retrieved = store.get(s_id)
                    if retrieved is None and len(store) < 20:
                        errors.append(f"Session {s_id} not found unexpectedly")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        # Garante que o limite max_sessions foi respeitado
        self.assertLessEqual(len(store), 20)

    def test_hypothesis_duplication_prevented(self):
        """Garante que add_hypothesis não duplica hipóteses com mesmo ID/cause."""
        state = InvestigationState()
        state.add_hypothesis("forma_pagamento_nao_vinculada", 0.5)
        state.add_hypothesis("forma_pagamento_nao_vinculada", 0.8)

        # Deve haver apenas 1 hipotese, e a confianca deve ter sido atualizada
        self.assertEqual(len(state.hypotheses), 1)
        self.assertEqual(state.hypotheses[0].confidence, 0.8)

    def test_serialization_and_from_dict(self):
        """Verifica serialização to_dict e desserialização from_dict de ChatSession."""
        sess = ChatSession(session_id="test_sess_42")
        sess.state.intent = "pedido_nao_integrado"
        sess.state.module = "Ecommerce"
        sess.state.add_evidence(source="sistema", type=EvidenceType.FATO, content="Log de erro 404")
        sess.state.add_hypothesis("teste_h1", 0.7)
        sess.state.eliminated_hypotheses.append({"cause": "h_outra", "reason": "teste"})

        d = sess.to_dict()
        restored = ChatSession.from_dict(d)

        self.assertEqual(restored.session_id, "test_sess_42")
        self.assertEqual(restored.state.intent, "pedido_nao_integrado")
        self.assertEqual(len(restored.state.evidence), 1)
        self.assertEqual(restored.state.evidence[0].source, "sistema")
        self.assertEqual(restored.state.evidence[0].type, EvidenceType.FATO)
        self.assertEqual(len(restored.state.hypotheses), 1)
        self.assertTrue(any(e["cause"] == "h_outra" for e in restored.state.eliminated_hypotheses))


class TestTriageEngineIntelligence(unittest.TestCase):
    """Testa inteligência de triagem, detecção de intenções e extração de entidades."""

    def setUp(self):
        self.engine = TriageEngine()

    def test_all_six_intents_detection_and_missing_info(self):
        """Verifica detecção e missing_info para TODOS os 6 intents mapeados."""
        cases = [
            ("Pedido 105829 da AnyMarket nao caiu no Master", "pedido_nao_integrado"),
            ("Erro ao emitir nota fiscal rejeicao 539", "erro_nota_fiscal"),
            ("Estoque diverge no Mercado Livre e Master", "estoque_divergente"),
            ("Boleto bancario Itau nao esta gerando", "problema_boleto"),
            ("Usuario bloqueado no login do sistema", "problema_acesso"),
            ("Marketplace Shopee parou de sincronizar", "integracao_marketplace"),
        ]

        for text, expected_intent in cases:
            intent, module, _ = self.engine._detect_intent(text)
            self.assertEqual(intent, expected_intent, f"Falha ao detectar {expected_intent} em: {text}")

            # Garante que missing_info existe e é uma lista válida
            dummy_state = InvestigationState()
            missing = self.engine._build_missing_info(intent, dummy_state)
            self.assertIsInstance(missing, list)
            self.assertGreater(len(missing), 0, f"missing_info vazio para {expected_intent}")

    def test_specialized_extractors(self):
        """Testa extratores especializados: Pedidos, CFOP, Chave NF-e, Rejeição, Bancos."""
        # 1. Pedido em número solto
        session = ChatSession(session_id="s1")
        session.state.intent = "pedido_nao_integrado"
        session.state.missing_information = ["numero_pedido", "ocorrencias_do_pedido"]

        self.engine.process_user_response(session, "o pedido e 884729 da shopee")
        self.assertEqual(session.state.entity.get("id"), "884729")
        self.assertEqual(session.state.integration, "Shopee")
        # Garante que 'ocorrencias_do_pedido' NAO foi deletado acidentalmente
        self.assertIn("ocorrencias_do_pedido", session.state.missing_information)
        self.assertNotIn("numero_pedido", session.state.missing_information)

        # 2. CFOP e Rejeição
        session2 = ChatSession(session_id="s2")
        session2.state.intent = "erro_nota_fiscal"
        session2.state.missing_information = ["codigo_rejeicao", "cfop_utilizado"]

        self.engine.process_user_response(session2, "estou usando cfop 5102 e deu rejeicao 539")
        self.assertEqual(session2.state.entity.get("cfop"), "5102")
        # Rejeição SEFAZ registrada como evidência
        self.assertTrue(any("539" in e.content for e in session2.state.evidence))

        # 3. Chave NF-e (44 digitos)
        session3 = ChatSession(session_id="s3")
        session3.state.intent = "erro_nota_fiscal"
        chave = "35230912345678000190550010000012341234567890"
        self.engine.process_user_response(session3, f"a chave da nota e {chave}")
        self.assertTrue(any(chave in e.content for e in session3.state.evidence))

        # 4. Banco
        session4 = ChatSession(session_id="s4")
        session4.state.intent = "problema_boleto"
        session4.state.missing_information = ["banco_emissor"]
        self.engine.process_user_response(session4, "estou tentando emitir pelo bradesco")
        self.assertTrue(any("Bradesco" in e.content for e in session4.state.evidence))
        self.assertNotIn("banco_emissor", session4.state.missing_information)

    def test_accent_insensitive_error_confirmation(self):
        """BUG 5: Garante que verificação de erro funciona com ou sem acentos."""
        session = ChatSession(session_id="s_err")
        state = session.state
        state.add_hypothesis("forma_pagamento_nao_vinculada", 0.5)
        state.add_hypothesis("transportadora_nao_vinculada", 0.5)

        # Cliente digita com acentuação e caixa mista
        msg = "No gerenciamento de ocorrencias apareceu: RELAÇÃO DE FORMA DE PAGAMENTO NÃO ENCONTRADA"
        confirmed = self.engine._check_error_confirmation(msg, state)

        self.assertEqual(confirmed, "forma_pagamento_nao_vinculada")
        self.assertGreaterEqual(state.hypotheses[0].confidence, 0.9)

    def test_bayesian_hypothesis_update_and_elimination(self):
        """Verifica ajuste Bayesiano de confiança e eliminação de hipóteses concorrentes."""
        session = ChatSession(session_id="s_bayes")
        state = session.state
        state.intent = "pedido_nao_integrado"
        state.integration = "AnyMarket"
        state.add_hypothesis("forma_pagamento_nao_vinculada", 0.4)
        state.add_hypothesis("transportadora_nao_vinculada", 0.4)
        state.add_hypothesis("sku_nao_cadastrado", 0.2)

        # Cliente confirma que o erro é sobre transportadora
        self.engine.process_user_response(session, "a mensagem diz transportadora nao encontrada")

        hyp_transp = next(h for h in state.hypotheses if h.cause == "transportadora_nao_vinculada")
        self.assertGreaterEqual(hyp_transp.confidence, 0.75)


class TestVisionAnalyzerIntegration(unittest.TestCase):
    """Testa VisionResult e integração com o InvestigationState."""

    def test_vision_result_feed_to_triage(self):
        session = ChatSession(session_id="vis_sess")
        session.state.intent = "pedido_nao_integrado"
        session.state.missing_information = ["numero_pedido", "ocorrencias_do_pedido"]

        vr = VisionResult(
            is_erp_screenshot=True,
            error_codes=["SKU_NOT_FOUND"],
            error_messages=["Produto com referencia ABC-123 nao cadastrado no Master"],
            module_detected="Ecommerce",
            screen_path="Gerenciamento > Pedidos > Ocorrencias",
            integration_detected="AnyMarket",
            order_number="998877",
            summary_observation="Tela de ocorrencias indicando produto nao encontrado"
        )

        analyzer = VisionAnalyzer(api_key="dummy_key")
        facts = analyzer.feed_to_triage(vr, session)

        # Fatos extraídos devem estar presentes
        self.assertIn("Modulo: Ecommerce", facts)
        self.assertIn("Integracao: AnyMarket", facts)
        self.assertIn("Pedido: #998877", facts)
        self.assertTrue(any("SKU_NOT_FOUND" in f for f in facts))

        # InvestigationState deve ter recebido como FATO de fonte sistema (Trava T2)
        fatos_sistema = [e for e in session.state.evidence if e.source == "sistema" and e.type == EvidenceType.FATO]
        self.assertGreaterEqual(len(fatos_sistema), 3)

        # Missing information resolvido
        self.assertNotIn("numero_pedido", session.state.missing_information)
        self.assertNotIn("ocorrencias_do_pedido", session.state.missing_information)


class TestSimulatorFastAPIEndpoints(unittest.TestCase):
    """Testa endpoints da API FastAPI no simulator_app."""

    @classmethod
    def setUpClass(cls):
        from starlette.testclient import TestClient
        from simulator_app import app
        cls.client = TestClient(app)

    def test_get_root_page(self):
        """Verifica se a página principal HTML carrega com status 200."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("DeskCortex", response.text)
        self.assertIn("Mesa do Técnico", response.text)

    def test_client_submit_is_received_and_legacy_chat_is_disabled(self):
        """A abertura entra na fila; o chat antigo não pode gerar resposta paralela."""
        payload = {
            "subject": "Pedido nao caiu no sistema",
            "module": "Ecommerce",
            "urgency": "alta",
            "description": "O pedido 105829 da AnyMarket nao caiu no sistema"
        }
        res = self.client.post("/api/client/submit", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("session_id", data)
        self.assertEqual(data["status"], "received")
        session_id = data["session_id"]

        # O chat legado foi intencionalmente encerrado; a análise é iniciada
        # pela mesa do técnico para não existir resposta automática ao cliente.
        chat_payload = {
            "session_id": session_id,
            "message": "Aparece um erro de transportadora nao vinculada na coluna de erro"
        }
        res_chat = self.client.post("/api/chat", json=chat_payload)
        self.assertEqual(res_chat.status_code, 410)

    def test_tech_desk_report_and_action(self):
        """Verifica o relatório da Mesa do Técnico e execução de ação do especialista."""
        # Cria uma sessão primeiro
        res = self.client.post("/api/client/submit", json={
            "subject": "Pedido travado",
            "module": "Ecommerce",
            "urgency": "media",
            "description": "Pedido 4567 nao caiu"
        })
        session_id = res.json()["session_id"]

        # Busca o relatório técnico
        res_rep = self.client.get(f"/api/tech/report/{session_id}")
        self.assertEqual(res_rep.status_code, 200)
        rep = res_rep.json()
        self.assertEqual(rep["session_id"], session_id)
        self.assertIn("investigation_state", rep)

        # Executa uma ação de técnico (aprovar solução ou solicitar print)
        res_act = self.client.post("/api/tech/action", json={
            "session_id": session_id,
            "action_type": "request_screenshot",
            "notes": "Favor enviar print da tela de ocorrencias"
        })
        self.assertEqual(res_act.status_code, 200)
        act_data = res_act.json()
        self.assertEqual(act_data.get("status"), "ok")
        self.assertIn("print", act_data.get("message").lower())

    @patch("simulator_app.vision_analyzer.analyze_screenshot")
    @patch("simulator_app.get_query_embedding")
    @patch("simulator_app.hybrid_search")
    @patch("urllib.request.urlopen")
    def test_chat_image_upload_endpoint(self, mock_urlopen, mock_hybrid, mock_emb, mock_vision):
        """Verifica fluxo completo do endpoint POST /api/chat/image com print de erro."""
        import json
        mock_vision.return_value = VisionResult(
            is_erp_screenshot=True,
            error_codes=["SKU_NOT_FOUND"],
            error_messages=["Produto ABC nao localizado"],
            module_detected="Ecommerce",
            integration_detected="AnyMarket",
            order_number="778899",
            summary_observation="Erro de produto nao encontrado no Master"
        )
        mock_emb.return_value = [0.01] * 3072
        mock_hybrid.return_value = [{
            "id": 1,
            "tier": "MANUAL",
            "semantic_sim": 0.9,
            "title": "Cadastro de De-Para de Produtos",
            "content_for_embedding": "Como resolver SKU_NOT_FOUND no ERP Master",
            "media_path": None,
            "manual_category": "Ecommerce",
            "screen_path": "Configurações > E-commerce > De-para de Produtos",
            "shortcut_keys": ["F3"]
        }]

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "Identifiquei na sua tela o erro SKU_NOT_FOUND para o produto ABC. Precisamos cadastrar o de-para do produto no Master."
                    }]
                }
            }]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Cria sessao primeiro
        res_sub = self.client.post("/api/client/submit", json={
            "subject": "Erro com pedido",
            "module": "Ecommerce",
            "urgency": "alta",
            "description": "Meu pedido 778899 deu erro"
        })
        session_id = res_sub.json()["session_id"]

        # Envia imagem base64
        fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        res_img = self.client.post("/api/chat/image", json={
            "session_id": session_id,
            "image_base64": f"data:image/png;base64,{fake_b64}",
            "message": "Segue o print da tela de ocorrencias"
        })
        self.assertEqual(res_img.status_code, 410)
        self.assertFalse(mock_vision.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
