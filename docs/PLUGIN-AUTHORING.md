# Writing a pegasus-monitord plugin

This repo curates plugins for the `pegasus-monitord` entry-point plugin host
(pegasus branch `monitord-plugin-system`). This page is the contract a plugin
codes against, plus the conventions for adding a plugin to this repo.

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
    def start(self, props=None):
        """Called once, before any events flow, on monitord's MAIN thread.

        props is the Pegasus Properties object. Read your config with:
            cfg = props.propertyset("pegasus.monitord.plugins.myname.", remove=True)
        which returns a dict of your namespace's keys with the prefix stripped.
        """

    def handle_event(self, event, kw):
        """Called once per event on this plugin's OWN dedicated worker thread.

        event: fully-qualified stampede event name, e.g. "stampede.job_inst.main.end"
        kw:    payload dict; keys use "__" as separator (xwf__id, job__id).
               The payload is snapshotted at enqueue time — your copy is stable.
        Events are FIFO per plugin; different plugins run concurrently.
        """

    def tick(self):
        """Optional wall-clock callback, on the SAME worker thread as
        handle_event (never concurrent with it — no locking needed).

        Opt-in: fires only when pegasus.monitord.plugins.myname.tick_interval > 0.
        Cadence is "at most every interval", not exact.
        """

    def stop(self):
        """Called once on monitord's MAIN thread, after all queued events are
        drained and the worker thread is joined."""
```

## Host guarantees

- **Exception isolation**: exceptions raised in `handle_event()` / `tick()`
  are caught and logged by the host; the worker thread survives. A failing
  `start()` skips that plugin only. A plugin cannot kill monitord.
- **Back-pressure**: each plugin has a bounded event queue
  (drop-on-overflow); monitord's parse loop never blocks on a slow plugin.
- **Shutdown**: queued events are drained, the worker is joined with a
  timeout, then `stop()` runs. A wedged worker is abandoned with a warning.

## Host-reserved properties (per plugin namespace)

| Key | Default | Meaning |
|---|---|---|
| `pegasus.monitord.plugins.<name>.enabled` | `false` | Required `true` to load the plugin |
| `pegasus.monitord.plugins.<name>.queue_size` | `10000` | Bounded event queue; drop-on-overflow (≤ 0 = unbounded) |
| `pegasus.monitord.plugins.<name>.join_timeout` | `10.0` | Seconds to wait for the worker at shutdown |
| `pegasus.monitord.plugins.<name>.tick_interval` | `0.0` | > 0 enables `tick()` at roughly that cadence (seconds) |

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
