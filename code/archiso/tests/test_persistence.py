"""Persistent filesystem + simulated Linux account operations; never changes host users."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import test_admin as existing
from test_assignments import document

helper = existing.helper
REAL_PERSIST = helper.persist_accounts


def shadow(value='$6$salt$hashed', expire=''):
    return {'hash': value, 'last_change': '20740', 'min': '0', 'max': '99999',
            'warn': '7', 'inactive': '', 'expire': expire}


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        existing.StorageAndAccountTests.setUp(self)
        self.registry = self.path / 'account-state-persistent.json'
        self.deleted = self.path / 'deleted-students.json'
        self.assignments = self.path / 'assignments.json'
        self.defaults = self.path / 'defaults.json'
        self.defaults.write_text(json.dumps(self.data))
        self.registry.write_text('{"version": 1, "students": {}}')
        self.registry.chmod(0o600)
        self.assignments.write_text(json.dumps(document()))
        self.hashes = {'student1000': shadow()}
        self.groups = {1001: 'student1000'}
        for target, value in [('ROOT', self.path), ('REGISTRY', self.registry), ('ASSIGNMENTS', self.assignments),
                              ('DEFAULT_DATA', self.defaults), ('VIEWS', self.path / 'student-views'),
                              ('DELETED', self.deleted)]:
            self.stack.enter_context(patch.object(helper, target, value))
        self.stack.enter_context(patch.object(helper.labs, 'SUBMISSIONS', self.path / 'submissions'))
        self.stack.enter_context(patch.object(helper, 'shadow_record', side_effect=lambda name: copy.deepcopy(self.hashes[name])))
        self.stack.enter_context(patch.object(helper, 'shadow_status', side_effect=lambda name: {'locked': self.hashes[name]['hash'].startswith(('!', '*')), 'expire': self.hashes[name]['expire']}))
        self.persist.side_effect = REAL_PERSIST
        self.commands.side_effect = self.linux_operation
        def group(gid):
            if gid not in self.groups:
                raise KeyError(gid)
            return existing.types.SimpleNamespace(gr_name=self.groups[gid])
        self.stack.enter_context(patch.object(helper.grp, 'getgrgid', side_effect=group))
        self.stack.enter_context(patch.object(helper.os.path, 'lexists', side_effect=lambda path: False if str(path).startswith('/home/') else Path(path).exists()))

    def linux_operation(self, command, input_text=None):
        operation = Path(command[0]).name
        name = command[-1]
        if operation == 'useradd':
            uid = int(command[command.index('--uid') + 1]) if '--uid' in command else max((p.pw_uid for p in self.users.values()), default=1000) + 1
            gid = int(command[command.index('--gid') + 1]) if '--gid' in command else uid
            self.users[name] = existing.account(name, uid, gid)
            self.groups[gid] = name
            self.hashes[name] = shadow('!')
        elif operation == 'groupadd':
            self.groups[int(command[command.index('--gid') + 1])] = name
        elif operation == 'chpasswd':
            name, value = input_text.rstrip('\n').split(':', 1)
            self.hashes[name]['hash'] = value if '--encrypted' in command else '$6$salt$' + hashlib.sha256(value.encode()).hexdigest()
        elif operation == 'usermod':
            if '--lock' in command and not self.hashes[name]['hash'].startswith('!'):
                self.hashes[name]['hash'] = '!' + self.hashes[name]['hash']
            if '--unlock' in command:
                self.hashes[name]['hash'] = self.hashes[name]['hash'].lstrip('!')
            if '--expiredate' in command:
                self.hashes[name]['expire'] = command[command.index('--expiredate') + 1]
        elif operation == 'chage':
            for option, key in [('-d', 'last_change'), ('-m', 'min'), ('-M', 'max'), ('-W', 'warn'), ('-I', 'inactive'), ('-E', 'expire')]:
                if option not in command:
                    continue
                value = command[command.index(option) + 1]
                self.hashes[name][key] = '' if value == '-1' else value

    def request(self, action, **kwargs):
        return helper.dispatch({'action': action, **kwargs})

    def create(self):
        return self.request('create', revision=helper.revision(self.data_path.read_bytes()), username='student1005',
                            roll_number='1005', display_name='New Student', password='NEVER-PERSIST-THIS')

    def reboot(self):
        self.users.clear()
        self.groups.clear()
        self.hashes.clear()
        with patch.object(helper.os, 'getuid', return_value=0), patch.object(helper.os, 'geteuid', return_value=0):
            helper.restore_state()

    def test_new_student_serialized_hash_only_private_registry(self):
        self.create()
        registry = helper.load_registry()
        self.assertIn('student1005', registry['students'])
        self.assertEqual(registry['students']['student1005']['shadow'], self.hashes['student1005'])
        self.assertNotIn('NEVER-PERSIST-THIS', self.registry.read_text())
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('hash', json.dumps(self.request('status')))

    def test_create_restore_same_uid_password_metadata_and_assignments(self):
        self.create()
        record = helper.load_registry()['students']['student1005']
        self.assignments.write_text(json.dumps({'assignments': [{**document()['assignments'][0], 'assigned_to': ['student1005']}]}))
        submission = self.path / 'submissions/student1005/LAB-0001/Q1/source.cpp'
        submission.parent.mkdir(parents=True)
        submission.write_text('int main() {}')
        original_assignments = self.assignments.read_bytes()
        self.reboot()
        self.assertEqual(self.users['student1005'].pw_uid, record['uid'])
        self.assertEqual(self.hashes['student1005'], record['shadow'])
        self.assertEqual(json.loads(self.data_path.read_text())['student1005']['display_name'], 'New Student')
        self.assertEqual(self.assignments.read_bytes(), original_assignments)
        self.assertEqual(submission.read_text(), 'int main() {}')
        # A second boot remains idempotent and retains IDs/hashes.
        self.reboot()
        self.assertEqual(self.hashes['student1005'], record['shadow'])

    def test_recreate_previously_deleted_student_survives_reboot(self):
        self.deleted.write_text(json.dumps({'students': ['student1001']}))
        result = self.request('create', revision=helper.revision(self.data_path.read_bytes()),
                              username='student1001', roll_number='1001',
                              display_name='Recreated Student', password='NEVER-PERSIST-THIS')
        self.assertIn('prior deletion marker was cleared', result['message'])
        self.assertNotIn('student1001', helper.load_deleted()['students'])
        record = helper.load_registry()['students']['student1001']
        self.reboot()
        self.assertEqual(self.users['student1001'].pw_uid, record['uid'])
        self.assertEqual(self.hashes['student1001'], record['shadow'])
        self.assertEqual(json.loads(self.data_path.read_text())['student1001']['display_name'],
                         'Recreated Student')

    def test_password_reset_survives_reboot(self):
        self.request('reset_password', username='student1000', password='NEW-PASSWORD')
        hashed = self.hashes['student1000']['hash']
        self.reboot()
        self.assertEqual(self.hashes['student1000']['hash'], hashed)
        self.assertNotIn('NEW-PASSWORD', self.registry.read_text())

    def test_disable_reset_while_disabled_and_enable_survive_reboot(self):
        self.request('disable', username='student1000')
        self.request('reset_password', username='student1000', password='new-secret')
        self.reboot()
        self.assertTrue(self.hashes['student1000']['hash'].startswith('!'))
        self.assertEqual(self.hashes['student1000']['expire'], '1')
        self.request('enable', username='student1000')
        self.reboot()
        self.assertFalse(self.hashes['student1000']['hash'].startswith('!'))
        self.assertEqual(self.hashes['student1000']['expire'], '')

    def test_first_boot_seeds_and_does_not_overwrite_existing(self):
        for path in (self.registry, self.data_path, self.assignments, self.state_path):
            path.unlink()
        helper.initialize_state()
        self.assertEqual(json.loads(self.data_path.read_text()), self.data)
        self.assertEqual(json.loads(self.assignments.read_text()), {'assignments': []})
        self.assignments.write_text(json.dumps(document()))
        changed = copy.deepcopy(self.data)
        changed['student1000']['display_name'] = 'Persistent Name'
        self.data_path.write_text(json.dumps(changed))
        originals = {p: p.read_bytes() for p in (self.data_path, self.assignments, self.registry)}
        self.defaults.unlink()  # A rebuilt ISO's seed is irrelevant once state exists.
        helper.initialize_state()
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_malformed_state_not_silently_reset(self):
        self.registry.write_text('{')
        with self.assertRaises(helper.RequestError):
            helper.initialize_state()
        self.assertEqual(self.registry.read_text(), '{')

    def test_plaintext_hash_field_rejected(self):
        for value in ('plaintext-password', 'user1', '!plaintext', '', 'hash\nroot:bad'):
            registry = {'version': 1, 'students': {'student1000': {'uid': 1001, 'gid': 1001, 'shadow': shadow(value)}}}
            with self.assertRaises(helper.RequestError):
                helper.validate_registry(registry)

    def test_restore_rejects_uid_collision(self):
        self.users['unrelated'] = existing.account('unrelated', 1001)
        with self.assertRaisesRegex(helper.RequestError, 'conflicts'):
            helper.restore_account('student1000', {'uid': 1001, 'gid': 1001, 'shadow': shadow()}, self.data)
        self.commands.assert_not_called()

    def test_boot_restore_error_names_account_without_exposing_hash(self):
        REAL_PERSIST()
        with patch.object(helper, 'restore_account', side_effect=helper.RequestError('Linux operation chage failed (exit 1).')):
            with patch.object(helper.os, 'getuid', return_value=0), patch.object(helper.os, 'geteuid', return_value=0):
                with self.assertRaisesRegex(helper.RequestError, 'Could not restore student1000: Linux operation chage failed') as caught:
                    helper.restore_state()
        self.assertNotIn('$6$salt$hashed', str(caught.exception))

    def test_persistence_failure_reported_not_success(self):
        self.persist.side_effect = OSError('disk full')
        with self.assertRaisesRegex(helper.RequestError, 'PARTIAL FAILURE'):
            self.request('reset_password', username='student1000', password='SECRET')

    def test_create_does_not_publish_metadata_without_account_snapshot(self):
        self.persist.side_effect = OSError('disk full')
        with self.assertRaisesRegex(helper.RequestError, 'PARTIAL FAILURE'):
            self.create()
        self.assertIn('student1005', self.users)  # Runtime mutation is reported for repair.
        self.assertNotIn('student1005', json.loads(self.data_path.read_text()))
        self.assertNotIn('student1005', helper.load_registry()['students'])

    def test_account_creation_rejected_when_state_disk_is_not_mounted(self):
        self.persistent_state.return_value = False
        with self.assertRaisesRegex(helper.RequestError, 'EDUOS_STATE disk is not mounted'):
            self.create()
        self.assertNotIn('student1005', self.users)
        self.commands.assert_not_called()
        self.assertEqual(json.loads(self.data_path.read_text()), self.data)

    def test_hash_restore_uses_stdin_not_argv(self):
        helper.restore_account('student1000', {'uid': 1001, 'gid': 1001, 'shadow': shadow()}, self.data)
        call = next(c for c in self.commands.call_args_list if c.args[0][0].endswith('/chpasswd'))
        self.assertEqual(call.args[0], ['/usr/bin/chpasswd', '--encrypted'])
        self.assertIn('$6$salt$hashed', call.args[1])
        aging = next(c.args[0] for c in self.commands.call_args_list if c.args[0][0].endswith('/chage'))
        self.assertNotIn('-m', aging)

    def test_unsupported_minimum_age_fails_before_linux_mutation(self):
        record = {'uid': 1001, 'gid': 1001, 'shadow': shadow()}
        record['shadow']['min'] = '7'
        with self.assertRaisesRegex(helper.RequestError, 'minimum password age is unsupported'):
            helper.restore_account('student1000', record, self.data)
        self.commands.assert_not_called()

    def test_legacy_tasks_cannot_be_edited(self):
        changed = copy.deepcopy(self.data)
        changed['student1000']['tasks'] = [{'title': 'New task', 'status': 'Pending', 'due': ''}]
        with self.assertRaisesRegex(helper.RequestError, 'deprecated'):
            self.request('save', revision=helper.revision(self.data_path.read_bytes()), data=changed)


class HostScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        for name in ('create-eduos-state-disk.sh', 'run-eduos.sh'):
            shutil.copyfile(Path('scripts') / name, self.root / 'scripts' / name)

    def test_create_sparse_ext4_label_and_never_reformat(self):
        script = self.root / 'scripts/create-eduos-state-disk.sh'
        subprocess.run(['bash', str(script)], check=True, capture_output=True)
        image = self.root / 'runtime/eduos-state.img'
        self.assertEqual(image.stat().st_size, 4 * 1024 ** 3)
        self.assertLess(image.stat().st_blocks * 512, image.stat().st_size // 2)
        label = subprocess.check_output(['blkid', '-p', '-s', 'LABEL', '-o', 'value', str(image)], text=True).strip()
        self.assertEqual(label, 'EDUOS_STATE')
        before = image.stat()
        subprocess.run(['bash', str(script)], check=True, capture_output=True)
        self.assertEqual(image.stat().st_ino, before.st_ino)
        self.assertEqual(image.stat().st_mtime_ns, before.st_mtime_ns)

    def test_existing_image_even_invalid_is_untouched(self):
        (self.root / 'runtime').mkdir()
        image = self.root / 'runtime/eduos-state.img'
        image.write_text('Do not format')
        subprocess.run(['bash', str(self.root / 'scripts/create-eduos-state-disk.sh')], check=True, capture_output=True)
        self.assertEqual(image.read_text(), 'Do not format')

    def test_run_requires_state_and_reuses_it_with_selected_iso(self):
        script = self.root / 'scripts/run-eduos.sh'
        self.assertNotEqual(subprocess.run(['bash', str(script)], capture_output=True).returncode, 0)
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/eduos-state.img').touch()
        (self.root / 'out-admin').mkdir()
        iso = self.root / 'out-admin/current.iso'
        iso.touch()
        fake = self.root / 'scripts/qemu-system-x86_64'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        fake.chmod(0o755)
        result = subprocess.check_output(['bash', str(script)], env={**os.environ, 'PATH': str(fake.parent) + ':' + os.environ['PATH']}, text=True)
        self.assertIn('if=virtio', result)
        self.assertIn(str(self.root / 'runtime/eduos-state.img'), result)
        self.assertIn(str(iso), result)
        self.assertIn('-enable-kvm\n-m\n4096\n-smp\n4', result)

    def test_boot_order_and_no_automatic_format(self):
        root = existing.PROFILE / 'airootfs'
        setup = (root / 'etc/systemd/system/eduos-setup.service').read_text()
        display = (root / 'etc/systemd/system/display-manager.service.d/eduos.conf').read_text()
        mount = (root / 'usr/local/bin/eduos-state-mount').read_text()
        self.assertIn('Requires=eduos-state.service', setup)
        self.assertIn('After=eduos-setup.service', display)
        self.assertIn('/dev/disk/by-label/EDUOS_STATE', mount)
        self.assertNotIn('mkfs', mount)
        self.assertIn('EPHEMERAL', mount)


if __name__ == '__main__':
    unittest.main(verbosity=2)
