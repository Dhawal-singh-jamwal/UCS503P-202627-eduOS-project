"""Local C++ evaluator. Bounded execution, NOT a native-code security sandbox."""
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


def validate_test_cases(cases):
    if not isinstance(cases, list) or len(cases) != 2:
        raise ValueError('Exactly two test cases are required.')
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
    with subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
        return {key: value.decode('utf-8', errors='replace') for key, value in output.items()} | {
            'returncode': process.returncode, 'error': reason}


def evaluate_cpp(source_code, test_cases):
    result = {'compile_success': False, 'compile_error': '', 'tests': [], 'error': ''}
    try:
        if os.getuid() == 0 or os.geteuid() == 0 or os.getuid() != os.geteuid():
            raise ValueError('The evaluator must run as an ordinary logged-in user, never root.')
        if (not isinstance(source_code, str) or not source_code.strip()
                or len(source_code.encode('utf-8')) > SOURCE_LIMIT or '\0' in source_code):
            raise ValueError('Enter nonempty C++ source, at most 128 KiB, without NUL characters.')
        validate_test_cases(test_cases)
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
                    status, actual, error = 'RUNTIME ERROR', '', str(exc)
                result['tests'].append({'passed': status == 'PASS', 'status': status,
                                        'input': case['input'], 'expected': case['expected_output'],
                                        'actual': actual, 'error': error[:OUTPUT_LIMIT]})
    except (ValueError, OSError) as exc:
        result['error'] = str(exc)[:4096]
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
        if len(raw) > REQUEST_LIMIT:
            raise ValueError('Evaluator request exceeds 1 MiB.')
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {'source_code', 'test_cases'}:
            raise ValueError('Request needs source_code and test_cases only.')
        result = evaluate_cpp(request['source_code'], request['test_cases'])
    except (ValueError, UnicodeError, RecursionError) as exc:
        result = {'compile_success': False, 'compile_error': '', 'tests': [], 'error': str(exc)[:4096]}
    print(json.dumps(result))
    return 1 if result['error'] else 0
