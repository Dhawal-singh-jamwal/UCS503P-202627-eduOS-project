"""Assignment validation and unprivileged, account-bound submission storage."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat

from eduos_evaluator import validate_test_cases

STATE_ROOT = Path('/var/lib/eduos')
VIEWS = STATE_ROOT / 'student-views'
SUBMISSIONS = STATE_ROOT / 'submissions'
MAX_BYTES = 1024 * 1024
STUDENT = re.compile(r'student[0-9]{4}', re.ASCII)
LAB_ID = re.compile(r'LAB-[0-9]{4,12}', re.ASCII)
FIELDS = {'title', 'description', 'due', 'assigned_to', 'problems'}
LANGUAGES = {'C++': 'source.cpp', 'C': 'source.c', 'Java': 'Main.java', 'Python': 'solution.py', 'Text': 'source.txt'}


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key.')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Invalid JSON constant.')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid assignment JSON.') from error


def text(value, label, limit, optional=False, multiline=False):
    if (not isinstance(value, str) or len(value) > limit or (not optional and not value.strip())
            or any((ord(c) < 32 and not (multiline and c in '\n\r\t')) or ord(c) == 127 for c in value)):
        raise ValueError(f'Invalid {label} (maximum {limit} characters).')


def problem_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'Q[1-9][0-9]{0,11}', value):
        raise ValueError('Invalid problem ID.')
    return value


def validate_problem(item):
    required = {'id', 'title', 'problem_statement', 'language'}
    if (not isinstance(item, dict) or not required <= set(item)
            or set(item) - required - {'status', 'test_cases'}):
        raise ValueError('Invalid problem fields.')
    problem_id(item['id'])
    text(item['title'], 'problem title', 256)
    text(item['problem_statement'], 'problem statement', 65536, multiline=True)
    if not isinstance(item['language'], str) or item['language'] not in LANGUAGES:
        raise ValueError('Choose C++, C, Java, Python, or Text.')
    if 'status' in item:
        text(item['status'], 'problem status', 128)
    if 'test_cases' in item:
        if item['language'] != 'C++':
            raise ValueError('The EduOS Evaluator supports C++ only.')
        validate_test_cases(item['test_cases'])
    return item


def evaluator_enabled(problem):
    return problem.get('language') == 'C++' and 'test_cases' in problem


def validate_fields(item):
    if not isinstance(item, dict) or set(item) != FIELDS:
        raise ValueError('Invalid assignment fields.')
    text(item['title'], 'title', 256)
    text(item['description'], 'description', 65536, optional=True, multiline=True)
    text(item['due'], 'due information', 256, optional=True)
    names = item['assigned_to']
    if (not isinstance(names, list) or not names or len(names) > 10000
            or any(not isinstance(n, str) or not STUDENT.fullmatch(n) for n in names)
            or len(names) != len(set(names))):
        raise ValueError('Select one or more distinct student#### accounts.')
    if not isinstance(item['problems'], list) or not item['problems']:
        raise ValueError('Add at least one problem before saving the lab.')
    ids = set()
    for problem in item['problems']:
        validate_problem(problem)
        if problem['id'] in ids:
            raise ValueError('Duplicate problem ID.')
        ids.add(problem['id'])
    return item


def assignment_id(value):
    if not isinstance(value, str) or not LAB_ID.fullmatch(value) or int(value[4:]) < 1:
        raise ValueError('Invalid assignment ID.')
    return value


def validate_store(data):
    if (not isinstance(data, dict) or not {'assignments'} <= set(data)
            or set(data) - {'assignments', 'next_id'} or not isinstance(data['assignments'], list)
            or len(data['assignments']) > 10000):
        raise ValueError('Invalid assignment store.')
    ids = set()
    for item in data['assignments']:
        if not isinstance(item, dict) or set(item) != FIELDS | {'id', 'created_by', 'created_at', 'next_problem_id'}:
            raise ValueError('Invalid assignment record.')
        validate_fields({key: item[key] for key in FIELDS})
        if (type(item['next_problem_id']) is not int or not 1 <= item['next_problem_id'] <= 999999999999
                or item['next_problem_id'] <= max(int(p['id'][1:]) for p in item['problems'])):
            raise ValueError('Invalid problem sequence.')
        ident = assignment_id(item['id'])
        if ident in ids:
            raise ValueError('Duplicate assignment ID.')
        ids.add(ident)
        if item['created_by'] != 'eduos':
            raise ValueError('Invalid assignment creator.')
        text(item['created_at'], 'creation timestamp', 64)
        datetime.datetime.fromisoformat(item['created_at'])
    if 'next_id' in data and (type(data['next_id']) is not int
            or not 1 <= data['next_id'] <= 999999999999
            or data['next_id'] <= max((int(i[4:]) for i in ids), default=0)):
        raise ValueError('Invalid assignment ID sequence.')
    return data


def load_store():
    entry = identity()
    with private_directory(VIEWS, entry, (), create=False) as fd:
        raw = read_local(fd, 'assignments.json', entry.pw_uid)
    data = validate_store(decode(raw))
    if any(entry.pw_name not in a['assigned_to'] for a in data['assignments']):
        raise ValueError('Invalid student projection.')
    return data


def for_student(data, username):
    validate_store(data)
    if not isinstance(username, str) or not STUDENT.fullmatch(username):
        return []
    return [item for item in data['assignments'] if username in item['assigned_to']]


def identity():
    entry = pwd.getpwuid(os.getuid())
    if os.getuid() != os.geteuid() or not STUDENT.fullmatch(entry.pw_name):
        raise ValueError('Only a logged-in student can submit.')
    return entry


@contextlib.contextmanager
def private_directory(base, entry, components, create=False):
    """Root-owned parent anchors a private user tree; all opens reject symlinks."""
    if not STUDENT.fullmatch(entry.pw_name):
        raise ValueError('Invalid student identity.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(base, flags)
    try:
        base_info = os.fstat(fd)
        if base_info.st_uid != 0 or base_info.st_mode & 0o022:
            raise ValueError('Unsafe submission/view root.')
        for index, component in enumerate((entry.pw_name, *components)):
            if create and index:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != entry.pw_uid or info.st_mode & 0o077:
                os.close(child)
                raise ValueError('Unsafe private student directory.')
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_local(fd, name, uid):
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError('Unsafe submission file.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Submission too large.')
    return raw


def write_local(fd, name, raw):
    temporary = '.pending-' + secrets.token_hex(16)
    dest = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(dest, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError:
            pass


def selected_problem(ident, question):
    assignment_id(ident)
    problem_id(question)
    entry = identity()
    assignment = next((a for a in for_student(load_store(), entry.pw_name) if a['id'] == ident), None)
    problem = next((p for p in assignment['problems'] if p['id'] == question), None) if assignment else None
    if problem is None:
        raise ValueError('Problem no longer assigned to you. Refresh the dashboard.')
    return problem


def submit(ident, question, code):
    entry = identity()
    problem = selected_problem(ident, question)
    if not isinstance(code, str) or not code.strip() or len(code.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Enter nonempty code, at most 1 MiB.')
    filename = LANGUAGES[problem['language']]
    raw = code.encode('utf-8')
    metadata = {'assignment_id': ident, 'problem_id': question, 'username': entry.pw_name,
                'submitted_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'language': problem['language'], 'status': 'Submitted', 'sha256': hashlib.sha256(raw).hexdigest()}
    with private_directory(SUBMISSIONS, entry, (ident, question), create=True) as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_local(fd, filename, raw)
        write_local(fd, 'submission.json', (json.dumps(metadata, indent=2) + '\n').encode())
    return metadata


def read_submission(entry, ident, question):
    """Internal reader shared with the root helper; caller supplies a verified account."""
    assignment_id(ident)
    problem_id(question)
    try:
        with private_directory(SUBMISSIONS, entry, (ident, question)) as fd:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            meta = decode(read_local(fd, 'submission.json', entry.pw_uid))
            if (not isinstance(meta, dict) or meta.get('assignment_id') != ident
                    or meta.get('problem_id') != question or meta.get('username') != entry.pw_name
                    or meta.get('status') != 'Submitted' or meta.get('language') not in LANGUAGES):
                raise ValueError('Invalid submission metadata.')
            text(meta.get('submitted_at'), 'submission timestamp', 64)
            datetime.datetime.fromisoformat(meta['submitted_at'])
            raw = read_local(fd, LANGUAGES[meta['language']], entry.pw_uid)
            if not raw.strip() or hashlib.sha256(raw).hexdigest() != meta.get('sha256'):
                raise ValueError('Submission content does not match metadata.')
            return {'status': 'Submitted', 'submitted_at': meta['submitted_at'],
                    'language': meta['language'], 'code': raw.decode('utf-8')}
    except FileNotFoundError:
        return {'status': 'Pending'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'status': 'Pending', 'warning': 'Submission is incomplete, busy, or unsafe; resubmit to repair.'}


def read_problem_submission(entry, ident, problem):
    if evaluator_enabled(problem):
        import eduos_submissions
        return eduos_submissions.read(entry, ident, problem['id'])
    return read_submission(entry, ident, problem['id'])


def submission_status(ident, question):
    problem = selected_problem(ident, question)
    return read_problem_submission(identity(), ident, problem)['status']


def progress(statuses):
    total = len(statuses)
    submitted = sum(s == 'Submitted' for s in statuses)
    status = 'Not Started' if not submitted else 'Submitted' if submitted == total else 'In Progress'
    return {'submitted': submitted, 'total': total, 'status': status}


def assignment_progress(assignment):
    return progress([read_problem_submission(identity(), assignment['id'], p)['status'] for p in assignment['problems']])
