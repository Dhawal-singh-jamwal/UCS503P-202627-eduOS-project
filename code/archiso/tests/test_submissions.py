"""Evaluator submissions in a temporary persistent-state tree."""
import contextlib
import copy
import datetime
import json
import os
from pathlib import Path
import queue
import shutil
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import test_admin as existing
from test_assignments import document, problem
from test_evaluator import CASES, SUM
import eduos_submissions as submissions

labs = existing.helper.labs
REAL_EVALUATE = submissions.evaluator.evaluate_cpp


def result(statuses=('PASS', 'PASS'), compiled=True):
    return {'compile_success': compiled, 'compile_error': '' if compiled else 'syntax error', 'error': '',
            'tests': [dict(passed=s == 'PASS', status=s, input=c['input'], expected=c['expected_output'],
                           actual='5\n', error='') for s, c in zip(statuses, CASES)] if compiled else []}


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.homes = self.path / 'home'
        self.homes.mkdir(mode=0o755)
        self.home = self.homes / 'student1000'
        self.home.mkdir(mode=0o700)
        self.state_submissions = self.path / 'submissions'
        self.state_submissions.mkdir(mode=0o755)
        (self.state_submissions / 'student1000').mkdir(mode=0o700)
        self.entry = types.SimpleNamespace(pw_name='student1000', pw_uid=os.getuid(), pw_dir=str(self.home))
        self.problem = {**problem(), 'test_cases': copy.deepcopy(CASES)}
        self.store = document()
        self.store['assignments'][0]['problems'][0] = self.problem
        self.assignments = self.path / 'assignments.json'
        self.assignments.write_text(json.dumps(self.store))
        (self.path / 'student-data.json').write_text(json.dumps({'student1000': {'roll_number': '1000'}}))
        self.destination = self.state_submissions / 'student1000/LAB-0001-Q1.json'
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(submissions, 'HOME_ROOT', self.homes))
        self.stack.enter_context(patch.object(labs, 'SUBMISSIONS', self.state_submissions))
        self.stack.enter_context(patch.object(labs, 'STATE_ROOT', self.path))
        self.stack.enter_context(patch.object(labs, 'identity', return_value=self.entry))
        self.selected = self.stack.enter_context(patch.object(labs, 'selected_problem', side_effect=lambda *args: copy.deepcopy(self.problem)))
        self.evaluate = self.stack.enter_context(patch.object(submissions.evaluator, 'evaluate_cpp', return_value=result()))
        real_fstat = os.fstat
        root_inode = self.state_submissions.stat().st_ino
        def fstat(fd):
            info = real_fstat(fd)
            if info.st_ino == root_inode:
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info
        self.stack.enter_context(patch.object(submissions.os, 'fstat', side_effect=fstat))
        self.stack.enter_context(patch.object(existing.helper, 'ASSIGNMENTS', self.assignments))
        self.stack.enter_context(patch.object(existing.helper, 'read_bytes', side_effect=lambda p: p.read_bytes()))
        self.stack.enter_context(patch.object(existing.helper, 'mutation_lock', side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(existing.helper, 'student_account', return_value=self.entry))

    def submit(self, source=SUM):
        return submissions.submit('LAB-0001', 'Q1', source)

    def read(self):
        return submissions.read_own('LAB-0001', 'Q1')

    def test_success_file_timestamp_identity_source_permissions(self):
        source = SUM + '\n// preserve trailing whitespace  \n'
        record = self.submit(source)
        self.assertEqual(json.loads(self.destination.read_text()), record)
        self.assertEqual(record['source_code'], source)
        self.assertEqual(record['evaluation']['passed'], 2)
        self.assertEqual(record['task']['task_id'], 'LAB-0001-Q1')
        self.assertEqual(record['student'], {'username': 'student1000', 'roll_number': '1000'})
        self.assertIsNotNone(datetime.datetime.fromisoformat(record['submitted_at']).utcoffset())
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.destination.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.read()['record'], record)
        self.evaluate.assert_called_once_with(source, CASES)

    def test_partial_wrong_runtime_and_compile_error_allowed(self):
        for value in (result(('PASS', 'FAIL')), result(('FAIL', 'FAIL')),
                      result(('RUNTIME ERROR', 'TIME LIMIT EXCEEDED')), result(compiled=False)):
            self.evaluate.return_value = value
            record = self.submit()
            self.assertEqual(self.read()['status'], 'Submitted')
            self.assertEqual(record['evaluation']['passed'], sum(t['passed'] for t in value['tests']))
            self.assertEqual(record['evaluation']['compile_error'], value['compile_error'])

    def test_latest_replaces_after_title_change_with_fresh_evaluation(self):
        first = self.submit()
        self.problem['title'] = 'Renamed'
        self.evaluate.return_value = result(('FAIL', 'FAIL'))
        second = self.submit('int main(){}')
        self.assertEqual(self.read()['record'], second)
        self.assertEqual(first['task']['task_id'], second['task']['task_id'])
        self.assertEqual(second['task']['title'], 'Renamed')
        self.assertNotEqual(first['submitted_at'], second['submitted_at'])
        self.assertEqual(list(self.destination.parent.glob('*.json')), [self.destination])
        self.assertEqual(self.evaluate.call_count, 2)

    def test_malformed_and_traversal_ids_rejected(self):
        for ident, question in ((None, 'Q1'), ('../LAB-0001', 'Q1'), ('LAB-0001', '../../secret'),
                                ('LAB-0', 'Q1'), ('LAB-0001', 'Q0'), ('LAB-0001/x', 'Q1')):
            with self.assertRaises(ValueError):
                submissions.submit(ident, question, SUM)
            with self.assertRaises(ValueError):
                submissions.read(self.entry, ident, question)
        self.evaluate.assert_not_called()

    def test_cannot_target_other_student_or_home_environment(self):
        for extra in ({'username': 'student1001'}, {'path': '/tmp/other'}, {'entry': self.entry}):
            with self.assertRaises(TypeError):
                submissions.submit('LAB-0001', 'Q1', SUM, **extra)
        with patch.dict(os.environ, {'HOME': '/home/student1001', 'USER': 'student1001', 'XDG_DATA_HOME': '/tmp/other'}):
            self.submit()
        self.assertTrue(self.destination.exists())
        self.entry.pw_dir = str(self.homes / 'student1001')
        with self.assertRaises(ValueError):
            self.submit()

    def test_atomic_replace_failure_preserves_previous_and_removes_temp(self):
        self.submit()
        original = self.destination.read_bytes()
        with patch.object(labs.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.submit('int main(){}')
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(list(self.destination.parent.glob('.pending-*')), [])

    def test_fsync_failure_before_replace_preserves_previous(self):
        self.submit()
        original = self.destination.read_bytes()
        with patch.object(labs.os, 'fsync', side_effect=OSError('fsync error')):
            with self.assertRaises(OSError):
                self.submit('int main(){}')
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertEqual(list(self.destination.parent.glob('.pending-*')), [])

    def test_missing_submission_does_not_create_directories(self):
        self.assertEqual(self.read(), {'status': 'Pending'})
        self.assertFalse((self.home / '.local').exists())

    def test_problem_changed_or_unassigned_during_evaluation(self):
        self.selected.side_effect = [copy.deepcopy(self.problem), {**self.problem, 'title': 'changed'}]
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.submit()
        self.assertFalse(self.destination.exists())
        self.selected.side_effect = ValueError('No longer assigned')
        with self.assertRaises(ValueError):
            self.submit()

    def test_unavailable_evaluation_does_not_replace_submission(self):
        self.submit()
        original = self.destination.read_bytes()
        self.evaluate.return_value = {'error': 'compiler missing'}
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.submit()
        self.assertEqual(self.destination.read_bytes(), original)

    def test_helper_reads_and_lists_summary_without_full_source(self):
        record = self.submit()
        response = existing.helper.dispatch({'action': 'get_submission', 'username': 'student1000',
                                             'id': 'LAB-0001', 'problem_id': 'Q1'})
        self.assertEqual(response['record'], record)
        listing = existing.helper.dispatch({'action': 'list_student_submissions', 'username': 'student1000'})
        item = listing['assignments'][0]
        self.assertEqual(item['submitted'], 1)
        self.assertEqual(item['problems'][0]['result'], '2 / 2 tests passed')
        self.assertNotIn('source_code', json.dumps(listing))
        self.assertNotIn('record', item['problems'][0])

    def test_helper_rejects_arbitrary_path_username_and_task(self):
        base = {'action': 'get_submission', 'username': 'student1000', 'id': 'LAB-0001', 'problem_id': 'Q1'}
        for request in ({**base, 'path': '/etc/shadow'}, {**base, 'username': 'root'},
                        {**base, 'username': 'student1000/../eduos'}, {**base, 'id': '../x'},
                        {**base, 'problem_id': '../../shadow'}, {**base, 'problem_id': 'Q99'},
                        {'action': 'get-file', 'path': '/etc/shadow'}):
            with self.assertRaises(existing.helper.RequestError):
                existing.helper.dispatch(request)

    def test_corrupt_mismatched_and_oversized_records_rejected(self):
        record = self.submit()
        variants = [None, {**record, 'schema_version': 2}, {**record, 'source_code': 1},
                    {**record, 'student': {'username': 'student1001', 'roll_number': '1000'}},
                    {**record, 'task': {**record['task'], 'task_id': '../x'}},
                    {**record, 'evaluation': {**record['evaluation'], 'passed': 0}},
                    {**record, 'submitted_at': 'no date'}]
        for value in variants:
            self.destination.write_text(json.dumps(value))
            self.assertIn('warning', self.read())
        self.destination.write_text('x' * (submissions.MAX_BYTES + 1))
        self.assertIn('warning', self.read())

    def test_reader_rejects_symlink_hardlink_fifo_and_public_file(self):
        self.submit()
        outside = self.path / 'outside'
        outside.write_bytes(self.destination.read_bytes())
        outside.chmod(0o600)
        self.destination.unlink()
        self.destination.symlink_to(outside)
        self.assertIn('warning', self.read())
        self.destination.unlink()
        os.link(outside, self.destination)
        self.assertIn('warning', self.read())
        self.destination.unlink()
        os.mkfifo(self.destination, 0o600)
        self.assertIn('warning', self.read())
        self.destination.unlink()
        self.submit()
        self.destination.chmod(0o644)
        self.assertIn('warning', self.read())

    def test_foreign_file_owner_rejected(self):
        self.submit()
        original = os.fstat
        inode = self.destination.stat().st_ino
        def foreign(fd):
            info = original(fd)
            if info.st_ino == inode:
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info
        with patch.object(submissions.os, 'fstat', side_effect=foreign):
            self.assertIn('warning', self.read())

    def test_admin_viewer_displays_record_and_is_read_only(self):
        record = self.submit()
        app = existing.app
        dashboard = object.__new__(app.Dashboard)
        dashboard.busy = False
        dashboard.selected = 'student1000'
        dashboard.root = MagicMock()
        dashboard.student_labs = MagicMock()
        dashboard.student_labs.selection.return_value = ['LAB-0001']
        dashboard.student_lab_records = {'LAB-0001': {'id': 'LAB-0001', 'title': 'Lab', 'problems': []}}
        dashboard.request = lambda payload, callback: callback(self.read())
        with patch.object(app, 'tk', MagicMock()), patch.object(app, 'ttk', MagicMock()) as ttk, patch.object(app, 'ScrolledText', MagicMock()) as text:
            ttk.Treeview.return_value.selection.return_value = ['Q1']
            dashboard.view_submissions()
            button = next(c for c in ttk.Button.call_args_list if c.kwargs.get('text') == 'View Code')
            button.kwargs['command']()
            content = text.return_value.insert.call_args.args[1]
            for expected in (record['submitted_at'], '2 / 2 tests passed', 'Test Case 1: PASS', 'Roll: 1000', SUM):
                self.assertIn(expected, content)
            text.return_value.configure.assert_called_with(state='disabled')

    def test_directory_symlink_and_writable_parent_rejected(self):
        outside = self.path / 'outside'
        outside.mkdir()
        student_dir = self.state_submissions / 'student1000'
        student_dir.rmdir()
        student_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.submit()
        self.assertEqual(list(outside.iterdir()), [])
        student_dir.unlink()
        student_dir.mkdir(mode=0o700)
        student_dir.chmod(0o777)
        with self.assertRaises(ValueError):
            self.submit()

    def test_old_problem_needs_no_new_id_or_migration(self):
        old = problem()
        self.assertNotIn('task_id', old)
        self.assertEqual(labs.validate_problem(old), old)
        self.assertEqual(submissions.task_id('LAB-0001', old['id']), 'LAB-0001-Q1')
        self.assertFalse(labs.evaluator_enabled(old))

    def workspace(self):
        app = existing.student_app
        workspace = object.__new__(app.EvaluatorWorkspace)
        workspace.running = False
        workspace.key = ('LAB-0001', 'Q1')
        workspace.drafts = {}
        workspace.problem = self.problem
        workspace.results = queue.Queue()
        workspace.code = MagicMock(get=lambda *args: SUM)
        workspace.run_button, workspace.submit_button = MagicMock(), MagicMock()
        workspace.submission_status = MagicMock()
        workspace.display, workspace.after = MagicMock(), MagicMock()
        return workspace

    def test_run_tests_never_submits(self):
        workspace = self.workspace()
        with patch.object(existing.student_app.threading, 'Thread') as thread, patch.object(submissions, 'submit') as save:
            workspace.run_tests()
            thread.call_args.kwargs['target']()
            workspace.poll()
            save.assert_not_called()
        self.assertFalse(self.destination.exists())
        workspace.submission_status.set.assert_not_called()

    def test_submit_ui_uses_exact_snapshot_and_restores_buttons(self):
        workspace = self.workspace()
        with patch.object(existing.student_app.threading, 'Thread') as thread:
            workspace.run_tests(submit=True)
            workspace.run_button.configure.assert_called_with(state='disabled', text='Running...')
            workspace.submit_button.configure.assert_called_with(state='disabled', text='Submitting...')
            workspace.code.get = lambda *args: 'edited after click'
            thread.call_args.kwargs['target']()
            workspace.poll()
        self.assertEqual(self.read()['code'], SUM)
        self.assertIn('Submission successful', workspace.submission_status.set.call_args.args[0])
        workspace.submit_button.configure.assert_called_with(state='normal', text='Submit Code')
        workspace.run_button.configure.assert_called_with(state='normal', text='Run Tests')

    def test_persistence_failure_never_claims_success(self):
        workspace = self.workspace()
        with patch.object(existing.student_app.threading, 'Thread') as thread, patch.object(labs.os, 'replace', side_effect=OSError('disk full')):
            workspace.run_tests(submit=True)
            thread.call_args.kwargs['target']()
            workspace.poll()
        self.assertIn('Submission failed', workspace.submission_status.set.call_args.args[0])
        self.assertFalse(self.destination.exists())

    @unittest.skipUnless(shutil.which('g++') and os.getuid() != 0, 'Needs ordinary user and g++')
    def test_real_compiler_submission(self):
        self.evaluate.side_effect = REAL_EVALUATE
        for source, passed, compiled in ((SUM, 2, True), (SUM.replace('a+b', '(a==10?29:a+b)'), 1, True), ('int main( {', 0, False)):
            record = self.submit(source)
            self.assertEqual(record['evaluation']['passed'], passed)
            self.assertEqual(record['evaluation']['compile_success'], compiled)
            self.assertEqual(self.read()['code'], source)


if __name__ == '__main__':
    unittest.main()
