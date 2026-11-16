"""Account-scoped local project continuity. Python standard library only."""
from __future__ import annotations
import argparse
import contextlib
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ACCOUNT = 'electron-account-renderer-state-v1'
PROJECTS = 'local-projects'
THREADS = 'thread-project-assignments'
HOSTS = 'thread-project-membership-host-ids'
ORDER = 'project-order'
PROJECTLESS = 'projectless-thread-ids'
MAPPING = 'app-server-project-id-by-legacy-project-id-by-host'
MIGRATION = 'app-server-projects-migration-by-host'
FIELDS = (PROJECTS, THREADS, HOSTS, ORDER, MAPPING, MIGRATION)
VERSION = 1


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key; refusing ambiguous state')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def scopes(state):
    if not isinstance(state, dict) or not isinstance(state.get(ACCOUNT), dict):
        raise ValueError('Unsupported account state schema')
    account = state[ACCOUNT]
    if not isinstance(account.get('inactive'), dict) or 'owner' not in account:
        raise ValueError('Incomplete account state')
    owner = account['owner']
    valid = lambda s: isinstance(s, str) and bool(re.fullmatch(r'[0-9a-f]{64}', s))
    if owner is not None and not valid(owner):
        raise ValueError('Unsupported account owner')
    result = {}
    for key, snap in account['inactive'].items():
        if not valid(key) or not isinstance(snap, dict) or not isinstance(snap.get('globals'), dict):
            raise ValueError('Unsupported inactive account snapshot')
        if key != owner:
            result[key] = snap['globals']
    if owner is not None:
        result[owner] = state
    for value in result.values():
        validate_globals(value)
    return result


def validate_globals(g):
    for field in FIELDS:
        if field in g and not isinstance(g[field], list if field == ORDER else dict):
            raise ValueError('Unsupported field type: ' + field)
    for pid, p in g.get(PROJECTS, {}).items():
        if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not isinstance(p.get('rootPaths'), list) or not p['rootPaths'] or not all(isinstance(x, str) and x for x in p['rootPaths']):
            raise ValueError('Unsupported local project registration')
    for assignment in g.get(THREADS, {}).values():
        if not isinstance(assignment, dict) or not isinstance(assignment.get('projectKind'), str) or not isinstance(assignment.get('projectId'), str):
            raise ValueError('Unsupported chat membership')
    if not all(isinstance(x, str) for x in g.get(ORDER, [])):
        raise ValueError('Unsupported project ordering')
    if not isinstance(g.get(PROJECTLESS, []), list) or not all(isinstance(x, str) for x in g.get(PROJECTLESS, [])):
        raise ValueError('Unsupported explicit projectless membership')
    if not all(isinstance(v, str) for v in g.get(HOSTS, {}).values()):
        raise ValueError('Unsupported membership host')
    for host, mapping in g.get(MAPPING, {}).items():
        if not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values()):
            raise ValueError('Unsupported project ID mapping')
    for host, migration in g.get(MIGRATION, {}).items():
        if not isinstance(migration, dict) or migration.get('version') != 1 or set(migration) - {'version', 'projectsMigrated', 'threadAssignmentsMigrated', 'threadAssignmentsReadMigrated', 'pendingThreadAssignmentIds'}:
            raise ValueError('Unsupported project migration schema')
        if not isinstance(migration.get('projectsMigrated'), bool) or not isinstance(migration.get('threadAssignmentsMigrated'), bool) or not isinstance(migration.get('pendingThreadAssignmentIds', []), list) or not all(isinstance(t, str) for t in migration.get('pendingThreadAssignmentIds', [])) or ('threadAssignmentsReadMigrated' in migration and not isinstance(migration['threadAssignmentsReadMigrated'], bool)):
            raise ValueError('Unsupported project migration values')


def roots(project):
    # Windows/UNC paths compare without case; Linux paths retain case.
    return tuple(sorted((p.replace('/', '\\').rstrip('\\').casefold() if re.match(r'^[a-zA-Z]:[\\/]|^[\\/]{2}', p) else p.rstrip('/')) for p in project['rootPaths']))


def new_catalog():
    return {'version': VERSION, 'paused': False, 'projects': {}, 'threads': {},
            'hosts': {}, 'mappings': {}, 'migrationHosts': [], 'accounts': {},
            'excludedProjects': [], 'conflicts': []}


def local_threads(g):
    return {k: v for k, v in g.get(THREADS, {}).items() if v['projectKind'] == 'local'}


def observe(state, catalog, closed=False):
    """Read additions anytime; infer removals only from a fully exited app."""
    c = copy.deepcopy(catalog)
    if c.get('version') != VERSION:
        raise ValueError('Unsupported catalog version')
    for scope, g in scopes(state).items():
        projects, threads = g.get(PROJECTS, {}), local_threads(g)
        a = c['accounts'].setdefault(scope, {'seenProjects': [], 'seenThreads': {}, 'removedProjects': [], 'removedThreads': []})
        previous_threads = copy.deepcopy(a['seenThreads'])
        if closed:
            a['removedProjects'] = sorted(set(a['removedProjects']) | (set(a['seenProjects']) - set(projects)))
            a['removedThreads'] = sorted(set(a['removedThreads']) | (set(a['seenThreads']) - set(threads)))
        # Explicitly re-adding an entry in the UI clears its local suppression.
        a['removedProjects'] = [p for p in a['removedProjects'] if p not in projects]
        a['removedThreads'] = [t for t in a['removedThreads'] if t not in threads]
        a['seenProjects'] = sorted(set(a['seenProjects']) | set(projects)) if not closed else sorted(projects)
        a['seenThreads'] = {**a['seenThreads'], **copy.deepcopy(threads)} if not closed else copy.deepcopy(threads)
        for pid, p in projects.items():
            if pid not in c['excludedProjects']:
                c['projects'].setdefault(pid, copy.deepcopy(p))
        for tid, assignment in threads.items():
            pid = assignment['projectId']
            if pid in projects and pid not in c['excludedProjects'] and pid in c['projects'] and roots(projects[pid]) == roots(c['projects'][pid]):
                # A change against this account's accepted baseline supplies the
                # latest observed local assignment for future, empty accounts.
                # Pre-existing disagreements in a newly encountered account do not.
                if tid in previous_threads and previous_threads[tid] != assignment:
                    c['threads'][tid] = copy.deepcopy(assignment)
                    c['hosts'].pop(tid, None)
                c['threads'].setdefault(tid, copy.deepcopy(assignment))
                if c['threads'][tid] == assignment and tid in g.get(HOSTS, {}):
                    c['hosts'].setdefault(tid, g[HOSTS][tid])
        for host, mapping in g.get(MAPPING, {}).items():
            if not host.startswith('local:'):
                continue
            dest = c['mappings'].setdefault(host, {})
            for pid, native in mapping.items():
                if pid not in c['excludedProjects'] and pid in projects and pid in c['projects'] and roots(projects[pid]) == roots(c['projects'][pid]):
                    dest.setdefault(pid, native)
        for host in g.get(MIGRATION, {}):
            if host.startswith('local:') and host not in c['migrationHosts']:
                c['migrationHosts'].append(host)
    return c


def strip_fields(state):
    result = copy.deepcopy(state)
    for g in scopes(result).values():
        for field in FIELDS:
            g.pop(field, None)
    return result


def merge(state, catalog):
    result = copy.deepcopy(state)
    c = observe(state, catalog, closed=True)
    conflicts, added_projects, added_threads = [], 0, 0
    for scope, g in scopes(result).items():
        account = c['accounts'][scope]
        projects = g.get(PROJECTS, {})
        eligible, inserted = set(), []
        for pid, project in c['projects'].items():
            if pid in c['excludedProjects'] or pid in account['removedProjects']:
                continue
            if pid in projects:
                if roots(projects[pid]) != roots(project):
                    conflicts.append({'account': scope[:12], 'kind': 'project-id', 'id': pid})
                    continue
                if projects[pid] != project:
                    conflicts.append({'account': scope[:12], 'kind': 'project-metadata', 'id': pid})
                eligible.add(pid)
                continue
            if any(roots(p) == roots(project) for p in projects.values()):
                conflicts.append({'account': scope[:12], 'kind': 'project-path-alias', 'id': pid})
                continue
            projects = g.setdefault(PROJECTS, {})
            projects[pid] = copy.deepcopy(project)
            eligible.add(pid)
            inserted.append(pid)
            added_projects += 1
        if inserted:
            order = g.setdefault(ORDER, [])
            order.extend(p for p in inserted if p not in order)
        imported_threads = []
        for tid, assignment in c['threads'].items():
            if assignment['projectId'] not in eligible or tid in account['removedThreads'] or tid in g.get(PROJECTLESS, []):
                continue
            existing = g.get(THREADS, {}).get(tid)
            if existing is not None and existing != assignment:
                conflicts.append({'account': scope[:12], 'kind': 'thread-assignment', 'id': tid})
                continue
            if existing is None:
                g.setdefault(THREADS, {})[tid] = copy.deepcopy(assignment)
                imported_threads.append(tid)
                added_threads += 1
            if tid in c['hosts']:
                hosts = g.setdefault(HOSTS, {})
                if tid in hosts and hosts[tid] != c['hosts'][tid]:
                    conflicts.append({'account': scope[:12], 'kind': 'membership-host', 'id': tid})
                else:
                    hosts.setdefault(tid, c['hosts'][tid])
        for host, mapping in c['mappings'].items():
            for pid, native in mapping.items():
                if pid not in eligible:
                    continue
                dest = g.setdefault(MAPPING, {}).setdefault(host, {})
                if pid in dest and dest[pid] != native:
                    conflicts.append({'account': scope[:12], 'kind': 'native-project-id', 'id': pid})
                else:
                    dest.setdefault(pid, native)
        if inserted or imported_threads:
            for host in c['migrationHosts']:
                migration = g.setdefault(MIGRATION, {}).setdefault(host, {'version': 1, 'projectsMigrated': False, 'threadAssignmentsMigrated': False, 'pendingThreadAssignmentIds': []})
                if any(pid not in g.get(MAPPING, {}).get(host, {}) for pid in inserted):
                    migration['projectsMigrated'] = False
                # Migration keys are <membership host>:<Codex home>. The app
                # invalidates all homes with the same host prefix. Absent host
                # metadata for a local registration defaults to the local host.
                host_threads = [t for t in imported_threads if host.startswith(g.get(HOSTS, {}).get(t, 'local') + ':')]
                if host_threads:
                    migration['threadAssignmentsMigrated'] = False
                    pending = migration.setdefault('pendingThreadAssignmentIds', [])
                    pending.extend(t for t in host_threads if t not in pending)
        account['seenProjects'] = sorted(g.get(PROJECTS, {}))
        account['seenThreads'] = copy.deepcopy(local_threads(g))
    if strip_fields(state) != strip_fields(result):
        raise AssertionError('Unrelated state changed')
    scopes(result)
    c['conflicts'] = conflicts
    return result, c, {'addedProjects': added_projects, 'addedMemberships': added_threads, 'conflicts': conflicts}


class WindowsGuard:
    def __init__(self, lock_path):
        if os.name != 'nt':
            raise RuntimeError('Live synchronization requires Windows')
        self.lock_path = Path(lock_path)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.k.CreateFileW.restype = wintypes.HANDLE
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    def running(self):
        class Entry(ctypes.Structure):
            _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
        handle = self.k.CreateToolhelp32Snapshot(2, 0)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(entry)
            self.k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
            self.k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
            ok = self.k.Process32FirstW(handle, ctypes.byref(entry))
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            while ok:
                if entry.szExeFile.casefold() in ('chatgpt.exe', 'codex.exe'):
                    return True
                ok = self.k.Process32NextW(handle, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:
                raise ctypes.WinError(ctypes.get_last_error())
            return False
        finally:
            self.k.CloseHandle(handle)

    @contextlib.contextmanager
    def exclusive(self, path=None):
        path = Path(path or self.lock_path)
        if not path.parent.is_dir():
            raise RuntimeError('Known app profile not found; review compatibility')
        handle = self.k.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            self.k.CloseHandle(handle)

    @contextlib.contextmanager
    def closed(self):
        for _ in range(3):
            if self.running():
                raise RuntimeError('Desktop app is running; changes deferred')
            time.sleep(1)
        with self.exclusive():
            if self.running():
                raise RuntimeError('Desktop app restarted; changes deferred')
            yield


class Service:
    def __init__(self, state_path, data_dir, guard):
        self.state = Path(state_path)
        self.data = Path(data_dir)
        self.catalog_path = self.data / 'catalog.json'
        self.guard = guard
        self.data.mkdir(parents=True, exist_ok=True)

    def catalog(self):
        return parse(self.catalog_path.read_bytes()) if self.catalog_path.exists() else new_catalog()

    def status(self, phase, **details):
        atomic(self.data / 'status.json', encode({'phase': phase, 'pid': os.getpid(), 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), **details}))

    def backups(self):
        return sorted((self.data / 'backups').glob('*/receipt.json'))

    def pending(self):
        return any(parse(p.read_bytes())['phase'] == 'prepared' for p in self.backups())

    def recover(self):
        for path in self.backups():
            receipt = parse(path.read_bytes())
            if receipt['phase'] != 'prepared':
                continue
            directory = path.parent
            for target, before, after in [(self.state, 'state.before', 'state.after'), (Path(str(self.state) + '.bak'), 'fallback.before', 'state.after'), (self.catalog_path, 'catalog.before', 'catalog.after')]:
                current = target.read_bytes() if target.exists() else None
                old = (directory / before).read_bytes() if (directory / before).exists() else None
                new = (directory / after).read_bytes()
                if current not in (old, new):
                    raise RuntimeError('Interrupted transaction has newer external changes; manual review required')
            for target, name in [(self.state, 'state.before'), (Path(str(self.state) + '.bak'), 'fallback.before'), (self.catalog_path, 'catalog.before')]:
                if (directory / name).exists():
                    atomic(target, (directory / name).read_bytes())
                else:
                    target.unlink(missing_ok=True)
            receipt['phase'] = 'reverted-interruption'
            atomic(path, encode(receipt))

    def commit(self, raw, result, catalog, info):
        new = encode(result)
        directory = self.data / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True)
        fallback = Path(str(self.state) + '.bak')
        atomic(directory / 'state.before', raw)
        if fallback.exists():
            atomic(directory / 'fallback.before', fallback.read_bytes())
        if self.catalog_path.exists():
            atomic(directory / 'catalog.before', self.catalog_path.read_bytes())
        atomic(directory / 'state.after', new)
        atomic(directory / 'catalog.after', encode(catalog))
        receipt = {'phase': 'prepared', 'summary': info, 'stateHash': hashlib.sha256(new).hexdigest()}
        atomic(directory / 'receipt.json', encode(receipt))
        def unchanged(target, saved):
            current = target.read_bytes() if target.exists() else None
            expected = saved.read_bytes() if saved.exists() else None
            return current == expected
        if self.state.read_bytes() != raw or not unchanged(fallback, directory / 'fallback.before') or not unchanged(self.catalog_path, directory / 'catalog.before') or self.guard.running():
            receipt['phase'] = 'aborted-before-write'
            atomic(directory / 'receipt.json', encode(receipt))
            raise RuntimeError('State or process changed before commit; deferred')
        try:
            atomic(self.state, new)
            atomic(fallback, new)
            atomic(self.catalog_path, encode(catalog))
            if self.state.read_bytes() != new or fallback.read_bytes() != new:
                raise RuntimeError('Readback verification failed')
            receipt['phase'] = 'committed'
            atomic(directory / 'receipt.json', encode(receipt))
        except Exception:
            self.recover()
            raise
        return directory.name

    def cycle(self, apply=False):
        if self.pending() and not apply:
            self.status('recovery-pending-app-exit')
            return
        catalog = self.catalog()
        if catalog['paused'] and not self.pending():
            self.status('paused')
            return
        raw = self.state.read_bytes()
        state = parse(raw)
        if apply:
            with self.guard.closed():
                self.recover()
                if self.catalog()['paused']:
                    self.status('paused')
                    return
                raw = self.state.read_bytes()
                state = parse(raw)
                result, c, info = merge(state, self.catalog())
                if result != state:
                    info['backup'] = self.commit(raw, result, c, info)
                else:
                    atomic(self.catalog_path, encode(c))
                self.status('synchronized', projects=len(c['projects']), memberships=len(c['threads']), accounts=len(c['accounts']), **info)
        else:
            c = observe(state, catalog)
            if self.state.read_bytes() != raw:
                return
            if c != catalog or not self.catalog_path.exists():
                atomic(self.catalog_path, encode(c))
            self.status('watching-app-exit', projects=len(c['projects']), memberships=len(c['threads']), accounts=len(c['accounts']), conflicts=c['conflicts'])

    def watch(self):
        self.status('starting')
        previous = None
        applied = None
        while not (self.data / 'stop').exists():
            try:
                raw = self.state.read_bytes()
                if raw == previous:
                    running = self.guard.running()
                    if running:
                        self.cycle(False)
                        applied = None
                    elif applied != raw:
                        self.cycle(True)
                        applied = self.state.read_bytes()
                previous = raw
            except Exception as error:
                self.status('deferred-error', error=str(error))
            time.sleep(2)
        self.status('stopped')

    def rollback(self, backup):
        if not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', backup):
            raise ValueError('Use a backup ID from status/backups')
        directory = self.data / 'backups' / backup
        if parse((directory / 'receipt.json').read_bytes())['phase'] != 'committed':
            raise ValueError('Only a completed transaction can be rolled back')
        with self.guard.closed():
            self.recover()
            raw = self.state.read_bytes()
            state = parse(raw)
            current = scopes(state)
            before = scopes(parse((directory / 'state.before').read_bytes()))
            after = scopes(parse((directory / 'state.after').read_bytes()))
            for account, old in before.items():
                if account not in current:
                    raise ValueError('Account snapshot no longer present')
                for field in FIELDS:
                    if current[account].get(field) != after[account].get(field):
                        raise ValueError('Synchronized fields changed since backup; refusing stale rollback')
            for account, old in before.items():
                for field in FIELDS:
                    if field in old:
                        current[account][field] = copy.deepcopy(old[field])
                    else:
                        current[account].pop(field, None)
            catalog = self.catalog()
            catalog['paused'] = True
            receipt = self.commit(raw, state, catalog, {'rollbackOf': backup})
            self.status('rolled-back-paused', backup=receipt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['watch', 'status', 'preview', 'sync', 'pause', 'resume', 'exclude', 'include', 'backups', 'rollback'])
    p.add_argument('--data-dir', type=Path, default=Path.home() / '.codex' / 'project-follow')
    p.add_argument('--state', type=Path, default=Path.home() / '.codex' / '.codex-global-state.json')
    p.add_argument('--profile-lock', type=Path, default=Path(os.getenv('LOCALAPPDATA', '')) / 'Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Roaming/Codex/web/Codex/lockfile')
    p.add_argument('--project')
    p.add_argument('--backup')
    args = p.parse_args()
    service = Service(args.state, args.data_dir, WindowsGuard(args.profile_lock))
    if args.command == 'status':
        status = parse((args.data_dir / 'status.json').read_bytes()) if (args.data_dir / 'status.json').exists() else {'phase': 'not-started'}
        status['catalogPaused'] = service.catalog()['paused']
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    if args.command == 'preview':
        state = parse(args.state.read_bytes())
        _, c, info = merge(state, service.catalog())
        print(json.dumps({'projects': len(c['projects']), 'memberships': len(c['threads']), 'accounts': len(c['accounts']), **info}, ensure_ascii=False, indent=2))
        return
    if args.command == 'backups':
        print(json.dumps([{'id': x.parent.name, **parse(x.read_bytes())} for x in service.backups()], indent=2))
        return
    with service.guard.exclusive(args.data_dir / 'service.lock'):
        if args.command == 'watch':
            service.watch()
        elif args.command == 'sync':
            service.cycle(True)
        elif args.command == 'rollback':
            service.rollback(args.backup or '')
        else:
            if service.pending():
                raise RuntimeError('Recover the interrupted transaction after app exit before changing settings')
            catalog = service.catalog()
            if args.command in ('pause', 'resume'):
                catalog['paused'] = args.command == 'pause'
            else:
                if not args.project or args.project not in catalog['projects']:
                    raise ValueError('Provide a known project ID with --project')
                excluded = set(catalog['excludedProjects'])
                if args.command == 'exclude':
                    excluded.add(args.project)
                else:
                    excluded.discard(args.project)
                catalog['excludedProjects'] = sorted(excluded)
            atomic(service.catalog_path, encode(catalog))
            service.status('configuration-updated')


if __name__ == '__main__':
    main()
