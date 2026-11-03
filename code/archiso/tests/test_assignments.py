"""Assignment/problem/projection/submission tests; no real account operations."""
import contextlib
import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
import test_admin as existing

helper = existing.helper
labs = helper.labs


def problem(ident='Q1', language='C++'):
    return {'id': ident, 'title': 'Rotate', 'problem_statement': 'Given an array,\nrotate it.', 'language': language}


def fields():
    return {'title': 'Arrays Lab', 'description': 'Practice arrays', 'due': 'Tomorrow',
            'assigned_to': ['student1000', 'student1002'], 'problems': [problem(), problem('Q2')]}


def document():
    return {'assignments': [{**fields(), 'id': 'LAB-0001', 'next_problem_id': 3,
                             'created_by': 'eduos', 'created_at': '2026-10-13T18:00:00+00:00'}]}


class ParsingTests(unittest.TestCase):
    def test_multiline_multiple_problems_and_empty_store(self):
        self.assertEqual(labs.validate_store(labs.decode(json.dumps(document()))), document())
        self.assertEqual(labs.validate_store({'assignments': []}), {'assignments': []})

    def test_malformed_json(self):
        for raw in ('{', '', '{"assignments": [], "assignments": []}', '{"assignments": NaN}', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                labs.validate_store(labs.decode(raw))

    def test_invalid_fields_and_no_ready_lab_without_problems(self):
        for key, value in [('title', ''), ('problems', []), ('problems', [problem(), problem()]),
                           ('assigned_to', []), ('assigned_to', ['root']), ('assigned_to', ['student１２３４'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                labs.validate_fields({**fields(), key: value})
        with self.assertRaises(ValueError):
            labs.validate_problem({**problem(), 'problem_statement': '\n '})

    def test_invalid_ids_and_duplicate_assignments(self):
        for ident in ('../LAB-0001', 'LAB-0', 'LAB-0000', None):
            with self.assertRaises(ValueError):
                labs.assignment_id(ident)
        for ident in ('Q0', '../Q1', 'Q1/x', 'Q１', None):
            with self.assertRaises(ValueError):
                labs.problem_id(ident)
        with self.assertRaises(ValueError):
            labs.validate_store({'assignments': document()['assignments'] * 2})

    def test_filtering(self):
        self.assertEqual(len(labs.for_student(document(), 'student1000')), 1)
        self.assertEqual(labs.for_student(document(), 'student1001'), [])

    def test_progress_zero_partial_complete(self):
        for statuses, expected in [(['Pending'] * 2, ('Not Started', 0)),
                                   (['Submitted', 'Pending'], ('In Progress', 1)),
                                   (['Submitted'] * 2, ('Submitted', 2))]:
            result = labs.progress(statuses)
            self.assertEqual((result['status'], result['submitted']), expected)
            self.assertEqual(result['total'], 2)


class HelperAssignmentTests(unittest.TestCase):
    def setUp(self):
        existing.StorageAndAccountTests.setUp(self)
        self.assignment_path = self.path / 'assignments.json'
        self.assignment_path.write_text('{"assignments": []}\n')
        self.stack.enter_context(patch.object(helper, 'ASSIGNMENTS', self.assignment_path))
        self.users['student1002'] = existing.account('student1002', 1003)

    def request(self, action, **kwargs):
        if action not in ('assignment_status', 'list_student_submissions', 'get_submission') and 'revision' not in kwargs:
            kwargs['revision'] = helper.revision(self.assignment_path.read_bytes())
        return helper.dispatch({'action': action, **kwargs})

    def create(self):
        return self.request('create_assignment', assignment=fields())

    def test_create_unique_ids_private_store_and_projection_regeneration(self):
        self.assertEqual(self.create()['id'], 'LAB-0001')
        self.assertEqual(self.create()['id'], 'LAB-0002')
        self.assertEqual(self.assignment_path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.views.call_args_list[0].kwargs['empty'])
        self.assertEqual(self.views.call_count, 4)
        self.assertEqual(json.loads(self.data_path.read_text()), self.data)

    def test_edit_assignment_and_add_edit_delete_problem_preserve_ids(self):
        self.create()
        values = fields()
        values['title'] = 'Changed'
        values['problems'].append(problem('Q3', 'Python'))
        self.request('update_assignment', id='LAB-0001', assignment=values)
        values['problems'][0]['problem_statement'] = 'Changed\nstatement'
        values['assigned_to'] = ['student1002']
        self.request('update_assignment', id='LAB-0001', assignment=values)
        values['problems'].pop()
        self.request('update_assignment', id='LAB-0001', assignment=values)
        item = json.loads(self.assignment_path.read_text())['assignments'][0]
        self.assertEqual(item['id'], 'LAB-0001')
        self.assertEqual(item['problems'][0]['id'], 'Q1')
        self.assertEqual(item['next_problem_id'], 4)
        self.assertEqual(labs.for_student({'assignments': [item]}, 'student1000'), [])
        values['problems'].append(problem('Q3'))
        with self.assertRaisesRegex(helper.RequestError, 'cannot be reused'):
            self.request('update_assignment', id='LAB-0001', assignment=values)

    def test_delete_never_reuses_lab_id(self):
        self.create()
        self.request('delete_assignment', id='LAB-0001')
        self.assertEqual(json.loads(self.assignment_path.read_text())['assignments'], [])
        self.assertEqual(self.create()['id'], 'LAB-0002')

    def test_stale_create_cannot_duplicate(self):
        revision = helper.revision(self.assignment_path.read_bytes())
        self.create()
        with self.assertRaises(helper.RequestError):
            self.request('create_assignment', revision=revision, assignment=fields())

    def test_invalid_nonexistent_students_and_empty_problem(self):
        for names in (['root'], ['student9999']):
            with self.assertRaises(helper.RequestError):
                self.request('create_assignment', assignment={**fields(), 'assigned_to': names})
        with self.assertRaises(helper.RequestError):
            self.request('create_assignment', assignment={**fields(), 'problems': []})

    def test_malformed_store_not_reset(self):
        self.assignment_path.write_text('{')
        with self.assertRaises(helper.RequestError):
            self.create()
        self.assertEqual(self.assignment_path.read_text(), '{')

    def test_atomic_failure_preserves_store(self):
        self.create()
        original = self.assignment_path.read_bytes()
        for operation in ('replace', 'fsync'):
            with patch.object(helper.os, operation, side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    self.create()
            self.assertEqual(self.assignment_path.read_bytes(), original)
            self.assertEqual(list(self.path.glob('.assignments.json-*')), [])

    def test_revocation_failure_prevents_commit(self):
        self.create()
        original = self.assignment_path.read_bytes()
        self.views.side_effect = OSError('full disk')
        with self.assertRaises(OSError):
            self.request('delete_assignment', id='LAB-0001')
        self.assertEqual(self.assignment_path.read_bytes(), original)

    def test_helper_rejects_paths_and_ids(self):
        for request in ({'action': 'get_submission', 'username': 'root', 'id': 'LAB-0001', 'problem_id': 'Q1'},
                        {'action': 'get_submission', 'username': 'student1000', 'id': '../x', 'problem_id': 'Q1'},
                        {'action': 'get_submission', 'username': 'student1000', 'id': 'LAB-0001', 'problem_id': '../x'},
                        {'action': 'get_submission', 'username': 'student1000', 'id': 'LAB-0001', 'problem_id': 'Q1', 'path': '/etc/shadow'},
                        {'action': 'read_file', 'path': '/etc/shadow'}):
            with self.subTest(request=request), self.assertRaises(helper.RequestError):
                helper.dispatch(request)

    def test_admin_progress_pending_and_view_code(self):
        self.create()
        def read(_entry, _lab, question):
            return {'status': 'Submitted', 'submitted_at': '2026-10-13T18:00:00', 'code': 'int main() {}'} if question == 'Q1' else {'status': 'Pending'}
        with patch.object(labs, 'read_submission', side_effect=read):
            response = self.request('list_student_submissions', username='student1000')['assignments'][0]
            self.assertEqual(response['status'], 'In Progress')
            self.assertEqual(response['submitted'], 1)
            self.assertNotIn('code', response['problems'][0])
            self.assertEqual(response['problems'][1]['status'], 'Pending')
            self.assertEqual(self.request('get_submission', username='student1000', id='LAB-0001', problem_id='Q1')['code'], 'int main() {}')
            with self.assertRaises(helper.RequestError):
                self.request('get_submission', username='student1002', id='LAB-9999', problem_id='Q1')
        self.commands.assert_not_called()


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.views = self.root / 'student-views'
        self.submissions = self.root / 'submissions'
        self.views.mkdir()
        self.submissions.mkdir()
        self.entry = types.SimpleNamespace(pw_name='student1000', pw_uid=os.getuid(), pw_gid=os.getgid())
        for parent in (self.views, self.submissions):
            (parent / self.entry.pw_name).mkdir(mode=0o700)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(labs, 'VIEWS', self.views))
        self.stack.enter_context(patch.object(labs, 'SUBMISSIONS', self.submissions))
        self.stack.enter_context(patch.object(helper, 'VIEWS', self.views))
        self.stack.enter_context(patch.object(labs.pwd, 'getpwuid', return_value=self.entry))
        self.stack.enter_context(patch.object(helper, 'secure_directory'))
        self.stack.enter_context(patch.object(helper, 'managed_accounts', return_value=[self.entry]))
        self.stack.enter_context(patch.object(helper.os, 'fchown'))
        real_fstat = os.fstat
        inodes = {self.views.stat().st_ino, self.submissions.stat().st_ino}
        def fstat(fd):
            info = real_fstat(fd)
            if info.st_ino in inodes:
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info
        self.stack.enter_context(patch.object(labs.os, 'fstat', side_effect=fstat))
        helper.regenerate_views(document())
        self.destination = self.submissions / 'student1000/LAB-0001/Q1'

    def test_projection_has_only_assigned_work_and_private_permissions(self):
        other = copy.deepcopy(document()['assignments'][0])
        other.update(id='LAB-0002', assigned_to=['student1001'])
        store = {'assignments': [*document()['assignments'], other]}
        helper.regenerate_views(store)
        projection = labs.load_store()
        self.assertEqual([a['id'] for a in projection['assignments']], ['LAB-0001'])
        path = self.views / 'student1000/assignments.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_uid, os.getuid())

    def test_projection_regeneration_on_edit_and_unassignment(self):
        store = document()
        store['assignments'][0]['title'] = 'Changed'
        helper.regenerate_views(store)
        self.assertEqual(labs.load_store()['assignments'][0]['title'], 'Changed')
        store['assignments'][0]['assigned_to'] = ['student1002']
        helper.regenerate_views(store)
        self.assertEqual(labs.load_store()['assignments'], [])
        with self.assertRaises(ValueError):
            labs.submit('LAB-0001', 'Q1', 'code')

    def test_typed_code_metadata_and_resubmission(self):
        first = labs.submit('LAB-0001', 'Q1', 'original')
        second = labs.submit('LAB-0001', 'Q1', 'new')
        self.assertEqual((self.destination / 'source.cpp').read_text(), 'new')
        self.assertNotEqual(first['submitted_at'], second['submitted_at'])
        self.assertEqual(second['problem_id'], 'Q1')
        self.assertEqual(second['username'], 'student1000')
        self.assertNotIn('filename', second)
        self.assertEqual(json.loads((self.destination / 'submission.json').read_text()), second)
        self.assertEqual(labs.submission_status('LAB-0001', 'Q1'), 'Submitted')
        self.assertEqual((self.destination / 'source.cpp').stat().st_mode & 0o777, 0o600)

    def test_fixed_language_mapping(self):
        for language, filename in labs.LANGUAGES.items():
            store = document()
            store['assignments'][0]['problems'][0]['language'] = language
            helper.regenerate_views(store)
            labs.submit('LAB-0001', 'Q1', 'code')
            self.assertEqual((self.destination / filename).read_text(), 'code')
        self.assertEqual(labs.LANGUAGES['Java'], 'Main.java')

    def test_no_filename_or_username_parameters_environment_ignored(self):
        with patch.dict(os.environ, {'USER': 'student1001', 'HOME': '/home/student1001'}):
            labs.submit('LAB-0001', 'Q1', 'code')
        self.assertTrue(self.destination.exists())
        for kwargs in ({'filename': 'evil'}, {'username': 'student1001'}, {'home': '/tmp'}):
            with self.assertRaises(TypeError):
                labs.submit('LAB-0001', 'Q1', 'code', **kwargs)

    def test_empty_code_invalid_paths_and_nonstudent(self):
        for ident, question, code in [('LAB-0001', 'Q1', ''), ('LAB-0001', 'Q1', ' \n'), ('../x', 'Q1', 'code'), ('LAB-0001', '../Q1', 'code')]:
            with self.assertRaises(ValueError):
                labs.submit(ident, question, code)
        self.entry.pw_name = 'eduos'
        with self.assertRaises(ValueError):
            labs.submit('LAB-0001', 'Q1', 'code')

    def test_symlink_directory_rejected_for_student_and_root_reader(self):
        lab = self.destination.parent
        lab.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            labs.submit('LAB-0001', 'Q1', 'code')
        self.assertEqual(labs.read_submission(self.entry, 'LAB-0001', 'Q1')['status'], 'Pending')
        self.assertFalse((self.root / 'Q1').exists())

    def test_root_reader_rejects_file_symlink_and_hardlink(self):
        labs.submit('LAB-0001', 'Q1', 'code')
        path = self.destination / 'source.cpp'
        outside = self.root / 'secret'
        outside.write_text('code')
        outside.chmod(0o600)
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(labs.read_submission(self.entry, 'LAB-0001', 'Q1')['status'], 'Pending')
        path.unlink()
        os.link(outside, path)
        self.assertEqual(labs.read_submission(self.entry, 'LAB-0001', 'Q1')['status'], 'Pending')

    def test_cross_student_ownership_and_wide_permissions_rejected(self):
        (self.submissions / 'student1001').mkdir(mode=0o700)
        other = types.SimpleNamespace(pw_name='student1001', pw_uid=os.getuid() + 1, pw_gid=os.getgid() + 1)
        with self.assertRaises(ValueError):
            with labs.private_directory(self.submissions, other, ()):
                self.fail('Foreign owner was accepted')
        (self.views / 'student1000').chmod(0o755)
        with self.assertRaises(ValueError):
            labs.load_store()

    def test_progress_and_pending_tampered_metadata(self):
        assignment = document()['assignments'][0]
        self.assertEqual(labs.assignment_progress(assignment)['status'], 'Not Started')
        labs.submit('LAB-0001', 'Q1', 'code')
        self.assertEqual(labs.assignment_progress(assignment)['status'], 'In Progress')
        labs.submit('LAB-0001', 'Q2', 'code')
        self.assertEqual(labs.assignment_progress(assignment)['status'], 'Submitted')
        (self.destination / 'submission.json').write_text('{')
        self.assertEqual(labs.submission_status('LAB-0001', 'Q1'), 'Pending')

    def test_failed_replace_preserves_code(self):
        labs.submit('LAB-0001', 'Q1', 'original')
        with patch.object(labs.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                labs.submit('LAB-0001', 'Q1', 'new')
        self.assertEqual((self.destination / 'source.cpp').read_text(), 'original')
        self.assertEqual(list(self.destination.glob('.pending-*')), [])

    def test_unassign_and_delete_retain_submissions(self):
        labs.submit('LAB-0001', 'Q1', 'code')
        helper.regenerate_views({'assignments': []})
        self.assertEqual((self.destination / 'source.cpp').read_text(), 'code')
        with self.assertRaises(ValueError):
            labs.submit('LAB-0001', 'Q1', 'new')


class DashboardTests(unittest.TestCase):
    def test_admin_fullscreen_and_escape_without_exit(self):
        app = existing.app
        root = MagicMock()
        state = {'fullscreen': False}
        def attributes(name, *value):
            if value:
                state['fullscreen'] = value[0]
            return state['fullscreen']
        root.attributes.side_effect = attributes
        with patch.object(app, 'tk', MagicMock()), patch.object(app, 'ttk', MagicMock()) as ttk, patch.object(app, 'ScrolledText', MagicMock()), patch.object(app.Dashboard, 'refresh'):
            app.Dashboard(root)
            self.assertTrue(state['fullscreen'])
            callbacks = {c.args[0]: c.args[1] for c in root.bind.call_args_list}
            callbacks['<F11>'](None)
            self.assertFalse(state['fullscreen'])
            callbacks['<F11>'](None)
            callbacks['<Escape>'](None)
            self.assertFalse(state['fullscreen'])
            root.destroy.assert_not_called()
            self.assertFalse(any('Task' in c.kwargs.get('text', '') for c in ttk.Label.call_args_list))

    def test_student_fullscreen_no_tasks_no_file_controls(self):
        app = existing.student_app
        with patch.object(app, 'tk', MagicMock()) as tk, patch.object(app, 'ttk', MagicMock()) as ttk, patch.object(labs, 'load_store', return_value=document()), patch.object(labs, 'assignment_progress', return_value=labs.progress(['Pending'] * 2)):
            app.build_window('student1000', {'roll_number': '1000', 'display_name': 'Student', 'tasks': [{'title': 'legacy'}]})
            tk.Tk.return_value.attributes.assert_called_with('-fullscreen', True)
            texts = [c.kwargs.get('text', '') for c in ttk.Label.call_args_list + ttk.Button.call_args_list]
            self.assertIn('Open Lab', texts)
            self.assertNotIn('Tasks', texts)
        source = (existing.BIN / 'eduos-student-app').read_text()
        for forbidden in ('Create Submission File', 'Save & Submit', 'filedialog', 'self.filename'):
            self.assertNotIn(forbidden, source)

    def test_student_nonstudent_rejected_before_tk(self):
        app = existing.student_app
        with patch.object(app.pwd, 'getpwuid', return_value=existing.account('eduos')), patch.object(app, 'tk', MagicMock()) as tk:
            self.assertEqual(app.main(), 0)
            tk.Tk.assert_not_called()

    def test_admin_code_view_is_read_only(self):
        app = existing.app
        dashboard = object.__new__(app.Dashboard)
        dashboard.root = MagicMock()
        dashboard.busy = False
        dashboard.selected = 'student1000'
        dashboard.student_labs = MagicMock()
        dashboard.student_labs.selection.return_value = ['LAB-0001']
        dashboard.student_lab_records = {'LAB-0001': {'id': 'LAB-0001', 'title': 'Lab', 'problems': [{'id': 'Q1', 'title': 'Rotate', 'status': 'Submitted'}]}}
        dashboard.request = lambda _payload, callback: callback({'status': 'Submitted', 'submitted_at': 'now', 'code': 'code'})
        with patch.object(app, 'tk', MagicMock()), patch.object(app, 'ttk', MagicMock()) as ttk, patch.object(app, 'ScrolledText', MagicMock()) as text:
            ttk.Treeview.return_value.selection.return_value = ['Q1']
            dashboard.view_submissions()
            button = next(c for c in ttk.Button.call_args_list if c.kwargs.get('text') == 'View Code')
            button.kwargs['command']()
            text.return_value.configure.assert_called_with(state='disabled')


if __name__ == '__main__':
    unittest.main(verbosity=2)
