"""
Tests for the wfevents plugin's condor polling (driven by the pegasus-monitord
plugin host's tick()).

Ported from workflow-monitor's tests/test_monitord_plugin_tick.py
@ de0c9482ec8ce5ccf9ccbd670d255d83225fe21f (see ../SOURCES.md); the upstream
--no-condor-poll CLI-flag test stays in workflow-monitor, where that CLI lives.
"""

import monitord_wfevents.plugin as mp
from conftest import (
    EXPECTED_CONSTRAINT,
    _FakePool,
    _FakeTime,
    _job,
    _records,
    _send_wf_plan,
    _start,
)

# --------------------------------------------------------------------------- #
# regression: the planner's 1970-era roster timestamps must not poison records
# --------------------------------------------------------------------------- #


def test_jobs_init_timestamp_falls_back_to_wall_clock(tmp_path, monkeypatch):
    """Roster events carry monotonic-as-epoch 1970 stamps; with no wall-clock
    ts seen, jobs_init must fall back to time.time() -- this is also the
    import-time regression (a NameError if `import time` goes missing)."""
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    plugin.handle_event(
        "stampede.job.info", {"xwf__id": "u", "job__id": "j1", "ts": 692252}
    )
    plugin.handle_event("stampede.static.end", {"xwf__id": "u", "ts": 692253})
    (rec,) = _records(tmp_path, "jobs_init")
    assert rec["timestamp"] > 1e9
    plugin.stop()


# --------------------------------------------------------------------------- #
# tick gating
# --------------------------------------------------------------------------- #


def test_tick_noop_without_condor_poll(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mp, "query_queue", lambda **kw: calls.append(kw) or [])
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    plugin.tick()
    assert calls == []
    assert _records(tmp_path, "htcondor_poll") == []
    plugin.stop()


def test_tick_skips_until_wf_plan_then_uses_constraint(tmp_path, monkeypatch):
    calls = []

    def fake_queue(**kw):
        calls.append(kw)
        return [_job()]

    monkeypatch.setattr(mp, "query_queue", fake_queue)
    monkeypatch.setattr(mp, "query_history", lambda **kw: [])
    monkeypatch.setattr(mp, "query_slots", lambda **kw: None)
    plugin = _start(tmp_path, monkeypatch)

    plugin.tick()  # before wf.plan: no submit_dir -> never poll unconstrained
    assert calls == []

    _send_wf_plan(plugin)
    plugin.tick()
    assert len(calls) == 1
    assert calls[0]["constraint"] == EXPECTED_CONSTRAINT
    assert calls[0]["raise_on_error"] is True
    (rec,) = _records(tmp_path, "htcondor_poll")
    assert rec["wf_uuid"] == "uuid-1"
    assert rec["timestamp"] > 1e9
    plugin.stop()


# --------------------------------------------------------------------------- #
# fingerprint dedup
# --------------------------------------------------------------------------- #


def test_classad_exprtree_values_serialize(tmp_path, monkeypatch):
    """The bindings path returns dict(ad) with classad.ExprTree values; _write
    must stringify them (default=str, EventLogger parity), not raise."""

    class _ExprTreeLike:
        def __str__(self):
            return "RemoteHost =?= undefined"

    job = _job()
    job["MemoryUsage"] = _ExprTreeLike()
    monkeypatch.setattr(mp, "query_queue", lambda **kw: [job])
    monkeypatch.setattr(mp, "query_history", lambda **kw: [])
    monkeypatch.setattr(mp, "query_slots", lambda **kw: None)
    plugin = _start(tmp_path, monkeypatch)
    _send_wf_plan(plugin)
    plugin.tick()
    (rec,) = _records(tmp_path, "htcondor_poll")
    assert rec["jobs"][0]["MemoryUsage"] == "RemoteHost =?= undefined"
    plugin.stop()


def test_queue_fingerprint_dedup(tmp_path, monkeypatch):
    jobs = [[_job(status=1)], [_job(status=1)], [_job(status=2)]]
    it = iter(jobs)
    monkeypatch.setattr(mp, "query_queue", lambda **kw: next(it))
    monkeypatch.setattr(mp, "query_history", lambda **kw: [])
    monkeypatch.setattr(mp, "query_slots", lambda **kw: None)
    ft = _FakeTime()
    monkeypatch.setattr(mp, "time", ft)
    plugin = _start(tmp_path, monkeypatch)
    _send_wf_plan(plugin)

    for _ in jobs:
        plugin.tick()
        ft.mono += 6  # past the backoff base each time

    recs = _records(tmp_path, "htcondor_poll")
    # identical poll deduped; the JobStatus change re-emits
    assert len(recs) == 2
    assert recs[0]["jobs"][0]["JobStatus"] == 1
    assert recs[1]["jobs"][0]["JobStatus"] == 2
    plugin.stop()


# --------------------------------------------------------------------------- #
# history / pool cadence and failure gating
# --------------------------------------------------------------------------- #


def test_history_and_pool_cadence(tmp_path, monkeypatch):
    hist_calls, pool_calls = [], []
    monkeypatch.setattr(mp, "query_queue", lambda **kw: [_job()])
    monkeypatch.setattr(
        mp, "query_history", lambda **kw: hist_calls.append(kw) or [_job(9)]
    )
    monkeypatch.setattr(
        mp, "query_slots", lambda **kw: pool_calls.append(kw) or _FakePool()
    )
    ft = _FakeTime()
    monkeypatch.setattr(mp, "time", ft)
    plugin = _start(tmp_path, monkeypatch, tick_interval="5")
    _send_wf_plan(plugin)
    # base 5 -> history >= 15s, pool >= 25s

    plugin.tick()  # t=1000: first tick polls everything (last marks at 0)
    assert (len(hist_calls), len(pool_calls)) == (1, 1)

    ft.mono += 10
    plugin.tick()  # t=+10: below both intervals
    assert (len(hist_calls), len(pool_calls)) == (1, 1)

    ft.mono += 6
    plugin.tick()  # t=+16: history due, pool not
    assert (len(hist_calls), len(pool_calls)) == (2, 1)

    ft.mono += 10
    plugin.tick()  # t=+26: pool due
    assert (len(hist_calls), len(pool_calls)) == (2, 2)

    # pool kwargs never include schedd_name
    assert all("schedd_name" not in kw for kw in pool_calls)
    assert len(_records(tmp_path, "htcondor_history")) == 1  # ClusterId set unchanged
    assert len(_records(tmp_path, "pool_status")) == 1
    plugin.stop()


def test_backoff_on_failure_skips_history_and_pool(tmp_path, monkeypatch):
    hist_calls, pool_calls = [], []

    def failing_queue(**kw):
        raise RuntimeError("schedd down")

    monkeypatch.setattr(mp, "query_queue", failing_queue)
    monkeypatch.setattr(mp, "query_history", lambda **kw: hist_calls.append(kw) or [])
    monkeypatch.setattr(
        mp, "query_slots", lambda **kw: pool_calls.append(kw) or _FakePool()
    )
    ft = _FakeTime()
    monkeypatch.setattr(mp, "time", ft)
    plugin = _start(tmp_path, monkeypatch)
    _send_wf_plan(plugin)

    plugin.tick()
    assert plugin._backoff.fail_streak == 1
    # while failing, history/pool are skipped entirely
    assert hist_calls == [] and pool_calls == []
    # next tick inside the backoff window: queue poll gated too
    ft.mono += 1
    plugin.tick()
    assert plugin._backoff.fail_streak == 1
    assert _records(tmp_path, "htcondor_poll") == []

    # recovery: queue succeeds again, emission resumes
    monkeypatch.setattr(mp, "query_queue", lambda **kw: [_job()])
    ft.mono += 120  # past max backoff
    plugin.tick()
    assert plugin._backoff.fail_streak == 0
    assert len(_records(tmp_path, "htcondor_poll")) == 1
    plugin.stop()


# --------------------------------------------------------------------------- #
# final flush on stop
# --------------------------------------------------------------------------- #


def test_stop_final_flush(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "query_queue", lambda **kw: [_job(status=4)])
    monkeypatch.setattr(mp, "query_history", lambda **kw: [_job(7, status=4)])
    monkeypatch.setattr(mp, "query_slots", lambda **kw: _FakePool(claimed=0))
    plugin = _start(tmp_path, monkeypatch)
    _send_wf_plan(plugin)

    plugin.stop()  # forces one last queue + history + pool poll
    assert len(_records(tmp_path, "htcondor_poll")) == 1
    assert len(_records(tmp_path, "htcondor_history")) == 1
    assert len(_records(tmp_path, "pool_status")) == 1
    # no xwf.end seen -> no terminal marker
    assert _records(tmp_path, "workflow_end") == []
    assert plugin._output is None  # file closed after the flush


def test_workflow_end_is_last_after_final_flush(tmp_path, monkeypatch):
    """The terminal marker must follow the final condor flush — a poll event
    after workflow_end reads as a server resume to the --remote consumer."""
    monkeypatch.setattr(mp, "query_queue", lambda **kw: [_job(status=4)])
    monkeypatch.setattr(mp, "query_history", lambda **kw: [_job(7, status=4)])
    monkeypatch.setattr(mp, "query_slots", lambda **kw: _FakePool(claimed=0))
    plugin = _start(tmp_path, monkeypatch)
    _send_wf_plan(plugin)
    plugin.handle_event(
        "stampede.xwf.end", {"xwf__id": "uuid-1", "ts": 1.78e9 + 50, "status": 0}
    )

    plugin.stop()
    recs = _records(tmp_path)
    assert recs[-1]["event_type"] == "workflow_end"
    assert {"htcondor_poll", "htcondor_history", "pool_status"} <= {
        r["event_type"] for r in recs[:-1]
    }


# --------------------------------------------------------------------------- #
# stop() flush budget: the host abandons stop() after join_timeout, so the
# flush must never spend the time reserved for the workflow_end record
# --------------------------------------------------------------------------- #


def _budget_fakes(monkeypatch, calls):
    monkeypatch.setattr(
        mp, "query_queue", lambda **kw: calls.append("queue") or [_job(status=4)]
    )
    monkeypatch.setattr(mp, "query_history", lambda **kw: calls.append("history") or [])
    monkeypatch.setattr(
        mp, "query_slots", lambda **kw: calls.append("pool") or _FakePool(claimed=0)
    )


def test_stop_flush_skipped_when_budget_too_small(tmp_path, monkeypatch, caplog):
    """At the host-default join_timeout (10s) no flush step's worst case fits:
    all polls are skipped and the terminal workflow_end still lands."""
    calls = []
    _budget_fakes(monkeypatch, calls)
    plugin = _start(tmp_path, monkeypatch, join_timeout=None)  # host default
    _send_wf_plan(plugin)
    plugin.handle_event(
        "stampede.xwf.end", {"xwf__id": "uuid-1", "ts": 1.78e9 + 50, "status": 0}
    )
    with caplog.at_level("WARNING", logger="monitord_wfevents.plugin"):
        plugin.stop()
    assert calls == []
    recs = _records(tmp_path)
    assert recs[-1]["event_type"] == "workflow_end"
    assert any("skipped" in r.message for r in caplog.records)


def test_stop_flush_partial_budget_runs_queue_only(tmp_path, monkeypatch):
    """join_timeout=15 fits the queue poll's worst case (10s) but not
    history/pool (15s each)."""
    calls = []
    _budget_fakes(monkeypatch, calls)
    plugin = _start(tmp_path, monkeypatch, join_timeout="15")
    _send_wf_plan(plugin)
    plugin.stop()
    assert calls == ["queue"]
    assert len(_records(tmp_path, "htcondor_poll")) == 1
    assert _records(tmp_path, "htcondor_history") == []
    assert _records(tmp_path, "pool_status") == []


def test_stop_flush_slow_step_consumes_budget(tmp_path, monkeypatch):
    """A queue poll that stalls (hung schedd) eats the budget; the later
    steps must be skipped instead of running past join_timeout."""
    ft = _FakeTime()
    monkeypatch.setattr(mp, "time", ft)
    calls = []
    _budget_fakes(monkeypatch, calls)

    def slow_queue(**kw):
        calls.append("queue")
        ft.mono += 50.0  # stall: budget 58 (join_timeout 60) -> 8s left
        return [_job(status=4)]

    monkeypatch.setattr(mp, "query_queue", slow_queue)
    plugin = _start(tmp_path, monkeypatch)  # conftest default join_timeout=60
    _send_wf_plan(plugin)
    plugin.stop()
    assert calls == ["queue"]  # 8s remaining < history/pool worst case
