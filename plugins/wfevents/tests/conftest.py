"""Shared fakes and helpers for the wfevents plugin tests.

Ported from workflow-monitor's tests/test_monitord_plugin_tick.py
@ de0c9482ec8ce5ccf9ccbd670d255d83225fe21f (see ../SOURCES.md).

The plugin is driven directly -- no Pegasus install needed (the duck-typed
base-class stub kicks in) and no live condor: query_queue / query_history /
query_slots are monkeypatched at the module level where the plugin looks
them up.
"""

import json

from monitord_wfevents.plugin import WfEventsPlugin

SUBMIT_DIR = "/opt/workflows/submit/run-x"
EXPECTED_CONSTRAINT = (
    f'Cmd =!= UNDEFINED && substr(Cmd, 0, {len(SUBMIT_DIR)}) == "{SUBMIT_DIR}"'
)
EVENTS_FILE = "wfevents.jsonl"


class _Props:
    """Stub of Pegasus.tools.properties.Properties: propertyset() returns the
    plugin's config keys with the prefix already stripped."""

    def __init__(self, cfg):
        self._cfg = dict(cfg)

    def propertyset(self, prefix, remove=True):
        return dict(self._cfg)


class _FakeTime:
    """Replaces the plugin module's `time` so cadence is deterministic."""

    def __init__(self, monotonic=1000.0, wall=1.8e9):
        self.mono = monotonic
        self.wall = wall

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall


class _FakePool:
    def __init__(self, claimed=1):
        self._claimed = claimed

    def to_dict(self):
        return {
            "total_slots": 2,
            "claimed_slots": self._claimed,
            "idle_slots": 2 - self._claimed,
            "total_cpus": 16,
            "idle_cpus": 8,
        }


def _job(cluster=1, status=2, host="work1"):
    return {
        "ClusterId": cluster,
        "ProcId": 0,
        "JobStatus": status,
        "RemoteHost": host,
        "BytesSent": 0,
        "BytesRecvd": 0,
    }


def _start(
    tmp_path,
    monkeypatch,
    condor_poll=True,
    tick_interval="5",
    host_restart=None,
    **extra,
):
    cfg = {"events_path": str(tmp_path / EVENTS_FILE)}
    if condor_poll:
        cfg["condor_poll"] = "true"
        cfg["tick_interval"] = tick_interval
        # the recommended production setting: fits the worst-case stop() flush
        cfg["join_timeout"] = "60"
    # extra cfg keys override; None means "leave unset" (host-default behavior)
    for key, val in extra.items():
        if val is None:
            cfg.pop(key, None)
        else:
            cfg[key] = val
    plugin = WfEventsPlugin()
    if host_restart is None:
        plugin.start(_Props(cfg))  # old-host call shape: props only
    else:
        plugin.start(_Props(cfg), restart=host_restart)
    return plugin


def _send_wf_plan(plugin, submit_dir=SUBMIT_DIR):
    plugin.handle_event(
        "stampede.wf.plan",
        {
            "xwf__id": "uuid-1",
            "ts": 1.78e9,
            "submit_dir": submit_dir,
            "dax__label": "diamond",
        },
    )


def _records(tmp_path, event_type=None):
    path = tmp_path / EVENTS_FILE
    if not path.exists():
        return []
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    if event_type:
        recs = [r for r in recs if r.get("event_type") == event_type]
    return recs
