from dataclasses import dataclass, field
from enum import StrEnum


class Source(StrEnum):
    PMC = "PubMed Central"
    BMC = "BMC Medicine"


@dataclass(frozen=True)
class SearchPlan:
    question: str
    keywords: str
    terms: tuple[str, ...]
    start_year: int
    end_year: int
    limit: int
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Article:
    title: str
    journal: str
    year: int | None
    authors: str
    abstract: str
    doi: str = ""
    pmcid: str = ""
    links: dict[Source, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceResult:
    source: Source
    articles: tuple[Article, ...] = ()
    error: str = ""
    note: str = ""


@dataclass(frozen=True)
class Evidence:
    id: str
    article: Article
    excerpt: str


@dataclass(frozen=True)
class Finding:
    statement: str
    evidence_id: str
    quote: str


@dataclass(frozen=True)
class Event:
    agent: str
    detail: str
    seconds: float


@dataclass(frozen=True)
class TranslationCheck:
    agent: str
    passed: bool
    detail: str
    seconds: float


@dataclass(frozen=True)
class QueryTranslation:
    original: str
    english: str
    reverse: str
    checks: tuple[TranslationCheck, ...]
    seconds: float
    override: str = ""

    @property
    def approved(self) -> bool:
        return bool(self.english) and all(check.passed for check in self.checks)


@dataclass(frozen=True)
class Report:
    plan: SearchPlan
    sources: tuple[SourceResult, ...]
    articles: tuple[Article, ...]
    evidence: tuple[Evidence, ...]
    findings: tuple[Finding, ...]
    events: tuple[Event, ...]
    translation: QueryTranslation | None = None
