# Provenance

Parts of this plugin are derived from
[pegasus-isi/workflow-monitor](https://github.com/pegasus-isi/workflow-monitor)
(Apache-2.0), branch `monitord-plugin-adapter`, pinned at commit
**`de0c9482ec8ce5ccf9ccbd670d255d83225fe21f`**.

| File here | Upstream source | Adaptation |
|---|---|---|
| `src/monitord_wfevents/htcondor_poll.py` | `src/workflow_monitor/htcondor_poll.py` | **Verbatim** + 4-line provenance header |
| `src/monitord_wfevents/plugin.py` | `src/workflow_monitor/monitord_plugin.py` | Renames (`WorkflowMonitorPlugin`→`WfEventsPlugin`, `NAME` `wfmonitor`→`wfevents`, default output `monitord-events.jsonl`→`wfevents.jsonl`, log prefixes); module docstring rewritten; comments referencing workflow-monitor internals reworded; the lazy `from .event_log import EventLogger` replaced by module-level copies of its three stateless fingerprint staticmethods (`_condor_fingerprint` event_log.py:428-444, `_history_fingerprint` :460-467, `_pool_fingerprint` :483-492), bodies byte-identical. **Deliberate divergence (v0.2.0):** `stop()` synthesizes a terminal `workflow_end` record (EventLogger shape) once `xwf.end` was seen — upstream wfmonitor relies on the `--serve` EventLogger for that marker; this plugin is consumed without `--serve`, and `workflow-monitor --remote` keys session termination on `workflow_end` |
| `tests/conftest.py`, `tests/test_tick_condor.py` | `tests/test_monitord_plugin_tick.py` | Helpers split into conftest; imports/filenames renamed; the upstream `--no-condor-poll` CLI-flag test dropped (that CLI lives in workflow-monitor) |

`tests/synthetic_run.py`, `tests/test_translation.py`, and
`tests/test_replay_smoke.py` are original to this repo.

## Resyncing against upstream

To see what (if anything) drifted, diff the vendored file against the pinned
upstream blob — the only expected difference is the provenance header:

```bash
git -C /path/to/workflow-monitor show \
  de0c9482ec8c:src/workflow_monitor/htcondor_poll.py \
  | diff - src/monitord_wfevents/htcondor_poll.py
```

When resyncing to a newer upstream commit, repeat for
`monitord_plugin.py`/`plugin.py` (expect the adaptation diff above), update
the pinned sha in this file, in the source-file headers, and in the root
`NOTICE`.
