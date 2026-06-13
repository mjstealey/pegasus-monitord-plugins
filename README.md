# pegasus-monitord-plugins

Curated entry-point plugins for [`pegasus-monitord`](https://pegasus.isi.edu/),
registered under the `pegasus.monitord.plugins` entry-point group. Each plugin
is a self-contained Python package under `plugins/<name>/`, installable on its
own and runnable inside the `pegasus-monitord` process that tracks a Pegasus
workflow run.

## Plugin host requirements

These plugins require a Pegasus build that includes the monitord entry-point
plugin host — branch
[`monitord-plugin-system`](https://github.com/pegasus-isi/pegasus/tree/monitord-plugin-system)
of pegasus-isi/pegasus:

- minimum commit `25b37965e44fb6f7d950997d07a1ceb701b2a0d1` (entry-point host,
  cross-thread payload-race fix, and the `tick()` hook), branch tip at time of
  writing `ca2e6384f3b0248b840d2355274d8b77d006b886` (last rebased onto master
  2026-06-13; each pre-rebase tip is tagged `monitord-plugin-system-pre-rebase`,
  `…-pre-rebase-2`).

Verify the host is present with the interpreter that `pegasus-monitord`
resolves:

```bash
python3 -c "from Pegasus.monitoring.plugin import MonitordEventPlugin; print('plugin host OK')"
```

For overlaying the plugin host onto an apt-installed Pegasus, see the retrofit
procedure in
[fabric-deployments `deploy/MONITORD-PLUGIN.md`](https://github.com/mjstealey/fabric-deployments).

## Plugins

| Plugin | Distribution | Description |
|---|---|---|
| [`wfevents`](plugins/wfevents/) | `pegasus-monitord-plugin-wfevents` | Translates the live monitord (stampede) event stream into workflow-monitor-native JSONL (`wfevents.jsonl`), optionally absorbing HTCondor queue/history/pool polling via `tick()`. Consumable live by the [workflow-monitor](https://github.com/pegasus-isi/workflow-monitor) TUI over SSH. |

## Installing a plugin

Install into the environment that `pegasus-monitord` runs from (on a typical
submit node, that is the system `python3`):

```bash
python3 -m pip install --user \
  'git+https://github.com/mjstealey/pegasus-monitord-plugins.git#subdirectory=plugins/wfevents'
```

Then enable it per run in `pegasus.properties` — see the plugin's README for
its property namespace and options.

## Writing a new plugin

See [`docs/PLUGIN-AUTHORING.md`](docs/PLUGIN-AUTHORING.md) for the host
contract (callbacks, threading model, host-reserved properties) and the repo
conventions for adding a plugin.

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) (parts of the
`wfevents` plugin are derived from pegasus-isi/workflow-monitor).
