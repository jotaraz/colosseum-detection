"""One-line health summary per running cluster job — errors, progress, terminal state.

Reads the job's own .out and HTCondor .log rather than asking condor_q, which serves a cached
jobads file and lags a minute or two behind reality (see the cluster skill). The .log is
written the instant anything happens, so it is the honest source for "has this job finished".
"""
import re
import sys
from pathlib import Path

P = Path("/fast/jtaraz/LIARS/colosseum-detection")
#: (stdout, stderr, condor log) per job. BOTH streams are read: `lie_over_agent1` logs through
#: `logging`, which writes to stderr, so a job's 429s and judge failures land in .err while its
#: own echo lines land in .out. Reading only .out reports 429=0 for a job drowning in them.
JOBS = {
    "17483419": ("cluster/glm_batch2_judge_17483419.out", "cluster/glm_batch2_judge_17483419.err",
                 "cluster/glm_batch2_judge_17483419.log"),
}


def count(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


def read(name: str) -> str:
    f = P / name
    return f.read_text(errors="replace") if f.exists() else ""


for job, (out_name, err_name, log_name) in JOBS.items():
    out = read(out_name) + read(err_name)
    log = read(log_name)
    state = "running"
    if "Job terminated" in log:
        state = "terminated"
    elif "Job was aborted" in log:
        state = "aborted"
    elif re.search(r"Job was held|SYSTEM_PERIODIC_HOLD", log):
        state = "HELD"
    elif "Job executing on host" not in log:
        state = "idle"
    # Built outside the f-string: the patterns contain backslashes, which an f-string
    # expression may not (SyntaxError on py<3.12).
    n429 = count(out, r"HTTP 429")
    n5xx = count(out, r"HTTP 5[0-9][0-9]")
    judgefail = count(out, r"FAILED:")
    rolloutfail = count(out, r"\] FAIL ")
    parse = count(out, r"PARSE-ERROR")
    finished = count(out, r"BATCH FINISHED|sweep complete")
    progress = count(out, r"finding\(s\)") + count(out, r"\] DONE ")
    print(f"{job} {state} 429={n429} 5xx={n5xx} judgefail={judgefail} "
          f"rolloutfail={rolloutfail} parse={parse} done={finished} progress={progress}")
