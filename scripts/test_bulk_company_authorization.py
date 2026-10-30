import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
import admin_console as admin
import agent_control


class BulkCompanyAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(admin, 'STATE_FILE', Path(tmp)/'state.json'))
        self.audit = self.stack.enter_context(patch.object(admin.operations, 'audit'))
        self.catalog = self.stack.enter_context(patch.object(agent_control, 'all_selectable_company_ids', return_value=['1','2']))
        self.request = SimpleNamespace(client=None, state=SimpleNamespace(cortex_authenticated=True,
            cortex_user_obj={'role':'super_admin','id':'qa-admin'}))

    def test_bulk_adds_full_snapshot_and_preserves_publication_controls(self):
        ids = [str(i) for i in range(1,201)]
        self.catalog.return_value = ids
        admin.set_state({'pilot_organization_ids':['existing'], 'pilot_paused':True, 'auto_internal_note':False})
        preview = admin.all_companies_preview(self.request)
        self.assertEqual(preview['new'], 200)
        before = admin.get_state()
        result = admin.authorize_all_companies(self.request, admin.AllCompaniesAuthorization(token=preview['token']))
        after = admin.get_state()
        self.assertEqual(result['added'],200)
        self.assertEqual(set(after['pilot_organization_ids']),set(ids+['existing']))
        for key in before:
            if key not in {'pilot_organization_ids','company_authorized_at'}: self.assertEqual(after[key],before[key],key)
        self.assertEqual(self.audit.call_args.args[1]['actor'],'qa-admin')

    def test_changed_catalog_requires_new_confirmation_and_has_no_partial_write(self):
        preview = admin.all_companies_preview(self.request)
        self.catalog.return_value.append('3')
        with self.assertRaises(HTTPException) as error:
            admin.authorize_all_companies(self.request,admin.AllCompaniesAuthorization(token=preview['token']))
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(admin.get_state()['pilot_organization_ids'],[])
        self.audit.assert_not_called()

    def test_non_admin_cannot_preview_or_authorize(self):
        self.request.state.cortex_user_obj['role']='support_analyst'
        for operation in [lambda:admin.all_companies_preview(self.request),
                          lambda:admin.authorize_all_companies(self.request,admin.AllCompaniesAuthorization(token='a'*64))]:
            with self.assertRaises(HTTPException) as error: operation()
            self.assertEqual(error.exception.status_code,403)
        self.catalog.assert_not_called()

    def test_query_snapshot_excludes_internal_organizations(self):
        conn = MagicMock()
        internal = next(iter(agent_control.INTERNAL_ORGANIZATION_IDS))
        conn.__enter__.return_value.execute.return_value.fetchall.return_value=[('2',),('1',),(internal,),('2',)]
        with patch.object(agent_control.psycopg,'connect',return_value=conn):
            self.assertEqual(agent_control.all_selectable_company_ids(),['1','2'])
