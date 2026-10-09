import unittest
from datetime import datetime, timezone, timedelta
import overview_insights as i

class OverviewTests(unittest.TestCase):
    def test_department_alias_and_status_are_independent(self):
        self.assertEqual(i.department_for('Desenvolvimento - DEV'),'Desenvolvimento')
        self.assertEqual(i.department_for('Comercial'),'Comercial')
        self.assertEqual(i.department_for(None),'Sem equipe')
        rows=[{'owner_team':'Desenvolvimento - DEV','queue':'other','topic':'Outros','age_days':1,'is_urgent':False,'organization':'Cliente','external_id':1,'status':'Novo','created_date':None}]
        self.assertEqual(i.summarize(rows)['departments'],[{'name':'Desenvolvimento','count':1}])

    def test_support_waiting_development_is_separate_from_both_team_cards(self):
        rows = [
            {'owner_team': 'Suporte', 'status': 'Novo', 'topic': 'Outros', 'age_days': 1,
             'is_urgent': False, 'organization': 'A', 'external_id': 1, 'created_date': None},
            {'owner_team': 'Suporte', 'status': 'SUP - Aguardando Desenvolvimento',
             'topic': 'Outros', 'age_days': 2, 'is_urgent': False,
             'organization': 'A', 'external_id': 2, 'created_date': None},
            {'owner_team': 'Desenvolvimento - DEV', 'status': 'DEV - Novo',
             'topic': 'Outros', 'age_days': 3, 'is_urgent': False,
             'organization': 'B', 'external_id': 3, 'created_date': None},
        ]
        rows = i.enrich(rows, datetime(2026, 10, 5, tzinfo=timezone.utc), lambda *_: 'Outros')
        counts = {r['name']: r['count'] for r in i.summarize(rows)['departments']}
        self.assertEqual(counts, {'Suporte': 1,
                                  i.SUPPORT_WAITING_DEVELOPMENT: 1,
                                  'Desenvolvimento': 1})
        self.assertEqual(sum(counts.values()), len(rows))

    def test_queues_are_conservative(self):
        for status in ['DEV - Em coding', 'Aguardando Desenvolvimento',
                       'SUP PRM - Aguardando Desenvolvimento Viabilidade']:
            self.assertEqual(i.queue_for(status), 'development')
        self.assertEqual(i.queue_for('Recebido Desenvolvimento'), 'support')
        self.assertEqual(i.queue_for('Aguardando Retorno do cliente'), 'customer')
        self.assertEqual(i.queue_for('Aguardando'), 'other')
        self.assertEqual(i.queue_for('IMP - Agendado'), 'other')

    def test_age_at_snapshot_and_missing_dates(self):
        reference = datetime(2026, 9, 29, tzinfo=timezone.utc)
        self.assertEqual(i.age_days(reference-timedelta(days=31), reference), 31)
        self.assertIsNone(i.age_days(None, reference))
        self.assertEqual(i.age_days(reference+timedelta(days=1), reference), 0)

    def test_scope_counts_and_drilldown_reconcile(self):
        reference = datetime(2026, 9, 29, tzinfo=timezone.utc)
        rows = [{'external_id':n, 'subject':'Teste', 'status':status, 'urgency':priority,
                 'organization':'Cliente', 'created_date':reference-timedelta(days=days)}
                for n,status,priority,days in [(1,'Novo','Alta',2),(2,'Novo','Normal',31),
                    (3,'DEV - Novo','Alta',100),(4,'Aguardando Retorno do cliente','Normal',7)]]
        rows = i.enrich(rows, reference, lambda *_:'Produtos')
        summary = i.summarize(rows)
        self.assertEqual(sum(q['count'] for q in summary['queues']), len(rows))
        self.assertEqual([r['id'] for r in summary['priority']], [1,2])
        support = i.summarize(rows, 'support')
        self.assertEqual(support['total'],2)
        self.assertEqual(support['urgent'],len(i.select(rows,'support',high=True)))
        self.assertEqual(support['aged'],len(i.select(rows,'support',age='30+')))
        self.assertEqual(sum(a['count'] for a in support['ages']),2)

    def test_priority_returns_before_age_after_urgency(self):
        rows=[{'external_id':1,'is_urgent':False,'status':'Novo','age_days':100},
              {'external_id':2,'is_urgent':False,'status':'Retorno Recebido','age_days':5},
              {'external_id':3,'is_urgent':True,'status':'Novo','age_days':1}]
        self.assertEqual([r['external_id'] for r in sorted(rows,key=i.priority_key)],[3,2,1])

if __name__ == '__main__': unittest.main()
