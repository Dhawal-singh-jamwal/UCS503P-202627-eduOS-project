"""Protected local assessment state. Only the root helpers import this module."""
import contextlib
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import fcntl
import json
import logging
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile

ROOT = Path('/var/lib/eduos/edulab')
ASSESSMENTS = ROOT / 'assessments.json'
ATTEMPTS = ROOT / 'attempts.json'
LOCK = Path('/run/eduos-edulab.lock')
IDENT = re.compile(r'EXAM-[0-9]{4,8}', re.ASCII)
STUDENT = re.compile(r'student[0-9]{4}', re.ASCII)
QUESTION = re.compile(r'q[1-9][0-9]{0,3}', re.ASCII)
EVENTS = {'FULLSCREEN_EXIT'}
MAX_SOURCE = 131072
LOG = logging.getLogger(__name__)


class LabError(ValueError):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise LabError('Invalid timestamp.')
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as error:
        raise LabError('Invalid timestamp.') from error
    if parsed.tzinfo is None:
        raise LabError('Timestamp needs a timezone.')
    return parsed.astimezone(dt.timezone.utc)


def stamp(value):
    return value.isoformat(timespec='seconds')


def ident(value, pattern, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise LabError('Invalid ' + label + '.')
    return value


def text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\0' in value:
        raise LabError('Invalid ' + label + '.')
    return value


def validate_assessment(item):
    if not isinstance(item, dict) or set(item) != {'assessment_id', 'title', 'instructions', 'scheduled_start',
                                                   'duration_minutes', 'state', 'questions'}:
        raise LabError('Invalid assessment fields.')
    ident(item['assessment_id'], IDENT, 'assessment ID')
    text(item['title'], 'title', 256)
    text(item['instructions'], 'instructions', 8192)
    timestamp(item['scheduled_start'])
    if type(item['duration_minutes']) is not int or not 1 <= item['duration_minutes'] <= 240:
        raise LabError('Duration must be 1–240 minutes.')
    if item['state'] not in ('draft', 'active', 'completed'):
        raise LabError('Invalid assessment state.')
    questions = item['questions']
    # The store's 4 MiB atomic-write limit is the overall resource bound. Do
    # not impose an arbitrary question-count limit on an instructor.
    if not isinstance(questions, list) or not questions:
        raise LabError('Add at least one question.')
    ids = set()
    for question in questions:
        if (not isinstance(question, dict)
                or set(question) not in ({'question_id', 'title', 'problem_statement', 'language',
                                          'max_score', 'hidden_test_cases'},
                                         {'question_id', 'title', 'problem_statement', 'language',
                                          'max_score', 'example_input', 'hidden_test_cases'},
                                         {'question_id', 'title', 'problem_statement', 'language',
                                          'max_score', 'example_input', 'example_output', 'hidden_test_cases'})):
            raise LabError('Invalid question fields.')
        qid = ident(question['question_id'], QUESTION, 'question ID')
        if qid in ids:
            raise LabError('Duplicate question ID.')
        ids.add(qid)
        text(question['title'], 'question title', 256)
        text(question['problem_statement'], 'problem statement', 65536)
        if question['language'] != 'cpp' or type(question['max_score']) is not int or not 1 <= question['max_score'] <= 1000:
            raise LabError('Only C++ and a positive integer maximum score are supported.')
        for key in ('example_input', 'example_output'):
            value = question.get(key, '')
            if (not isinstance(value, str) or len(value.encode('utf-8')) > 16384
                    or '\0' in value):
                raise LabError(f'{key.replace("_", " ").capitalize()} exceeds 16 KiB or contains NUL.')
        cases = question['hidden_test_cases']
        if not isinstance(cases, list) or not 2 <= len(cases) <= 20:
            raise LabError('Each question needs 2–20 hidden tests.')
        for case in cases:
            if not isinstance(case, dict) or set(case) != {'input', 'expected_output'}:
                raise LabError('Invalid hidden test fields.')
            for value in case.values():
                if not isinstance(value, str) or len(value.encode('utf-8')) > 16384 or '\0' in value:
                    raise LabError('Hidden test text exceeds 16 KiB or contains NUL.')
    return item


def validate_store(store):
    if not isinstance(store, dict) or set(store) != {'next_id', 'assessments'} or type(store['next_id']) is not int or not isinstance(store['assessments'], list):
        raise LabError('Invalid assessment store.')
    ids = set()
    for item in store['assessments']:
        validate_assessment(item)
        if item['assessment_id'] in ids:
            raise LabError('Duplicate assessment ID.')
        ids.add(item['assessment_id'])
    return store


def _pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise LabError('Duplicate JSON key.')
        data[key] = value
    return data


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(LabError('Invalid JSON number.')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LabError('Invalid EduLab JSON.') from error


def secure_root():
    if os.geteuid() != 0:
        raise LabError('EduLab storage requires root.')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    info = ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise LabError('Unsafe EduLab storage directory.')


def read(path, default):
    secure_root()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return default
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise LabError('Unsafe EduLab state file.')
        raw = stream.read(4_000_001)
    if len(raw) > 4_000_000:
        raise LabError('EduLab state is too large.')
    return decode(raw)


def atomic(path, value):
    secure_root()
    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode()
    if len(raw) > 4_000_000:
        raise LabError('EduLab state is too large.')
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=ROOT)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextlib.contextmanager
def locked():
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def store():
    return validate_store(read(ASSESSMENTS, {'next_id': 1, 'assessments': []}))


def attempts():
    data = read(ATTEMPTS, {})
    if not isinstance(data, dict):
        raise LabError('Invalid attempt store.')
    return data


def public(item):
    return {key: value for key, value in item.items() if key != 'questions'} | {
        'questions': [{key: value for key, value in q.items() if key != 'hidden_test_cases'}
                      for q in item['questions']]}


def public_attempt(record):
    """Return only this student's result; it contains no hidden input or expected output."""
    return record


def find(assessment_id):
    ident(assessment_id, IDENT, 'assessment ID')
    item = next((a for a in store()['assessments'] if a['assessment_id'] == assessment_id), None)
    if item is None:
        raise LabError('Assessment not found.')
    return item


def admin(action, request):
    with locked():
        data = store()
        if action == 'list':
            return {'assessments': data['assessments'], 'attempts': attempts()}
        if action == 'create':
            identifier = f"EXAM-{data['next_id']:04d}"
            item = {**request['assessment'], 'assessment_id': identifier}
            validate_assessment(item)
            if item['state'] != 'draft':
                raise LabError('New assessments must start as drafts.')
            data['assessments'].append(item)
            data['next_id'] += 1
            atomic(ASSESSMENTS, data)
            return {'assessment_id': identifier}
        identifier = ident(request['assessment_id'], IDENT, 'assessment ID')
        item = next((a for a in data['assessments'] if a['assessment_id'] == identifier), None)
        if item is None:
            raise LabError('Assessment not found.')
        if action == 'review':
            student = ident(request['student'], STUDENT, 'student')
            records = attempts()
            record = records.get(identifier + ':' + student)
            if record is None or record['status'] == 'in_progress':
                raise LabError('Only completed attempts can be reviewed.')
            feedback = request['feedback']
            if not isinstance(feedback, str) or len(feedback) > 4096 or '\0' in feedback:
                raise LabError('Feedback must be at most 4096 characters.')
            final_score = request['final_score']
            maximum = sum(q['max_score'] for q in item['questions'])
            if final_score is not None:
                if type(final_score) not in (int, float):
                    raise LabError('Invalid final score.')
                score = Decimal(str(final_score))
                if not score.is_finite() or not 0 <= score <= maximum or score.as_tuple().exponent < -2:
                    raise LabError('Final score must be within the available marks, to two decimals.')
                final_score = float(score)
            record.update(feedback=feedback, final_score=final_score, reviewed_at=stamp(now()))
            atomic(ATTEMPTS, records)
            return {'message': 'Instructor review saved.'}
        if action == 'reset':
            student = ident(request['student'], STUDENT, 'student')
            records = attempts()
            records.pop(identifier + ':' + student, None)
            atomic(ATTEMPTS, records)
            return {'message': 'Attempt reset.'}
        if action == 'delete':
            records = attempts()
            related = {k: v for k, v in records.items() if k.startswith(identifier + ':')}
            if any(v.get('status') == 'in_progress' and remaining(v) > 0 for v in related.values()):
                raise LabError('An assessment with an active attempt cannot be deleted.')
            data['assessments'].remove(item)
            if related:
                remaining_records = {k: v for k, v in records.items() if k not in related}
                atomic(ATTEMPTS, remaining_records)
                try:
                    atomic(ASSESSMENTS, data)
                except Exception:
                    atomic(ATTEMPTS, records)
                    raise
                return {'message': 'Assessment and completed attempts deleted.'}
        elif action == 'edit':
            if item['state'] != 'draft' or any(k.startswith(identifier + ':') for k in attempts()):
                raise LabError('Only unused draft assessments may be edited.')
            replacement = {**request['assessment'], 'assessment_id': identifier}
            validate_assessment(replacement)
            if replacement['state'] != 'draft':
                raise LabError('Editing cannot activate an assessment.')
            data['assessments'][data['assessments'].index(item)] = replacement
        elif action == 'activate':
            if item['state'] != 'draft':
                raise LabError('Only drafts may be activated.')
            item['state'] = 'active'
        elif action == 'deactivate':
            if item['state'] != 'active':
                raise LabError('Assessment is not active.')
            item['state'] = 'completed'
        else:
            raise LabError('Unknown admin action.')
        atomic(ASSESSMENTS, data)
        return {'message': 'Assessment saved.'}


def student_name(uid):
    try:
        entry = pwd.getpwuid(uid)
    except KeyError as error:
        raise LabError('Unknown student identity.') from error
    if uid < 1000 or entry.pw_name == 'eduos' or STUDENT.fullmatch(entry.pw_name) is None:
        raise LabError('Student access denied.')
    return entry.pw_name


def remaining(record):
    return max(0, int((timestamp(record['deadline']) - now()).total_seconds()))


def key(identifier, student):
    return identifier + ':' + student


def evaluate(question, source):
    if not source.strip():
        return {'score_awarded': 0.0, 'max_score': question['max_score'], 'tests_passed': 0,
                'tests_total': len(question['hidden_test_cases']), 'compile_success': False,
                'status': 'No answer', 'compile_error': '', 'output': []}
    payload = {'source_code': source, 'test_cases': question['hidden_test_cases'], 'edulab': True}
    try:
        result = evaluator_request(payload)
    except (OSError, KeyError, subprocess.TimeoutExpired, LabError) as error:
        LOG.error('EduLab evaluation unavailable: %s', error)
        raise LabError('Evaluation failed; retry submission.') from error
    passed = sum(case['passed'] for case in result['tests'])
    total = len(question['hidden_test_cases'])
    score = Decimal(question['max_score']) * Decimal(passed) / Decimal(total)
    score = float(score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    return {'score_awarded': score, 'max_score': question['max_score'], 'tests_passed': passed,
            'tests_total': total, 'compile_success': result['compile_success'],
            'status': 'Compile Error' if not result['compile_success'] else
                      'Passed' if passed == total else 'Partial' if passed else 'Failed',
            'compile_error': result.get('compile_error', ''),
            'output': [{'test': index, 'verdict': case['status'], 'output': case['actual']}
                       for index, case in enumerate(result['tests'], 1)]}


def evaluator_request(payload):
    runner = pwd.getpwnam('edulab-runner')
    if runner.pw_uid == 0 or runner.pw_gid == 0:
        raise LabError('Invalid EduLab evaluator identity.')
    run = subprocess.run(['/usr/bin/setpriv', '--reuid', str(runner.pw_uid),
                          '--regid', str(runner.pw_gid), '--clear-groups', '--',
                          '/usr/local/bin/eduos-evaluator'], input=json.dumps(payload), text=True,
                         capture_output=True, timeout=180,
                         env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, check=False)
    if run.returncode:
        LOG.error('EduLab evaluator failed: exit=%s stderr=%s', run.returncode, run.stderr[:2000])
        raise LabError('Evaluator process failed. Check the EduLab service log.')
    result = decode(run.stdout)
    if result.get('error'):
        LOG.error('EduLab evaluator error: %s', result['error'])
        raise LabError('Evaluation unavailable; retry.')
    return result


def run_example(question, source):
    if 'example_input' not in question:
        raise LabError('No example input is configured for this question.')
    try:
        result = evaluator_request({'mode': 'example', 'source_code': source,
                                    'example_input': question['example_input']})
    except (OSError, KeyError, subprocess.TimeoutExpired, LabError) as error:
        LOG.error('EduLab example run unavailable: %s', error)
        raise LabError('Example run failed; retry.') from error
    return {'compile_success': result['compile_success'], 'compile_error': result['compile_error'],
            'output': result['output'], 'runtime_error': result['runtime_error'],
            'status': result['status']}


def finalize(item, record, reason, records):
    if record['status'] != 'in_progress':
        return record
    scores = {q['question_id']: evaluate(q, record['drafts'].get(q['question_id'], '')) for q in item['questions']}
    record.update(status='timed_out' if reason == 'timeout' else 'submitted',
                  submitted_at=stamp(now()), reason=reason, scores=scores,
                  total_score=round(sum(q['score_awarded'] for q in scores.values()), 2))
    records[key(item['assessment_id'], record['student'])] = record
    atomic(ATTEMPTS, records)
    return record


def student(uid, action, request):
    name = student_name(uid)
    with locked():
        if action == 'list':
            records = {k: v for k, v in attempts().items() if k.endswith(':' + name)}
            visible = [item for item in store()['assessments']
                       if item['state'] == 'active' or key(item['assessment_id'], name) in records]
            return {'assessments': [public(item) for item in visible],
                    'attempts': {k: {'status': v['status'], 'remaining': remaining(v) if v['status'] == 'in_progress' else 0}
                                 for k, v in records.items()}}
        identifier = ident(request.get('assessment_id'), IDENT, 'assessment ID')
        item = find(identifier)
        records = attempts()
        record = records.get(key(identifier, name))
        if action == 'start':
            beginning = timestamp(item['scheduled_start'])
            end = beginning + dt.timedelta(minutes=item['duration_minutes'])
            if record is None:
                if item['state'] != 'active' or now() < beginning or now() >= end:
                    raise LabError('Assessment is not open.')
                record = {'assessment_id': identifier, 'student': name, 'started_at': stamp(now()),
                          'deadline': stamp(min(now() + dt.timedelta(minutes=item['duration_minutes']), end)),
                          'status': 'in_progress', 'drafts': {}, 'violations': [],
                          'submitted_at': None, 'reason': None, 'scores': {}, 'total_score': None}
                records[key(identifier, name)] = record
                atomic(ATTEMPTS, records)
        if record is None:
            raise LabError('Attempt has not started.')
        if record['status'] == 'in_progress' and remaining(record) == 0:
            record = finalize(item, record, 'timeout', records)
        if action == 'save':
            if record['status'] != 'in_progress':
                raise LabError('Attempt is closed.')
            qid = ident(request.get('question_id'), QUESTION, 'question ID')
            if qid not in {q['question_id'] for q in item['questions']}:
                raise LabError('Question is not in this assessment.')
            source = request.get('source')
            if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE or '\0' in source:
                raise LabError('Invalid C++ draft.')
            record['drafts'][qid] = source
            atomic(ATTEMPTS, records)
        elif action == 'run_example':
            if record['status'] != 'in_progress':
                raise LabError('Attempt is closed.')
            qid = ident(request.get('question_id'), QUESTION, 'question ID')
            question = next((q for q in item['questions'] if q['question_id'] == qid), None)
            if question is None:
                raise LabError('Question is not in this assessment.')
            source = request.get('source')
            if not isinstance(source, str) or len(source.encode('utf-8')) > MAX_SOURCE or '\0' in source:
                raise LabError('Invalid C++ source.')
            return {'example': run_example(question, source)}
        elif action == 'violation':
            if record['status'] != 'in_progress' or request.get('type') not in EVENTS:
                raise LabError('Invalid violation event.')
            if len(record['violations']) < 1000:
                record['violations'].append({'type': request['type'], 'timestamp': stamp(now())})
                atomic(ATTEMPTS, records)
        elif action == 'submit':
            if record['status'] != 'in_progress':
                raise LabError('Attempt is already closed.')
            record = finalize(item, record, 'manual', records)
        elif action == 'result':
            if record['status'] == 'in_progress':
                raise LabError('Result is available after submission.')
        elif action not in ('start', 'state'):
            raise LabError('Unknown student action.')
        return {'assessment': public(item), 'attempt': public_attempt(record), 'remaining': remaining(record)
                if record['status'] == 'in_progress' else 0}
