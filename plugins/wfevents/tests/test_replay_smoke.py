"""
Full synthetic run -> the invariants the workflow-monitor TUI relies on when
consuming the file via --replay or --remote.
"""

import json

from monitord_wfevents.plugin import WfEventsPlugin
from synthetic_run import emit_synthetic_run


def test_synthetic_run_satisfies_tui_contract(tmp_path):
    path = tmp_path / "wfevents.jsonl"
    emit_synthetic_run(path)

    recs = [json.loads(line) for line in path.read_text().splitlines()]
    assert recs

    # the TUI requires the first record to be workflow_start
    assert recs[0]["event_type"] == "workflow_start"
    # every record is stamped with the workflow uuid
    assert all(r.get("wf_uuid") for r in recs)

    (init,) = [r for r in recs if r["event_type"] == "jobs_init"]
    assert init["total_jobs"] == len(init["jobs"]) == 4
    assert init["timestamp"] > 1e9  # 1970-era roster stamps must not leak here

    vocabulary = {s for pair in WfEventsPlugin._JOB_STATES.values() for s in pair}
    job_states = [r for r in recs if r["event_type"] == "job_state"]
    assert job_states
    assert all(r["state"] in vocabulary for r in job_states)
    # the failure + retry both surface
    analyze = [
        r["state"] for r in job_states if r["exec_job_id"] == "analyze_ID0000004"
    ]
    assert "JOB_FAILURE" in analyze
    assert analyze[-1] == "JOB_SUCCESS"

    wf_states = [r["state"] for r in recs if r["event_type"] == "workflow_state"]
    assert wf_states == ["WORKFLOW_STARTED", "WORKFLOW_TERMINATED"]

    # the terminal marker the --remote TUI keys on to end its session
    end = recs[-1]
    assert end["event_type"] == "workflow_end"
    assert end["wf_state"] == "WORKFLOW_TERMINATED"
    assert end["wf_status"] == 0
    assert end["total_jobs"] == 4
    assert end["done"] == 4  # analyze failed once but its retry succeeded
    assert end["failed"] == 0
