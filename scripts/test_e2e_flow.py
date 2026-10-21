# coding: utf-8
import json
import os
import unittest
import urllib.request

if os.getenv("CORTEX_RUN_LIVE_TESTS") != "1":
    raise unittest.SkipTest("Teste live: defina CORTEX_RUN_LIVE_TESTS=1 com o servidor isolado ativo")

base_url = "http://127.0.0.1:8000"

print("--- 1. Testando GET /tecnico ---")
req_tec = urllib.request.urlopen(f"{base_url}/tecnico")
assert req_tec.status == 200, f"Status /tecnico: {req_tec.status}"
content_tec = req_tec.read().decode("utf-8")
assert "Todos os tickets" in content_tec, "Triagem nao encontrada em /tecnico"
print("OK: /tecnico retornou 200 e contém Triagem (Todos os tickets)")

print("\n--- 2. Testando GET /cliente ---")
req_cli = urllib.request.urlopen(f"{base_url}/cliente")
assert req_cli.status == 200, f"Status /cliente: {req_cli.status}"
content_cli = req_cli.read().decode("utf-8")
assert "Portal do Cliente" in content_cli, "Portal do Cliente nao encontrado em /cliente"
print("OK: /cliente retornou 200 e contém Portal do Cliente")

print("\n--- 3. Testando GET /api/tickets/all ---")
req_all = urllib.request.urlopen(f"{base_url}/api/tickets/all")
data_all = json.loads(req_all.read().decode("utf-8"))
total = data_all.get("total", 0)
print(f"OK: /api/tickets/all retornou {total} tickets na triagem")
assert total >= 9, "Quantidade inicial de tickets insuficiente"

print("\n--- 4. Testando Abertura de Chamado pelo Cliente (POST /api/client/ticket) ---")
ticket_payload = {
    "company": "AR15 Sport",
    "requester": "Lucas Souza",
    "email": "lucas@ar15sport.com.br",
    "phone": "(11) 98765-4321",
    "subject": "Rejeição 481: Código Regime Tributário do emitente divergente do cadastro na SEFAZ",
    "urgency": "Alta",
    "description": "Ao tentar emitir NF-e 2419 no ERP Master, o sistema retornou Rejeição 481. Verificamos que a empresa migrou para Regime Normal recentemente.",
    "attachments": [
        {
            "name": "print_rejeicao_481.png",
            "data": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "type": "image/png"
        }
    ]
}

post_req = urllib.request.Request(
    f"{base_url}/api/client/ticket",
    data=json.dumps(ticket_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
post_resp = urllib.request.urlopen(post_req)
created = json.loads(post_resp.read().decode("utf-8"))
assert created["success"] is True, "Falha ao criar chamado"
new_id = created["ticket_id"]
print(f"OK: Chamado aberto com sucesso! Ticket #{new_id}")
ai_note = created["ticket"]["ai_internal_note"]
print(f"   [IA Diagnóstico]: {ai_note['diagnosis']}")
print(f"   [IA Resolução]: {ai_note['possible_resolution'][:90]}...")
print(f"   [IA Rota no Master]: {ai_note.get('screen_path')}")
print(f"   [IA Confiança]: {ai_note['confidence']}")

print("\n--- 5. Verificando se o Ticket caiu na Triagem dos Técnicos ---")
req_after = urllib.request.urlopen(f"{base_url}/api/tickets/all")
data_after = json.loads(req_after.read().decode("utf-8"))
first_ticket = data_after["tickets"][0]
assert first_ticket["id"] == new_id, f"Ticket #{new_id} nao esta no topo da fila"
print(f"OK: Ticket #{new_id} está no topo da Triagem do Técnico! Status: {first_ticket['status']}")

print("\n--- 6. Técnico Atende, Adiciona Ação Pública e Resolve (POST /api/tickets/{id}/action) ---")
action_payload = {
    "action_type": "public",
    "content": "Olá Lucas! Conforme orientação do diagnóstico, alteramos o CRT da empresa emissora para Regime Normal. A NF-e foi autorizada com sucesso pela SEFAZ.",
    "author": "Lucas Souza (Suporte)",
    "time_spent": "15 min",
    "new_status": "Resolvido"
}

action_req = urllib.request.Request(
    f"{base_url}/api/tickets/{new_id}/action",
    data=json.dumps(action_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)
action_resp = urllib.request.urlopen(action_req)
action_data = json.loads(action_resp.read().decode("utf-8"))
assert action_data["success"] is True
assert action_data["ticket"]["status"] == "Resolvido"
print(f"OK: Técnico registrou ação pública e finalizou ticket #{new_id} como Resolvido")

print("\n--- 7. Verificando Área do Cliente (GET /api/client/my-tickets) ---")
my_req = urllib.request.urlopen(f"{base_url}/api/client/my-tickets?company=AR15%20Sport")
my_tickets = json.loads(my_req.read().decode("utf-8"))
assert any(t["id"] == new_id for t in my_tickets), "Ticket nao apareceu na visao do cliente"
my_t = next(t for t in my_tickets if t["id"] == new_id)
assert my_t["status"] == "Resolvido", f"Status esperado Resolvido, obtido {my_t['status']}"
assert len(my_t["actions"]) == 1, "Cliente deve ver apenas a acao publica do tecnico"
assert "ai_internal_note" not in my_t, "SEGURANÇA: ai_internal_note NUNCA deve ser exposta ao cliente!"
print(f"OK: Cliente vê seu chamado #{new_id} com a resposta oficial do técnico.")
print("OK: Confidencialidade garantida — a Nota Interna da IA é estritamente privada para os técnicos.")

print("\n" + "="*60)
print(">>> FLUXO COMPLETO VALIDADO COM 100% DE SUCESSO! <<<")
print("="*60)

