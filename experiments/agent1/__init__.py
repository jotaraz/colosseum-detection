"""agent1: the agentic variant of social-jira3 — the world is discovered, not dumped.

The game is social_jira3's, unchanged (each agent is the personal assistant of one
employee; employees are paired onto role-driven tasks; a task is claimed by two assistants
calling ``assign_task`` on the same id). What changes is *delivery*: sj3 renders the
roster, the task board, the employee's message inbox and their calendar into the user
prompt, so every assistant starts every turn omniscient about its own slice of the world.
Here the prompt carries only identity, the rules of the game and the current commitments;
the same facts sit behind a per-environment toolset (:mod:`experiments.agent1.tools`) and
have to be fetched.

Two things follow, and they are the point of the experiment:

  1. **Uptake becomes observable.** In sj3 the signal is always *in* the context, so
     "did the assistant take up the private constraint?" can only be inferred from what it
     says. Here reading is an action: ``read_inbox`` appears in the tool log or it does
     not, and a grievance cited without a matching ``read_inbox(thread_id=...)`` call is
     fabricated on the record rather than by a judge's guess.
  2. **Privacy moves from the prompt to the handler.** sj3 keeps colleagues' calendars
     private by never rendering them (``social_jira3/environment.py:233``). A toolset is
     served from one ``env_state`` carrying *everyone's* private data, so the invariant now
     lives in :meth:`TaskAssignTools._own_view` and is tested, not structural.

Scenario generation, inboxes, availability, judges and metrics are sj3's, imported
unchanged; only the environment, the toolset, the prompts and the turn policy are new.
"""
