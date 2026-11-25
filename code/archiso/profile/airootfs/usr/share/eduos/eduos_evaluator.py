"""Local C++ evaluator with Bubblewrap isolation and bounded execution."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time

OUTPUT_LIMIT = 65536
SOURCE_LIMIT = 131072
TEXT_LIMIT = 16384
REQUEST_LIMIT = 1048576
COMPILE_TIMEOUT = 20
RUN_TIMEOUT = 2
ISOLATION_ERROR = 'Evaluator isolation unavailable.'


def process_limit():
    """Allow a bounded number of new threads beyond this UID's current use."""
    current = 0
    uid = os.getuid()
    with os.scandir('/proc') as processes:
        for process in processes:
            if not process.name.isdecimal():
                continue
            try:
                if process.stat(follow_symlinks=False).st_uid == uid:
                    with os.scandir(f'/proc/{process.name}/task') as tasks:
                        current += sum(1 for _ in tasks)
            except (FileNotFoundError, PermissionError):
                continue
    return max(128, current + 64)


def isolated_command(argv, cwd, timeout):
    """Expose only system binaries and the current scratch directory to C++."""
    cwd = Path(cwd).resolve(strict=True)
    command = ['/usr/bin/prlimit', '--core=0', '--fsize=16777216', '--nofile=64',
               f'--nproc={process_limit()}',
               '--as=1073741824' if argv[0] == '/usr/bin/g++' else '--as=268435456',
               f'--cpu={max(2, int(timeout) + 1)}', '--',
               '/usr/bin/bwrap', '--unshare-all', '--die-with-parent',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/etc', '/etc',
               '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
               '--symlink', 'usr/lib', '/lib64', '--dev', '/dev', '--proc', '/proc',
               '--tmpfs', '/tmp', '--dir', '/work', '--bind', str(cwd), '/work',
               '--chdir', '/work', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
               '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'HOME', '/work',
               '--setenv', 'TMPDIR', '/tmp']
    if argv[0] == '/usr/bin/g++':
        return command + ['--', *argv]
    executable = Path(argv[0]).resolve(strict=True)
    if executable.name != 'solution' or executable.parent != cwd.parent:
        raise ValueError('Invalid evaluator executable.')
    return command + ['--dir', '/app', '--ro-bind', str(executable), '/app/solution',
                      '--', '/app/solution', *argv[1:]]


def validate_test_cases(cases, minimum=2, maximum=2):
    if not isinstance(cases, list) or not minimum <= len(cases) <= maximum:
        raise ValueError(f'{minimum} to {maximum} test cases are required.')
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'input', 'expected_output'}:
            raise ValueError('Each test needs input and expected_output.')
        for value in case.values():
            if not isinstance(value, str) or len(value.encode('utf-8')) > TEXT_LIMIT or '\0' in value:
                raise ValueError('Test text must be UTF-8 text, at most 16 KiB, without NUL characters.')
    return cases


def normalize_output(value):
    return '\n'.join(line.rstrip() for line in value.replace('\r\n', '\n').split('\n')).rstrip('\n')


def run_bounded(argv, cwd, input_text, timeout):
    """Drain both pipes incrementally; kill/reap the process group on every exit."""
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    reason = ''
    # A fixed environment avoids compiler search-path injection via inherited variables.
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(cwd), 'TMPDIR': str(cwd)}
    isolated = isolated_command(argv, cwd, timeout)
    with subprocess.Popen(isolated, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, start_new_session=True, env=env) as process:
        try:
            with selectors.DefaultSelector() as selector:
                for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                pending = memoryview(input_text.encode('utf-8'))
                if pending:
                    os.set_blocking(process.stdin.fileno(), False)
                    selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
                else:
                    process.stdin.close()
                deadline = time.monotonic() + timeout
                while selector.get_map() or process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = 'TIME LIMIT EXCEEDED'
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        if key.data == 'stdin':
                            try:
                                pending = pending[os.write(key.fd, pending[:4096]):]
                            except BrokenPipeError:
                                pending = pending[:0]
                            if not pending:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                        else:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            buffer = output[key.data]
                            room = OUTPUT_LIMIT - len(buffer)
                            buffer.extend(chunk[:room])
                            if len(chunk) > room:
                                reason = 'OUTPUT LIMIT EXCEEDED'
                                break
                    if reason:
                        break
        finally:
            # Also terminate children left behind by a normally exiting program/compiler.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        decoded = {key: value.decode('utf-8', errors='replace') for key, value in output.items()}
        if process.returncode and (decoded['stderr'].startswith('bwrap:') or
                                   decoded['stderr'].startswith('prlimit:')):
            raise OSError(ISOLATION_ERROR)
        return decoded | {
            'returncode': process.returncode, 'error': reason}


def evaluate_cpp(source_code, test_cases, edulab=False):
    result = {'compile_success': False, 'compile_error': '', 'tests': [], 'error': ''}
    try:
        if os.getuid() == 0 or os.geteuid() == 0 or os.getuid() != os.geteuid():
            raise ValueError('The evaluator must run as an ordinary logged-in user, never root.')
        if (not isinstance(source_code, str) or not source_code.strip()
                or len(source_code.encode('utf-8')) > SOURCE_LIMIT or '\0' in source_code):
            raise ValueError('Enter nonempty C++ source, at most 128 KiB, without NUL characters.')
        validate_test_cases(test_cases, 2, 20 if edulab else 2)
        with tempfile.TemporaryDirectory(prefix='eduos-evaluator-', dir='/tmp') as temporary:
            directory = Path(temporary)
            (directory / 'solution.cpp').write_text(source_code, encoding='utf-8')
            compiled = run_bounded(['/usr/bin/g++', 'solution.cpp', '-std=c++17', '-O2', '-o', 'solution'],
                                   directory, '', COMPILE_TIMEOUT)
            if compiled['error'] or compiled['returncode']:
                result['compile_error'] = (compiled['error'] + '\n' + compiled['stderr']).strip()[:OUTPUT_LIMIT]
                return result
            result['compile_success'] = True
            for index, case in enumerate(test_cases, 1):
                # Separate test working directories prevent ordinary files leaking between cases.
                working = directory / f'test-{index}'
                working.mkdir()
                try:
                    run = run_bounded([str(directory / 'solution')], working, case['input'], RUN_TIMEOUT)
                    status = run['error'] or ('RUNTIME ERROR' if run['returncode'] else
                             'PASS' if normalize_output(run['stdout']) == normalize_output(case['expected_output']) else 'FAIL')
                    error = run['stderr']
                    if run['returncode'] and status == 'RUNTIME ERROR':
                        error = f"Exit status {run['returncode']}\n" + error
                    actual = run['stdout']
                except OSError as exc:
                    if str(exc) == ISOLATION_ERROR:
                        raise
                    status, actual, error = 'RUNTIME ERROR', '', str(exc)
                result['tests'].append({'passed': status == 'PASS', 'status': status,
                                        'input': case['input'], 'expected': case['expected_output'],
                                        'actual': actual, 'error': error[:OUTPUT_LIMIT]})
    except (ValueError, OSError) as exc:
        result['error'] = str(exc)[:4096]
    return result


def run_example_cpp(source_code, example_input):
    """Compile once and run one public example input without a hidden verdict."""
    result = {'compile_success': False, 'compile_error': '', 'output': '',
              'runtime_error': '', 'status': '', 'error': ''}
    try:
        if os.getuid() == 0 or os.geteuid() == 0 or os.getuid() != os.geteuid():
            raise ValueError('The evaluator must run as an ordinary logged-in user, never root.')
        if (not isinstance(source_code, str) or not source_code.strip()
                or len(source_code.encode('utf-8')) > SOURCE_LIMIT or '\0' in source_code):
            raise ValueError('Enter nonempty C++ source, at most 128 KiB, without NUL characters.')
        if (not isinstance(example_input, str) or len(example_input.encode('utf-8')) > TEXT_LIMIT
                or '\0' in example_input):
            raise ValueError('Example input must be at most 16 KiB and contain no NUL characters.')
        with tempfile.TemporaryDirectory(prefix='eduos-example-', dir='/tmp') as temporary:
            directory = Path(temporary)
            (directory / 'solution.cpp').write_text(source_code, encoding='utf-8')
            compiled = run_bounded(['/usr/bin/g++', 'solution.cpp', '-std=c++17', '-O2', '-o', 'solution'],
                                   directory, '', COMPILE_TIMEOUT)
            if compiled['error'] or compiled['returncode']:
                result['compile_error'] = (compiled['error'] + '\n' + compiled['stderr']).strip()[:OUTPUT_LIMIT]
                result['status'] = 'COMPILE ERROR'
                return result
            result['compile_success'] = True
            working = directory / 'example'
            working.mkdir()
            run = run_bounded([str(directory / 'solution')], working, example_input, RUN_TIMEOUT)
            result['output'] = run['stdout']
            result['runtime_error'] = run['stderr'][:OUTPUT_LIMIT]
            result['status'] = run['error'] or ('RUNTIME ERROR' if run['returncode'] else 'FINISHED')
            if run['returncode'] and not result['runtime_error']:
                result['runtime_error'] = f"Exit status {run['returncode']}"
    except (ValueError, OSError) as exc:
        result['error'] = str(exc)[:4096]
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
        if len(raw) > REQUEST_LIMIT:
            raise ValueError('Evaluator request exceeds 1 MiB.')
        request = json.loads(raw)
        if isinstance(request, dict) and set(request) == {'source_code', 'example_input', 'mode'}:
            if request['mode'] != 'example':
                raise ValueError('Invalid evaluator mode.')
            result = run_example_cpp(request['source_code'], request['example_input'])
        else:
            if not isinstance(request, dict) or set(request) not in ({'source_code', 'test_cases'},
                                                                      {'source_code', 'test_cases', 'edulab'}):
                raise ValueError('Invalid evaluator request.')
            if 'edulab' in request and request['edulab'] is not True:
                raise ValueError('Invalid EduLab evaluation mode.')
            result = evaluate_cpp(request['source_code'], request['test_cases'], request.get('edulab', False))
    except (ValueError, UnicodeError, RecursionError) as exc:
        result = {'compile_success': False, 'compile_error': '', 'tests': [], 'error': str(exc)[:4096]}
    print(json.dumps(result))
    return 1 if result['error'] else 0
