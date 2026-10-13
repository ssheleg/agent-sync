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

Current state: release plan committed before version edits. PR35 is still under
hosted verification; this stack does not claim integration or publication.
Next: prepare synchronized version metadata and run the bounded release checks.
No native host installation or model outcome is inferred from local fixtures.

Skills: make-skill (release construction), task-pipeline (delivery), agent-sync
(local advisory ownership), evidence-docs (source and check receipts).
