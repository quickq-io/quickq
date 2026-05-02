!!! note "Generated output"
    This page was produced by running `quickq render demo/study.db <id>` against a database loaded with the Gout Symptoms Check-In instrument. Nothing was written by hand.

# Gout Symptoms Check-In and Family History

**Version:** 1.0  
**URL:** http://quickq.io/instruments/gout-checkin  

Synthetic gout check-in form. Covers question types: date, datetime, multiple_choice, grid, slider, ranked, boolean, numeric, text.

---

## Recent Attacks

**1. When did your most recent gout attack begin?**
`gout.last_attack_date` · `date`

*Date*

**2. Date and time your most recent gout attack began (if known):**
`gout.last_attack_datetime` · `datetime`

*Date and time*

**3. How many gout attacks have you had in the past 12 months?**
`gout.attacks_12mo` · `numeric`

*Numeric response*

**4. Which joints were affected in your most recent attack? (select all that apply)**
`gout.attack_joints` · `multiple_choice`

- `big_toe` Big toe (either foot)
- `ankle` Ankle
- `knee` Knee
- `wrist` Wrist
- `elbow` Elbow
- `finger` Finger joints

## Current Symptoms

**5. Rate pain and swelling in each joint right now:**
`gout.joint_severity` · `grid`

| | None (`0`) | Mild (`1`) | Moderate (`2`) | Severe (`3`) |
|---|---|---|---|---|
| Right big toe | | | | |
| Left big toe | | | | |
| Right ankle | | | | |
| Left ankle | | | | |
| Right knee | | | | |
| Left knee | | | | |

## Family History

**6. Which blood relatives have been diagnosed with gout? (select all that apply)**
`gout.family_gout` · `multiple_choice`

- `father` Biological father
- `mother` Biological mother
- `sibling` Brother or sister
- `mat_gp` Maternal grandparent
- `pat_gp` Paternal grandparent
- `cousin` First cousin, aunt, or uncle
- `none` None of the above

**7. Has any first-degree blood relative (parent, sibling) ever been diagnosed with any of the following?**
`gout.family_conditions` · `grid`

| | Gout (`gout`) | Kidney stones (`kidney`) | Hypertension (`htn`) | Diabetes (`dm`) | None of the above (`none`) |
|---|---|---|---|---|---|
| Biological father | | | | | |
| Biological mother | | | | | |
| Brother or sister | | | | | |

## Management & Labs

**8. Are you currently taking a urate-lowering therapy (e.g., allopurinol, febuxostat, probenecid)?**
`gout.on_ult` · `boolean`

- Yes / No

**9. Most recent serum uric acid level (mg/dL):**
`gout.uric_acid` · `numeric`

*Numeric response*

**10. Date of that uric acid blood test:**
`gout.uric_acid_date` · `date`

*Date*

**11. How would you rate your overall gout-related pain right now? (0 = no pain, 100 = worst imaginable pain)**
`gout.pain_vas` · `slider`

*Slider (visual analog scale)*

## Priorities & Notes

**12. Rank the following treatment goals from most important (1) to least important (5) to you:**
`gout.treatment_priorities` · `ranked`

- `pain_relief` Reducing pain during attacks
- `prevention` Preventing future attacks
- `side_effects` Minimizing medication side effects
- `function` Maintaining daily activities
- `uric_acid` Reaching target uric acid level

**13. Any additional information you would like your care team to know:**
`gout.notes` · `text`

*Free-text response*
