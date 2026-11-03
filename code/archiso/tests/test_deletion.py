"""Permanent student deletion tests. No host accounts or homes are changed."""

import copy
import inspect
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import test_admin as existing
import test_persistence as persistence
from test_assignments import document


helper = existing.helper


class DeletionTests(unittest.TestCase):
    def setUp(self):
        persistence.PersistenceTests.setUp(self)
        self.stack.enter_context(patch.object(helper, 'student_has_processes', return_value=False))
        self.registry.write_text(json.dumps({'version': 1, 'students': {
            'student1000': {'uid': 1001, 'gid': 1001, 'shadow': persistence.shadow()}}}))
        self.commands.side_effect = self.linux_operation

    def linux_operation(self, command, input_text=None):
        if command[0] == '/usr/bin/userdel':
            self.users.pop(command[-1], None)
            self.hashes.pop(command[-1], None)
            return
        return persistence.PersistenceTests.linux_operation(self, command, input_text)

    reboot = persistence.PersistenceTests.reboot

    def delete(self, name='student1000', revision=None):
        return helper.dispatch({'action': 'delete_student', 'username': name,
                                'revision': revision or helper.revision(self.data_path.read_bytes())})

    def test_delete_existing_student_and_persistent_record(self):
        result = self.delete()
        self.assertTrue(result['linux_account_removed'])
        self.assertFalse(result['linux_account_already_missing'])
        self.assertTrue(result['metadata_removed'])
        self.assertNotIn('student1000', self.users)
        self.assertEqual(json.loads(self.data_path.read_text()), {})
        self.assertEqual(helper.load_registry()['students'], {})
        self.assertEqual(helper.load_deleted()['students'], ['student1000'])
        self.assertEqual(self.deleted.stat().st_mode & 0o777, 0o600)
        self.assertIn(['/usr/bin/userdel', '--remove', '--', 'student1000'],
                      [call.args[0] for call in self.commands.call_args_list])

    def test_orphan_metadata_and_repeated_delete(self):
        self.users.pop('student1000')
        result = self.delete()
        self.assertTrue(result['linux_account_already_missing'])
        self.assertTrue(result['metadata_removed'])
        self.assertEqual(json.loads(self.data_path.read_text()), {})
        self.assertFalse(any(call.args[0][0].endswith('/userdel') for call in self.commands.call_args_list))
        self.assertTrue(self.delete()['already_deleted'])

    def test_invalid_privileged_and_path_names_rejected(self):
        for name in ('root', 'eduos', 'daemon', 'nobody', 'student1000/../root',
                     'student1000;sh', '../student1000', 'arbitrary', 'student１２３４'):
            with self.subTest(name=name), self.assertRaises(helper.RequestError):
                self.delete(name)
        with self.assertRaisesRegex(helper.RequestError, 'already absent'):
            self.delete('student9999')

    def test_privileged_student_shaped_account_rejected(self):
        self.users['student1000'] = existing.account('student1000', uid=0, gid=0)
        with self.assertRaises(helper.RequestError):
            self.delete()
        self.assertIn('student1000', json.loads(self.data_path.read_text()))
        self.assertFalse(self.deleted.exists())

    def test_active_student_blocks_before_marker(self):
        with patch.object(helper, 'student_has_processes', return_value=True):
            with self.assertRaisesRegex(helper.RequestError, 'currently logged in'):
                self.delete()
        self.assertFalse(self.deleted.exists())
        self.assertIn('student1000', self.users)

    def test_userdel_failure_keeps_tombstone_for_safe_retry(self):
        with patch.object(helper, 'run_account', side_effect=helper.RequestError('userdel failed')):
            with self.assertRaisesRegex(helper.RequestError, 'PARTIAL FAILURE'):
                self.delete()
        self.assertIn('student1000', self.users)
        self.assertIn('student1000', json.loads(self.data_path.read_text()))
        self.assertEqual(helper.load_deleted()['students'], ['student1000'])
        self.delete()
        self.assertNotIn('student1000', self.users)

    def test_existing_account_with_symlink_home_is_rejected(self):
        home_root = self.path / 'home'
        home_root.mkdir()
        target = self.path / 'unrelated'
        target.mkdir()
        (home_root / 'student1000').symlink_to(target, target_is_directory=True)
        self.users['student1000'].pw_dir = str(home_root / 'student1000')
        with patch.object(helper.submissions, 'HOME_ROOT', home_root):
            with self.assertRaisesRegex(helper.RequestError, 'unsafe'):
                self.delete()
        self.assertTrue(target.exists())
        self.assertFalse(self.deleted.exists())

    def test_assignments_are_unassigned_or_removed(self):
        assignment = document()['assignments'][0]
        only = {**copy.deepcopy(assignment), 'id': 'LAB-0002', 'assigned_to': ['student1000']}
        self.assignments.write_text(json.dumps({'assignments': [assignment, only], 'next_id': 3}))
        self.delete()
        saved = json.loads(self.assignments.read_text())
        self.assertEqual(len(saved['assignments']), 1)
        self.assertEqual(saved['assignments'][0]['assigned_to'], ['student1002'])
        self.assertEqual(saved['next_id'], 3)

    def test_orphan_submissions_and_views_removed(self):
        self.users.pop('student1000')
        self.registry.write_text('{"version":1,"students":{}}')
        for root in (helper.VIEWS, helper.labs.SUBMISSIONS):
            child = root / 'student1000'
            child.mkdir(parents=True)
            (child / 'student-file.txt').write_text('private')
        self.delete()
        for root in (helper.VIEWS, helper.labs.SUBMISSIONS):
            self.assertFalse((root / 'student1000').exists())

    def test_verified_orphan_home_removed(self):
        self.users.pop('student1000')
        uid = os.getuid()
        self.registry.write_text(json.dumps({'version': 1, 'students': {
            'student1000': {'uid': uid, 'gid': uid, 'shadow': persistence.shadow()}}}))
        home_root = self.path / 'home'
        home = home_root / 'student1000'
        home.mkdir(parents=True)
        (home / 'file.txt').write_text('old work')
        with patch.object(helper.submissions, 'HOME_ROOT', home_root), \
             patch.object(helper.pwd, 'getpwuid', side_effect=KeyError(uid)):
            self.delete()
        self.assertFalse(home.exists())

    def test_unknown_or_unsafe_orphan_home_is_not_deleted(self):
        self.users.pop('student1000')
        self.registry.write_text('{"version":1,"students":{}}')
        home_root = self.path / 'home'
        home = home_root / 'student1000'
        home.mkdir(parents=True)
        with patch.object(helper.submissions, 'HOME_ROOT', home_root):
            result = self.delete()
        self.assertTrue(home.exists())
        self.assertIn('Stale home was left', result['warning'])
        # A symlink is never treated as a managed home, even with a recorded UID.
        (home_root / 'student1001').symlink_to(home, target_is_directory=True)
        with patch.object(helper.submissions, 'HOME_ROOT', home_root):
            with self.assertRaises(helper.RequestError):
                helper.checked_home('student1001', os.getuid())

    def test_atomic_metadata_failure_keeps_json_and_blocks_reboot_recreation(self):
        original = self.data_path.read_bytes()
        replace = helper.os.replace
        def fail_data(source, destination, *args, **kwargs):
            if Path(destination) == self.data_path:
                raise OSError('disk failure')
            return replace(source, destination, *args, **kwargs)
        with patch.object(helper.os, 'replace', side_effect=fail_data):
            with self.assertRaisesRegex(helper.RequestError, 'PARTIAL FAILURE'):
                self.delete()
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertEqual(list(self.path.glob('.student-data.json-*')), [])
        self.assertEqual(helper.load_deleted()['students'], ['student1000'])
        self.reboot()
        self.assertNotIn('student1000', self.users)
        self.delete()  # Safe retry finishes cleanup after the failed write.
        self.assertEqual(json.loads(self.data_path.read_text()), {})

    def test_uid_reuse_without_home_does_not_block_orphan_record_deletion(self):
        self.users.pop('student1000')
        with patch.object(helper.pwd, 'getpwuid', return_value=existing.account('other', 1001)):
            result = self.delete()
        self.assertTrue(result['metadata_removed'])

    def test_deleted_student_stays_deleted_after_reboot(self):
        self.delete()
        self.reboot()
        self.assertNotIn('student1000', self.users)
        self.assertEqual(helper.load_registry()['students'], {})
        self.assertEqual(json.loads(self.data_path.read_text()), {})

    def test_seed_cannot_reintroduce_tombstoned_builtin(self):
        self.delete()
        self.data_path.unlink()
        helper.initialize_state()
        self.assertEqual(json.loads(self.data_path.read_text()), {})

    def test_unmarked_partial_create_remains_restorable(self):
        registry = helper.load_registry()
        registry['students']['student1005'] = {'uid': 1005, 'gid': 1005,
                                                'shadow': persistence.shadow()}
        self.registry.write_text(json.dumps(registry))
        self.reboot()
        self.assertIn('student1005', self.users)

    def test_other_student_and_create_remain_available(self):
        self.data['student1001'] = {'roll_number': '1001', 'display_name': 'Other', 'tasks': []}
        self.data_path.write_text(json.dumps(self.data))
        self.users['student1001'] = existing.account('student1001', 1002, 1002)
        self.hashes['student1001'] = persistence.shadow()
        registry = helper.load_registry()
        registry['students']['student1001'] = {'uid': 1002, 'gid': 1002,
                                                'shadow': persistence.shadow()}
        self.registry.write_text(json.dumps(registry))
        self.delete()
        self.assertIn('student1001', json.loads(self.data_path.read_text()))
        self.assertIn('student1001', helper.load_registry()['students'])
        self.assertIn('student1001', self.users)
        with self.assertRaisesRegex(helper.RequestError, 'permanently deleted'):
            helper.dispatch({'action': 'create', 'revision': helper.revision(self.data_path.read_bytes()),
                             'username': 'student1000', 'roll_number': '1000',
                             'display_name': 'Reused ID', 'password': 'SECRET'})

    def test_gui_orphan_delete_uses_confirmation_and_helper(self):
        sent = []
        dashboard = types.SimpleNamespace(require_saved=lambda: True, selected='student1000',
                                          accounts={'student1000': {'exists': False}}, revision='revision', root=None,
                                          request=lambda payload, callback: sent.append(payload),
                                          deleted_student=lambda _: None)
        with patch.object(existing.app.messagebox, 'askyesno', return_value=True, create=True) as confirm:
            existing.app.Dashboard.delete_student(dashboard)
        self.assertEqual(sent[0]['action'], 'delete_student')
        self.assertEqual(sent[0]['username'], 'student1000')
        self.assertIn('already missing', confirm.call_args.args[1])
        sent.clear()
        with patch.object(existing.app.messagebox, 'askyesno', return_value=False, create=True):
            existing.app.Dashboard.delete_student(dashboard)
        self.assertEqual(sent, [])

    def test_gui_button_is_constructed_and_success_clears_selection(self):
        source = inspect.getsource(existing.app.Dashboard.__init__)
        self.assertIn("self.button(left, 'Delete Student', self.delete_student)", source)
        events = []
        students = types.SimpleNamespace(exists=lambda _: True,
                                         delete=lambda name: events.append(('removed', name)))
        dashboard = types.SimpleNamespace(selected='student1000', students=students,
                                          show_details=lambda: events.append(('cleared',)),
                                          clear_after_delete=False,
                                          refresh=lambda: events.append(('refreshed',)))
        existing.app.Dashboard.deleted_student(dashboard, {'warning': None})
        self.assertEqual(events, [('removed', 'student1000'), ('cleared',), ('refreshed',)])
        self.assertIsNone(dashboard.selected)
        self.assertTrue(dashboard.clear_after_delete)


if __name__ == '__main__':
    unittest.main()
