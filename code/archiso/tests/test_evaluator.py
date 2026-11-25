"""Real local compiler tests with harmless programs; no privilege changes."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_admin as existing
import test_assignments as assignment_tests
from test_assignments import document, fields, problem
import eduos_evaluator as evaluator

CASES = [{'input': '2 3\n', 'expected_output': '5\n'},
         {'input': '10 20\n', 'expected_output': '30\n'}]
SUM = '#include <iostream>\nint main(){int a,b; std::cin>>a>>b; std::cout<<a+b<<"\\n";}'


class ComparisonTests(unittest.TestCase):
    def test_normalization(self):
        for value in ('5', '5\n', '5   \n', '5\r\n', '5\n \t\n'):
            self.assertEqual(evaluator.normalize_output(value), '5')
        self.assertEqual(evaluator.normalize_output('a  b\t\r\nc  \n\n'), 'a  b\nc')
        for left, right in [('5', '6'), ('a b', 'ab'), ('a  b', 'a b'), (' a', 'a'), ('a\n\nb', 'a\nb')]:
            self.assertNotEqual(evaluator.normalize_output(left), evaluator.normalize_output(right))

    def test_malformed_cases(self):
        for bad in (None, {}, [], CASES[:1], CASES * 2, [None, None],
                    [{'input': 1, 'expected_output': ''}] * 2,
                    [{'input': '', 'expected_output': '', 'command': 'x'}] * 2,
                    [{'input': '\0', 'expected_output': ''}] * 2,
                    [{'input': 'x' * 16385, 'expected_output': ''}] * 2):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(ValueError):
                evaluator.validate_test_cases(bad)

    def test_root_and_setuid_refused_before_process_creation(self):
        for uid, euid in ((0, 0), (1000, 0), (1000, 1001)):
            with patch.object(evaluator.os, 'getuid', return_value=uid), patch.object(evaluator.os, 'geteuid', return_value=euid), patch.object(evaluator.subprocess, 'Popen') as popen:
                self.assertIn('never root', evaluator.evaluate_cpp(SUM, CASES)['error'])
                popen.assert_not_called()

    def test_bad_cli_requests(self):
        for raw in (b'{', b'[]', b'{}', b'\xff', b'x' * (evaluator.REQUEST_LIMIT + 1)):
            with patch.object(evaluator.sys, 'stdin', type('Input', (), {'buffer': io.BytesIO(raw)})()), patch.object(evaluator.sys, 'stdout', new_callable=io.StringIO) as output:
                self.assertEqual(evaluator.main(), 1)
                self.assertTrue(json.loads(output.getvalue())['error'])


@unittest.skipUnless(shutil.which('g++') and os.getuid() != 0, 'Requires g++ and an ordinary user')
class ExecutionTests(unittest.TestCase):
    def evaluate(self, source, cases=None):
        directories = []
        original = tempfile.TemporaryDirectory
        def temporary(*args, **kwargs):
            result = original(*args, **kwargs)
            directories.append(Path(result.name))
            return result
        with patch.object(evaluator.tempfile, 'TemporaryDirectory', side_effect=temporary):
            result = evaluator.evaluate_cpp(source, CASES if cases is None else cases)
        self.assertTrue(all(not p.exists() for p in directories), 'Temporary files leaked')
        return result

    def test_correct(self):
        result = self.evaluate(SUM)
        self.assertTrue(result['compile_success'])
        self.assertEqual([t['status'] for t in result['tests']], ['PASS', 'PASS'])

    def test_one_wrong(self):
        result = self.evaluate(SUM.replace('a+b', '(a==10 ? 29 : a+b)'))
        self.assertEqual([t['status'] for t in result['tests']], ['PASS', 'FAIL'])

    def test_compilation_error(self):
        result = self.evaluate('int main( {')
        self.assertFalse(result['compile_success'])
        self.assertIn('error', result['compile_error'])
        self.assertEqual(result['tests'], [])

    def test_infinite_loop(self):
        result = self.evaluate('int main(){while(true){}}')
        self.assertEqual([t['status'] for t in result['tests']], ['TIME LIMIT EXCEEDED'] * 2)

    def test_nonzero_exit_and_stderr(self):
        result = self.evaluate('#include <cstdio>\nint main(){fputs("bad input",stderr);return 7;}')
        self.assertEqual([t['status'] for t in result['tests']], ['RUNTIME ERROR'] * 2)
        self.assertIn('bad input', result['tests'][0]['error'])
        self.assertIn('7', result['tests'][0]['error'])

    def test_crash_without_core_dump(self):
        result = self.evaluate('#include <sys/resource.h>\n#include <csignal>\nint main(){rlimit r{0,0};setrlimit(RLIMIT_CORE,&r);raise(SIGSEGV);}')
        self.assertEqual([t['status'] for t in result['tests']], ['RUNTIME ERROR'] * 2)

    def test_empty_output(self):
        self.assertEqual([t['status'] for t in self.evaluate('int main(){}')['tests']], ['FAIL'] * 2)
        empty = [{'input': '', 'expected_output': ''}] * 2
        self.assertTrue(all(t['passed'] for t in self.evaluate('int main(){}', empty)['tests']))

    def test_multiline_input_and_output(self):
        cases = [{'input': '1\n2\n3\n', 'expected_output': '2\n4\n6\n'},
                 {'input': '4\n5\n', 'expected_output': '8\n10\n'}]
        result = self.evaluate('#include <iostream>\nint main(){int n;while(std::cin>>n)std::cout<<n*2<<"  \\r\\n";}', cases)
        self.assertTrue(all(t['passed'] for t in result['tests']))

    def test_stdout_and_stderr_limits(self):
        for stream in ('stdout', 'stderr'):
            result = self.evaluate(f'#include <cstdio>\nint main(){{while(true)fputs("xxxxxxxxxxxxxxxx",{stream});}}')
            self.assertEqual([t['status'] for t in result['tests']], ['OUTPUT LIMIT EXCEEDED'] * 2)
            self.assertLessEqual(len(result['tests'][0]['actual']), evaluator.OUTPUT_LIMIT)
            self.assertLessEqual(len(result['tests'][0]['error']), evaluator.OUTPUT_LIMIT)

    def test_compiler_output_limit(self):
        result = self.evaluate('#error x\n' * 10000)
        self.assertFalse(result['compile_success'])
        self.assertIn('OUTPUT LIMIT EXCEEDED', result['compile_error'])
        self.assertLessEqual(len(result['compile_error']), evaluator.OUTPUT_LIMIT)

    def test_compile_timeout(self):
        with patch.object(evaluator, 'COMPILE_TIMEOUT', 0):
            result = self.evaluate(SUM)
        self.assertFalse(result['compile_success'])
        self.assertIn('TIME LIMIT EXCEEDED', result['compile_error'])

    def test_missing_compiler_and_execution_failure(self):
        with patch.object(evaluator.subprocess, 'Popen', side_effect=FileNotFoundError('compiler missing')):
            self.assertIn('compiler missing', self.evaluate(SUM)['error'])
        with patch.object(evaluator, 'run_bounded', side_effect=[{'error': '', 'returncode': 0}, OSError('cannot execute'), OSError('cannot execute')]):
            self.assertEqual([t['status'] for t in self.evaluate(SUM)['tests']], ['RUNTIME ERROR'] * 2)

    def test_invalid_source(self):
        for source in (None, 42, '', ' ', '\0', 'x' * (evaluator.SOURCE_LIMIT + 1)):
            self.assertTrue(self.evaluate(source)['error'])

    def test_working_directory_is_fresh_per_case(self):
        source = '#include <fstream>\n#include <iostream>\nint main(){std::ifstream f("marker");std::cout<<(f?1:0);std::ofstream o("marker");}'
        cases = [{'input': '', 'expected_output': '0'}] * 2
        self.assertTrue(all(t['passed'] for t in self.evaluate(source, cases)['tests']))

    def test_isolation_hides_student_data_and_unshares_network(self):
        source = ('#include <iostream>\n#include <filesystem>\n'
                  'int main(){std::cout<<std::filesystem::exists("/home")<<" "'
                  '<<std::filesystem::exists("/var/lib/eduos");}')
        cases = [{'input': '', 'expected_output': '0 0'}] * 2
        result = self.evaluate(source, cases)
        self.assertTrue(result['compile_success'])
        self.assertEqual([case['actual'] for case in result['tests']], ['0 0'] * 2)
        command = evaluator.isolated_command(['/usr/bin/g++', 'solution.cpp'], Path('/tmp'), 20)
        self.assertIn('--unshare-all', command)
        self.assertNotIn('--share-net', command)


class IntegrationTests(unittest.TestCase):
    setUp = assignment_tests.HelperAssignmentTests.setUp
    request = assignment_tests.HelperAssignmentTests.request

    def test_helper_roundtrip_and_stale_save(self):
        values = fields()
        values['problems'][0].update(test_cases=copy.deepcopy(CASES), status='Assigned')
        response = self.request('create_assignment', assignment=values)
        loaded = self.request('assignment_status')['store']['assignments'][0]
        self.assertEqual(loaded['problems'], values['problems'])
        values['problems'][0]['test_cases'][0]['input'] = '1\n2\n'
        self.request('update_assignment', id=response['id'], assignment=values)
        self.assertEqual(self.request('assignment_status')['store']['assignments'][0]['problems'], values['problems'])
        with self.assertRaises(existing.helper.RequestError):
            self.request('update_assignment', id=response['id'], assignment=values, revision=response['revision'])

    def test_invalid_cases_rejected_without_save(self):
        original = self.assignment_path.read_bytes()
        for language, cases in [('C++', []), ('Python', CASES)]:
            values = fields()
            values['problems'][0].update(language=language, test_cases=cases)
            with self.assertRaises(existing.helper.RequestError):
                self.request('create_assignment', assignment=values)
            self.assertEqual(self.assignment_path.read_bytes(), original)

    def test_old_data_compatible(self):
        self.assertEqual(existing.helper.labs.validate_store(document()), document())
        self.assertFalse(existing.helper.labs.evaluator_enabled(problem()))
        seed = existing.PROFILE / 'airootfs/usr/share/eduos/student-data.json'
        with patch.object(existing.student_app, 'DATA_FILE', seed):
            self.assertEqual(existing.student_app.load_student_data('student1000')['tasks'], json.loads(seed.read_text())['student1000']['tasks'])
        existing.helper.validate_data(json.loads(seed.read_text()))

    def test_admin_apply_preserves_multiline(self):
        editor = object.__new__(existing.app.ProblemEditor)
        editor.ident = 'Q1'
        editor.title_value = MagicMock(get=lambda: 'Addition')
        editor.language = MagicMock(get=lambda: 'C++')
        editor.status = MagicMock(get=lambda: 'Assigned')
        editor.statement = MagicMock(get=lambda *args: 'Read two integers.\nPrint sum.')
        editor.evaluator = MagicMock(get=lambda: True)
        editor.test_widgets = [{key: MagicMock(get=lambda *args, value=value: value) for key, value in case.items()} for case in CASES]
        editor.destroy = MagicMock()
        editor.apply()
        self.assertEqual(editor.result['test_cases'], CASES)
        self.assertEqual(editor.result['problem_statement'], 'Read two integers.\nPrint sum.')

    def test_evaluator_workspace_never_submits(self):
        app = existing.student_app
        panel = object.__new__(app.LabAssignments)
        panel.parent = MagicMock()
        panel.drafts = {('LAB-0001', 'Q1'): 'draft'}
        panel.evaluator_window = None
        item = {**problem(), 'test_cases': CASES}
        with patch.object(app.labs, 'selected_problem', return_value=item), patch.object(app, 'EvaluatorWorkspace') as workspace, patch.object(app.labs, 'submit') as submit:
            panel.show_problem('LAB-0001', 'Q1')
            self.assertEqual(workspace.call_args.args[2], panel.drafts)
            panel.show_problem('LAB-0001', 'Q1')
            self.assertEqual(workspace.call_count, 1)
            submit.assert_not_called()

    def test_worker_uses_snapshot_and_ui_polls_result(self):
        app = existing.student_app
        workspace = object.__new__(app.EvaluatorWorkspace)
        workspace.running = False
        workspace.code = MagicMock(get=lambda *args: SUM)
        workspace.drafts, workspace.key = {}, ('LAB-0001', 'Q1')
        workspace.problem = {'test_cases': CASES}
        workspace.results = app.queue.Queue()
        workspace.run_button = MagicMock()
        workspace.submit_button = MagicMock()
        workspace.submission_status = MagicMock()
        workspace.display, workspace.after = MagicMock(), MagicMock()
        with patch.object(app.threading, 'Thread') as thread, patch.object(app.evaluator, 'evaluate_cpp', return_value={'error': 'test error'}) as evaluate:
            workspace.run_tests()
            self.assertTrue(workspace.running)
            evaluate.assert_not_called()
            workspace.run_button.configure.assert_called_with(state='disabled', text='Running...')
            workspace.run_tests()
            self.assertEqual(thread.call_count, 1)
            thread.call_args.kwargs['target']()
            evaluate.assert_called_once_with(SUM, CASES)
            workspace.poll()
        self.assertFalse(workspace.running)
        workspace.run_button.configure.assert_called_with(state='normal', text='Run Tests')
        self.assertEqual(workspace.drafts[workspace.key], SUM)

    def test_result_format(self):
        app = existing.student_app
        self.assertIn('No test cases were executed', app.evaluation_text({'compile_success': False, 'compile_error': 'syntax error'}))
        result = {'compile_success': True, 'tests': [dict(passed=True, status='PASS', input='2 3', expected='5', actual='5', error=''), dict(passed=False, status='TIME LIMIT EXCEEDED', input='', expected='', actual='', error='')]}
        text = app.evaluation_text(result)
        for value in ('1 / 2 passed', 'PASS', 'TIME LIMIT EXCEEDED', 'Input:', 'Expected:', 'Actual:'):
            self.assertIn(value, text)


if __name__ == '__main__':
    unittest.main()
