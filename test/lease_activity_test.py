#!/usr/bin/env python3
"""AS-23 real CLI with a fake wall clock, isolated HOME, no remote services.

The wrapper chooses the legacy heartbeat on an old script so baseline failures
describe continuing renewal, not an unrecognised new command-line option.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(os.environ.get('AGENT_SYNC_SCRIPT') or Path(__file__).resolve().parents[1] /
              'plugins/agent-sync/skills/agent-sync/scripts/agent_sync.py')
WRAPPER = '''import importlib.util,inspect,sys
from datetime import datetime,timezone
spec=importlib.util.spec_from_file_location('under_test',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
now=float(sys.argv[2]);m.time.time=lambda: now
m.now_iso=lambda: datetime.fromtimestamp(now,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
args=sys.argv[3:]
if args[0]=='_release_failure':
 original=m.Sync.release
 m.Sync.release=lambda self,key: False if key.startswith(self.RESOURCE_PREFIX) else original(self,key)
 args=['release']+args[1:]
if args[0]=='_heartbeat':
 args=['renew']+(['--active-only'] if 'active_only' in inspect.signature(m.Sync.renew).parameters else [])+args[1:]
raise SystemExit(m.main(args))
'''


class Activity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-sync-activity-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.wrapper = Path(self.tmp.name) / 'cli.py'
        self.wrapper.write_text(WRAPPER)
        self.env = {k:v for k,v in os.environ.items() if k not in
                    ('CLAUDE_SESSION_ID','CLAUDE_CODE_SESSION_ID','CLAUDE_PROJECT_DIR')}
        self.env.update(HOME=str(self.home), AGENT_SYNC_RUN_ID='activity-a',
                        PYTHONDONTWRITEBYTECODE='1')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        (self.root/'.claude').mkdir()
        (self.root/'.claude/agent-sync.json').write_text(json.dumps({
            'backend':'fs','leaseTtlSeconds':90,'renewIntervalSeconds':10,
            'guardedFiles':['one.md','two.md']}))
        (self.root/'one.md').write_text('one\n')
        (self.root/'two.md').write_text('two\n')
        self.now = 1800000000

    def cli(self,*args,run='activity-a',status=0):
        p=subprocess.run([sys.executable,str(self.wrapper),str(SCRIPT),str(self.now),*args],
                         cwd=self.root,env=dict(self.env,AGENT_SYNC_RUN_ID=run),
                         text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,status,p.stdout+p.stderr)
        return p.stdout+p.stderr

    def tick(self,n=11):
        self.now+=n

    def lock(self,key):
        return json.loads((self.root/'.agent-sync/leases'/f'{key}.lock').read_text())

    def test_unrelated_heartbeats_stop_renewing_task(self):
        self.cli('acquire','TASK-A')
        for _ in range(12):
            self.tick(); self.cli('_heartbeat')
        self.assertNotIn('TASK-A',self.cli('whoami'))
        self.cli('guard','one.md',status=2)
        self.cli('acquire','TASK-A',run='activity-b')

    def test_active_guard_keeps_resource_and_its_task(self):
        self.cli('acquire','TASK-A')
        for _ in range(12):
            self.tick(); self.cli('guard','one.md'); self.cli('_heartbeat')
        self.assertIn('TASK-A',self.cli('whoami'))
        self.cli('acquire','TASK-A',run='activity-b',status=1)
        self.cli('acquire','TASK-B',run='activity-b')
        self.cli('guard','one.md',run='activity-b',status=2)

    def test_other_task_work_does_not_renew_bound_old_task(self):
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.tick(); self.cli('acquire','TASK-B')
        # A named renewal is an explicit assertion that TASK-B remains active.
        for _ in range(12):
            self.tick(); self.cli('renew','TASK-B'); self.cli('guard','two.md'); self.cli('_heartbeat')
        held=self.cli('whoami')
        self.assertNotIn('TASK-A',held)
        self.assertIn('TASK-B',held)

    def test_release_pushed_task_releases_its_resources_with_other_task_held(self):
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.cli('acquire','TASK-B'); self.cli('release','TASK-A')
        self.cli('acquire','TASK-C',run='activity-b')
        self.cli('guard','one.md',run='activity-b')
        self.assertIn('TASK-B',self.cli('whoami'))

    def test_explicit_renew_remains_intentional_keepalive(self):
        self.cli('acquire','TASK-A')
        for _ in range(12):
            self.tick(); self.cli('renew','TASK-A')
        self.assertIn('TASK-A',self.cli('whoami'))

    def test_partial_resource_release_is_not_a_successful_handoff(self):
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.cli('acquire','TASK-B')
        out=self.cli('_release_failure','TASK-A',status=1)
        self.assertIn('handoff is incomplete',out)
        self.assertIn('TASK-A',self.cli('whoami'))
        self.cli('release','TASK-A')
        self.cli('acquire','TASK-C',run='activity-b')
        self.cli('guard','one.md',run='activity-b')

    def test_unwritable_activity_cleanup_does_not_block_confirmed_release(self):
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.cli('acquire','TASK-B')
        directory=self.root/'.agent-sync/activity'
        directory.chmod(0o555)
        try:
            out=self.cli('release','TASK-A')
            self.assertIn('activity metadata remains',out)
            self.assertNotIn('Traceback',out)
            held=self.cli('whoami')
            self.assertNotIn('TASK-A',held)
            self.assertIn('TASK-B',held)
            self.tick(); self.cli('_heartbeat')
            self.assertNotIn('TASK-A',self.cli('whoami'))
        finally:
            directory.chmod(0o755)
        self.cli('acquire','TASK-C',run='activity-b')
        self.cli('guard','one.md',run='activity-b')

    def test_signal_help_states_dependency_record_is_not_an_ask(self):
        out=self.cli('signal','--help')
        self.assertIn('dependency state only',out)
        self.assertIn('com.submit',out)

    def test_unguarded_activity_is_not_task_activity(self):
        self.cli('acquire','TASK-A')
        for _ in range(12):
            self.tick(); self.cli('guard','unrelated.md'); self.cli('_heartbeat')
        self.assertNotIn('TASK-A',self.cli('whoami'))

    def test_legacy_missing_activity_needs_explicit_keepalive(self):
        self.cli('acquire','TASK-A')
        activity=self.root/'.agent-sync/activity'
        if activity.exists():
            for p in activity.iterdir(): p.unlink()
        before=self.lock('TASK-A')['ts']
        self.tick(); self.cli('_heartbeat')
        self.assertEqual(self.lock('TASK-A')['ts'],before)
        self.cli('renew','TASK-A')
        self.assertNotEqual(self.lock('TASK-A')['ts'],before)

    def test_malformed_activity_does_not_renew_or_erase_ownership(self):
        self.cli('acquire','TASK-A')
        activity=self.root/'.agent-sync/activity'
        if activity.exists():
            for p in activity.iterdir(): p.write_text('{broken')
        before=self.lock('TASK-A')['ts']
        self.tick(); self.cli('_heartbeat')
        self.assertEqual(self.lock('TASK-A')['ts'],before)
        self.cli('acquire','TASK-A',run='activity-b',status=1)
        self.cli('release','TASK-A')
        self.cli('acquire','TASK-A',run='activity-b')

    def test_real_hook_activity_filter_reaches_other_repository(self):
        # Real wall clock for the real hook's children, which run the shipped CLI.
        self.now=int(time.time())-30
        first=self.root
        self.cli('acquire','TASK-A')
        second=Path(self.tmp.name)/'other'
        second.mkdir(); subprocess.run(['git','init','-q',str(second)],check=True)
        (second/'.claude').mkdir()
        (second/'.claude/agent-sync.json').write_bytes((first/'.claude/agent-sync.json').read_bytes())
        self.root=second; self.cli('acquire','TASK-B')
        before=[json.loads((r/'.agent-sync/leases'/f'{k}.lock').read_text())['ts']
                for r,k in [(first,'TASK-A'),(second,'TASK-B')]]
        plugin=SCRIPT.parents[3]
        p=subprocess.run(['bash',str(plugin/'hooks/renew.sh')],cwd=first,
                         env=dict(self.env,CLAUDE_PROJECT_DIR=str(first),CLAUDE_PLUGIN_ROOT=str(plugin)),
                         input='{}',text=True,capture_output=True,timeout=20)
        self.assertEqual(p.returncode,0,p.stderr)
        after=[json.loads((r/'.agent-sync/leases'/f'{k}.lock').read_text())['ts']
               for r,k in [(first,'TASK-A'),(second,'TASK-B')]]
        self.assertEqual(after,before,'heartbeat renewed inactive owner or registered sibling')
        self.cli('release','--held','--session-repos')
        self.assertNotIn('TASK-B',self.cli('whoami'))
        self.root=first
        self.assertNotIn('TASK-A',self.cli('whoami'))

    def test_legacy_lock_contends_under_same_key(self):
        self.cli('acquire','TASK-A')
        lock=self.root/'.agent-sync/leases/TASK-A.lock'
        data=json.loads(lock.read_text()); data.pop('key',None)
        lock.write_text(json.dumps(data))
        self.cli('acquire','TASK-A',run='activity-b',status=1)
        self.cli('release','TASK-A')
        self.cli('acquire','TASK-A',run='activity-b')

    def test_git_plane_activity_handoff_and_inactive_expiry(self):
        remote=Path(self.tmp.name)/'remote.git'
        subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        subprocess.run(['git','remote','add','origin',str(remote)],cwd=self.root,check=True)
        config=self.root/'.claude/agent-sync.json'
        data=json.loads(config.read_text()); data['leaseBackend']='git'
        config.write_text(json.dumps(data))
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.cli('acquire','TASK-B'); self.cli('release','TASK-A')
        self.cli('acquire','TASK-C',run='activity-b')
        self.cli('guard','one.md',run='activity-b')
        self.cli('acquire','TASK-B',run='activity-b',status=1)
        self.cli('release','TASK-C',run='activity-b')
        for _ in range(12):
            self.tick(); self.cli('_heartbeat')
        self.assertNotIn('TASK-B',self.cli('whoami'))
        self.cli('acquire','TASK-B',run='activity-b')
        self.cli('release','--held',run='activity-b')
        refs=subprocess.check_output(['git','--git-dir',str(remote),'for-each-ref',
                                      '--format=%(refname)','refs/agent-sync/leases/'],text=True)
        self.assertEqual(refs.strip(),'')

    def test_expired_claim_cannot_be_revived_by_guard_or_heartbeat(self):
        self.cli('acquire','TASK-A'); self.cli('guard','one.md')
        self.tick(100); self.cli('_heartbeat')
        self.cli('guard','one.md',status=2)
        self.cli('renew','TASK-A')
        self.assertNotIn('TASK-A',self.cli('whoami'))
        self.cli('acquire','TASK-A',run='activity-b')
        self.cli('guard','one.md',run='activity-b')

    def test_ambiguous_tasks_do_not_all_become_active(self):
        self.cli('acquire','TASK-A'); self.cli('acquire','TASK-B')
        for _ in range(7):
            self.tick(); self.cli('guard','one.md'); self.cli('_heartbeat')
        self.assertEqual(self.lock('TASK-A')['ts'],self.lock('TASK-B')['ts'])
        self.assertLess(self.lock('TASK-A')['ts'],'2027-01-15T08:00:30Z')

    def test_live_resource_requires_reacquire_after_all_tasks_expire(self):
        self.cli('acquire','TASK-A'); self.cli('acquire','TASK-B')
        for _ in range(7):
            self.tick(); self.cli('guard','one.md'); self.cli('_heartbeat')
        self.tick(33)
        self.cli('guard','one.md',status=2)
        self.cli('acquire','TASK-C',run='activity-b')
        self.cli('guard','one.md',run='activity-b',status=2)
        self.cli('acquire','TASK-D')
        self.cli('guard','one.md')


if __name__=='__main__':
    unittest.main()
