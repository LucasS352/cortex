import unittest
from datetime import datetime, timedelta, timezone

import ticket_dashboard as dashboard


class TicketDashboardTests(unittest.TestCase):
    def test_internal_contact_does_not_make_customer_company_ambiguous(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        def contact(person_id, organization_id, name):
            return {"id": person_id, "personType": 1,
                    "organization": {"id": organization_id, "businessName": name}}
        base = {"subject": "Conciliação", "status": "Novo", "urgency": "Normal",
                "created_date": now, "problem_statement": "", "technical_narrative": "",
                "owner": None, "owner_team": "Suporte"}
        internal = contact("357532854", "1258969477", "Alternativa Sistemas LTDA")
        customer = contact("U300", "139", "Homem da Terra Produtos Naturais")
        rows = dashboard.prepare([
            {**base, "external_id": 131057, "clients": [internal, customer]},
            {**base, "external_id": 100, "clients": [internal]},
            {**base, "external_id": 101, "clients": [internal, customer,
                 contact("U301", "535", "Alta Montanha")]},
        ], now, lambda *_: "Outros")
        by_id = {row["external_id"]: row for row in rows}
        self.assertEqual((by_id[131057]["company_id"], by_id[131057]["company_name"]),
                         ("139", "Homem da Terra Produtos Naturais"))
        self.assertEqual(by_id[100]["company_id"], "1258969477")
        self.assertEqual(by_id[101]["company_id"], dashboard.AMBIGUOUS_COMPANY)
        self.assertEqual(by_id[101]["associated_companies"],
                         ["Homem da Terra Produtos Naturais", "Alta Montanha"])

    def test_company_client_without_nested_organization_is_identified(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        base = {"subject": "Recebimentos", "status": "Novo", "urgency": "Normal",
                "created_date": now, "problem_statement": "", "technical_narrative": "",
                "owner": None, "owner_team": "Suporte"}
        raw = [
            {**base, "external_id": 131216, "clients": [{"id": "58", "personType": 2,
                "businessName": "Floricultura Dalin Decorações LTDA ME", "organization": None}]},
            {**base, "external_id": 131156, "clients": []},
            {**base, "external_id": 130950, "clients": [{"id": "1990488302", "personType": 1,
                "businessName": "Financeiro SK", "organization": None}]},
        ]
        rows = dashboard.prepare(raw, now, lambda *_: "Outros")
        result = dashboard.dashboard(rows, {})
        self.assertEqual(result["kpis"]["unidentified"], 2)
        self.assertEqual(result["kpis"]["without_client"], 1)
        self.assertEqual(result["kpis"]["person_without_company"], 1)
        company = dashboard.dashboard(rows, {"company": "58"})["items"]
        self.assertEqual([(x["id"], x["company"]) for x in company],
                         [(131216, "Floricultura Dalin Decorações LTDA ME")])

    def test_company_and_owner_counts_reconcile_with_distinct_organizations(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        def org(org_id, name):
            return {"organization": {"id": org_id, "businessName": name}}
        raw = [
            {"external_id": 1, "subject": "Pedido", "status": "Novo", "urgency": "Normal",
             "created_date": now - timedelta(days=2), "problem_statement": "", "technical_narrative": "",
             "clients": [org(10, "Empresa A"), org(10, "Empresa A")],
             "owner": {"id": 5, "businessName": "Ana"}, "owner_team": "Suporte"},
            {"external_id": 2, "subject": "Nota", "status": "Novo", "urgency": "Alta",
             "created_date": now - timedelta(days=40), "problem_statement": "", "technical_narrative": "",
             "clients": [org(10, "Empresa A"), org(20, "Empresa B")],
             "owner": {"id": 5, "businessName": "Ana"}, "owner_team": "Suporte"},
            {"external_id": 3, "subject": "Sem vínculo", "status": "Novo", "urgency": "Normal",
             "created_date": now, "problem_statement": "", "technical_narrative": "",
             "clients": [], "owner": None, "owner_team": None},
        ]
        rows = dashboard.prepare(raw, now, lambda *_: "Outros")
        result = dashboard.dashboard(rows, {}, 1, 20)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["kpis"]["ambiguous"], 1)
        self.assertEqual(result["kpis"]["unidentified"], 1)
        self.assertEqual(result["kpis"]["unassigned"], 1)
        self.assertEqual(sum(g["count"] for g in result["companies"]), 3)
        self.assertEqual(sum(g["count"] for g in result["owners"]), 3)
        self.assertEqual(dashboard.dashboard(rows, {"company": "10"})["items"][0]["id"], 1)
        filtered = dashboard.dashboard(rows, {"owner": "5", "high": True, "age": "30+"})
        self.assertEqual([item["id"] for item in filtered["items"]], [2])
        self.assertEqual(filtered["companies"][0]["id"], dashboard.AMBIGUOUS_COMPANY)


if __name__ == "__main__":
    unittest.main()
