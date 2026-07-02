# Writing a pegasus-monitord plugin

This repo curates plugins for the `pegasus-monitord` entry-point plugin host
(pegasus branch `monitord-plugin-system`). This page is the contract a plugin
codes against, plus the conventions for adding a plugin to this repo.

The contract below matches the branch as of commit `e7da14941` (2026-07-02).
Hosts as old as the pinned minimum in the root README still run these
plugins — they simply never pass `restart`, never read `event_filter`, and
deliver every event.

## How plugins are discovered

The host discovers plugins with `importlib.metadata.entry_points()` under the
group **`pegasus.monitord.plugins`**. A plugin is a class registered in your
package metadata:

```toml
[project.entry-points."pegasus.monitord.plugins"]
myname = "my_package.plugin:MyPlugin"
```

The entry-point **name** (`myname`) doubles as the plugin's property namespace:
`pegasus.monitord.plugins.myname.*`. A plugin only loads and runs when
`pegasus.monitord.plugins.myname.enabled = true` is set in the run's
`pegasus.properties`.

## The plugin contract

The contract is duck-typed — monitord never `isinstance`-checks. Subclassing
`Pegasus.monitoring.plugin.MonitordEventPlugin` is optional; the conventional
pattern (used by `wfevents`) is a try/except import with a stub fallback so the
module also imports where Pegasus is not installed (e.g. in CI).

```python
class MyPlugin:
    # Optional event filter: prefixes matched with str.startswith against the
    # fully-qualified event name (so include the "stampede." prefix). None
    # (the default) delivers everything; () delivers nothing (tick-only
    # plugins). Filtering happens in the host BEFORE the per-plugin payload
    # copy, so it is the way to keep large-workflow replays cheap — the bulk
    # roster events are stampede.task.info/.task.edge/.job.info/.job.edge.
    event_filter = ("stampede.job_inst.", "stampede.xwf.")

    def start(self, props=None, restart=False, **kwargs):
        """Called once, before any events flow, on a bounded helper thread
        (a plugin whose start() does not return within start_timeout is
        skipped).

        props is the Pegasus Properties object. Read your config with:
            cfg = props.propertyset("pegasus.monitord.plugins.myname.", remove=True)
        which returns a dict of your namespace's keys with the prefix stripped.

        restart=True means monitord is re-emitting the ENTIRE event stream
        from the beginning of dagman.out — a user replay (pegasus-monitord -r)
        or recovery after an unclean shutdown. Durable output must truncate
        or deduplicate, the way monitord's own sinks do (the stampede DB rows
        are purged, jobstate.log is rotated, the file sink truncates).
        restart=False does NOT mean "first run ever" (a rescue-DAG retry
        starts a fresh monitord emitting only new events) — it means events
        are not being re-emitted.

        The host passes restart only when your override names it or accepts
        **kwargs; the historical one-argument start(self, props=None) keeps
        working unchanged. Accepting **kwargs is recommended so future host
        context keywords do not require another signature change.
        """

    def handle_event(self, event, kw):
        """Called once per event on this plugin's OWN dedicated worker thread.

        event: fully-qualified stampede event name, e.g. "stampede.job_inst.main.end"
        kw:    payload dict; keys use "__" as separator (xwf__id, job__id).
               The payload is deep-copied at enqueue time — your copy is
               stable and private, including nested values.
        Events are FIFO per plugin; different plugins run concurrently.
        """

    def tick(self):
        """Optional wall-clock callback, on the SAME worker thread as
        handle_event (never concurrent with it — no locking needed).

        Opt-in: fires only when pegasus.monitord.plugins.myname.tick_interval > 0.
        Cadence is "at most every interval", not exact.
        """

    def stop(self):
        """Called once on a bounded helper thread, after all queued events
        are drained and the worker thread is joined. Must return within
        join_timeout: anything still running past it is abandoned and dies
        with the monitord process — budget slow cleanup (network flushes,
        final polls) accordingly. If the worker itself failed to join,
        stop() is skipped entirely."""
```

## Host guarantees

- **Exception isolation**: exceptions raised in `handle_event()` / `tick()`
  are caught and logged by the host; the worker thread survives. A failing
  or hanging `start()` (bounded by `start_timeout`) skips that plugin only.
  A plugin cannot kill monitord.
- **Payload isolation**: each queued payload is a `copy.deepcopy` snapshot,
  so monitord's (and other plugins') mutations can never tear it, including
  nested composite-event values.
- **Back-pressure**: each plugin has a bounded event queue; monitord's parse
  loop never blocks on a slow plugin. On overflow exactly one event is lost:
  the one being submitted (`drop-newest`, default) or the oldest queued one
  (`overflow_policy = drop-oldest`, for live-monitoring plugins that prefer
  the freshest state). Drops are counted and logged, with a final per-plugin
  total at shutdown. Events rejected by the event filter are skipped before
  the payload copy and counted separately — they are by-design, not drops.
- **Shutdown**: queued events are drained, the worker is joined with a
  timeout, then `stop()` runs on a bounded helper thread. A wedged worker is
  abandoned with a warning — and its `stop()` is skipped, since the contract
  ("stop only after the worker is joined") can no longer be honored.
- **Misconfiguration signal**: enabling a name that no installed package
  registers under the entry-point group logs a startup warning (the usual
  cause: the plugin is installed in a different interpreter than monitord's).

## Host-reserved properties (per plugin namespace)

| Key | Default | Meaning |
|---|---|---|
| `pegasus.monitord.plugins.<name>.enabled` | `false` | Required `true` to load the plugin. The plugin host only activates when at least one plugin is enabled — leftover config with every plugin disabled changes nothing |
| `pegasus.monitord.plugins.<name>.queue_size` | `10000` | Bounded event queue (≤ 0 = unbounded) |
| `pegasus.monitord.plugins.<name>.overflow_policy` | `drop-newest` | Which event is lost on queue overflow; `drop-oldest` evicts the oldest queued event instead |
| `pegasus.monitord.plugins.<name>.start_timeout` | `join_timeout` | Seconds allowed for `start()`; a timed-out plugin is skipped |
| `pegasus.monitord.plugins.<name>.join_timeout` | `10.0` | Seconds to wait for the worker at shutdown; also bounds `stop()` |
| `pegasus.monitord.plugins.<name>.tick_interval` | `0.0` | > 0 enables `tick()` at roughly that cadence (seconds) |
| `pegasus.monitord.plugins.<name>.events` | *(all)* | Comma-separated event-name prefixes; **replaces** the plugin's declared `event_filter` (`*` restores all events). Override with care: a plugin's own filter is usually load-bearing |

Every other key in the namespace is yours to define.

## Repo conventions

- One directory per plugin: `plugins/<name>/` with its own `pyproject.toml`,
  `src/`, `tests/`, and `README.md`. Plugins are installed individually:
  `pip install 'git+https://github.com/mjstealey/pegasus-monitord-plugins.git#subdirectory=plugins/<name>'`.
- Distribution named `pegasus-monitord-plugin-<name>`; flat import package
  (no namespace packages). Keep runtime dependencies at zero unless the
  plugin genuinely needs more — it runs inside monitord's interpreter.
- **Tests must pass without Pegasus or HTCondor installed**: fake the
  Properties object (a stub whose `propertyset(prefix, remove=True)` returns
  the config dict) and monkeypatch any external queries. See
  `plugins/wfevents/tests/` for the established pattern.
- Code vendored or derived from another project gets a provenance header, an
  entry in the plugin's `SOURCES.md` (pinned upstream commit + resync recipe),
  and a mention in the root `NOTICE`.
- Add the plugin to the CI matrix in `.github/workflows/ci.yml` and to the
  index table in the root `README.md`.
