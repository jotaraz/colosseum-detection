"""social-jira3: implicit feelings via a personal-message inbox, naturalized instructions.

Successor to social_jira2. The game is unchanged (each agent is the personal assistant of
one employee; employees must be paired onto role-driven tasks; each task needs a pair formed
by two assistants claiming the same task id), but the private information now arrives
realistically and the discretion ask is naturalized (SPEC.md):

  1. **Implicit feelings via an inbox.** The dislike is no longer an explicit briefing; it
     appears in ONE past personal message the employee sent to someone else, buried among
     unrelated distractor messages. The assistant may or may not infer that it constrains
     the pairing (upstream question: *signal uptake*).
  2. **Naturalized instructions.** No per-case secrecy scaffolding and no red line: one
     generic workplace norm + a generic "make sure your employee is happy" goal (together
     the ``pointer`` axis, on/off). The constraint's weight comes entirely from the message
     wording (``dislike_strength``: mild / strong / quit).
  3. **A closing summary.** After committing, each assistant writes a short private summary
     (audience ``employee`` or ``manager``) — a second leakage surface and a faithfulness
     probe.

Scenario generation (roster, tasks, goodness, G*-set, feelings, resolvable/conflict types)
is reused from ``social_jira2.scenario`` unchanged; the inbox is a presentation layer on a
separate seed stream (``inbox.py``), so enabling or reparameterizing it never shifts the
instance. Like jira2, the base build is log-only: clean transcripts + structured ground
truth (full inbox with the signal flagged) in ``scenario.json``; the adapted turn judge and
the new summary judge run offline (``judge.py``).
"""
