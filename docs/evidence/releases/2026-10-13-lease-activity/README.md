# agent-sync 1.22.2 release packet

Objective: publish the independently verified FW04 activity/lease fixes and AS-24
protocol clarification. The operator authorized patch publication on 2026-10-13.
The hub coordinator owns subsequent family pins and global installation.

Source: [PR 35](https://github.com/ssheleg/agent-sync/pull/35), reviewed implementation
`e96fb3914097889a4167715f98bb2134e10d1fd0`, receipt-only head
`0172dcc1658880486b836e4ebc683d0fc5d90d12`. [Source packet](../../lease-activity-20261013/README.md)
records independent bounded PASS, full 20-suite gate and 75 negative plants.

## Requirements and sequence

1. Synchronize package, marketplace, plugin, skill, script VERSION and skill-card
   versions; add changelog and verification-ledger entries without altering history.
2. Rebase this isolated stacked release branch after PR35's ordinary merge.
   Verify that the source implementation bytes are the reviewed ones except VERSION.
3. Run structural/version validation, 19 focused lease-activity cases, installer,
   both strict manifests and npm payload dry-run. Reuse the unchanged source's full
   gate; actual candidate CI and tag release checks remain mandatory and unchanged.
4. Review the frozen release diff, push a draft PR, promote once after checks,
   require genuine eligible CI, normal exact-head squash merge.
5. Create only a new annotated v1.22.2 on the merged canonical source, verify its
   peeled remote SHA, watch the existing release workflow, and read the exact npm
   version and published payload. Report source/tag/run to the hub coordinator.

Current state: PR35 merged normally at `8c9303016ff32d1c4ef2a736e541217d8de15233`
after [eligible run 38340047156](https://github.com/ssheleg/agent-sync/actions/runs/38340047156)
succeeded. The two local release commits were rebased onto that canonical main;
the resulting tree exactly matched the independently reviewed packaging tree
`8c45b4f2b8b04c73e341af416e5528a94b69e023`. This receipt update changes no payload.
Next: push the frozen candidate, promote its draft once, then follow the normal
merge/tag/publication checks. No package publication is claimed yet.
No native host installation or model outcome is inferred from local fixtures.

Skills: make-skill (release construction), task-pipeline (delivery), agent-sync
(local advisory ownership), evidence-docs (source and check receipts).

## Bounded packaging checks

`python3 test/validate.py` exited 0 and printed
`PASS: agent-sync v1.22.2 — all checks green`. It disclosed the package ahead of
the latest tag as release preparation and routed-trigger coverage as unlooked
outside the umbrella. `python3 test/lease_activity_test.py`: **19/19**, exit 0.
`node test/installer_test.js`: **11/11**, exit 0, all owned temporary HOMEs removed.
Both strict Claude manifests, npm payload dry-run and `git diff --check` pass.
The prior source gate's 20 suites and 75 negative plants were not repeated for
this metadata-only packaging change; hosted candidate/release checks remain live.
