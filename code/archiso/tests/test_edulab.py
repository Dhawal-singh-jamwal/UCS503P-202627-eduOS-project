"""Host-side EduLab tests. No privileged commands or C++ programs are run."""
import copy
import datetime as dt
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

PROFILE = Path(__file__).resolve().parents[1] / 'profile/airootfs'
sys.path.insert(0, str(PROFILE / 'usr/share/eduos'))
import eduos_edulab as lab
import test_admin as existing
import eduos_edulab_ui as student_ui
import eduos_edulab_admin as admin_ui

loader = importlib.machinery.SourceFileLoader('eduos_edulab_service_test', str(PROFILE / 'usr/local/bin/eduos-edulab-service'))
service = importlib.util.module_from_spec(importlib.util.spec_from_loader('eduos_edulab_service_test', loader))
loader.exec_module(service)


def assessment():
    return {'assessment_id': 'EXAM-0001', 'title': 'C++ Lab Assessment 1',
            'instructions': 'Answer both questions.', 'scheduled_start': '2026-11-05T10:00:00+00:00',
            'duration_minutes': 60, 'state': 'draft',
            'questions': [{'question_id': 'q1', 'title': 'Sum', 'problem_statement': 'Add two integers.',
                           'language': 'cpp', 'max_score': 10, 'example_input': '2 3\n',
                           'hidden_test_cases': [{'input': '2 3\n', 'expected_output': '5\n'},
                                                 {'input': '10 20\n', 'expected_output': '30\n'}]}]}


class EditorVar:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def trace_add(self, *_args):
        pass


class EditorText:
    def __init__(self, *_args, **_kwargs):
        self.value = ''

    def insert(self, _index, value):
        self.value = value

    def get(self, *_args):
        return self.value

    def delete(self, *_args):
        self.value = ''

    def configure(self, **_kwargs):
        pass

    def see(self, *_args):
        pass

    def pack(self, **_kwargs):
        pass


class EditorTree:
    def __init__(self):
        self.rows = {}
        self.selected = ()

    def insert(self, _parent, _position, iid, text):
        self.rows[iid] = text

    def selection_set(self, iid):
        self.selected = (iid,)

    def selection(self):
        return self.selected

    def exists(self, iid):
        return iid in self.rows

    def item(self, iid, **kwargs):
        self.rows[iid] = kwargs['text']

    def delete(self, iid):
        del self.rows[iid]


class EditorNotebook:
    def __init__(self):
        self.pages = []
        self.current = None

    def add(self, page, **_kwargs):
        self.pages.append(page)

    def select(self, page):
        self.current = page

    def forget(self, page):
        self.pages.remove(page)

    def tab(self, *_args, **_kwargs):
        pass


class Storage:
    def __init__(self):
        self.data = {'next_id': 1, 'assessments': []}
        self.records = {}

    def read(self, path, default):
        return copy.deepcopy(self.data if path == lab.ASSESSMENTS else self.records)

    def atomic(self, path, value):
        if path == lab.ASSESSMENTS:
            self.data = copy.deepcopy(value)
        else:
            self.records = copy.deepcopy(value)


class EduLabTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage()
        self.patches = [patch.object(lab, 'read', self.storage.read),
                        patch.object(lab, 'atomic', self.storage.atomic),
                        patch.object(lab, 'locked', lambda: __import__('contextlib').nullcontext()),
                        patch.object(lab, 'student_name', return_value='student1000'),
                        patch.object(lab.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=990, pw_gid=990))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def create(self):
        data = assessment()
        data.pop('assessment_id')
        return lab.admin('create', {'assessment': data})['assessment_id']

    def active(self):
        self.create()
        lab.admin('activate', {'assessment_id': 'EXAM-0001'})
        self.clock = patch.object(lab, 'now', return_value=dt.datetime(2026, 11, 5, 10, 5, tzinfo=dt.timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_create_edit_stable_id_and_hidden_storage(self):
        self.assertEqual(self.create(), 'EXAM-0001')
        item = self.storage.data['assessments'][0]
        self.assertEqual(item['questions'][0]['hidden_test_cases'][0]['expected_output'], '5\n')
        replacement = copy.deepcopy(item)
        replacement.pop('assessment_id')
        replacement['title'] = 'Edited'
        lab.admin('edit', {'assessment_id': 'EXAM-0001', 'assessment': replacement})
        self.assertEqual(self.storage.data['assessments'][0]['title'], 'Edited')
        self.assertEqual(self.create(), 'EXAM-0002')

    def test_public_example_output_validation_and_legacy_compatibility(self):
        item = assessment()
        lab.validate_assessment(item)  # Existing input-only assessments remain valid.
        question = item['questions'][0]
        question['example_output'] = '5\n'
        lab.validate_assessment(item)
        visible = lab.public(item)['questions'][0]
        self.assertEqual(visible['example_output'], '5\n')
        self.assertNotIn('hidden_test_cases', visible)
        for invalid in (None, 5, 'bad\0output', 'é' * 8193):
            question['example_output'] = invalid
            with self.assertRaises(lab.LabError):
                lab.validate_assessment(item)
        question['example_output'] = ''
        lab.validate_assessment(item)

    def test_validation_rejects_ids_duration_score_and_paths(self):
        for key, bad in [('assessment_id', '../etc/passwd'), ('duration_minutes', 0)]:
            item = assessment()
            item[key] = bad
            with self.assertRaises(lab.LabError):
                lab.validate_assessment(item)
        item = assessment()
        item['questions'][0]['question_id'] = '../q1'
        with self.assertRaises(lab.LabError):
            lab.validate_assessment(item)
        item['questions'][0]['question_id'] = 'q1'
        item['questions'][0]['max_score'] = 0
        with self.assertRaises(lab.LabError):
            lab.validate_assessment(item)

    def test_public_view_has_no_hidden_cases(self):
        self.active()
        response = lab.student(1000, 'list', {})
        self.assertEqual(len(response['assessments']), 1)
        self.assertNotIn('hidden_test_cases', json.dumps(response))
        self.assertNotIn('expected_output', json.dumps(response))
        self.assertEqual(response['assessments'][0]['questions'][0]['example_input'], '2 3\n')

    def test_attempt_timer_draft_and_reopen(self):
        self.active()
        started = lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})['attempt']
        self.assertEqual(started['deadline'], '2026-11-05T11:00:00+00:00')
        lab.student(1000, 'save', {'assessment_id': 'EXAM-0001', 'question_id': 'q1', 'source': 'int main(){}'})
        again = lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})['attempt']
        self.assertEqual(again['started_at'], started['started_at'])
        self.assertEqual(again['drafts']['q1'], 'int main(){}')
        with self.assertRaises(lab.LabError):
            lab.student(1000, 'save', {'assessment_id': 'EXAM-0001', 'question_id': '../q1', 'source': 'x'})

    def test_timeout_and_submitted_cannot_edit(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        with patch.object(lab, 'now', return_value=dt.datetime(2026, 11, 5, 11, 1, tzinfo=dt.timezone.utc)):
            result = lab.student(1000, 'state', {'assessment_id': 'EXAM-0001'})['attempt']
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['reason'], 'timeout')
        with self.assertRaises(lab.LabError):
            lab.student(1000, 'save', {'assessment_id': 'EXAM-0001', 'question_id': 'q1', 'source': 'x'})

    def test_violations_and_admin_reset(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        lab.student(1000, 'violation', {'assessment_id': 'EXAM-0001', 'type': 'FULLSCREEN_EXIT'})
        self.assertEqual(len(self.storage.records['EXAM-0001:student1000']['violations']), 1)
        for event in ('COPY_ATTEMPT', 'PASTE_ATTEMPT', 'CUT_ATTEMPT', 'FOCUS_LOST', 'CLOSE_ATTEMPT'):
            with self.assertRaises(lab.LabError):
                lab.student(1000, 'violation', {'assessment_id': 'EXAM-0001', 'type': event})
        lab.admin('reset', {'assessment_id': 'EXAM-0001', 'student': 'student1000'})
        self.assertEqual(self.storage.records, {})
        for bad in ('root', 'eduos', '../student1000'):
            with self.assertRaises(lab.LabError):
                lab.admin('reset', {'assessment_id': 'EXAM-0001', 'student': bad})

    def test_instructor_review_is_bounded_visible_and_reset_with_attempt(self):
        self.create()
        key = 'EXAM-0001:student1000'
        self.storage.records[key] = {'assessment_id': 'EXAM-0001', 'student': 'student1000',
                                     'status': 'submitted', 'total_score': 5.0,
                                     'submitted_at': '2026-11-05T10:10:00+00:00'}
        request = {'assessment_id': 'EXAM-0001', 'student': 'student1000',
                   'feedback': 'Check edge cases.', 'final_score': 7.5}
        lab.admin('review', request)
        record = self.storage.records[key]
        self.assertEqual(record['final_score'], 7.5)
        self.assertEqual(record['feedback'], 'Check edge cases.')
        self.assertEqual(lab.public_attempt(record)['feedback'], 'Check edge cases.')
        for value in (-1, 11, 1.234, True):
            with self.assertRaises(lab.LabError):
                lab.admin('review', {**request, 'final_score': value})
        with self.assertRaises(lab.LabError):
            lab.admin('review', {**request, 'feedback': 'x' * 4097})
        lab.admin('reset', {'assessment_id': 'EXAM-0001', 'student': 'student1000'})
        self.assertNotIn(key, self.storage.records)

    def test_csv_export_omits_hidden_cases_and_escapes_formula(self):
        import csv
        import io
        item = assessment()
        records = {'EXAM-0001:student1000': {'assessment_id': 'EXAM-0001',
            'student': 'student1000', 'status': 'submitted', 'total_score': 5,
            'final_score': 7, 'feedback': '=1+1', 'submitted_at': '2026-11-05T10:10:00+00:00'}}
        report = admin_ui.csv_report(item, records)
        row = list(csv.DictReader(io.StringIO(report)))[0]
        self.assertEqual(row['Final Score'], '7')
        self.assertEqual(row['Feedback'], "'=1+1")
        self.assertNotIn('expected_output', report)

    def test_scoring_and_nonroot_runner(self):
        question = assessment()['questions'][0]
        def fake_run(argv, **kwargs):
            self.assertEqual(argv, ['/usr/bin/setpriv', '--reuid', '990', '--regid', '990',
                                    '--clear-groups', '--', '/usr/local/bin/eduos-evaluator'])
            self.assertEqual(kwargs['env']['PATH'], '/usr/bin:/bin')
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({'compile_success': True,
                'compile_error': '', 'tests': [{'passed': True, 'status': 'PASS', 'actual': '5'},
                                              {'passed': False, 'status': 'FAIL', 'actual': '29'}],
                'error': ''}))
        with patch.object(lab.subprocess, 'run', side_effect=fake_run):
            score = lab.evaluate(question, 'int main(){}')
        self.assertEqual(score['score_awarded'], 5.0)
        self.assertEqual(score['tests_passed'], 1)
        self.assertEqual(score['output'][0]['output'], '5')
        self.assertNotIn('expected_output', json.dumps(score))

    def test_real_evaluator_compiles_and_runs_educational_answer(self):
        if not shutil.which('g++'):
            self.skipTest('g++ unavailable on host')
        source = '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}'
        original_run = subprocess.run
        module_path = str(PROFILE / 'usr/share/eduos')
        def run_evaluator(argv, **kwargs):
            self.assertEqual(argv[:2], ['/usr/bin/setpriv', '--reuid'])
            command = [sys.executable, '-B', '-c',
                       f'import sys; sys.path.insert(0, {module_path!r}); '
                       'from eduos_evaluator import main; raise SystemExit(main())']
            return original_run(command, **kwargs)
        with patch.object(lab.subprocess, 'run', side_effect=run_evaluator):
            score = lab.evaluate(assessment()['questions'][0], source)
        self.assertEqual(score['score_awarded'], 10.0)
        self.assertEqual(score['status'], 'Passed')
        self.assertEqual([case['output'] for case in score['output']], ['5', '30'])

    def test_real_public_example_compiles_and_returns_output(self):
        if not shutil.which('g++'):
            self.skipTest('g++ unavailable on host')
        source = '#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}'
        from eduos_evaluator import run_example_cpp
        outcome = run_example_cpp(source, '2 3\n')
        self.assertEqual(outcome['status'], 'FINISHED')
        self.assertEqual(outcome['output'], '5')
        self.assertNotIn('expected', json.dumps(outcome))

    def test_student_example_run_uses_admin_input_not_hidden_tests(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        example = {'compile_success': True, 'compile_error': '', 'output': '5',
                   'runtime_error': '', 'status': 'FINISHED'}
        with patch.object(lab, 'evaluator_request', return_value=example) as evaluator:
            response = lab.student(1000, 'run_example', {'assessment_id': 'EXAM-0001',
                'question_id': 'q1', 'source': 'int main(){}'})
        self.assertEqual(response['example']['output'], '5')
        payload = evaluator.call_args.args[0]
        self.assertEqual(payload['example_input'], '2 3\n')
        self.assertNotIn('test_cases', payload)

    def test_student_workspace_starts_with_template_and_shows_own_output(self):
        workspace = object.__new__(student_ui.EduLabWorkspace)
        workspace.assessment = lab.public(assessment())
        workspace.attempt = {'drafts': {}, 'violations': [], 'status': 'in_progress'}
        workspace.index = 0
        workspace.example_outputs = {}
        workspace.heading = EditorVar()
        workspace.violations = EditorVar()
        workspace.statement = EditorText()
        workspace.code = EditorText()
        workspace.output = EditorText()
        workspace.show_question()
        self.assertEqual(workspace.code.get(), student_ui.STARTER_CPP)
        self.assertIn('Example Input:\n2 3', workspace.output.get())
        self.assertNotIn('hidden_test_cases', workspace.output.get())
        workspace.save = MagicMock()
        workspace.update_idletasks = MagicMock()
        example = {'compile_success': True, 'compile_error': '', 'output': 'my answer\n',
                   'runtime_error': '', 'status': 'FINISHED'}
        with patch.object(student_ui, 'request', return_value={'example': example}) as request:
            workspace.run_example()
        self.assertIn('Your Output:\nmy answer', workspace.output.get())
        self.assertIn('Expected Output:', workspace.output.get())
        self.assertEqual(request.call_args.kwargs['source'], student_ui.STARTER_CPP)
        workspace.attempt['drafts']['q1'] = 'my saved code'
        workspace.show_question()
        self.assertEqual(workspace.code.get(), 'my saved code')
        self.assertIn('Your Output:\nmy answer', workspace.output.get())

    def test_student_submission_opens_own_result(self):
        workspace = object.__new__(student_ui.EduLabWorkspace)
        workspace.assessment = {'assessment_id': 'EXAM-0001'}
        workspace.parent = MagicMock()
        workspace.parent_escape = ''
        workspace.parent_f11 = ''
        workspace.save = MagicMock()
        workspace.refreshed = MagicMock()
        workspace.show_result = MagicMock()
        workspace.unbind_all = MagicMock()
        workspace.grab_release = MagicMock()
        workspace.destroy = MagicMock()
        response = {'assessment': assessment(), 'attempt': {'status': 'submitted',
                    'drafts': {'q1': 'answer'}, 'scores': {}, 'total_score': 0}}
        with patch.object(student_ui, 'request', return_value=response), \
             patch.object(student_ui, 'restore_lockdown'), \
             patch.object(student_ui.messagebox, 'showinfo', create=True):
            workspace.finish('manual')
        workspace.save.assert_called_once()
        workspace.refreshed.assert_called_once()
        workspace.show_result.assert_called_once_with(response)

    def test_student_can_fetch_only_own_closed_result(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        with self.assertRaisesRegex(lab.LabError, 'after submission'):
            lab.student(1000, 'result', {'assessment_id': 'EXAM-0001'})
        self.storage.records['EXAM-0001:student1000'].update(
            status='submitted', scores={}, total_score=0)
        response = lab.student(1000, 'result', {'assessment_id': 'EXAM-0001'})
        self.assertEqual(response['attempt']['student'], 'student1000')
        self.storage.records['EXAM-0001:student1001'] = {
            'assessment_id': 'EXAM-0001', 'student': 'student1001', 'status': 'submitted'}
        self.assertNotEqual(response['attempt']['student'], 'student1001')

    def test_compile_error_is_preserved_only_for_admin(self):
        question = assessment()['questions'][0]
        outcome = {'compile_success': False, 'compile_error': 'error: expected ;', 'tests': [], 'error': ''}
        with patch.object(lab.subprocess, 'run', return_value=types.SimpleNamespace(
                returncode=0, stdout=json.dumps(outcome))):
            score = lab.evaluate(question, 'int main( {')
        self.assertEqual(score['status'], 'Compile Error')
        self.assertIn('expected', score['compile_error'])
        record = {'scores': {'q1': score}, 'drafts': {'q1': 'int main( {'}}
        own_result = lab.public_attempt(record)
        self.assertIn('compile_error', json.dumps(own_result))
        self.assertIn('output', own_result['scores']['q1'])
        self.assertNotIn('expected_output', json.dumps(own_result))

    def test_root_evaluator_identity_is_rejected(self):
        with patch.object(lab.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=0, pw_gid=0)), \
                patch.object(lab.subprocess, 'run') as runner:
            with self.assertRaises(lab.LabError):
                lab.evaluate(assessment()['questions'][0], 'int main(){}')
            runner.assert_not_called()

    def test_delete_completed_assessment_and_results_but_not_active_attempt(self):
        self.create()
        lab.admin('activate', {'assessment_id': 'EXAM-0001'})
        self.storage.records['EXAM-0001:student1000'] = {
            'status': 'in_progress', 'deadline': '2099-01-01T00:00:00+00:00'}
        with self.assertRaisesRegex(lab.LabError, 'active attempt'):
            lab.admin('delete', {'assessment_id': 'EXAM-0001'})
        self.storage.records['EXAM-0001:student1000'] = {'status': 'submitted'}
        self.storage.records['EXAM-9999:student1001'] = {'status': 'submitted'}
        lab.admin('deactivate', {'assessment_id': 'EXAM-0001'})
        lab.admin('delete', {'assessment_id': 'EXAM-0001'})
        self.assertEqual(self.storage.data['assessments'], [])
        self.assertNotIn('EXAM-0001:student1000', self.storage.records)
        self.assertIn('EXAM-9999:student1001', self.storage.records)

    def test_delete_expired_attempt_and_preserve_other_assessment(self):
        self.create()
        self.create()
        self.storage.records['EXAM-0001:student1000'] = {
            'status': 'in_progress', 'deadline': '2020-01-01T00:00:00+00:00'}
        lab.admin('delete', {'assessment_id': 'EXAM-0001'})
        self.assertEqual([item['assessment_id'] for item in self.storage.data['assessments']], ['EXAM-0002'])
        self.assertEqual(self.storage.records, {})

    def test_editor_can_add_and_remove_questions_without_reusing_ids(self):
        editor = self.mock_editor()
        editor.populate_questions([])
        self.assertEqual([entry[0] for entry in editor.questions], ['q1'])
        self.assertEqual(editor.question_list.selection(), ('q1',))
        for _ in range(2):
            editor.add_question()
        self.assertEqual([entry[0] for entry in editor.questions], ['q1', 'q2', 'q3'])
        self.assertEqual(editor.question_list.selection(), ('q3',))
        editor.question_list.selection_set('q2')
        editor.select_question()
        self.assertIs(editor.notebook.current, editor.questions[1][1])
        editor.remove_question()
        self.assertEqual([entry[0] for entry in editor.questions], ['q1', 'q3'])
        editor.add_question()
        self.assertEqual([entry[0] for entry in editor.questions], ['q1', 'q3', 'q4'])

    def mock_editor(self):
        editor = object.__new__(admin_ui.AssessmentEditor)
        editor.notebook = EditorNotebook()
        editor.question_list = EditorTree()
        editor.questions = []
        editor.case_frames = {}
        editor.next_question_id = 1
        editor.title_var = EditorVar('Assessment')
        editor.start_var = EditorVar('2026-11-05T10:00:00+00:00')
        editor.duration_var = EditorVar('60')
        editor.instructions = EditorText()
        editor.instructions.insert('1.0', 'Solve the problems.')
        editor.destroy = MagicMock()
        self.enterContext(patch.object(admin_ui, 'ttk', MagicMock()))
        mocked_tk = self.enterContext(patch.object(admin_ui, 'tk', MagicMock()))
        mocked_tk.StringVar.side_effect = lambda value='': EditorVar(value)
        self.enterContext(patch.object(admin_ui, 'ScrolledText', side_effect=EditorText))
        self.enterContext(patch.object(admin_ui.ui, 'code_widget', side_effect=lambda widget: widget))
        self.errors = self.enterContext(patch.object(admin_ui.messagebox, 'showerror', create=True))
        return editor

    def test_editor_preserves_fields_on_selection_and_saves_all_question_data(self):
        editor = self.mock_editor()
        editor.add_question()
        first = editor.questions[0]
        first[2].set('Sum')
        first[3].set('15')
        first[4].insert('1.0', 'Add two numbers')
        first[5].insert('1.0', '2 3\n')
        first[7].insert('1.0', '5\n')
        first[6][0][1].insert('1.0', '2 3\n')
        first[6][0][2].insert('1.0', '5\n')
        editor.add_hidden_test('q1', {'input': '9 1\n', 'expected_output': '10\n'})
        editor.add_question()
        second = editor.questions[1]
        second[2].set('Product')
        second[4].insert('1.0', 'Multiply two numbers')
        editor.question_list.selection_set('q1')
        editor.select_question()
        self.assertEqual(first[4].get(), 'Add two numbers')
        self.assertEqual(first[5].get(), '2 3\n')
        self.assertIs(editor.notebook.current, first[1])
        editor.save()
        self.errors.assert_not_called()
        self.assertEqual(editor.result['questions'][0]['max_score'], 15)
        self.assertEqual(editor.result['questions'][0]['hidden_test_cases'][2],
                         {'input': '9 1\n', 'expected_output': '10\n'})
        self.assertEqual(editor.result['questions'][1]['problem_statement'], 'Multiply two numbers')
        self.assertEqual(editor.result['questions'][0]['example_input'], '2 3\n')
        self.assertEqual(editor.result['questions'][0]['example_output'], '5\n')
        reloaded = self.mock_editor()
        reloaded.populate_questions(editor.result['questions'])
        self.assertEqual(reloaded.questions[0][7].get(), '5\n')
        editor.destroy.assert_called_once()
        self.assertEqual(lab.admin('create', {'assessment': editor.result})['assessment_id'], 'EXAM-0001')
        self.assertEqual(self.storage.data['assessments'][0]['questions'][0]['problem_statement'], 'Add two numbers')

    def test_editor_loads_existing_question_values_and_keeps_stable_ids(self):
        editor = self.mock_editor()
        item = assessment()
        item['questions'].append({**copy.deepcopy(item['questions'][0]), 'question_id': 'q3', 'title': 'Second'})
        editor.populate_questions(item['questions'])
        loaded = editor.questions[0]
        self.assertEqual(loaded[0], 'q1')
        self.assertEqual(editor.question_list.selection(), ('q1',))
        self.assertIs(editor.notebook.current, loaded[1])
        self.assertEqual(loaded[2].get(), 'Sum')
        self.assertEqual(loaded[4].get(), 'Add two integers.')
        self.assertEqual(loaded[6][0][2].get(), '5\n')
        editor.add_question()
        self.assertEqual(editor.questions[2][0], 'q4')

    def test_hidden_test_add_and_remove_preserves_first_two_cases(self):
        editor = self.mock_editor()
        editor.add_question()
        editor.add_hidden_test('q1', {'input': 'third', 'expected_output': 'answer'})
        self.assertEqual(len(editor.questions[0][6]), 3)
        editor.remove_hidden_test('q1')
        self.assertEqual(len(editor.questions[0][6]), 2)
        editor.remove_hidden_test('q1')
        self.assertIn('at least two', self.errors.call_args.args[1])

    def test_successful_panel_create_and_edit_refresh_assessment_list(self):
        panel = object.__new__(admin_ui.EduLabPanel)
        panel.dashboard = MagicMock()
        panel.dashboard.root = MagicMock()
        panel.refresh = MagicMock()
        draft = assessment()
        panel.items = {draft['assessment_id']: draft}
        panel.list = MagicMock()
        panel.list.selection.return_value = (draft['assessment_id'],)
        with patch.object(admin_ui, 'AssessmentEditor', return_value=types.SimpleNamespace(result=draft)):
            panel.create()
            request = panel.dashboard.request.call_args
            self.assertEqual(request.args[0]['action'], 'edulab_create')
            request.args[1]({})
            panel.edit()
            request = panel.dashboard.request.call_args
            self.assertEqual(request.args[0]['action'], 'edulab_edit')
            request.args[1]({})
        self.assertEqual(panel.refresh.call_count, 2)

    def test_editor_validation_explains_invalid_fields(self):
        editor = self.mock_editor()
        editor.add_question()
        editor.save()
        self.assertIn('question title', self.errors.call_args.args[1])
        editor.questions[0][2].set('Sum')
        editor.questions[0][4].insert('1.0', 'Add numbers')
        editor.questions[0][3].set('0')
        editor.save()
        self.assertIn('maximum marks', self.errors.call_args.args[1])
        editor.questions[0][3].set('10')
        editor.start_var.set('tomorrow')
        editor.save()
        self.assertIn('timestamp', self.errors.call_args.args[1])
        editor.start_var.set('2026-11-05T10:00:00+00:00')
        editor.duration_var.set('0')
        editor.save()
        self.assertIn('Duration', self.errors.call_args.args[1])
        editor.duration_var.set('60')
        editor.title_var.set('')
        editor.save()
        self.assertIn('assessment title', self.errors.call_args.args[1])
        editor.destroy.assert_not_called()

    def test_editor_visible_actions_and_scrollable_detail_are_structural(self):
        source = Path(admin_ui.__file__).read_text()
        self.assertIn('for question in original or [{}]:', source)
        self.assertIn("text='+ Add Question'", source)
        self.assertIn("text='Delete Question'", source)
        self.assertIn("text='Save Assessment'", source)
        self.assertIn("text='Cancel'", source)
        self.assertIn("actions.grid(row=5, column=0, sticky='ew')", source)
        self.assertIn("question_actions.grid(row=2, column=0, columnspan=2, sticky='ew'", source)
        self.assertIn('outer.rowconfigure(4, weight=1)', source)
        self.assertIn('canvas = tk.Canvas(page', source)

    def test_assessment_accepts_more_than_twenty_questions(self):
        item = assessment()
        template = item['questions'][0]
        item['questions'] = [{**template, 'question_id': f'q{index}',
                              'title': f'Question {index}'} for index in range(1, 26)]
        self.assertEqual(len(lab.validate_assessment(item)['questions']), 25)

    def test_admin_result_view_is_readable(self):
        item = assessment()
        records = {'EXAM-0001:student1000': {'assessment_id': 'EXAM-0001', 'student': 'student1000',
            'status': 'submitted', 'drafts': {'q1': 'std::cout << 5;'},
            'scores': {'q1': {'status': 'Passed', 'score_awarded': 10, 'compile_success': True,
                              'output': [{'test': 1, 'verdict': 'PASS', 'output': '5'}]}}}}
        report = admin_ui.format_results(item, records)
        for value in ('std::cout << 5;', 'Output:', 'PASS', '10 / 10'):
            self.assertIn(value, report)
        self.assertNotIn('expected_output', report)
        self.assertNotIn('violations', report.lower())

    def test_admin_result_view_shows_compile_error(self):
        item = assessment()
        records = {'one': {'assessment_id': 'EXAM-0001', 'student': 'student1000',
            'status': 'submitted', 'drafts': {'q1': 'int main( {'},
            'scores': {'q1': {'status': 'Compile Error', 'score_awarded': 0,
                              'compile_success': False, 'compile_error': 'error: expected ;', 'output': []}}}}
        report = admin_ui.format_results(item, records)
        self.assertIn('Compilation error:', report)
        self.assertIn('error: expected ;', report)
        self.assertIn('Score: 0 / 10', report)

    def test_only_actual_fullscreen_exit_is_logged(self):
        workspace = object.__new__(student_ui.EduLabWorkspace)
        workspace.closed = False
        workspace.attempt = {'status': 'in_progress', 'violations': []}
        workspace.assessment = {'assessment_id': 'EXAM-0001'}
        workspace.fullscreen_exit_recorded = False
        workspace.attributes = MagicMock(return_value=True)
        workspace.violations = MagicMock()
        workspace.lift = MagicMock()
        workspace.focus_force = MagicMock()
        with patch.object(student_ui, 'request', return_value={'attempt': {
                'status': 'in_progress', 'violations': [{'type': 'FULLSCREEN_EXIT'}]}}) as request:
            self.assertEqual(workspace.block(None), 'break')
            workspace.check_fullscreen()
            request.assert_not_called()
            workspace.attributes.return_value = False
            workspace.check_fullscreen()
            workspace.check_fullscreen()
            request.assert_called_once_with('violation', assessment_id='EXAM-0001', type='FULLSCREEN_EXIT')
            workspace.attributes.assert_any_call('-fullscreen', True)
            workspace.attributes.assert_any_call('-topmost', True)
            workspace.focus_force.assert_called()

    def test_escape_reasserts_fullscreen_without_popup(self):
        workspace = object.__new__(student_ui.EduLabWorkspace)
        workspace.attributes = MagicMock()
        workspace.lift = MagicMock()
        workspace.focus_force = MagicMock()
        workspace.after_idle = lambda callback: callback()
        with patch.object(student_ui.messagebox, 'showinfo', create=True) as popup:
            self.assertEqual(workspace.escape_attempt(None), 'break')
        popup.assert_not_called()
        self.assertGreaterEqual(workspace.attributes.call_count, 3)
        workspace.lift.assert_called_once()
        workspace.focus_force.assert_called_once()

    def test_manual_submission_totals_and_closes_attempt(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        lab.student(1000, 'save', {'assessment_id': 'EXAM-0001', 'question_id': 'q1', 'source': 'answer'})
        with patch.object(lab, 'evaluate', return_value={'score_awarded': 7.5, 'max_score': 10,
                'tests_passed': 3, 'tests_total': 4, 'compile_success': True, 'status': 'Partial'}):
            result = lab.student(1000, 'submit', {'assessment_id': 'EXAM-0001'})['attempt']
        self.assertEqual((result['status'], result['reason'], result['total_score']),
                         ('submitted', 'manual', 7.5))
        self.assertEqual(result['scores']['q1']['tests_passed'], 3)
        with self.assertRaises(lab.LabError):
            lab.student(1000, 'submit', {'assessment_id': 'EXAM-0001'})

    def test_student_identity_comes_from_uid(self):
        self.patches[-2].stop()
        with patch.object(lab.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='student1000')):
            self.assertEqual(lab.student_name(1001), 'student1000')
        for name, uid in [('root', 0), ('eduos', 1000), ('student1000', 999), ('arbitrary', 1001)]:
            with patch.object(lab.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name=name)):
                with self.assertRaises(lab.LabError):
                    lab.student_name(uid)

    def test_evaluation_failure_does_not_close_attempt(self):
        self.active()
        lab.student(1000, 'start', {'assessment_id': 'EXAM-0001'})
        lab.student(1000, 'save', {'assessment_id': 'EXAM-0001', 'question_id': 'q1', 'source': 'answer'})
        with patch.object(lab, 'evaluate', side_effect=lab.LabError('Evaluation unavailable')):
            with self.assertRaises(lab.LabError):
                lab.student(1000, 'submit', {'assessment_id': 'EXAM-0001'})
        self.assertEqual(self.storage.records['EXAM-0001:student1000']['status'], 'in_progress')

    def test_socket_request_shape_rejects_spoofing_and_paths(self):
        for request in ({'action': 'start', 'assessment_id': 'EXAM-0001', 'student': 'student1000'},
                        {'action': 'shell', 'command': 'id'},
                        {'action': 'save', 'assessment_id': '../etc', 'question_id': 'q1', 'source': 'x'}):
            with self.assertRaises(lab.LabError):
                service.handle(1001, json.dumps(request).encode())

    def test_lockdown_snapshot_restored_after_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / 'shortcuts.json'
            calls = []
            def fake_xfconf(*args):
                calls.append(args)
                if args == ('--list',):
                    return types.SimpleNamespace(returncode=0, stdout='/xfwm4/custom/Alt-Tab\n')
                return types.SimpleNamespace(returncode=0, stdout='cycle_windows\n')
            with patch.object(student_ui, 'SNAPSHOT', snapshot), patch.object(student_ui, 'xfconf', side_effect=fake_xfconf):
                student_ui.apply_lockdown()
                self.assertTrue(snapshot.exists())
                self.assertIn(('--property', '/xfwm4/custom/Alt-Tab', '--set', ''), calls)
                student_ui.restore_lockdown()
                self.assertFalse(snapshot.exists())
                self.assertIn(('--property', '/xfwm4/custom/Alt-Tab', '--set', 'cycle_windows'), calls)

    def test_missing_socket_reports_friendly_error(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.connect.side_effect = FileNotFoundError(2, 'No such file or directory', student_ui.SOCKET)
        with patch.object(student_ui.socket, 'socket', return_value=connection):
            with self.assertRaises(student_ui.EduLabUnavailable) as captured:
                student_ui.request('list')
        self.assertNotIn('Errno', str(captured.exception))
        self.assertNotIn(student_ui.SOCKET, str(captured.exception))

    def test_panel_starts_without_backend_and_still_refreshes(self):
        with patch.object(student_ui, 'tk', MagicMock()) as tk, \
                patch.object(student_ui, 'ttk', MagicMock()), \
                patch.object(student_ui, 'request', side_effect=student_ui.EduLabUnavailable('offline')):
            panel = student_ui.EduLabPanel(MagicMock(), MagicMock())
            panel.refresh()
            self.assertIn('currently unavailable', panel.status.set.call_args.args[0])
            self.assertNotIn('offline', panel.status.set.call_args.args[0])
            self.assertEqual(panel.items, {})
            tk.StringVar.assert_called_once()

    def test_panel_refresh_and_start_resume(self):
        item = lab.public({**assessment(), 'state': 'active'})
        response = {'assessments': [item], 'attempts': {}}
        with patch.object(student_ui, 'tk', MagicMock()), patch.object(student_ui, 'ttk', MagicMock()), \
                patch.object(student_ui.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='student1000')), \
                patch.object(student_ui, 'request', side_effect=[response,
                    {'attempt': {'status': 'in_progress'}, 'assessment': item}]) as request, \
                patch.object(student_ui, 'EduLabWorkspace', MagicMock()) as workspace, \
                patch.object(student_ui.messagebox, 'askyesno', return_value=True, create=True):
            panel = student_ui.EduLabPanel(MagicMock(), MagicMock())
            panel.refresh()
            self.assertEqual(panel.items['EXAM-0001']['title'], 'C++ Lab Assessment 1')
            panel.table.selection.return_value = ('EXAM-0001',)
            panel.start()
            self.assertEqual(request.call_args.args, ('start',))
            workspace.assert_called_once()

    def test_socket_unit_does_not_wait_for_late_state_service(self):
        units = PROFILE / 'etc/systemd/system'
        socket_unit = (units / 'eduos-edulab.socket').read_text()
        service_unit = (units / 'eduos-edulab.service').read_text()
        self.assertNotIn('After=eduos-state.service', socket_unit)
        self.assertNotIn('Requires=eduos-state.service', socket_unit)
        self.assertIn('After=eduos-state.service', service_unit)
        self.assertIn('Requires=eduos-state.service', service_unit)
        self.assertTrue((units / 'sockets.target.wants/eduos-edulab.socket').is_symlink())

    def test_helper_rejects_extra_fields_and_unauthorized_admin(self):
        with self.assertRaises(existing.helper.RequestError):
            existing.helper.dispatch({'action': 'edulab_reset', 'assessment_id': 'EXAM-0001',
                                      'student': 'student1000', 'path': '/etc/passwd'})
        with patch.object(existing.helper.os, 'getuid', return_value=1001), patch.object(existing.helper.os, 'geteuid', return_value=0):
            self.assertNotEqual(existing.helper.main(), 0)


if __name__ == '__main__':
    unittest.main()
