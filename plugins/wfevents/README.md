# wfevents — standalone monitord → workflow-monitor JSONL plugin

A [pegasus-monitord entry-point plugin](../../docs/PLUGIN-AUTHORING.md) that
translates the live stampede event stream into
[workflow-monitor](https://github.com/pegasus-isi/workflow-monitor)-native
JSONL records, written to **`wfevents.jsonl`**. With the plugin host's
`tick()` support it can also absorb the HTCondor queue/history/pool polling
that `workflow-monitor --serve` performs — all from inside the monitord
process, no extra daemon.

It is a standalone extraction of workflow-monitor's `wfmonitor` adapter
(pinned at `de0c9482ec8c`, branch `monitord-plugin-adapter` — see
[`SOURCES.md`](SOURCES.md)) with zero runtime dependencies. The record schema
is identical; workflow-monitor's `DATA_SOURCES.md` is the authoritative
contract.

## Requirements

- A Pegasus build with the monitord plugin host: branch
  [`monitord-plugin-system`](https://github.com/pegasus-isi/pegasus/tree/monitord-plugin-system),
  minimum commit `25b37965e44fb6f7d950997d07a1ceb701b2a0d1` (adds `tick()`,
  required for `condor_poll`). The replay/recovery `restart` signal and
  host-side event filtering need branch commit `e7da14941` (2026-07-02) or
  newer; the plugin degrades gracefully without them.
- Python ≥ 3.9. For condor polling: the HTCondor CLI tools on `PATH`
  (subprocess fallback) or the optional `htcondor` Python bindings
  (`pip install 'pegasus-monitord-plugin-wfevents[htcondor]'`).

## Install

Install into the interpreter that `pegasus-monitord` resolves (on a typical
submit node, the system `python3`):

```bash
python3 -m pip install --user \
  'git+https://github.com/mjstealey/pegasus-monitord-plugins.git#subdirectory=plugins/wfevents'
```

Verify the entry point is discoverable by that same interpreter:

```bash
python3 -c "from importlib.metadata import entry_points; \
  eps = {e.name: e for e in entry_points(group='pegasus.monitord.plugins')}; \
  eps['wfevents'].load(); print('wfevents OK')"
```

## Configure (per run, in `pegasus.properties`)

```properties
pegasus.monitord.plugins.wfevents.enabled = true
pegasus.monitord.plugins.wfevents.events_path = /abs/path/to/run/wfevents.jsonl
# optional: poll HTCondor from inside monitord via the plugin host's tick()
pegasus.monitord.plugins.wfevents.tick_interval = 5
pegasus.monitord.plugins.wfevents.condor_poll = true
# with condor_poll: give stop() room for the final flush (worst case ~42 s)
pegasus.monitord.plugins.wfevents.join_timeout = 60
```

| Key (`pegasus.monitord.plugins.wfevents.`) | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Required `true` to load the plugin (host-reserved) |
| `events_path` | `./wfevents.jsonl` | Output JSONL path. Relative to monitord's working directory — **use an absolute path** |
| `restart` | `false` | `true` truncates the file at start; default appends. Newer hosts also signal replay/recovery via the `restart` keyword of `start()`, which truncates regardless of this key |
| `tick_interval` | `0` | Host-reserved; > 0 enables `tick()` (seconds). Required for `condor_poll` |
| `condor_poll` | `false` | Poll condor_q / condor_history / condor_status from `tick()`, emitting `htcondor_poll` / `htcondor_history` / `pool_status` events (fingerprint-deduped) |
| `schedd`, `collector` | — | Non-default pool targets |
| `token_path`, `cert_path`, `key_path`, `password_file` | — | HTCondor credential passthroughs (not needed on FS-auth pools) |
| `queue_size` | `10000` | Host-reserved: bounded event queue, drop-on-overflow |
| `join_timeout` | `10.0` | Host-reserved: bounds worker shutdown **and** `stop()`. **Set `60` with `condor_poll`** — the final flush needs ~42 s worst case; flush steps that no longer fit the budget are skipped (the terminal `workflow_end` is never sacrificed) |
| `events` | *(unset)* | Host-reserved override of the plugin's declared event filter. **Leave unset**: the plugin already filters to exactly the events it translates; overriding can silently disable condor polling (`wf.plan` carries the submit dir) or the job roster |
| `overflow_policy` | `drop-newest` | Host-reserved. Leave the default: `drop-oldest` can evict roster events during the startup burst, truncating `jobs_init` |

## Consume with the workflow-monitor TUI

Live, over SSH, from any machine that can reach the submit node:

```bash
workflow-monitor --remote user@submit-host:/abs/path/to/run/wfevents.jsonl --sync-interval 5
```

(The TUI does an initial `ssh cat`, then incremental `ssh tail -c +offset`
syncs; it is filename-agnostic — the first record is always `workflow_start`,
which is what it keys on.)

Local replay of a finished run:

```bash
workflow-monitor --replay /path/to/wfevents.jsonl --speed 4
```

**Double-poll caveat:** if `condor_poll = true` here, run any standalone
`workflow-monitor --serve` with `--no-condor-poll` so the schedd is not
polled from two places.

## Behavior notes

- **Terminal marker**: once the workflow terminates (`xwf.end`), the plugin's
  `stop()` appends a final `workflow_end` record (after the last condor
  flush) with `wf_state`/`wf_status`/`wf_end`/`total_jobs`/`done`/`failed`/
  `elapsed`. This is what makes a `workflow-monitor --remote` session exit on
  its own a couple of seconds after completion. No `workflow_end` is written
  if monitord dies mid-run, so a viewer cannot be told a half-finished run
  completed.

- **`stop()` is on a budget**: the plugin host bounds `stop()` with
  `join_timeout` and abandons anything still running past it. The final
  condor flush therefore runs each step (queue → history → pool) only if its
  worst case — the condor CLI timeout — still fits the remaining budget,
  always reserving time for `workflow_end`. At the host-default
  `join_timeout = 10` the flush is skipped entirely (with a startup warning);
  set `join_timeout = 60` with `condor_poll` to guarantee the full flush.
- Condor cadence: queue polled per due tick behind an adaptive backoff;
  history at ≥ 3× the tick base (min 10 s); pool at ≥ 5× (min 15 s); one
  final flush of all three on `stop()` (budget permitting, see above).
  Queries are always scoped to the workflow (a `Cmd` prefix match on the
  planner's submit dir) and never run before `wf.plan` supplies it.
- **Event filter**: the plugin declares an `event_filter` covering exactly
  the events it translates, so newer hosts skip the bulk planner-roster
  events (`task.edge`, `job.edge`) before the per-plugin payload copy —
  keeping large-workflow replays cheap. Older hosts ignore the attribute and
  deliver everything (harmless, just more copying).
- `wf_uuid` is the first `xwf_id` seen by this plugin instance. Hierarchical
  workflows run one monitord per sub-workflow — give each its own
  `events_path`.
- **Replay/recovery**: when monitord re-emits the whole event stream
  (`pegasus-monitord -r`, or recovery after an unclean shutdown), newer hosts
  pass `restart=True` to `start()` and the plugin truncates the file —
  mirroring monitord's own sinks, no duplicate stream. On older hosts (or a
  plain monitord restart that only emits new events) append mode applies; a
  second `workflow_start` mid-file is tolerated by the TUI, and
  `restart = true` remains the manual clean-slate knob.
- Coexists with workflow-monitor's `wfmonitor` plugin (distinct entry-point
  names and property namespaces) — just don't point both at the same
  `events_path`.
- For deploying onto a FABRIC Pegasus/HTCondor slice (plugin-host overlay on
  an apt-installed Pegasus, Vector shipping, ES indexing), follow the
  procedure in fabric-deployments' `deploy/MONITORD-PLUGIN.md`, substituting
  this package's pip install and the `wfevents.*` property namespace.

## Develop

```bash
cd plugins/wfevents
uv run pytest -q          # or: pip install . pytest && python -m pytest tests -q
uv run python tests/synthetic_run.py /tmp/wfevents.jsonl   # demo file for the TUI
```
