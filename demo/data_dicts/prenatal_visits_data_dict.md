# Prenatal Visit Log

| # | Variable | Label | Type | Concept | Valid Values | Skip Conditions | Scoring Rules |
|---|---|---|---|---|---|---|---|
| 0 | `visit_count` | How many prenatal visits did you have in total? | numeric |  |  |  |  |
| 0 | `visits.week` | Week of pregnancy at visit | numeric |  |  |  |  |
| 1 | `visits` | Visit details | repeating_group |  |  |  |  |
| 1 | `visits.provider` | Type of provider seen | single_choice |  | ob=OB/GYN<br>midwife=Midwife<br>np=NP/PA |  |  |
| 2 | `visits.concern` | Were any concerns documented? | boolean |  |  |  |  |
