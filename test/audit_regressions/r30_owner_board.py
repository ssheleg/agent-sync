#!/usr/bin/env python3
# shared-mechanism: sshlg-skills/owner-board-controls-v1
# diverges: agent-sync adapter uses _guarded/check_owner_board and its errors list.
"""R30: declaration parsing, escaped-pipe controls, and the real validator adapter."""
from pathlib import Path
import importlib.util
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'test'))
from owner_board import board_errors, check

BASE='| ID | What |\n|---|---|\n| AS-1 | healthy |\n'
checks=0

def expect(text, needle=None):
    global checks
    errors=board_errors(text)
    assert (any(needle in e for e in errors) if needle else not errors), (needle,errors)
    checks+=1

expect(BASE)
expect(BASE+'\n| ID | What |\n|---|---|\n| AS-1 | again |\n','duplicate ID')
expect(BASE.replace('healthy','text | shifted'),'row width')
expect(BASE.replace('healthy',r'text \| content'))
expect(BASE.replace('healthy',r'text \\| separator'),'row width')
expect(BASE.replace('AS-1','**AS-1**'))
expect(BASE.replace('AS-1','AW-0906-desc'))
expect(BASE.replace('AS-1','AW-0906-refusal'))
expect(BASE.replace('AS-1','AW-0906-desc')+BASE.replace('AS-1','AW-0906-desc'),'duplicate ID')
expect(BASE.replace('AS-1','not-an-id'),'invalid ID')
expect(BASE.replace('|---|---|\n',''),'matching separator')
expect(BASE.replace('|---|---|','|---|---|---|'),'matching separator')
expect(BASE.replace('| AS-1 |','\n| AS-1 |'),'without ID header')
expect(BASE.replace('| AS-1 |','<!-- break -->\n| AS-1 |'),'without ID header')
expect('| AS-1 | orphan |\n','without ID header')
expect(BASE.replace('| ID |','| Row |'),'Row header unsupported')
expect(BASE.replace('healthy','citation AS-1, not a declaration'))
expect(BASE+'\n```md\n| AS-1 | fenced example |\n```\n')
expect(BASE+'\n| ID | What | More |\n|---|---|---|\n| AS-2 | second shape | accepted |\n')
expect(BASE.replace('healthy |','healthy'),'row width')
expect('','no declared rows')
expect(BASE.replace('| AS-1 | healthy |',''),'no declared rows')
expect((ROOT/'docs/evidence/backlog.md').read_text())
with tempfile.TemporaryDirectory(prefix='r30-board-check-') as tmp:
    out=[];check(Path(tmp),out.append);assert any('board missing' in x for x in out);checks+=1
    board=Path(tmp)/'docs/evidence/backlog.md';board.parent.mkdir(parents=True);board.write_bytes(b'\xff')
    out=[];check(Path(tmp),out.append);assert any('board unreadable' in x for x in out);checks+=1
# Exercise the real adapter and its existing error collection, not a duplicate wrapper.
spec=importlib.util.spec_from_file_location('r30_validator',ROOT/'test/validate.py')
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
with tempfile.TemporaryDirectory(prefix='r30-board-adapter-') as tmp:
    board=Path(tmp)/'docs/evidence/backlog.md';board.parent.mkdir(parents=True)
    validator.ROOT=Path(tmp)
    for text,needle in [(BASE+BASE,'duplicate ID'),(BASE.replace('healthy','extra | shifted'),'row width')]:
        board.write_text(text);validator.errors=[]
        validator._guarded(validator.check_owner_board)
        assert any(needle in e for e in validator.errors),validator.errors
        checks+=1
    release=Path(tmp)/'.github/workflows/release.yml';release.parent.mkdir(parents=True)
    source=(ROOT/'.github/workflows/release.yml').read_text()
    board.write_text(BASE)
    for value,needle in [(source,None),
        (source.replace('git merge-base --is-ancestor','git merge-base-removed --is-ancestor',1),'release ancestry guard'),
        (source.replace('fetch-depth: 0','fetch-depth: 1',1),'release full-history checkout')]:
        release.write_text(value);validator.errors=[]
        validator._guarded(validator.check_owner_board)
        assert (any(needle in e for e in validator.errors) if needle else not validator.errors),validator.errors
        checks+=1
print(f'PASS: owner board {checks} focused controls')
