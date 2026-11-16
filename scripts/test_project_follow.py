"""Behavioral tests use synthetic data and temporary directories only."""
import contextlib
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import project_follow as f

A, B, C = 'a' * 64, 'b' * 64, 'c' * 64
HOST = 'local:C:\\Synthetic\\.codex'


def project(name='Alpha', root='C:\\Synthetic\\Alpha'):
    return {'name': name, 'rootPaths': [root], 'createdAt': 1, 'updatedAt': 2}


def globals_a():
    return {f.PROJECTS: {'p1': project()}, f.THREADS: {'t1': {'projectKind': 'local', 'projectId': 'p1'}, 'cloud': {'projectKind': 'cloud', 'projectId': 'cloud1'}}, f.HOSTS: {'t1': 'local'}, f.ORDER: ['p1'], f.MAPPING: {HOST: {'p1': 'native1'}}, f.MIGRATION: {HOST: {'version': 1, 'projectsMigrated': True, 'threadAssignmentsMigrated': True, 'pendingThreadAssignmentIds': []}}, 'unrelated': {'date': '2000-01-01T00:00:00.000Z', 'nested': [1, False, None]}}


def state():
    return {f.ACCOUNT: {'owner': B, 'inactive': {A: {'globals': globals_a(), 'atoms': {'preserve': True}}}}, 'activePreference': 'preserve'}


def switch(s, target):
    s = copy.deepcopy(s)
    account = s[f.ACCOUNT]
    old = account['owner']
    current = {k: s.pop(k) for k in list(s) if k != f.ACCOUNT}
    if old:
        account['inactive'][old] = {'globals': current, 'atoms': {'preserve': True}}
    s.update(copy.deepcopy(account['inactive'].get(target, {'globals': {}})['globals']))
    account['owner'] = target
    return s


class Guard:
    def __init__(self, running=False):
        self.is_running = running

    def running(self):
        return self.is_running

    @contextlib.contextmanager
    def closed(self):
        if self.running():
            raise RuntimeError('running')
        yield


class FollowTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows lock integration')
    def test_native_exclusive_lock_and_process_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = f.WindowsGuard(Path(directory) / 'profile.lock')
            self.assertIsInstance(guard.running(), bool)
            with guard.exclusive():
                with self.assertRaises(OSError):
                    with guard.exclusive():
                        pass
            with guard.exclusive():
                pass

    def test_first_transfer_and_three_accounts(self):
        original = state()
        result, catalog, info = f.merge(original, f.new_catalog())
        self.assertEqual(info['addedProjects'], 1)
        self.assertEqual(info['addedMemberships'], 1)
        self.assertEqual(result[f.PROJECTS], globals_a()[f.PROJECTS])
        self.assertNotIn('cloud', result[f.THREADS])
        self.assertEqual(f.strip_fields(original), f.strip_fields(result))
        self.assertEqual(original, state())
        switched = switch(result, C)
        result, catalog, _ = f.merge(switched, catalog)
        self.assertIn('p1', result[f.PROJECTS])
        self.assertEqual(len(catalog['accounts']), 3)
        self.assertEqual(catalog['accounts'][C]['removedProjects'], [])
        result2, catalog2, info = f.merge(result, catalog)
        self.assertEqual(result, result2)
        self.assertEqual(catalog, catalog2)
        self.assertEqual(info['addedProjects'], 0)

    def test_future_additions_and_pending_migration(self):
        result, catalog, _ = f.merge(state(), f.new_catalog())
        result[f.PROJECTS]['p2'] = project('Beta', 'D:\\Synthetic\\Beta')
        result[f.THREADS]['t2'] = {'projectKind': 'local', 'projectId': 'p2'}
        result, catalog, info = f.merge(result, catalog)
        inactive = f.scopes(result)[A]
        self.assertIn('p2', inactive[f.PROJECTS])
        self.assertIn('t2', inactive[f.THREADS])
        self.assertFalse(inactive[f.MIGRATION][HOST]['projectsMigrated'])
        self.assertFalse(inactive[f.MIGRATION][HOST]['threadAssignmentsMigrated'])
        self.assertIn('t2', inactive[f.MIGRATION][HOST]['pendingThreadAssignmentIds'])

    def test_local_removals_and_readd(self):
        result, catalog, _ = f.merge(state(), f.new_catalog())
        del result[f.PROJECTS]['p1']
        del result[f.THREADS]['t1']
        catalog = f.observe(result, catalog)
        self.assertEqual(catalog['accounts'][B]['removedProjects'], [])
        result, catalog, _ = f.merge(result, catalog)
        self.assertNotIn('p1', result[f.PROJECTS])
        self.assertIn('p1', f.scopes(result)[A][f.PROJECTS])
        for _ in range(3):
            result, catalog, _ = f.merge(switch(result, A), catalog)
            result, catalog, _ = f.merge(switch(result, B), catalog)
            self.assertNotIn('p1', result[f.PROJECTS])
        result[f.PROJECTS]['p1'] = project()
        result[f.THREADS]['t1'] = {'projectKind': 'local', 'projectId': 'p1'}
        result, catalog, _ = f.merge(result, catalog)
        self.assertEqual(catalog['accounts'][B]['removedProjects'], [])

    def test_membership_removal_and_reassignment(self):
        result, catalog, _ = f.merge(state(), f.new_catalog())
        del result[f.THREADS]['t1']
        result, catalog, _ = f.merge(result, catalog)
        self.assertNotIn('t1', result[f.THREADS])
        self.assertIn('t1', f.scopes(result)[A][f.THREADS])
        result[f.THREADS]['t1'] = {'projectKind': 'cloud', 'projectId': 'other'}
        result, catalog, info = f.merge(result, catalog)
        self.assertEqual(result[f.THREADS]['t1']['projectKind'], 'cloud')

    def test_known_membership_move_supplies_new_accounts_only(self):
        result, catalog, _ = f.merge(state(), f.new_catalog())
        result[f.PROJECTS]['p2'] = project('Beta', 'D:\\Beta')
        result[f.THREADS]['t1'] = {'projectKind': 'local', 'projectId': 'p2'}
        result, catalog, _ = f.merge(result, catalog)
        self.assertEqual(f.scopes(result)[A][f.THREADS]['t1']['projectId'], 'p1')
        result, catalog, _ = f.merge(switch(result, C), catalog)
        self.assertEqual(result[f.THREADS]['t1']['projectId'], 'p2')

    def test_explicit_projectless_and_known_optional_migration_fields(self):
        s = state()
        s[f.PROJECTLESS] = ['t1']
        source_migration = f.scopes(s)[A][f.MIGRATION][HOST]
        del source_migration['pendingThreadAssignmentIds']
        source_migration['threadAssignmentsReadMigrated'] = True
        result, _, _ = f.merge(s, f.new_catalog())
        self.assertNotIn('t1', result.get(f.THREADS, {}))
        self.assertEqual(result[f.PROJECTLESS], ['t1'])

    def test_migration_queues_match_membership_host(self):
        s = state()
        f.scopes(s)[A][f.HOSTS]['t1'] = 'ssh-server'
        result, _, _ = f.merge(s, f.new_catalog())
        self.assertNotIn('t1', result[f.MIGRATION][HOST]['pendingThreadAssignmentIds'])

    def test_identity_conflicts_and_alias(self):
        s = state()
        s[f.PROJECTS] = {'p1': project('Different', 'D:\\Other')}
        result, _, info = f.merge(s, f.new_catalog())
        self.assertEqual(result[f.PROJECTS]['p1'], s[f.PROJECTS]['p1'])
        self.assertNotIn('t1', result.get(f.THREADS, {}))
        self.assertTrue(any(x['kind'] == 'project-id' for x in info['conflicts']))
        s[f.PROJECTS] = {'alias': project('Alias', 'c:/synthetic/ALPHA/')}
        result, _, info = f.merge(s, f.new_catalog())
        self.assertEqual(set(result[f.PROJECTS]), {'alias'})
        self.assertNotIn('t1', result.get(f.THREADS, {}))
        self.assertTrue(any(x['kind'] == 'project-path-alias' for x in info['conflicts']))

    def test_owner_stale_snapshot_signedout_and_exclusions(self):
        result, catalog, _ = f.merge(state(), f.new_catalog())
        result[f.ACCOUNT]['inactive'][B] = {'globals': {f.PROJECTS: {'stale': project()}}, 'atoms': {}}
        result, catalog, _ = f.merge(result, catalog)
        self.assertNotIn('stale', catalog['projects'])
        signedout = switch(result, None)
        signedout[f.PROJECTS] = {'unowned': project()}
        _, catalog, _ = f.merge(signedout, catalog)
        self.assertNotIn('unowned', catalog['projects'])
        catalog['excludedProjects'] = ['p1']
        result, _, _ = f.merge(switch(result, C), catalog)
        self.assertNotIn('p1', result.get(f.PROJECTS, {}))

    def test_schema_rejection(self):
        for mutate in [lambda s: s.update({f.PROJECTS: []}), lambda s: s[f.ACCOUNT].update(owner='invalid-scope'), lambda s: s.update({f.MIGRATION: {HOST: {'version': 2}}})]:
            s = state()
            mutate(s)
            with self.assertRaises(ValueError):
                f.merge(s, f.new_catalog())
        with self.assertRaises(ValueError):
            f.parse(b'{"a":1,"a":2}')

    def test_conflicting_assignment_never_borrows_other_host(self):
        s = state()
        del f.scopes(s)[A][f.HOSTS]['t1']
        s[f.PROJECTS] = {'p2': project('Beta', 'D:\\Synthetic\\Beta')}
        s[f.THREADS] = {'t1': {'projectKind': 'local', 'projectId': 'p2'}}
        s[f.HOSTS] = {'t1': 'local:other'}
        result, catalog, _ = f.merge(s, f.new_catalog())
        result, catalog, _ = f.merge(switch(result, C), catalog)
        self.assertEqual(result[f.THREADS]['t1']['projectId'], 'p1')
        self.assertNotIn('t1', result.get(f.HOSTS, {}))

    def test_target_metadata_and_host_conflicts_are_reported(self):
        s = state()
        s.update(copy.deepcopy(globals_a()))
        s[f.PROJECTS]['p1']['name'] = 'My local rename'
        s[f.HOSTS]['t1'] = 'other-host'
        result, _, info = f.merge(s, f.new_catalog())
        self.assertEqual(result[f.PROJECTS]['p1']['name'], 'My local rename')
        self.assertEqual(result[f.HOSTS]['t1'], 'other-host')
        self.assertTrue({'project-metadata', 'membership-host'} <= {v['kind'] for v in info['conflicts']})

    def service_fixture(self, directory, running=False):
        root = Path(directory)
        path = root / 'global.json'
        path.write_bytes(f.encode(state()))
        Path(str(path) + '.bak').write_bytes(b'{"oldFallback":true}')
        return f.Service(path, root / 'data', Guard(running))

    def test_transaction_and_running_deferral(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory, True)
            original = service.state.read_bytes()
            with self.assertRaises(RuntimeError):
                service.cycle(True)
            self.assertEqual(service.state.read_bytes(), original)
            service.guard.is_running = False
            service.cycle(True)
            self.assertEqual(len(service.backups()), 1)
            self.assertEqual(service.state.read_bytes(), Path(str(service.state) + '.bak').read_bytes())
            receipt = service.backups()[0]
            self.assertEqual((receipt.parent / 'state.before').read_bytes(), original)
            service.cycle(True)
            self.assertEqual(len(service.backups()), 1)

    def test_partial_write_failure_reverts(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            old = service.state.read_bytes()
            oldbak = Path(str(service.state) + '.bak').read_bytes()
            real = f.atomic
            failed = False
            def injected(path, data):
                nonlocal failed
                if str(path) == str(service.state) + '.bak' and not failed:
                    failed = True
                    raise OSError('synthetic interruption')
                real(path, data)
            with patch.object(f, 'atomic', injected), self.assertRaises(OSError):
                service.cycle(True)
            self.assertEqual(service.state.read_bytes(), old)
            self.assertEqual(Path(str(service.state) + '.bak').read_bytes(), oldbak)
            self.assertEqual(f.parse(service.backups()[0].read_bytes())['phase'], 'reverted-interruption')

    def test_interruption_recovery_and_external_change_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            service.cycle(True)
            receipt_path = service.backups()[0]
            receipt = f.parse(receipt_path.read_bytes())
            receipt['phase'] = 'prepared'
            f.atomic(receipt_path, f.encode(receipt))
            service.recover()
            self.assertEqual(service.state.read_bytes(), (receipt_path.parent / 'state.before').read_bytes())
            service.cycle(True)
            (path,) = set(service.backups()) - {receipt_path}
            rec = f.parse(path.read_bytes()); rec['phase'] = 'prepared'
            f.atomic(path, f.encode(rec))
            changed = f.parse(service.state.read_bytes()); changed['external'] = True
            f.atomic(service.state, f.encode(changed))
            with self.assertRaises(RuntimeError):
                service.recover()
            self.assertTrue(f.parse(service.state.read_bytes())['external'])

    def test_precommit_concurrent_change(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            real = f.atomic
            def injected(path, data):
                real(path, data)
                if Path(path).name == 'receipt.json' and f.parse(data)['phase'] == 'prepared':
                    modified = state(); modified['external'] = 99
                    real(service.state, f.encode(modified))
            with patch.object(f, 'atomic', injected), self.assertRaises(RuntimeError):
                service.cycle(True)
            self.assertEqual(f.parse(service.state.read_bytes())['external'], 99)
            self.assertNotIn(f.PROJECTS, f.parse(service.state.read_bytes()))

    def test_live_observation_does_not_break_pending_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            s = state(); s[f.PROJECTS] = {'p1': project('Conflict', 'D:\\Different')}
            f.scopes(s)[A][f.PROJECTS]['p2'] = project('Beta', 'D:\\Beta')
            f.atomic(service.state, f.encode(s))
            service.cycle(False)
            before_catalog = service.catalog_path.read_bytes()
            service.cycle(True)
            path = service.backups()[0]
            receipt = f.parse(path.read_bytes()); receipt['phase'] = 'prepared'
            f.atomic(path, f.encode(receipt))
            f.atomic(service.catalog_path, before_catalog)
            service.guard.is_running = True
            service.cycle(False)
            self.assertEqual(service.catalog_path.read_bytes(), before_catalog)
            self.assertEqual(f.parse((service.data / 'status.json').read_bytes())['phase'], 'recovery-pending-app-exit')
            service.guard.is_running = False
            service.recover()
            self.assertEqual(service.state.read_bytes(), (path.parent / 'state.before').read_bytes())

    def test_precommit_fallback_change_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            original = service.state.read_bytes()
            real = f.atomic
            fallback = Path(str(service.state) + '.bak')
            def injected(path, data):
                real(path, data)
                if Path(path).name == 'receipt.json' and f.parse(data)['phase'] == 'prepared':
                    real(fallback, b'{"externalBackup":99}')
            with patch.object(f, 'atomic', injected), self.assertRaises(RuntimeError):
                service.cycle(True)
            self.assertEqual(service.state.read_bytes(), original)
            self.assertEqual(fallback.read_bytes(), b'{"externalBackup":99}')

    def test_rollback_preserves_unrelated_and_refuses_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            service.cycle(True)
            backup = service.backups()[0].parent.name
            current = f.parse(service.state.read_bytes()); current['newSetting'] = 'keep'
            f.atomic(service.state, f.encode(current))
            service.rollback(backup)
            rolled = f.parse(service.state.read_bytes())
            self.assertEqual(rolled['newSetting'], 'keep')
            self.assertNotIn(f.PROJECTS, rolled)
            self.assertTrue(service.catalog()['paused'])
        with tempfile.TemporaryDirectory() as directory:
            service = self.service_fixture(directory)
            service.cycle(True)
            backup = service.backups()[0].parent.name
            current = f.parse(service.state.read_bytes()); current[f.PROJECTS]['p1']['name'] = 'Edited'
            f.atomic(service.state, f.encode(current))
            with self.assertRaises(ValueError):
                service.rollback(backup)
            self.assertEqual(f.parse(service.state.read_bytes()), current)


if __name__ == '__main__':
    unittest.main(verbosity=2)
