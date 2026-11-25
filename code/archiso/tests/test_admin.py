"""Host-side safety tests. All account-changing commands are mocked.

Run: python3 -B code/archiso/tests/test_admin.py
"""

import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


PROFILE = Path(__file__).resolve().parents[1] / 'profile'
BIN = PROFILE / 'airootfs/usr/local/bin'


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(filename))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


sys.path.insert(0, str(PROFILE / 'airootfs/usr/share/eduos'))

helper = load('eduos_admin_helper', BIN / 'eduos-admin-helper')
try:
    import tkinter
except ImportError:
    # The authorization and student-data tests do not create any GUI widgets.
    tkinter = types.ModuleType('tkinter')
    tkinter.Toplevel = object
    tkinter.messagebox = types.ModuleType('tkinter.messagebox')
    tkinter.ttk = types.ModuleType('tkinter.ttk')
    scrolledtext = types.ModuleType('tkinter.scrolledtext')
    scrolledtext.ScrolledText = object
    sys.modules['tkinter.scrolledtext'] = scrolledtext
    sys.modules.update({'tkinter': tkinter, 'tkinter.messagebox': tkinter.messagebox,
                        'tkinter.ttk': tkinter.ttk})
app = load('eduos_admin_app', BIN / 'eduos-admin-app')
student_app = load('eduos_student_app', BIN / 'eduos-student-app')


def account(name='student1000', uid=1001, gid=1001):
    return types.SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid,
                                 pw_dir='/home/' + name, pw_shell='/bin/bash')


class ValidationTests(unittest.TestCase):
    def test_strict_usernames(self):
        for valid in ('student0000', 'student1000', 'student9999'):
            self.assertEqual(helper.username(valid), valid)
        for bad in ('eduos', 'root', 'student1000\n', 'student123', 'student12345',
                    'student１２３４', 'student١٢٣٤', 'Student1000', '-student1000',
                    'student1000;id', '../student1000', None, 1000):
            with self.subTest(bad=bad), self.assertRaises(helper.RequestError):
                helper.username(bad)

    def test_password_delimiters(self):
        for bad in ('', ' ', 'a\nroot:bad', 'a:b', 'a\x00b', 'a\rb', 'a' * 1025):
            with self.subTest(bad=repr(bad)), self.assertRaises(helper.RequestError):
                helper.password_field(bad)
        self.assertEqual(helper.password_field('valid $ password'), 'valid $ password')

    def test_document_shape(self):
        valid = {'student1000': {'roll_number': '01000', 'display_name': 'Student', 'tasks': []}}
        self.assertEqual(helper.validate_data(valid), valid)
        for bad in ([], {'eduos': valid['student1000']}, {'student1000': {}},
                    {'student1000': {**valid['student1000'], 'tasks': {}}},
                    {'student1000': {**valid['student1000'], 'password': 'secret'}},
                    {'student1000': {**valid['student1000'], 'tasks': [{'title': 'x'}]}}):
            with self.assertRaises(helper.RequestError):
                helper.validate_data(bad)

    def test_duplicate_keys_and_malformed_json(self):
        for raw in ('', '{', '{"action":"status","action":"save"}', '{"x":NaN}', b'\xff'):
            with self.assertRaises(helper.RequestError):
                helper.decode(raw)

    def test_malformed_requests_rejected_before_io(self):
        for request in (None, [], {}, {'action': []}, {'action': 'shell'},
                        {'action': 'status', 'command': 'id'},
                        {'action': 'disable', 'username': 'root'},
                        {'action': 'save', 'revision': 'bad', 'data': {}}):
            with self.subTest(request=request), self.assertRaises(helper.RequestError):
                helper.dispatch(request)

    def test_authorization(self):
        with patch.dict(os.environ, {'SUDO_UID': '1000', 'SUDO_USER': 'eduos'}, clear=True), \
                patch.object(helper.os, 'getuid', return_value=0), \
                patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.pwd, 'getpwuid', return_value=account('eduos', 1000)):
            helper.authorize(['helper'])
            with self.assertRaises(helper.RequestError):
                helper.authorize(['helper', 'arbitrary-command'])
            with patch.object(helper.pwd, 'getpwuid', return_value=account()):
                with self.assertRaises(helper.RequestError):
                    helper.authorize(['helper'])
            with patch.object(helper.os, 'geteuid', return_value=1000):
                with self.assertRaises(helper.RequestError):
                    helper.authorize(['helper'])
        with patch.dict(os.environ, {}, clear=True), patch.object(helper.os, 'getuid', return_value=0), \
                patch.object(helper.os, 'geteuid', return_value=0):
            with self.assertRaises(helper.RequestError):
                helper.authorize(['helper'])

    def test_gui_uses_real_identity(self):
        with patch.dict(os.environ, {'USER': 'eduos'}), patch.object(app.os, 'getuid', return_value=1001), \
                patch.object(app.os, 'geteuid', return_value=1001), \
                patch.object(app.pwd, 'getpwuid', return_value=account()):
            self.assertFalse(app.is_administrator())
            self.assertEqual(app.main(), 1)

    def test_helper_executable_denies_host_user(self):
        result = subprocess.run([sys.executable, '-B', '-I', '-c',
                                 'import sys, runpy; sys.path.insert(0, ' + repr(str(PROFILE / 'airootfs/usr/share/eduos')) +
                                 '); sys.argv = [' + repr(str(BIN / 'eduos-admin-helper')) +
                                 ']; runpy.run_path(sys.argv[0], run_name="__main__")'],
                                input='{"action":"status"}', text=True, capture_output=True,
                                env={'PATH': '/usr/bin:/bin'}, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertEqual(result.stderr, '')

    def test_no_secret_in_failure_response(self):
        fake_input = types.SimpleNamespace(buffer=io.BytesIO(b'{"action":"reset_password","password":"SECRET"}'))
        with patch.object(helper, 'authorize'), patch.object(helper, 'dispatch', side_effect=OSError('SECRET')), \
                patch.object(helper.sys, 'stdin', fake_input), patch.object(helper.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(helper.main(), 1)
            self.assertNotIn('SECRET', output.getvalue())

    def test_password_only_on_stdin(self):
        with patch.object(helper.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)) as run:
            helper.run_account(['/usr/bin/chpasswd'], 'student1000:SECRET\n')
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/usr/bin/chpasswd'])
            self.assertEqual(kwargs['input'], 'student1000:SECRET\n')
            self.assertNotIn('shell', kwargs)
            self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)


class StorageAndAccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.data_path = self.path / 'student-data.json'
        self.state_path = self.path / 'account-state.json'
        self.data = {'student1000': {'roll_number': '1000', 'display_name': 'Student 1000', 'tasks': []}}
        self.data_path.write_text(json.dumps(self.data))
        self.state_path.write_text('{}')
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for target, value in [('DATA', self.data_path), ('STATE', self.state_path), ('LOCK', self.path / 'lock')]:
            self.stack.enter_context(patch.object(helper, target, value))
        # Filesystem tests use a temporary directory owned by the test runner.
        self.stack.enter_context(patch.object(helper, 'secure_directory'))
        self.stack.enter_context(patch.object(helper, 'read_bytes', side_effect=lambda p: p.read_bytes()))
        self.stack.enter_context(patch.object(helper, 'mutation_lock', side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(helper.os, 'fchown'))
        self.users = {'student1000': account()}
        def lookup(name):
            if name not in self.users:
                raise KeyError(name)
            return self.users[name]
        self.stack.enter_context(patch.object(helper.pwd, 'getpwnam', side_effect=lookup))
        self.stack.enter_context(patch.object(helper.pwd, 'getpwall', side_effect=lambda: list(self.users.values())))
        self.stack.enter_context(patch.object(helper.grp, 'getgrall', return_value=[]))
        self.stack.enter_context(patch.object(helper, 'local_users', side_effect=lambda: set(self.users)))
        self.stack.enter_context(patch.object(helper, 'shadow_status', return_value={'locked': False, 'expire': ''}))
        self.commands = self.stack.enter_context(patch.object(helper, 'run_account'))
        self.persist = self.stack.enter_context(patch.object(helper, 'persist_accounts'))
        self.views = self.stack.enter_context(patch.object(helper, 'regenerate_views'))
        self.persistent_state = self.stack.enter_context(
            patch.object(helper, 'persistent_state_available', return_value=True))

    def request(self, action, **fields):
        return helper.dispatch({'action': action, **fields})

    def current_revision(self):
        return helper.revision(self.data_path.read_bytes())

    def create(self, name='student1001'):
        return self.request('create', revision=self.current_revision(), username=name,
                            roll_number='1001', display_name='New Student', password='SECRET')

    def test_status_union_and_missing_account(self):
        self.users['student1001'] = account('student1001', 1002)
        self.users.pop('student1000')
        response = self.request('status')
        self.assertFalse(response['accounts']['student1000']['exists'])
        self.assertTrue(response['accounts']['student1001']['exists'])
        self.assertEqual(response['accounts']['student1001']['shell'], '/bin/bash')

    def test_empty_tasks_in_student_portal(self):
        with patch.object(student_app, 'DATA_FILE', self.data_path):
            self.assertEqual(student_app.load_student_data('student1000')['tasks'], [])

    def test_save_preserves_extra_fields_and_permissions(self):
        updated = copy.deepcopy(self.data)
        updated['student1000']['display_name'] = 'Renamed'
        updated['student1000']['course'] = 'Mathematics'
        response = self.request('save', revision=self.current_revision(), data=updated)
        self.assertEqual(json.loads(self.data_path.read_text()), updated)
        self.assertEqual(self.data_path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(response['revision'], self.current_revision())

    def test_validation_failure_preserves_original(self):
        original = self.data_path.read_bytes()
        with self.assertRaises(helper.RequestError):
            self.request('save', revision=self.current_revision(), data={'student1000': {}})
        self.assertEqual(self.data_path.read_bytes(), original)

    def test_failed_replace_preserves_original_and_cleans_temp(self):
        original = self.data_path.read_bytes()
        with patch.object(helper.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.request('save', revision=self.current_revision(), data=self.data)
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertEqual(list(self.path.glob('.student-data.json-*')), [])

    def test_failed_fsync_preserves_original(self):
        original = self.data_path.read_bytes()
        with patch.object(helper.os, 'fsync', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.request('save', revision=self.current_revision(), data=self.data)
        self.assertEqual(self.data_path.read_bytes(), original)

    def test_post_commit_sync_is_reported_as_warning(self):
        with patch.object(helper.os, 'fsync', side_effect=[None, OSError('disk error')]):
            response = self.request('save', revision=self.current_revision(), data=self.data)
        self.assertIn('durability', response['warning'])
        self.assertEqual(response['revision'], self.current_revision())

    def test_stale_revision_rejected(self):
        stale = self.current_revision()
        self.data_path.write_text(json.dumps(self.data, indent=2))
        with self.assertRaisesRegex(helper.RequestError, 'changed since loading'):
            self.request('save', revision=stale, data=self.data)

    def test_change_during_save_rejected(self):
        original = self.data_path.read_bytes()
        with patch.object(helper, 'read_bytes', side_effect=[original, b'{}', original + b'\n']):
            with self.assertRaisesRegex(helper.RequestError, 'changed during'):
                self.request('save', revision=self.current_revision(), data=self.data)
        self.assertEqual(self.data_path.read_bytes(), original)

    def test_duplicate_linux_user(self):
        self.users['student1001'] = account('student1001', 1002)
        with self.assertRaisesRegex(helper.RequestError, 'Linux username already exists'):
            self.create()
        self.commands.assert_not_called()

    def test_duplicate_metadata(self):
        with self.assertRaisesRegex(helper.RequestError, 'already exists in academic metadata'):
            self.create('student1000')
        self.commands.assert_not_called()

    def test_existing_home_rejected(self):
        with patch.object(helper.os.path, 'lexists', return_value=True):
            with self.assertRaisesRegex(helper.RequestError, 'home directory already exists'):
                self.create()
        self.commands.assert_not_called()

    def test_create_empty_tasks_and_no_password_in_json(self):
        self.create()
        saved = json.loads(self.data_path.read_text())
        self.assertEqual(saved['student1001']['tasks'], [])
        self.assertNotIn('SECRET', self.data_path.read_text())
        commands = self.commands.call_args_list
        self.assertEqual(commands[0].args[0][0], '/usr/bin/useradd')
        self.assertIn('--create-home', commands[0].args[0])
        self.assertEqual(commands[1].args, (['/usr/bin/chpasswd'], 'student1001:SECRET\n'))

    def test_creation_metadata_partial_failure(self):
        original = self.data_path.read_bytes()
        with patch.object(helper, 'atomic_json', side_effect=OSError('disk error')):
            with self.assertRaisesRegex(helper.RequestError, 'PARTIAL FAILURE: Linux account, home and password'):
                self.create()
        self.assertEqual(self.data_path.read_bytes(), original)
        self.assertEqual(self.commands.call_count, 2)

    def test_creation_password_partial_failure(self):
        with patch.object(helper, 'run_account', side_effect=[None, helper.RequestError('failure')]):
            with self.assertRaisesRegex(helper.RequestError, 'password setup failed'):
                self.create()
        self.assertNotIn('student1001', json.loads(self.data_path.read_text()))

    def test_disable_enable_restores_previous_state(self):
        self.request('disable', username='student1000')
        self.assertIn('student1000', json.loads(self.state_path.read_text()))
        self.commands.assert_called_with(['/usr/bin/usermod', '--lock', '--expiredate', '1', '--', 'student1000'])
        self.request('enable', username='student1000')
        self.commands.assert_called_with(['/usr/bin/usermod', '--expiredate', '', '--unlock', '--', 'student1000'])
        self.assertEqual(json.loads(self.state_path.read_text()), {})

    def test_enable_does_not_unlock_unmanaged_account(self):
        with self.assertRaisesRegex(helper.RequestError, 'disabled by this dashboard'):
            self.request('enable', username='student1000')
        self.commands.assert_not_called()

    def test_reset_disabled_account_keeps_disabled(self):
        self.request('disable', username='student1000')
        self.commands.reset_mock()
        self.request('reset_password', username='student1000', password='SECRET')
        self.assertEqual(self.commands.call_args_list[1].args, (['/usr/bin/chpasswd'], 'student1000:SECRET\n'))
        self.commands.assert_called_with(['/usr/bin/usermod', '--lock', '--expiredate', '1', '--', 'student1000'])
        self.assertIn('student1000', json.loads(self.state_path.read_text()))

    def test_privileged_targets_denied(self):
        self.users['student1000'].pw_uid = 0
        with self.assertRaises(helper.RequestError):
            self.request('reset_password', username='student1000', password='SECRET')
        self.commands.assert_not_called()

    def test_shared_uid_denied(self):
        self.users['eduos'] = account('eduos', 1001)
        with self.assertRaisesRegex(helper.RequestError, 'sharing a Linux UID'):
            self.request('disable', username='student1000')
        self.commands.assert_not_called()

    def test_wheel_member_denied(self):
        wheel = types.SimpleNamespace(gr_name='wheel', gr_gid=998, gr_mem=['student1000'])
        with patch.object(helper.grp, 'getgrall', return_value=[wheel]):
            with self.assertRaises(helper.RequestError):
                self.request('disable', username='student1000')
        self.commands.assert_not_called()

    def test_disable_failure_keeps_recovery_state(self):
        self.commands.side_effect = helper.RequestError('usermod failed')
        with self.assertRaisesRegex(helper.RequestError, 'Recovery state is retained'):
            self.request('disable', username='student1000')
        self.assertEqual(json.loads(self.state_path.read_text())['student1000'], {'locked': False, 'expire': ''})

    def test_enabled_password_reset_does_not_change_account_expiry(self):
        self.request('reset_password', username='student1000', password='SECRET')
        self.commands.assert_called_once_with(['/usr/bin/chpasswd'], 'student1000:SECRET\n')
        self.assertEqual(json.loads(self.state_path.read_text()), {})

    def test_enable_restores_prior_lock_and_expiry(self):
        self.state_path.write_text(json.dumps({'student1000': {'locked': True, 'expire': '21000'}}))
        self.request('enable', username='student1000')
        self.commands.assert_called_once_with(['/usr/bin/usermod', '--expiredate', '21000', '--lock', '--', 'student1000'])


class LockTests(unittest.TestCase):
    def test_concurrent_helper_rejected(self):
        actual_fstat = os.fstat
        def owned_stat(fd):
            fields = list(actual_fstat(fd))
            fields[4] = 0  # Simulate root ownership in this unprivileged test.
            return os.stat_result(fields)
        with tempfile.TemporaryDirectory() as directory, patch.object(helper, 'LOCK', Path(directory) / 'lock'), \
                patch.object(helper.os, 'fstat', side_effect=owned_stat):
            with helper.mutation_lock():
                with self.assertRaisesRegex(helper.RequestError, 'Another administrator operation'):
                    with helper.mutation_lock():
                        self.fail('Second mutation acquired the lock')


if __name__ == '__main__':
    unittest.main(verbosity=2)
