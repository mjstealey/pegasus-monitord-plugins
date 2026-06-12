"""
Tests for the stampede -> workflow-monitor JSONL translation path (the part
the upstream tick/condor tests do not cover).
"""

from conftest import EVENTS_FILE, _Props, _records, _send_wf_plan, _start
from monitord_wfevents.plugin import WfEventsPlugin

UUID = "uuid-1"


def _ev(plugin, short, **kw):
    kw.setdefault("xwf__id", UUID)
    kw.setdefault("ts", 1.78e9)
    plugin.handle_event(f"stampede.{short}", kw)


def _roster(plugin, job="preprocess_ID01", task="ID01"):
    _ev(plugin, "job.info", job__id=job, type_desc="compute", ts=692252)
    _ev(
        plugin,
        "task.info",
        task__id=task,
        transformation="preprocess",
        argv="-a preprocess",
        ts=692252,
    )
    _ev(plugin, "wf.map.task_job", job__id=job, task__id=task, ts=692252)


def test_workflow_start_is_first_record(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _roster(plugin)
    _ev(plugin, "static.end")
    plugin.stop()

    recs = _records(tmp_path)
    assert recs[0]["event_type"] == "workflow_start"
    assert recs[0]["wf_uuid"] == UUID
    assert recs[0]["dax_label"] == "diamond"
    assert recs[0]["submit_dir"] == "/opt/workflows/submit/run-x"
    assert recs[0]["wf_start"] is None  # authoritative start arrives with xwf.start


def test_jobs_init_roster_correlation(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _roster(plugin)
    _ev(plugin, "static.end")
    _ev(plugin, "static.end")  # second one must not duplicate the roster
    plugin.stop()

    (init,) = _records(tmp_path, "jobs_init")
    assert init["total_jobs"] == 1
    (entry,) = init["jobs"]
    assert entry["job_id"] == 1
    assert entry["exec_job_id"] == "preprocess_ID01"
    assert entry["type_desc"] == "compute"
    assert entry["transformation"] == "preprocess"
    assert entry["task_argv"] == "-a preprocess"


def test_job_state_mapping(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _roster(plugin, job="j1")
    _roster(plugin, job="j2", task="ID02")
    _ev(plugin, "static.end")

    _ev(plugin, "job_inst.submit.end", job__id="j1", status=0)
    _ev(plugin, "job_inst.main.start", job__id="j1")  # no status -> normal variant
    _ev(plugin, "job_inst.main.end", job__id="j1", status=0)
    _ev(plugin, "job_inst.main.end", job__id="j2", status=-1)
    plugin.stop()

    states = [(r["exec_job_id"], r["state"]) for r in _records(tmp_path, "job_state")]
    assert states == [
        ("j1", "SUBMIT"),
        ("j1", "EXECUTE"),
        ("j1", "JOB_SUCCESS"),
        ("j2", "JOB_FAILURE"),
    ]


def test_enrichment_carry_forward(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _roster(plugin, job="j1")
    _ev(plugin, "static.end")

    _ev(
        plugin,
        "job_inst.main.end",
        job__id="j1",
        status=0,
        exitcode=0,
        stdout__file="j1.out.000",
        stderr__file="j1.err.000",
    )
    _ev(plugin, "inv.end", job__id="j1", maxrss=123456)
    _ev(plugin, "job_inst.post.end", job__id="j1", status=0)
    plugin.stop()

    main_end, post_end = _records(tmp_path, "job_state")
    assert main_end["exitcode"] == 0
    assert main_end["stdout_file"] == "j1.out.000"
    assert main_end["stderr_file"] == "j1.err.000"
    assert "maxrss" not in main_end  # inv.end had not arrived yet
    assert post_end["state"] == "POST_SCRIPT_SUCCESS"
    assert post_end["maxrss"] == 123456  # carried forward from inv.end
    assert post_end["stdout_file"] == "j1.out.000"


def test_workflow_state_records(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _ev(plugin, "xwf.start", ts=1.78e9 + 10)
    _ev(plugin, "xwf.end", ts=1.78e9 + 100, status="0")
    plugin.stop()

    started, terminated = _records(tmp_path, "workflow_state")
    assert started["state"] == "WORKFLOW_STARTED"
    assert started["wf_start"] == 1.78e9 + 10
    assert terminated["state"] == "WORKFLOW_TERMINATED"
    assert terminated["wf_end"] == 1.78e9 + 100
    assert terminated["status"] == 0  # int-coerced from the string payload


def test_unqualified_event_names_dispatch(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    # monitord delivers qualified names, but the dispatch accepts both forms
    plugin.handle_event(
        "wf.plan",
        {"xwf__id": UUID, "ts": 1.78e9, "submit_dir": "/sd", "dax__label": "d"},
    )
    plugin.handle_event("job.info", {"xwf__id": UUID, "ts": 692252, "job__id": "j1"})
    plugin.handle_event(
        "job_inst.submit.end",
        {"xwf__id": UUID, "ts": 1.78e9, "job__id": "j1", "status": 0},
    )
    plugin.stop()

    assert len(_records(tmp_path, "workflow_start")) == 1
    (state,) = _records(tmp_path, "job_state")
    assert state["state"] == "SUBMIT"


def test_jobs_init_forced_before_first_job_state(tmp_path, monkeypatch):
    plugin = _start(tmp_path, monkeypatch, condor_poll=False)
    _send_wf_plan(plugin)
    _roster(plugin, job="j1")
    # no static.end: the first job_state must force the roster out first
    _ev(plugin, "job_inst.submit.end", job__id="j1", status=0)
    plugin.stop()

    types = [r["event_type"] for r in _records(tmp_path)]
    assert types.index("jobs_init") < types.index("job_state")


def test_restart_truncates_and_default_appends(tmp_path, monkeypatch):
    path = tmp_path / EVENTS_FILE

    def run_once(restart=None):
        cfg = {"events_path": str(path)}
        if restart is not None:
            cfg["restart"] = restart
        plugin = WfEventsPlugin()
        plugin.start(_Props(cfg))
        _send_wf_plan(plugin)
        plugin.stop()

    run_once()
    run_once()  # default append: a second workflow_start accumulates
    assert len(_records(tmp_path, "workflow_start")) == 2
    run_once(restart="true")  # truncates back to a single fresh header
    assert len(_records(tmp_path, "workflow_start")) == 1
