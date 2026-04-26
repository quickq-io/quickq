# PHQ-9 Patient Health Questionnaire

**Version:** 1.0  
**URL:** http://quickq.io/instruments/phq9  

9-item depression screening instrument (Kroenke & Spitzer, 2002)

---

## Over the last 2 weeks, how often have you been bothered by any of the following problems?

**1. Little interest or pleasure in doing things**
`phq9.1` · `single_choice` · LOINC:44250-9 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**2. Feeling down, depressed, or hopeless**
`phq9.2` · `single_choice` · LOINC:44255-8 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**3. Trouble falling or staying asleep, or sleeping too much**
`phq9.3` · `single_choice` · LOINC:44259-0 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**4. Feeling tired or having little energy**
`phq9.4` · `single_choice` · LOINC:44254-1 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**5. Poor appetite or overeating**
`phq9.5` · `single_choice` · LOINC:44251-7 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**6. Feeling bad about yourself — or that you are a failure or have let yourself or your family down**
`phq9.6` · `single_choice` · LOINC:44258-2 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**7. Trouble concentrating on things, such as reading the newspaper or watching television**
`phq9.7` · `single_choice` · LOINC:44252-5 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**8. Moving or speaking so slowly that other people could have noticed — or being so fidgety or restless that you have been moving around a lot more than usual**
`phq9.8` · `single_choice` · LOINC:44253-3 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

**9. Thoughts that you would be better off dead, or of hurting yourself in some way**
`phq9.9` · `single_choice` · LOINC:44260-8 · *required*
*Scored in: PHQ-9 Total Score*

- `0` Not at all · LOINC:LA6568-5
- `1` Several days · LOINC:LA6569-3
- `2` More than half the days · LOINC:LA6570-1
- `3` Nearly every day · LOINC:LA6571-9

## Functional Impact

**10. If you checked off any problems, how difficult have these problems made it for you to do your work, take care of things at home, or get along with other people?**
`phq9.difficulty` · `single_choice` · LOINC:44261-6
*Show when: `phq9.1` ≠ 0 or `phq9.2` ≠ 0 or `phq9.3` ≠ 0*

- `0` Not difficult at all · LOINC:LA6572-7
- `1` Somewhat difficult · LOINC:LA6573-5
- `2` Very difficult · LOINC:LA6574-3
- `3` Extremely difficult · LOINC:LA6575-0

---

## Scoring

### PHQ-9 Total Score

Sum of items 1–9 (0–27)

**Formula:** sum of `phq9.1`, `phq9.2`, `phq9.3`, `phq9.4`, `phq9.5`, `phq9.6`, `phq9.7`, `phq9.8`, `phq9.9`

| Score | Category |
|---|---|
| 0–4 | Minimal depression |
| 5–9 | Mild depression |
| 10–14 | Moderate depression |
| 15–19 | Moderately severe depression |
| 20–27 | Severe depression |

