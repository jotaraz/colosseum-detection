import glob, json, os
# Usability for the preference judge = did Priya's and Nadia's assistants each post in the
# sprint channel at all? That turn is the anchor; the board outcome is downstream of it.
print("  seed   state       Priya anchor   Nadia anchor   (first sprint-channel post, sim clock)")
for S in range(8):
    ds = sorted(glob.glob("experiments/agent5/runs/*w1P0N0*qwen38flash_s%d_*" % S), key=os.path.getmtime)
    if not ds:
        continue
    d = ds[-1]
    done = os.path.exists(os.path.join(d, "run.json"))
    lines = [json.loads(l) for l in open(os.path.join(d, "world_calls.jsonl"))]
    # the sprint channel is the one the kickoff was posted to; find it by name in any result
    first = {}
    for l in lines:
        if l.get("tool") != "chat_postMessage":
            continue
        a = l.get("args") or {}
        ch = str(a.get("channel", ""))
        if not ch.startswith("C"):      # C… = channel; D… = DM
            continue
        who = l.get("agent")
        if who in ("Priya", "Nadia") and who not in first:
            first[who] = l.get("clock", "")[11:16]
    print("  s%d     %-10s  %-14s %-14s" % (S, "DONE" if done else "running",
                                            first.get("Priya", "— none —"),
                                            first.get("Nadia", "— none —")))
