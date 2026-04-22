# Survey Authoring

Authoring in **quickq** is designed to be programmatic and reproducible. You can author surveys using the Python SDK, YAML definitions, or by importing existing FHIR resources.

## The Authoring Model

A questionnaire is structured into several planes:

*   **Instrument Plane:** Defines the hierarchy of Study → Questionnaire → Section → Question.
*   **Concept Plane:** Maps questions and options to standard vocabularies (LOINC, SNOMED).
*   **Response Plane:** Defines the expected answer types and options.

### Question Types

**quickq** supports all common health research question types:

| Type | Description |
|---|---|
| `single_choice` | Radio buttons / Multiple choice (one answer) |
| `multiple_choice` | Checkboxes / Select all that apply |
| `sata_other` | Select all that apply with a free-text "Other" option |
| `boolean` | Yes / No |
| `text` | Open-ended text |
| `numeric` | Integer or Decimal |
| `date` / `datetime` | Date or Date/Time pickers |
| `grid` | Matrix / Grid questions |
| `likert` | Ordered scales |
| `ranked` | Drag-and-drop ranking |
| `slider` | Visual analog scale |

## Example: Python SDK

```python
from quickq.authoring import QuestionnaireDef, SectionDef, QuestionDef, OptionDef

q_def = QuestionnaireDef(
    name="Patient Health Questionnaire (PHQ-9)",
    version="1.0",
    sections=[
        SectionDef(
            title="Mood",
            questions=[
                QuestionDef(
                    link_id="phq-1",
                    text="Little interest or pleasure in doing things?",
                    type="single_choice",
                    options=[
                        OptionDef(text="Not at all", value="0"),
                        OptionDef(text="Several days", value="1"),
                        OptionDef(text="More than half the days", value="2"),
                        OptionDef(text="Nearly every day", value="3"),
                    ]
                )
            ]
        )
    ]
)
```

## Idempotency and Immutability

To ensure data integrity, **quickq** treats questions as **immutable** once they have been used in a study.

* If you change the text of a question, you must create a new `link_id`.
* A new version of the question is created with a new `link_id` to preserve the relationship between versions.
* Importing the same FHIR URL and Version twice is a safe, no-op operation.
