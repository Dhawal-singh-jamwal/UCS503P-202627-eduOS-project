"""Local machine management tests; pacman and systemctl are always mocked."""

import contextlib
import copy
import ast
import hashlib
import io
import inspect
import json
from pathlib import Path
import stat
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

import test_admin as existing


helper = existing.helper
machine = helper.machine
PROFILE = existing.PROFILE / 'airootfs/usr/share/eduos/profiles/CSE-LAB-CPP-v1.json'
ARCHIVE = existing.PROFILE / 'airootfs/usr/share/eduos/packages/pv-1.12.7-1-x86_64.pkg.tar.zst'


def process(code=0, output=''):
    return types.SimpleNamespace(returncode=code, stdout=output)


class MachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = self.root / 'profiles'
        self.profiles.mkdir()
        self.profile = json.loads(PROFILE.read_text())
        (self.profiles / 'CSE-LAB-CPP-v1.json').write_text(json.dumps(self.profile))
        self.registration = self.root / 'machine.json'
        self.status = self.root / 'status.json'
        self.fleet_file = self.root / 'fleet.json'
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for key, path in [('ROOT', self.root), ('PROFILES', self.profiles),
                          ('MACHINE', self.registration), ('STATUS', self.status),
                          ('FLEET', self.fleet_file)]:
            self.stack.enter_context(patch.object(machine, key, path))
        self.stack.enter_context(patch.object(machine, 'secure_directory'))
        # The fixture directory is owned by the host user, not guest root.
        original = machine.read_document
        self.stack.enter_context(patch.object(machine, 'read_document',
                            side_effect=lambda path, validator: validator(machine.decode(Path(path).read_bytes()))))
        self.stack.enter_context(patch.object(machine.os, 'fchown'))
        self.stack.enter_context(patch.object(machine, 'require_root'))

    def register(self):
        return machine.register()

    def test_valid_profile_and_version(self):
        self.assertEqual(machine.load_profile('CSE-LAB-CPP-v1'), self.profile)
        self.assertEqual(machine.available_profiles()[0]['version'], 1)

    def test_malformed_and_unknown_profile_rejected(self):
        path = self.profiles / 'CSE-LAB-CPP-v1.json'
        for bad in ('{', '{"profile_id":"a","profile_id":"b"}',
                    json.dumps({**self.profile, 'arbitrary_command': 'sh'}),
                    json.dumps({**self.profile, 'required_packages': ['-evil']}),
                    json.dumps({**self.profile, 'required_services': ['../../evil.service']})):
            with self.subTest(bad=bad):
                path.write_text(bad)
                with self.assertRaises(machine.MachineError):
                    machine.load_profile('CSE-LAB-CPP-v1')

    def test_missing_profile_and_path_traversal(self):
        for ident in ('MISSING-v1', '../CSE-LAB-CPP-v1', 'CSE-LAB-CPP-v1/../x'):
            with self.subTest(ident=ident), self.assertRaises(machine.MachineError):
                machine.load_profile(ident)

    def test_registration_is_valid_persistent_and_idempotent(self):
        first = self.register()
        self.assertRegex(first['machine_id'], r'^LAB-PC-[A-F0-9]{16}$')
        raw = self.registration.read_bytes()
        with patch.object(machine.socket, 'gethostname', return_value='different-host'):
            self.assertEqual(machine.register(), first)
        self.assertEqual(self.registration.read_bytes(), raw)
        self.assertEqual(machine.load_machine(), first)

    def test_fresh_state_disks_receive_distinct_ids(self):
        with patch.object(machine.secrets, 'token_hex', side_effect=['1' * 16, '2' * 16]):
            first = self.register()['machine_id']
            self.registration.unlink()
            second = self.register()['machine_id']
        self.assertEqual((first, second), ('LAB-PC-' + '1' * 16, 'LAB-PC-' + '2' * 16))

    def test_offline_fleet_snapshot_import_and_removal(self):
        self.register()
        with patch.object(machine, 'inspect_package', return_value='present'):
            snapshot = machine.snapshot()
        remote = copy.deepcopy(snapshot)
        remote['machine']['machine_id'] = 'LAB-PC-ABCDEF1234567890'
        remote['status']['machine_id'] = remote['machine']['machine_id']
        records = machine.import_snapshot(remote)
        self.assertIn(remote['machine']['machine_id'], records)
        self.assertEqual(machine.fleet(), records)
        self.assertEqual(self.fleet_file.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(machine.MachineError):
            machine.import_snapshot(snapshot)
        mismatch = copy.deepcopy(remote)
        mismatch['status']['machine_id'] = 'LAB-PC-0000000000000000'
        with self.assertRaises(machine.MachineError):
            machine.import_snapshot(mismatch)
        self.assertEqual(machine.remove_snapshot(remote['machine']['machine_id']), {})
        self.assertEqual(machine.fleet(), {})

    def test_invalid_machine_id_and_document(self):
        self.register()
        for ident in ('../LAB-PC-001', 'LAB-PC-1;sh', 'student1000'):
            with self.subTest(ident=ident), self.assertRaises(machine.MachineError):
                machine.validate_machine({**machine.load_machine(), 'machine_id': ident})

    def test_valid_and_invalid_assignment(self):
        self.register()
        second = {**self.profile, 'profile_id': 'CSE-LAB-CPP-v2', 'version': 2}
        (self.profiles / 'CSE-LAB-CPP-v2.json').write_text(json.dumps(second))
        self.assertEqual(machine.assign_profile('CSE-LAB-CPP-v2')['assigned_profile'], 'CSE-LAB-CPP-v2')
        for ident in ('MISSING-v1', '../bad', 'CSE-LAB-CPP-v3'):
            with self.subTest(ident=ident), self.assertRaises(machine.MachineError):
                machine.assign_profile(ident)
        self.assertEqual(machine.load_machine()['assigned_profile'], 'CSE-LAB-CPP-v2')

    def test_package_present_missing_and_invalid_name(self):
        with patch.object(machine, 'run', return_value=process(0, 'pv\n')) as run:
            self.assertEqual(machine.inspect_package('pv'), 'present')
            self.assertEqual(run.call_args.args[0], ['/usr/bin/pacman', '-Qq', '--', 'pv'])
        with patch.object(machine, 'run', return_value=process(1)):
            self.assertEqual(machine.inspect_package('pv'), 'missing')
        for name in ('--overwrite', 'foo;sh', '../foo', '$(id)'):
            with self.subTest(name=name), self.assertRaises(machine.MachineError):
                machine.inspect_package(name)

    def test_bundled_demo_package_integrity(self):
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), machine.BUNDLED_PV_SHA256)
        package = self.root / ARCHIVE.name
        package.write_bytes(b'expected package')
        trusted = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0,
                                        st_size=package.stat().st_size)
        with patch.object(machine, 'BUNDLED_PV', package), \
             patch.object(machine, 'BUNDLED_PV_SHA256', hashlib.sha256(package.read_bytes()).hexdigest()), \
             patch.object(machine.os, 'fstat', return_value=trusted):
            self.assertEqual(machine.bundled_pv(), package)
            package.write_bytes(b'changed package')
            with self.assertRaisesRegex(machine.MachineError, 'integrity'):
                machine.bundled_pv()
            package.unlink()
            package.symlink_to(ARCHIVE)
            with self.assertRaisesRegex(machine.MachineError, 'unavailable'):
                machine.bundled_pv()

    def test_service_state_detection(self):
        with patch.object(machine, 'run', side_effect=[process(0, 'enabled\n'), process(0, 'active\n')]):
            self.assertEqual(machine.inspect_service('sshd.service'),
                             {'name': 'sshd.service', 'enabled': True, 'active': True})
        with patch.object(machine, 'run', side_effect=[process(1, 'disabled\n'), process(3, 'inactive\n')]):
            self.assertEqual(machine.inspect_service('sshd.service')['active'], False)
        with self.assertRaises(machine.MachineError):
            machine.inspect_service('../../sshd.service')

    def test_compliant_and_drift_status(self):
        self.register()
        with patch.object(machine, 'inspect_package', return_value='present'):
            compliant = machine.check()
        self.assertTrue(compliant['compliant'])
        self.assertEqual(compliant['issues'], [])
        with patch.object(machine, 'inspect_package', side_effect=lambda name: 'missing' if name == 'pv' else 'present'):
            drift = machine.check()
        self.assertFalse(drift['compliant'])
        self.assertEqual(drift['packages'][-1], {'name': 'pv', 'state': 'missing'})

    def test_service_drift_is_reported(self):
        self.register()
        self.profile['required_services'] = ['sshd.service']
        (self.profiles / 'CSE-LAB-CPP-v1.json').write_text(json.dumps(self.profile))
        with patch.object(machine, 'inspect_package', return_value='present'), \
             patch.object(machine, 'inspect_service', return_value={'name': 'sshd.service', 'enabled': False, 'active': True}):
            result = machine.check()
        self.assertFalse(result['compliant'])
        self.assertIn('sshd.service', result['issues'][0])

    def test_status_atomic_write_and_cache_assignment(self):
        self.register()
        with patch.object(machine, 'inspect_package', return_value='present'):
            result = machine.check(save=True)
        self.assertEqual(json.loads(self.status.read_text()), result)
        self.assertEqual(machine.cached_status(), result)
        with patch.object(machine.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                machine.atomic_json(self.status, {**result, 'compliant': False}, machine.validate_status)
        self.assertEqual(json.loads(self.status.read_text()), result)
        self.assertEqual(list(self.root.glob('.status.json-*')), [])
        second = {**self.profile, 'profile_id': 'CSE-LAB-CPP-v2', 'version': 2}
        (self.profiles / 'CSE-LAB-CPP-v2.json').write_text(json.dumps(second))
        machine.assign_profile('CSE-LAB-CPP-v2')
        self.assertIsNone(machine.cached_status())

    def test_sync_only_uses_profile_requirements(self):
        self.register()
        calls = []
        def fake(command, timeout=20):
            calls.append(command)
            if command[1] == '-Qq':
                return process(1 if command[-1] == 'pv' and len([x for x in calls if x[1] == '-U']) == 0 else 0,
                               command[-1] + '\n')
            return process()
        with patch.object(machine, 'run', side_effect=fake), \
             patch.object(machine, 'bundled_pv', return_value=machine.BUNDLED_PV):
            result = machine.sync()
        self.assertTrue(result['compliant'])
        self.assertEqual([c for c in calls if c[1] == '-U'],
                         [['/usr/bin/pacman', '-U', '--noconfirm', '--', str(machine.BUNDLED_PV)]])
        self.assertFalse(any(c[1] == '-S' for c in calls))
        self.assertFalse(any(c[1] in ('-R', '-Rns') for c in calls))

    def test_other_missing_packages_still_use_repositories(self):
        self.register()
        self.profile['required_packages'] = ['gcc']
        (self.profiles / 'CSE-LAB-CPP-v1.json').write_text(json.dumps(self.profile))
        calls = []
        def fake(command, timeout=20):
            calls.append(command)
            if command[1] == '-Qq':
                return process(1 if not any(c[1] == '-S' for c in calls) else 0, 'gcc\n')
            return process()
        with patch.object(machine, 'run', side_effect=fake):
            self.assertTrue(machine.sync()['compliant'])
        self.assertIn(['/usr/bin/pacman', '-S', '--needed', '--noconfirm', '--', 'gcc'], calls)

    def test_sync_restores_required_service(self):
        self.register()
        self.profile['required_services'] = ['sshd.service']
        (self.profiles / 'CSE-LAB-CPP-v1.json').write_text(json.dumps(self.profile))
        calls = []
        def fake(command, timeout=20):
            calls.append(command)
            if command[1] == '-Qq':
                return process(0, command[-1] + '\n')
            if command[1] == 'is-enabled':
                return process(0, 'enabled\n') if any(c[1] == 'enable' for c in calls) else process(1, 'disabled\n')
            if command[1] == 'is-active':
                return process(0, 'active\n') if any(c[1] == 'start' for c in calls) else process(3, 'inactive\n')
            return process()
        with patch.object(machine, 'run', side_effect=fake):
            result = machine.sync()
        self.assertTrue(result['compliant'])
        self.assertIn(['/usr/bin/systemctl', 'enable', '--', 'sshd.service'], calls)
        self.assertIn(['/usr/bin/systemctl', 'start', '--', 'sshd.service'], calls)

    def test_sync_requires_root_and_no_arbitrary_commands(self):
        self.register()
        with patch.object(machine, 'require_root', side_effect=machine.MachineError('root required')):
            with self.assertRaises(machine.MachineError):
                machine.sync()
        for request in ({'action': 'machine_sync', 'package': 'evil'},
                        {'action': 'machine_demo_drift', 'package': 'gcc'},
                        {'action': 'machine_sync', 'command': 'sh'},
                        {'action': 'machine_assign', 'profile_id': '../../evil'},
                        {'action': 'pacman', 'package': 'evil'}):
            with self.subTest(request=request), self.assertRaises(helper.RequestError):
                helper.dispatch(request)

    def test_demo_drift_removes_only_fixed_package(self):
        self.register()
        with patch.object(machine, 'inspect_package', return_value='present'), \
             patch.object(machine, 'bundled_pv', return_value=machine.BUNDLED_PV), \
             patch.object(machine, 'run', return_value=process()) as run:
            self.assertIn('pv', machine.demo_drift()['message'])
        run.assert_called_once_with(['/usr/bin/pacman', '-R', '--noconfirm', '--', 'pv'], timeout=300)
        with patch.object(machine, 'inspect_package', return_value='missing'), \
             patch.object(machine, 'run') as run:
            machine.demo_drift()
        run.assert_not_called()
        with patch.object(machine, 'inspect_package', return_value='present'), \
             patch.object(machine, 'bundled_pv', side_effect=machine.MachineError('archive absent')), \
             patch.object(machine, 'run') as run, \
             self.assertRaisesRegex(machine.MachineError, 'archive absent'):
            machine.demo_drift()
        run.assert_not_called()

    def test_helper_machine_action_and_student_authorization(self):
        self.register()
        with patch.object(helper, 'mutation_lock', side_effect=contextlib.nullcontext), \
             patch.object(machine, 'inspect_package', return_value='present'):
            result = helper.dispatch({'action': 'machine_check'})
        self.assertTrue(result['status']['compliant'])
        self.assertRegex(result['machine']['machine_id'], r'^LAB-PC-[A-F0-9]{16}$')
        with patch.object(helper.os, 'getuid', return_value=0), \
             patch.object(helper.os, 'geteuid', return_value=0), \
             patch.object(helper.pwd, 'getpwuid', return_value=existing.account('student1000')), \
             patch.dict(helper.os.environ, {'SUDO_UID': '1001', 'SUDO_USER': 'student1000'}):
            with self.assertRaises(helper.RequestError):
                helper.authorize(['eduos-admin-helper'])

    def test_admin_refresh_registers_missing_machine(self):
        self.assertFalse(self.registration.exists())
        with patch.object(helper, 'mutation_lock', side_effect=contextlib.nullcontext), \
             patch.object(machine, 'inspect_package', return_value='present'):
            response = helper.dispatch({'action': 'machine_check'})
        self.assertTrue(self.registration.exists())
        self.assertRegex(response['machine']['machine_id'], r'^LAB-PC-[A-F0-9]{16}$')

    def test_boot_registration_precedes_account_restore(self):
        setup = (existing.BIN / 'eduos-setup').read_text()
        self.assertLess(setup.index('/usr/local/bin/eduos-agent register'),
                        setup.index('/usr/local/bin/eduos-state-restore'))

    def test_dashboard_constructs_visible_machines_tab(self):
        source = textwrap.dedent(inspect.getsource(existing.app.Dashboard.__init__))
        body = ast.parse(source).body[0].body
        tabs = [node for node in body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'add'
                and any(isinstance(word, ast.Constant) and word.value == 'Machines'
                        for word in (keyword.value for keyword in node.value.keywords))]
        self.assertEqual(len(tabs), 1)  # Direct constructor call, not gated on registration.
        self.assertIn('machine-management-v2',
                      (existing.PROFILE / 'airootfs/usr/share/eduos/build-info.txt').read_text())

    def test_machine_panel_shows_registration_error(self):
        labels = {}
        def variable(name):
            return types.SimpleNamespace(set=lambda value: labels.__setitem__(name, value))
        panel = types.SimpleNamespace(machine={'old': True}, identity=variable('identity'),
                                      compliance=variable('compliance'), checked=variable('checked'),
                                      requirements=types.SimpleNamespace(get_children=lambda: ('row',),
                                                                         delete=lambda *_: None))
        existing.app.MachinePanel.failed(panel, 'Machine is not registered.')
        self.assertIn('Machine is not registered.', labels['identity'])
        self.assertEqual(labels['compliance'], 'Status: UNAVAILABLE')

    def test_student_request_never_reaches_sync(self):
        with patch.object(helper, 'authorize', side_effect=helper.RequestError('Access denied.')), \
             patch.object(machine, 'sync') as sync, \
             patch.object(helper.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(b'{"action":"machine_sync"}'))), \
             patch.object(helper.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(helper.main(), 1)
        sync.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
