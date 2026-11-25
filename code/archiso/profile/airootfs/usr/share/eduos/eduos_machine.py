"""Local EduOS desired state. Profiles are trusted, root-owned ISO files."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import tempfile


ROOT = Path('/var/lib/eduos')
MACHINE = ROOT / 'machine.json'
STATUS = ROOT / 'status.json'
FLEET = ROOT / 'fleet.json'
PROFILES = Path('/usr/share/eduos/profiles')
DEFAULT_PROFILE = 'CSE-LAB-CPP-v1'
BUNDLED_PV = Path('/usr/share/eduos/packages/pv-1.12.7-1-x86_64.pkg.tar.zst')
BUNDLED_PV_SHA256 = '89ca8540a98f34ba6387eb22f3773771a98aac97eff413786eb93c4812f76bad'
SAFE_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
PROFILE_ID = re.compile(r'[A-Z][A-Z0-9]*(?:-[A-Za-z0-9]+)+', re.ASCII)
MACHINE_ID = re.compile(r'LAB-PC-[A-Z0-9]{3,16}', re.ASCII)
PACKAGE = re.compile(r'[a-z0-9][a-z0-9@._+-]{0,127}', re.ASCII)
SERVICE = re.compile(r'[A-Za-z0-9][A-Za-z0-9@_.-]{0,127}\.service', re.ASCII)
HOSTNAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', re.ASCII)


class MachineError(ValueError):
    pass


def _name(value, pattern, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None or '..' in value:
        raise MachineError(f'Invalid {label}.')
    return value


def profile_id(value):
    return _name(value, PROFILE_ID, 'profile ID')


def machine_id(value):
    return _name(value, MACHINE_ID, 'machine ID')


def package_name(value):
    return _name(value, PACKAGE, 'package name')


def service_name(value):
    return _name(value, SERVICE, 'service name')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MachineError('Duplicate JSON key.')
        result[key] = value
    return result


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(MachineError('Invalid JSON number.')))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MachineError('Invalid JSON document.') from error


def validate_profile(data):
    required = {'profile_id', 'name', 'version', 'required_packages', 'required_services',
                'environment', 'managed_files'}
    if not isinstance(data, dict) or set(data) != required:
        raise MachineError('Invalid profile fields.')
    profile_id(data['profile_id'])
    if not isinstance(data['name'], str) or not 1 <= len(data['name']) <= 120 or not data['name'].isprintable():
        raise MachineError('Invalid profile name.')
    if type(data['version']) is not int or not 1 <= data['version'] <= 100000:
        raise MachineError('Invalid profile version.')
    for key, validator in [('required_packages', package_name), ('required_services', service_name)]:
        values = data[key]
        if not isinstance(values, list) or len(values) > 100 or len(set(map(str, values))) != len(values):
            raise MachineError('Invalid or duplicate profile requirements.')
        for value in values:
            validator(value)
    if data['environment'] != {'cpp_standard': 'c++17'} or data['managed_files'] != []:
        raise MachineError('Unsupported environment or managed files.')
    return data


def validate_machine(data):
    if not isinstance(data, dict) or set(data) != {'machine_id', 'hostname', 'assigned_profile', 'registered'}:
        raise MachineError('Invalid registration fields.')
    machine_id(data['machine_id'])
    _name(data['hostname'], HOSTNAME, 'hostname')
    profile_id(data['assigned_profile'])
    if data['registered'] is not True:
        raise MachineError('Machine is not registered.')
    return data


def secure_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise MachineError('Machine configuration directory is not trusted.')


def read_document(path, validator):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise MachineError('Machine configuration file is not trusted.')
        raw = source.read(65537)
    if len(raw) > 65536:
        raise MachineError('Machine configuration is too large.')
    return validator(decode(raw))


def load_profile(ident):
    profile_id(ident)
    secure_directory(PROFILES)
    try:
        data = read_document(PROFILES / (ident + '.json'), validate_profile)
    except FileNotFoundError as error:
        raise MachineError('Assigned profile does not exist.') from error
    if data['profile_id'] != ident:
        raise MachineError('Profile ID does not match its file name.')
    return data


def available_profiles():
    secure_directory(PROFILES)
    profiles = []
    for path in sorted(PROFILES.glob('*.json')):
        try:
            profiles.append(load_profile(path.stem))
        except (MachineError, OSError):
            continue
    return [{'profile_id': item['profile_id'], 'name': item['name'], 'version': item['version']}
            for item in profiles]


def load_machine():
    secure_directory(ROOT)
    try:
        return read_document(MACHINE, validate_machine)
    except FileNotFoundError as error:
        raise MachineError('Machine is not registered.') from error


def atomic_json(path, data, validator, mode=0o644):
    validator(data)
    secure_directory(path.parent)
    raw = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        validator(decode(Path(temporary).read_bytes()))
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_root():
    if os.getuid() != 0 or os.geteuid() != 0:
        raise MachineError('This operation requires root.')


def register():
    require_root()
    secure_directory(ROOT)
    if os.path.lexists(MACHINE):
        machine = load_machine()
        load_profile(machine['assigned_profile'])
        return machine
    load_profile(DEFAULT_PROFILE)
    machine = {'machine_id': 'LAB-PC-' + secrets.token_hex(8).upper(), 'hostname': socket.gethostname(),
               'assigned_profile': DEFAULT_PROFILE, 'registered': True}
    atomic_json(MACHINE, machine, validate_machine)
    return machine


def assign_profile(ident):
    require_root()
    load_profile(ident)
    machine = load_machine()
    machine = {**machine, 'assigned_profile': ident}
    atomic_json(MACHINE, machine, validate_machine)
    # A cached result from the previous assignment must not be shown as current.
    return machine


def run(command, timeout=20):
    try:
        return subprocess.run(command, capture_output=True, text=True, env=SAFE_ENV,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MachineError(f'Could not inspect {Path(command[0]).name}.') from error


def inspect_package(name):
    package_name(name)
    result = run(['/usr/bin/pacman', '-Qq', '--', name])
    if result.returncode == 0 and result.stdout.strip() == name:
        return 'present'
    if result.returncode == 1:
        return 'missing'
    raise MachineError('Package inspection failed.')


def bundled_pv():
    """Use only the exact root-owned archive shipped with this ISO."""
    try:
        secure_directory(BUNDLED_PV.parent)
        fd = os.open(BUNDLED_PV, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                    info.st_mode & 0o022 or info.st_size > 1024 * 1024):
                raise MachineError('Bundled pv package is not trusted.')
            digest = hashlib.sha256(source.read()).hexdigest()
    except OSError as error:
        raise MachineError('Bundled pv package is unavailable.') from error
    if digest != BUNDLED_PV_SHA256:
        raise MachineError('Bundled pv package failed its integrity check.')
    return BUNDLED_PV


def inspect_service(name):
    service_name(name)
    enabled = run(['/usr/bin/systemctl', 'is-enabled', '--', name])
    active = run(['/usr/bin/systemctl', 'is-active', '--', name])
    if enabled.returncode not in (0, 1) or active.returncode not in (0, 3):
        raise MachineError('Service inspection failed.')
    return {'name': name, 'enabled': enabled.stdout.strip() == 'enabled',
            'active': active.stdout.strip() == 'active'}


def validate_status(data):
    if not isinstance(data, dict) or set(data) != {'checked_at', 'machine_id', 'profile_id', 'profile_version',
                                                   'compliant', 'packages', 'services', 'issues'}:
        raise MachineError('Invalid status fields.')
    machine_id(data['machine_id'])
    profile_id(data['profile_id'])
    if not isinstance(data['checked_at'], str) or not data['checked_at'].endswith('Z'):
        raise MachineError('Invalid check time.')
    try:
        datetime.datetime.fromisoformat(data['checked_at'].replace('Z', '+00:00'))
    except ValueError as error:
        raise MachineError('Invalid check time.') from error
    if type(data['profile_version']) is not int or data['profile_version'] < 1 or type(data['compliant']) is not bool:
        raise MachineError('Invalid status values.')
    if not isinstance(data['packages'], list) or not isinstance(data['services'], list) or not isinstance(data['issues'], list):
        raise MachineError('Invalid status requirements.')
    for item in data['packages']:
        if not isinstance(item, dict) or set(item) != {'name', 'state'} or item['state'] not in ('present', 'missing'):
            raise MachineError('Invalid package status.')
        package_name(item['name'])
    for item in data['services']:
        if not isinstance(item, dict) or set(item) != {'name', 'enabled', 'active'} or type(item['enabled']) is not bool or type(item['active']) is not bool:
            raise MachineError('Invalid service status.')
        service_name(item['name'])
    if any(not isinstance(issue, str) or len(issue) > 256 for issue in data['issues']):
        raise MachineError('Invalid issue.')
    return data


def validate_snapshot(data):
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'machine', 'status'}
            or type(data['schema_version']) is not int or data['schema_version'] != 1):
        raise MachineError('Invalid fleet snapshot.')
    registration = validate_machine(data['machine'])
    status = validate_status(data['status'])
    if (status['machine_id'] != registration['machine_id'] or
            status['profile_id'] != registration['assigned_profile']):
        raise MachineError('Fleet snapshot identity does not match its status.')
    return data


def validate_fleet(data):
    if not isinstance(data, dict) or len(data) > 40:
        raise MachineError('Invalid fleet store.')
    for ident, item in data.items():
        machine_id(ident)
        if not isinstance(item, dict) or set(item) != {'snapshot', 'imported_at'}:
            raise MachineError('Invalid imported machine record.')
        validate_snapshot(item['snapshot'])
        if item['snapshot']['machine']['machine_id'] != ident:
            raise MachineError('Fleet key does not match machine ID.')
        if not isinstance(item['imported_at'], str) or not item['imported_at'].endswith('Z'):
            raise MachineError('Invalid fleet import time.')
    return data


def fleet():
    secure_directory(ROOT)
    try:
        return read_document(FLEET, validate_fleet)
    except FileNotFoundError:
        return {}


def snapshot():
    registration = load_machine()
    return validate_snapshot({'schema_version': 1, 'machine': registration, 'status': check()})


def import_snapshot(data):
    require_root()
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 65536:
        raise MachineError('Fleet snapshot exceeds 64 KiB.')
    validate_snapshot(data)
    ident = data['machine']['machine_id']
    if ident == load_machine()['machine_id']:
        raise MachineError('This machine already has its own live status.')
    records = fleet()
    records[ident] = {'snapshot': data, 'imported_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}
    if len((json.dumps(records, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()) > 65536:
        raise MachineError('Fleet store exceeds 64 KiB.')
    atomic_json(FLEET, records, validate_fleet, 0o600)
    return records


def remove_snapshot(ident):
    require_root()
    machine_id(ident)
    records = fleet()
    if ident not in records:
        raise MachineError('Imported machine was not found.')
    del records[ident]
    atomic_json(FLEET, records, validate_fleet, 0o600)
    return records


def check(save=False):
    machine = load_machine()
    profile = load_profile(machine['assigned_profile'])
    packages = [{'name': name, 'state': inspect_package(name)} for name in profile['required_packages']]
    services = [inspect_service(name) for name in profile['required_services']]
    issues = [f"Package {item['name']} is missing" for item in packages if item['state'] == 'missing']
    issues += [f"Service {item['name']} is disabled or stopped" for item in services
               if not item['enabled'] or not item['active']]
    result = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
              'machine_id': machine['machine_id'], 'profile_id': profile['profile_id'],
              'profile_version': profile['version'], 'compliant': not issues,
              'packages': packages, 'services': services, 'issues': issues}
    if save:
        require_root()
        atomic_json(STATUS, result, validate_status)
    return result


def cached_status():
    secure_directory(ROOT)
    try:
        result = read_document(STATUS, validate_status)
    except FileNotFoundError:
        return None
    machine = load_machine()
    if (result['machine_id'] != machine['machine_id'] or
            result['profile_id'] != machine['assigned_profile'] or
            result['profile_version'] != load_profile(machine['assigned_profile'])['version']):
        return None
    return result


def sync():
    require_root()
    before = check()
    # Re-resolve every action from the current, validated profile. Never accept
    # package names or service commands from a caller or cached status.
    profile = load_profile(load_machine()['assigned_profile'])
    for item in before['packages']:
        if item['state'] == 'missing':
            name = package_name(item['name'])
            if name not in profile['required_packages']:
                raise MachineError('Profile changed during synchronization.')
            # mkarchiso removes the sync database and package cache. The fixed
            # demo package is therefore installed from an ISO-bundled archive.
            command = (['/usr/bin/pacman', '-U', '--noconfirm', '--', str(bundled_pv())]
                       if name == 'pv' else
                       ['/usr/bin/pacman', '-S', '--needed', '--noconfirm', '--', name])
            result = run(command, timeout=300)
            if result.returncode:
                raise MachineError(f'Could not install required package {name}.')
    for item in before['services']:
        name = service_name(item['name'])
        if name not in profile['required_services']:
            raise MachineError('Profile changed during synchronization.')
        if not item['enabled'] and run(['/usr/bin/systemctl', 'enable', '--', name]).returncode:
            raise MachineError(f'Could not enable required service {name}.')
        if not item['active'] and run(['/usr/bin/systemctl', 'start', '--', name]).returncode:
            raise MachineError(f'Could not start required service {name}.')
    return check(save=True)


def demo_drift():
    """Deliberately remove only the isolated demo requirement, on admin request."""
    require_root()
    profile = load_profile(load_machine()['assigned_profile'])
    if 'pv' not in profile['required_packages']:
        raise MachineError('The assigned profile does not use the demo package.')
    if inspect_package('pv') == 'missing':
        return {'message': 'pv is already missing.'}
    bundled_pv()  # Refuse the demo if this boot cannot restore its package.
    result = run(['/usr/bin/pacman', '-R', '--noconfirm', '--', 'pv'], timeout=300)
    if result.returncode:
        raise MachineError('Could not remove the isolated demo package pv.')
    return {'message': 'Removed pv for the drift demonstration. Refresh machine status.'}
