import unittest
from datetime import date, datetime, timezone

import ticket_trends as trends


class TicketTrendTests(unittest.TestCase):
    def test_local_month_boundary_and_partial_month(self):
        rows = [
            {'external_id': 1, 'subject': 'SUP - SNC - Erro de sincronização',
             'created_date': datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc)},
            {'external_id': 2, 'subject': 'SUP - SNC - Erro de sincronização ',
             'created_date': datetime(2026, 10, 2, 12, tzinfo=timezone.utc)},
        ]
        data = trends.summarize(rows, date(2026, 8, 31), date(2026, 10, 5), '',
                                lambda subject, _: 'Integrações', 4)
        self.assertEqual([m['count'] for m in data['monthly']], [1, 0, 1])
        self.assertEqual([m['partial'] for m in data['monthly']], [True, False, True])
        self.assertEqual(data['selected_month'], '2026-09')

    def test_recurring_titles_and_error_filter(self):
        rows = [
            {'external_id': 10, 'subject': 'Erro ao emitir NF-e',
             'created_date': datetime(2026, 9, 2, tzinfo=timezone.utc)},
            {'external_id': 11, 'subject': ' erro ao emitir nf-e ',
             'created_date': datetime(2026, 9, 3, tzinfo=timezone.utc)},
            {'external_id': 12, 'subject': 'Implantação finalizada',
             'created_date': datetime(2026, 9, 4, tzinfo=timezone.utc)},
        ]
        data = trends.summarize(rows, date(2026, 9, 1), date(2026, 9, 30), '2026-09',
                                lambda subject, _: 'Fiscal', 0)
        self.assertEqual(data['reported_errors'][0]['count'], 2)
        self.assertEqual(len(data['reported_errors']), 1)
        self.assertEqual(len(data['recurring']), 2)

    def test_previous_period_same_number_of_days(self):
        self.assertEqual(trends.previous_dates(date(2026, 10, 1), date(2026, 10, 5)),
                         (date(2026, 9, 26), date(2026, 9, 30)))


if __name__ == '__main__':
    unittest.main()
