"""Generate a realistic wfevents.jsonl by driving a real WfEventsPlugin.

Replays the stampede event sequence of a 4-job diamond workflow (with one
job failure + retry) straight into the plugin -- no Pegasus, no HTCondor.
Used by test_replay_smoke.py, and runnable directly to produce a file for
the workflow-monitor TUI::

    python tests/synthetic_run.py /tmp/wfevents.jsonl
    workflow-monitor --replay /tmp/wfevents.jsonl --speed 8
"""

import sys

from monitord_wfevents.plugin import WfEventsPlugin

WF_UUID = "11111111-2222-3333-4444-555555555555"
SUBMIT_DIR = "/opt/workflows/submit/diamond-run-1"
T0 = 1_780_000_000.0

# exec_job_id, task_id, transformation, argv
JOBS = [
    (
        "preprocess_ID0000001",
        "ID0000001",
        "preprocess",
        "-a preprocess -i f.a -o f.b1 f.b2",
    ),
    ("findrange_ID0000002", "ID0000002", "findrange", "-a findrange -i f.b1 -o f.c1"),
    ("findrange_ID0000003", "ID0000003", "findrange", "-a findrange -i f.b2 -o f.c2"),
    ("analyze_ID0000004", "ID0000004", "analyze", "-a analyze -i f.c1 f.c2 -o f.d"),
]


class _Props:
    """Minimal stand-in for Pegasus.tools.properties.Properties."""

    def __init__(self, cfg):
        self._cfg = dict(cfg)

    def propertyset(self, prefix, remove=True):
        return dict(self._cfg)


def emit_synthetic_run(path):
    plugin = WfEventsPlugin()
    plugin.start(_Props({"events_path": str(path), "restart": "true"}))
    ev = plugin.handle_event

    ev(
        "stampede.wf.plan",
        {
            "xwf__id": WF_UUID,
            "ts": T0,
            "submit_dir": SUBMIT_DIR,
            "dax__label": "diamond",
            "user": "stealey",
            "planner__version": "5.1.2",
        },
    )
    # Planner roster replay: 1970-era monotonic stamps, like the real stream.
    for i, (job, task, transformation, argv) in enumerate(JOBS):
        ev(
            "stampede.job.info",
            {
                "xwf__id": WF_UUID,
                "ts": 692252 + i,
                "job__id": job,
                "type_desc": "compute",
            },
        )
        ev(
            "stampede.task.info",
            {
                "xwf__id": WF_UUID,
                "ts": 692252 + i,
                "task__id": task,
                "transformation": transformation,
                "argv": argv,
            },
        )
        ev(
            "stampede.wf.map.task_job",
            {"xwf__id": WF_UUID, "ts": 692252 + i, "job__id": job, "task__id": task},
        )
    ev("stampede.static.end", {"xwf__id": WF_UUID, "ts": 692260})

    ev("stampede.xwf.start", {"xwf__id": WF_UUID, "ts": T0 + 2})

    def run_job(job, t, status=0, exitcode=0, maxrss=None):
        ev(
            "stampede.job_inst.submit.end",
            {
                "xwf__id": WF_UUID,
                "ts": t,
                "job__id": job,
                "status": 0,
                "site": "condorpool",
            },
        )
        ev(
            "stampede.job_inst.main.start",
            {"xwf__id": WF_UUID, "ts": t + 3, "job__id": job},
        )
        if maxrss is not None:
            ev(
                "stampede.inv.end",
                {"xwf__id": WF_UUID, "ts": t + 9, "job__id": job, "maxrss": maxrss},
            )
        ev(
            "stampede.job_inst.main.end",
            {
                "xwf__id": WF_UUID,
                "ts": t + 10,
                "job__id": job,
                "status": status,
                "exitcode": exitcode,
                "stdout__file": f"{job}.out.000",
                "stderr__file": f"{job}.err.000",
            },
        )
        return t + 12

    preprocess, findrange_1, findrange_2, analyze = (j[0] for j in JOBS)
    t = run_job(preprocess, T0 + 7, maxrss=423_000)
    t = run_job(findrange_1, t)
    t = run_job(findrange_2, t)
    # analyze fails once, then succeeds on retry
    t = run_job(analyze, t, status=-1, exitcode=1)
    t = run_job(analyze, t, status=0, exitcode=0)

    ev("stampede.xwf.end", {"xwf__id": WF_UUID, "ts": t + 4, "status": 0})
    plugin.stop()
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python synthetic_run.py /path/to/wfevents.jsonl")
    out = emit_synthetic_run(sys.argv[1])
    print(f"wrote {out}")
