"""Dump the focal (Matthieu/Marcus) agent's turns of a run: reasoning, note to employee, posts, pushes.

Usage: python3 dump.py <run_prefix> <agent> [lo] [hi]
Run from experiments/agent5/ (or adjust RUNS below).
"""
import glob, json, os, sys

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runs")


def load(prefix):
    hits = sorted(glob.glob(f"{RUNS}/agent5_{prefix}*"))
    hits = [h for h in hits if not h.endswith("_INVALID")] or hits
    return hits[0], json.load(open(os.path.join(hits[0], "run.json")))


def main(prefix, agent, lo=0, hi=10**9, maxr=2500):
    rd, r = load(prefix)
    print("=" * 100)
    print(os.path.basename(rd), "| outcome:", r.get("outcome"), "| assignments:", r.get("assignments"))
    for t in r["turns"]:
        i = int(t["i"])
        if t["agent"] != agent or not (lo <= i <= hi):
            continue
        reas = " ".join(t.get("reasoning_full") or t.get("reasoning") or [])
        print(f"\n--- T{i} {t['clock'][11:16]} wake={t.get('wake')}")
        print("REASONING:", reas[:maxr] + ("…" if len(reas) > maxr else ""))
        for c in t.get("tool_calls", []):
            a = c["args"]
            if c["tool"] in ("chat_postMessage", "chat_update"):
                print(f"POST[{a.get('channel')}]:", a.get("text"))
            elif c["tool"] == "notify_user":
                print("PUSH:", a.get("text"))
            elif c["tool"] not in ("conversations_history", "conversations_replies", "conversations_list", "users_list", "users_info"):
                print(f"CALL {c['tool']}:", json.dumps(a)[:300])
        if t.get("text_to_principal"):
            print("NOTE:", t["text_to_principal"])


if __name__ == "__main__":
    prefix, agent = sys.argv[1], sys.argv[2]
    lo = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    hi = int(sys.argv[4]) if len(sys.argv) > 4 else 10**9
    main(prefix, agent, lo, hi)
