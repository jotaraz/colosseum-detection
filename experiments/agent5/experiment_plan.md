I want to run these groups of experiments:

(which models will be specified)

# (1) Variations on/Additions to PstrongNstrong_affBothNeutralPpl_hzReasonableHelenaProbe

- 1.a) ✅ just that, no addition
- 1.b) ✅ agent messages are clearly identifiable as agent messages (distinguishable from Human messages) with the via="api" tag /  slack_world.enable_client_blocks()

    very boring sofar

- 1.c) confiding

    (1.c.i) confide 1 (Nadia told Zofia shes bad at T2)

    (1.c.ii) confide 2 (Rafael knows as well)
    
    (1.c.iii) confide 3 (Priya knows too) 

        currently: deepseek x2, glm53flash x2, kimi x2

    (1.c.iv) ❗ confide 3 (Priya knows too) with dm-hint2

        currently: none

- 1.d) Callum bereavement

- 1.e) ✅ callumAsk 

    didnt work / the scheduling was messed up (by the time Priya entered the board with Callum's 4PM Matthieu had usually not proposed 4PM but the others had nonetheless settled already --> Priya felt forced)

- 1.f) ✅ callumAsk2

    works better, but they still never claim 4PM scheduling issue as the obstacle to pairing with Matthieu. **check cot**

- 1.g) ❗ callumAsk2, superstrong!, dm-hint

        currently: none

# (2) Other affection states with PstrongNstrong and _hzReasonableHelenaProbe

- 2.a) PstrongNstrong_hzReasonableHelenaProbe no addBothNeutral

- 2.b) PstrongNstrong_addBothT1_hzReasonableHelenaProbe

# (3) Other feelings with _affBothNeutralPpl_hzReasonableHelenaProbe

- 3.a) PdefNstrong_affBothNeutralPpl_hzReasonableHelenaProbe

- 3.b) ❗ PsillyNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbe, dm-hint2

        currently: none

- 3.c) ❗ PbadNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbe, dm-hint2

        currently: none

- 3.d) ❗ PNshared_affBothNeutralPpl_hzReasonableHelenaProbe, dm-hint2

        currently: none

# (4) Other asks

- 4.a) PstrongNstrong_affBothNeutralPpl_hzReasonable

- 4.b) PstrongNstrong_affBothNeutralPpl_hzRafaelStrongHelenaProbe

# (5) Others

- 5.a) ❗ PlazyNsuperstrong_affNNeutral_hzReasonableHelenaProbe, dm-hint2

        currently: none

# (6) Fight

- 6.a) PfightN0_affBothNeutralPpl_hzReasonableHelenaProbe

    seems useless

- 6.b) ✅ PfightNstrong_affBothNeutralPpl_hzReasonableHelenaProbe, dm-hint

    Matthieu's and Priya's combined tension (if they read it) wins against Nadia's dislike for Matthieu, leading to Matthieu+Nadia & Priya+Rafael (in the currently existing 4 rollouts). **How strongly is Matthieu framing here to get Nadia?**

- 6.c) ❗ PfightNsuperstrong_affBothNeutralPpl_hzReasonableHelenaProbe, dm-hint2

        currently: none


-- model classes:
flash: deepseek-v4-flash-0731, glm-5.3-flash, qwen-3.8-flash
medium: glm-5.2, kimi-k2.6
big: deepseek-v4-pro-0813, glm-5.3, kimi-k3