# PHQ-9 Patient Health Questionnaire

| # | Variable | Label | Type | Concept | Valid Values | Skip Conditions | Scoring Rules |
|---|---|---|---|---|---|---|---|
| 0 | `phq9.1` | Little interest or pleasure in doing things | single_choice | LOINC:44250-9 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 1 | `phq9.2` | Feeling down, depressed, or hopeless | single_choice | LOINC:44255-8 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 2 | `phq9.3` | Trouble falling or staying asleep, or sleeping too much | single_choice | LOINC:44259-0 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 3 | `phq9.4` | Feeling tired or having little energy | single_choice | LOINC:44254-1 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 4 | `phq9.5` | Poor appetite or overeating | single_choice | LOINC:44251-7 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 5 | `phq9.6` | Feeling bad about yourself — or that you are a failure or have let yourself or your family down | single_choice | LOINC:44258-2 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 6 | `phq9.7` | Trouble concentrating on things, such as reading the newspaper or watching television | single_choice | LOINC:44252-5 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 7 | `phq9.8` | Moving or speaking so slowly that other people could have noticed — or being so fidgety or restless that you have been moving around a lot more than usual | single_choice | LOINC:44253-3 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 8 | `phq9.9` | Thoughts that you would be better off dead, or of hurting yourself in some way | single_choice | LOINC:44260-8 | 0=Not at all<br>1=Several days<br>2=More than half the days<br>3=Nearly every day |  | PHQ-9 Total Score |
| 9 | `phq9.difficulty` | If you checked off any problems, how difficult have these problems made it for you to do your work, take care of things at home, or get along with other people? | single_choice | LOINC:44261-6 | 0=Not difficult at all<br>1=Somewhat difficult<br>2=Very difficult<br>3=Extremely difficult | show when phq9.1 != 0; show when phq9.2 != 0; show when phq9.3 != 0 |  |
