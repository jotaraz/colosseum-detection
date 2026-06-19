"""Self-sacrifice DCOP experiments.

Probes whether LLM agents accept a globally-optimal assignment that is individually
costly for a *designated* agent, and whether that willingness depends on framing:

  * solver      - the problem is posed as an abstract, fully-anonymized DCOP
                  (task variables T1..Tk, solver nodes N1..Nm, scalar costs).
  * personified - the full Jira narrative plus an explicit "this cost is a real
                  personal hardship for the person you represent" persona block.

crossed with three instance sets in which the designated agent's individual reward
at the global optimum is (1) higher, (2) similar to, or (3) much lower than the
other agents' (self-sacrifice).
"""
