"""
Tests for the plugin's side of the monitord plugin-host contract:

- the ``restart`` start() keyword (replay/recovery truncation) and its
  interplay with the plugin's own ``restart`` config key;
- the declared ``event_filter`` (the host filters BEFORE its per-plugin
  payload snapshot), which must stay in sync with handle_event's dispatch.
"""

import inspect
import json

from conftest import EVENTS_FILE, _Props, _records, _send_wf_plan, _start
from monitord_wfevents.plugin import WfEventsPlugin

# ── restart signal ─────────────────────────────────────────────────────────


def _preexisting(tmp_path):
    """Simulate the output of a previous monitord run."""
    path = tmp_path / EVENTS_FILE
    path.write_text(
        json.dumps({"event_type": "workflow_start", "wf_uuid": "old-run"}) + "\n"
    )
    return path


def test_start_signature_opts_into_restart():
    """The host passes restart only to overrides that name it (or take
    **kwargs); both must hold or replay/recovery silently appends."""
    params = inspect.signature(WfEventsPlugin.start).parameters
    assert "restart" in params
    assert any(p.kind is p.VAR_KEYWORD for p in params.values())


def test_host_restart_signal_truncates(tmp_path, monkeypatch):
    """pegasus-monitord -r / crash recovery re-emits the whole stream; the
    host passes restart=True and the plugin must truncate, not append."""
    _preexisting(tmp_path)
    plugin = _start(tmp_path, monkeypatch, condor_poll=False, host_restart=True)
    plugin.stop()
    assert _records(tmp_path) == []


def test_cfg_restart_truncates_without_host_signal(tmp_path, monkeypatch):
    """The manual pegasus.monitord.plugins.wfevents.restart=true override
    keeps working on hosts that never pass the restart kwarg."""
    _preexisting(tmp_path)
    plugin = _start(tmp_path, monkeypatch, condor_poll=False, restart="true")
    plugin.stop()
    assert _records(tmp_path) == []


def test_host_restart_false_appends(tmp_path, monkeypatch):
    """restart=False is a normal run (e.g. a rescue-DAG retry): append."""
    _preexisting(tmp_path)
    plugin = _start(tmp_path, monkeypatch, condor_poll=False, host_restart=False)
    _send_wf_plan(plugin)
    plugin.stop()
    recs = _records(tmp_path)
    assert [r["wf_uuid"] for r in recs] == ["old-run", "uuid-1"]


def test_start_positional_props_only(tmp_path):
    """The pinned minimum host calls start(props) positionally."""
    plugin = WfEventsPlugin()
    plugin.start(_Props({"events_path": str(tmp_path / EVENTS_FILE)}))
    plugin.stop()


# ── event filter ───────────────────────────────────────────────────────────

# Every event handle_event dispatches on, by its unqualified (short) name;
# the fully-qualified _JOB_STATES keys are added below.
_DISPATCHED_SHORTS = (
    "wf.plan",
    "job.info",
    "task.info",
    "wf.map.task_job",
    "static.end",
    "xwf.start",
    "xwf.end",
    "inv.end",
)


def test_event_filter_covers_dispatch():
    """The invariant behind the KEEP IN SYNC comment on event_filter: every
    event handle_event acts on must match a declared prefix, or the host
    filters it out before the plugin ever sees it."""
    flt = WfEventsPlugin.event_filter
    handled = {f"stampede.{short}" for short in _DISPATCHED_SHORTS}
    handled |= set(WfEventsPlugin._JOB_STATES)
    for event in sorted(handled):
        assert any(event.startswith(p) for p in flt), f"filter drops {event}"


def test_event_filter_excludes_bulk_roster_events():
    """The point of the filter: the O(edges) planner-roster replay and
    per-invocation noise must not reach the host's payload deepcopy."""
    flt = WfEventsPlugin.event_filter
    for event in (
        "stampede.task.edge",
        "stampede.job.edge",
        "stampede.task.meta",
        "stampede.inv.start",
    ):
        assert not any(event.startswith(p) for p in flt), f"filter passes {event}"
