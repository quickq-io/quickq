# Prenatal Visit Log

**Version:** 1.0  
**URL:** http://quickq.io/instruments/prenatal-visits  

Tracks individual prenatal care visits for a respondent.

---

**1. How many prenatal visits did you have in total?**
`visit_count` · `numeric` · *required*

*Numeric response*

**2. Visit details**
`visits` · `repeating_group`

*Repeating group — one set of sub-questions per instance*

  **1. Week of pregnancy at visit**
  `visits.week` · `numeric` · *required*

  *Numeric response*

  **2. Type of provider seen**
  `visits.provider` · `single_choice`

  - `ob` OB/GYN
  - `midwife` Midwife
  - `np` NP/PA

  **3. Were any concerns documented?**
  `visits.concern` · `boolean`

  - Yes / No

