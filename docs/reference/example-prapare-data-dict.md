!!! note "Generated output"
    This page was produced by running `quickq data-dict` against a database initialized with `quickq init --with-library`. Nothing was written by hand.

# PRAPARE

| # | Variable | Label | Type | Concept | Valid Values | Skip Conditions | Scoring Rules |
|---|---|---|---|---|---|---|---|
| 0 | `prapare.hispanic` | Are you Hispanic or Latino? | single_choice | LOINC:56051-6 | 1=Yes<br>0=No<br>7=Refused<br>9=Don't know |  |  |
| 1 | `prapare.race` | Which race(s) are you? (select best answer) | single_choice | LOINC:32624-9 | 1=American Indian or Alaska Native<br>2=Asian<br>3=Black or African American<br>4=Native Hawaiian or Other Pacific Islander<br>5=White<br>9=Unknown |  |  |
| 2 | `prapare.farm_worker` | At any point in the past 2 years, has seasonal or migrant farm work been your or your family's main source of income? | single_choice | LOINC:93035-4 | 1=Yes<br>0=No<br>9=I choose not to answer this question |  |  |
| 3 | `prapare.military` | Have you or any family members ever served in the military? | single_choice | LOINC:93034-7 | 1=Yes<br>0=No<br>9=I choose not to answer this question |  |  |
| 4 | `prapare.language` | What language are you most comfortable speaking? | single_choice | LOINC:54899-0 | 1=English<br>2=Spanish<br>3=Chinese<br>4=Vietnamese<br>5=Tagalog<br>6=Other |  |  |
| 5 | `prapare.household_size` | How many people are living or staying at your address? | numeric | LOINC:63512-8 |  |  |  |
| 6 | `prapare.housing_status` | What is your housing situation today? | single_choice | LOINC:71802-3 | 1=I have a steady place to live<br>2=I have a place to live today, but I am worried about losing it in the future<br>3=I do not have a steady place to live (temporarily staying with others, hotel, shelter, or outside) |  |  |
| 7 | `prapare.housing_concern` | Are you worried about losing your housing? | single_choice | LOINC:93033-9 | 1=Yes<br>0=No<br>9=I choose not to answer this question |  |  |
| 8 | `prapare.address` | What is your current address? | text | LOINC:56799-0 |  |  |  |
| 9 | `prapare.education` | What is the highest level of school that you have finished? | single_choice | LOINC:82589-3 | 1=Less than high school degree<br>2=High school diploma or GED<br>3=More than high school |  |  |
| 10 | `prapare.employment` | What is your current work situation? | single_choice | LOINC:67875-5 | 1=Unemployed<br>2=Part-time or temporary work<br>3=Full-time work<br>4=Otherwise unemployed but not seeking work (e.g., retired, student, disabled, caregiver)<br>5=Student |  |  |
| 11 | `prapare.insurance` | What is your main insurance? | single_choice | LOINC:76437-3 | 1=None/uninsured<br>2=Medicaid<br>3=CHIP Medicaid<br>4=Medicare<br>5=Other public insurance (not CHIP)<br>6=Other public insurance (CHIP)<br>7=Private insurance |  |  |
| 12 | `prapare.income` | During the past year, what was the total combined income for you and the family members you live with? Please include money from jobs, disability payments, unemployment benefits, food stamps, Social Security, child support, and any other sources. | numeric | LOINC:63586-2 |  |  |  |
| 13 | `prapare.necessities` | In the past year, have you or any family members you live with been unable to get any of the following when it was really needed? (check all that apply) | sata_other | LOINC:93031-3 | 1=Food<br>2=Clothing<br>3=Utilities<br>4=Child care<br>5=Medicine or Any Health Care<br>6=Phone<br>7=Other<br>9=I choose not to answer this question |  |  |
| 14 | `prapare.transportation` | Has lack of transportation kept you from medical appointments, meetings, work, or from getting things needed for daily living? | boolean | LOINC:93030-5 |  |  |  |
| 15 | `prapare.social_contact` | How often do you see or talk to people that you care about and feel close to (for example: talking to friends on the phone, visiting friends or family, going to church or club meetings)? | single_choice | LOINC:93029-7 | 1=Less than once a week<br>2=1 or 2 times a week<br>3=3 to 5 times a week<br>4=5 or more times a week<br>9=I choose not to answer this question |  |  |
| 16 | `prapare.stress` | Stress is when someone feels tense, nervous, anxious, or can't sleep at night because their mind is troubled. How stressed are you? | likert | LOINC:93038-8 | 1=Not at all<br>2=A little bit<br>3=Somewhat<br>4=Quite a bit<br>5=Very much<br>9=I choose not to answer this question |  |  |
| 17 | `prapare.incarceration` | Have you spent more than 2 nights in a row in a jail, prison, detention center, or juvenile correctional facility in the past 12 months? | single_choice | LOINC:93028-9 | 1=Yes<br>0=No<br>9=I choose not to answer this question |  |  |
| 18 | `prapare.refugee` | Are you a refugee? | single_choice | LOINC:93027-1 | 1=Yes<br>0=No<br>9=I choose not to answer this question |  |  |
| 19 | `prapare.safety` | Do you feel physically and emotionally safe where you currently live? | single_choice | LOINC:93026-3 | 1=Yes<br>0=No<br>2=Unsure<br>9=I choose not to answer this question |  |  |
| 20 | `prapare.partner_fear` | Are you afraid of your partner or ex-partner? | boolean | LOINC:76501-6 |  |  |  |
