import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ticket_sync_control as control


class TicketSyncScheduleTests(unittest.TestCase):
    def test_new_installation_defaults_to_enabled_midnight_brasilia(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
                control, 'STATE_FILE', Path(folder) / 'schedule.json'):
            plan = control.schedule()
        self.assertTrue(plan['enabled'])
        self.assertEqual(plan['time'], '00:00')
        self.assertEqual(plan['timezone'], 'America/Sao_Paulo')

    def test_old_six_am_schedule_is_migrated_once(self):
        with tempfile.TemporaryDirectory() as folder:
            state_file = Path(folder) / 'schedule.json'
            state_file.write_text(json.dumps({'enabled': True, 'time': '06:00',
                                              'last_auto_date': '2026-10-13'}), encoding='utf-8')
            with patch.object(control, 'STATE_FILE', state_file):
                state = control._read_state()
                control.save_schedule(True, '02:30')
                chosen = control._read_state()
        self.assertEqual(state['time'], '00:00')
        self.assertEqual(state['version'], control.SCHEDULE_VERSION)
        self.assertEqual(chosen['time'], '02:30')


if __name__ == '__main__':
    unittest.main()
