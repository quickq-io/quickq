from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class OptionDef:
    text: str
    value: str
    concept: str | None = None      # 'VOCAB:code', e.g. 'SNOMED:373066001'
    is_other: bool = False
    is_exclusive: bool = False


@dataclass
class SkipCondition:
    question: str                   # link_id of the trigger question
    operator: str                   # =, !=, >, <, >=, <=, exists, not_exists
    value: str | None = None


@dataclass
class ShowWhen:
    conditions: list[SkipCondition]
    behavior: str = "all"           # all = AND, any = OR


@dataclass
class QuestionDef:
    # link_id / text / type are required for inline questions; can be empty
    # when library_ref is set (the loader resolves all fields from the DB).
    link_id: str = ""
    text: str = ""
    type: str = ""
    library_ref: str | None = None  # link_id of a pre-loaded library question
    help_text: str | None = None
    concept: str | None = None      # 'VOCAB:code'
    options: list[OptionDef] | None = None
    option_set: str | None = None   # name of a shared option_set in the questionnaire
    show_when: ShowWhen | None = None
    required: bool = False
    source_instrument: str | None = None
    source_item_id: str | None = None
    citation: str | None = None
    numeric_min: float | None = None
    numeric_max: float | None = None
    numeric_step: float | None = None
    slider_min_label: str | None = None
    slider_max_label: str | None = None

    def __post_init__(self) -> None:
        if not self.library_ref and not self.link_id:
            raise ValueError("QuestionDef requires either link_id or library_ref")


@dataclass
class SectionDef:
    title: str | None = None
    description: str | None = None
    questions: list[QuestionDef] = field(default_factory=list)


@dataclass
class ScoringCategoryDef:
    label: str
    min_score: float | None = None
    max_score: float | None = None


@dataclass
class ScoringRuleDef:
    name: str
    formula: str                    # 'sum', 'mean', 'count', or expression
    items: list[str] = field(default_factory=list)  # link_ids of scored questions
    categories: list[ScoringCategoryDef] = field(default_factory=list)
    description: str | None = None


@dataclass
class QuestionnaireDef:
    name: str
    version: str = "1.0"
    canonical_url: str | None = None
    description: str | None = None
    fhir_status: str = "draft"
    option_sets: dict[str, list[OptionDef]] = field(default_factory=dict)
    sections: list[SectionDef] = field(default_factory=list)
    scoring: list[ScoringRuleDef] = field(default_factory=list)
