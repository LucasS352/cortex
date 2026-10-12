import unittest
from unittest.mock import patch
import httpx

from sync_current_tickets import PAGE_SIZE, fetch_ticket_by_id, fetch_page


class MissingTicketTest(unittest.TestCase):
    def test_transient_timeout_retries_same_page(self):
        class Client:
            calls = 0
            params = None
            def get(self, url, params):
                self.calls += 1
                self.params = params
                if self.calls == 1:
                    raise httpx.ReadTimeout('temporarily unavailable')
                class Response:
                    status_code = 200
                    def json(self): return [{'id': 123}]
                return Response()
        client = Client()
        with patch('sync_current_tickets.time.sleep') as sleep:
            self.assertEqual(fetch_page(client, 'id gt 0', False), [{'id': 123}])
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.params["$top"], PAGE_SIZE)
        self.assertEqual(PAGE_SIZE, 25)
        sleep.assert_called_once_with(5)

    def test_direct_404_is_an_explicit_missing_ticket(self):
        class Response:
            status_code = 404

        class Client:
            def get(self, url, params):
                return Response()

        self.assertIsNone(fetch_ticket_by_id(Client(), 129639))


if __name__ == '__main__':
    unittest.main()
