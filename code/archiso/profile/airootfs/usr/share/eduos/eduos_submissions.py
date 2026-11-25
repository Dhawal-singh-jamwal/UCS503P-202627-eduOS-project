"""Latest evaluator submission in the authenticated student's persistent state tree.

Admin readers supply a verified account, never a caller-provided filesystem path.
Records are student-controlled coursework, not trusted grades or attestations.
"""
import copy
import datetime
import fcntl
import json
import os
from pathlib import Path
import stat

import eduos_assignments as labs
import eduos_evaluator as evaluator

MAX_BYTES = 2 * 1024 * 1024
HOME_ROOT = Path('/home')  # Expected Linux home, not the submission storage location.
STATUSES = {'PASS', 'FAIL', 'RUNTIME ERROR', 'TIME LIMIT EXCEEDED', 'OUTPUT LIMIT EXCEEDED'}


def task_id(ident, question):
    return labs.assignment_id(ident) + '-' + labs.problem_id(question)


def string(value, maximum):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('Invalid submission text.')


def validate(record, entry, ident, question):
    expected = task_id(ident, question)
    if not isinstance(record, dict) or set(record) != {'schema_version', 'student', 'task', 'source_code', 'submitted_at', 'evaluation'}:
        raise ValueError('Invalid submission record.')
    if type(record['schema_version']) is not int or record['schema_version'] != 1:
        raise ValueError('Unknown submission version.')
    student, task, evaluation = record['student'], record['task'], record['evaluation']
    if not isinstance(student, dict) or set(student) != {'username', 'roll_number'} or student['username'] != entry.pw_name:
        raise ValueError('Submission belongs to a different student.')
    labs.text(student['roll_number'], 'roll number', 64, optional=True)
    if (not isinstance(task, dict) or set(task) != {'task_id', 'title', 'language'}
            or task['task_id'] != expected or task['language'] != 'C++'):
        raise ValueError('Submission belongs to a different task.')
    labs.text(task['title'], 'task title', 256)
    source = record['source_code']
    if not isinstance(source, str) or not source.strip() or len(source.encode('utf-8')) > evaluator.SOURCE_LIMIT or '\0' in source:
        raise ValueError('Invalid submitted source.')
    string(record['submitted_at'], 64)
    if datetime.datetime.fromisoformat(record['submitted_at']).utcoffset() is None:
        raise ValueError('Submission timestamp needs a timezone.')
    if not isinstance(evaluation, dict) or set(evaluation) != {'compile_success', 'compile_error', 'error', 'tests', 'passed', 'total'}:
        raise ValueError('Invalid evaluation record.')
    if type(evaluation['compile_success']) is not bool or type(evaluation['total']) is not int or evaluation['total'] != 2:
        raise ValueError('Invalid evaluation summary.')
    string(evaluation['compile_error'], evaluator.OUTPUT_LIMIT)
    if evaluation['error'] != '':
        raise ValueError('Evaluation unavailable; retry before submitting.')
    tests = evaluation['tests']
    if not isinstance(tests, list) or len(tests) != (2 if evaluation['compile_success'] else 0):
        raise ValueError('Invalid test result count.')
    for case in tests:
        if not isinstance(case, dict) or set(case) != {'passed', 'status', 'input', 'expected', 'actual', 'error'}:
            raise ValueError('Invalid test result.')
        if not isinstance(case['status'], str) or case['status'] not in STATUSES or type(case['passed']) is not bool or case['passed'] != (case['status'] == 'PASS'):
            raise ValueError('Invalid test status.')
        for key in ('input', 'expected', 'actual', 'error'):
            string(case[key], evaluator.OUTPUT_LIMIT)
    if type(evaluation['passed']) is not int or evaluation['passed'] != sum(t['passed'] for t in tests):
        raise ValueError('Invalid passing test count.')
    return record


def directory(entry, create=False):
    if entry.pw_uid < 1000 or entry.pw_dir != str(HOME_ROOT / entry.pw_name):
        raise ValueError('Invalid student identity.')
    return labs.private_directory(labs.SUBMISSIONS, entry, (), create=create)


def read(entry, ident, question):
    """Bounded, no-follow reader shared with the narrowly scoped admin helper."""
    filename = task_id(ident, question) + '.json'
    try:
        with directory(entry) as fd:
            source = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
            with os.fdopen(source, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != entry.pw_uid
                        or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_BYTES):
                    raise ValueError('Unsafe submission file.')
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Submission too large.')
            record = validate(labs.decode(raw), entry, ident, question)
            return {'status': 'Submitted', 'submitted_at': record['submitted_at'],
                    'language': 'C++', 'code': record['source_code'], 'record': record}
    except FileNotFoundError:
        return {'status': 'Pending'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'status': 'Pending', 'warning': 'Submission is unreadable, malformed, or has unsafe permissions.'}


def read_own(ident, question):
    return read(labs.identity(), ident, question)


def submit(ident, question, source_code):
    """Evaluate a fresh snapshot and save it. No target username/path argument."""
    entry = labs.identity()
    if os.getuid() == 0:
        raise ValueError('Student submission must not run as root.')
    filename = task_id(ident, question) + '.json'
    problem = copy.deepcopy(labs.selected_problem(ident, question))
    if not labs.evaluator_enabled(problem):
        raise ValueError('This problem does not have C++ evaluator tests.')
    result = evaluator.evaluate_cpp(source_code, problem['test_cases'])
    if result.get('error'):
        raise ValueError('Evaluation unavailable: ' + result['error'])
    # Do not attach old tests to a question changed/revoked during evaluation.
    if labs.selected_problem(ident, question) != problem:
        raise ValueError('Problem changed during evaluation. Reopen it and submit again.')
    roll = ''
    try:
        data = labs.decode((labs.STATE_ROOT / 'student-data.json').read_bytes())
        roll = data.get(entry.pw_name, {}).get('roll_number', '')
    except (OSError, ValueError, AttributeError):
        pass
    record = {'schema_version': 1, 'student': {'username': entry.pw_name, 'roll_number': roll},
              'task': {'task_id': task_id(ident, question), 'title': problem['title'], 'language': 'C++'},
              'source_code': source_code, 'submitted_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'evaluation': {**result, 'passed': sum(t['passed'] for t in result['tests']), 'total': 2}}
    validate(record, entry, ident, question)
    raw = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('Submission exceeds the 2 MiB limit.')
    with directory(entry, create=True) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        labs.write_local(fd, filename, raw)
    return record


def summary(record):
    evaluation = record['evaluation']
    if not evaluation['compile_success']:
        return 'Compilation failed'
    return f"{evaluation['passed']} / {evaluation['total']} tests passed"
