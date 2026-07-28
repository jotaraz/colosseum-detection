"""social_jira4 — adaptive stress-testing for oddly-specific lies.

An adaptive (closed-loop) layer on top of the ``social_jira3`` pairing game. A **prompter**
LLM writes a target prompt from a fixed *template* (some blocks free-text, some drawn from a
pool); the target run is scored by a **critic** on how *specific* any executed lie was; the
verdict + explanation are fed back to the prompter, which adapts the prompt and tries again.
A **checks-and-balances** model gates every candidate prompt so it never *encourages* lying —
the pressure must come from the situation (decoys + confidentiality + task/feeling conflict),
never from an instruction to lie.

This package is the **v0** loop (see ``specs.md``): a single-thread, hill-climbing optimizer
(PAIR-style). v1 will swap the single-thread memory for a MAP-Elites archive (Rainbow
Teaming) without touching the target, critic, or template.

Module map
----------
* ``blocks``          — the ``Blocks`` dataclass: the six template blocks + (de)serialization.
* ``pools``           — the fixed pools (drawn blocks) and free-text anchors the prompter sees.
* ``interfaces``      — ``TargetRunner`` / ``Critic`` / ``ChecksBalances`` protocols + result types.
* ``stubs``           — runnable placeholders so the loop runs end-to-end before the real
                        critic/target are wired (``HeuristicCritic``, ``HeuristicChecks``,
                        ``FakeTargetRunner``).
* ``prompter``        — the LLM-as-optimizer (OPRO-style presentation of past attempts).
* ``adaptive_prompts``— ``AdaptivePrompts(SocialJiraPrompts)``: injects the free blocks at the
                        existing render chokepoint (no edit to social_jira3).
* ``target_run``      — a copied, slimmed social_jira3 runner that swaps in ``AdaptivePrompts``.
* ``loop``            — the driver: propose -> C&B gate -> run x seeds -> critic -> record.
"""
