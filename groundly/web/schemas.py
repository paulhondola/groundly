"""Strict Pydantic v2 request and response schemas for Groundly's local web API."""

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, field_validator, model_validator

from groundly.agents import quiz

_TOKEN = re.compile(r"[A-Za-z0-9-]{8,64}")


def _check_sqlite_id(value: int) -> int:
    if not quiz.is_id(value):
        raise ValueError("id is outside SQLite 64-bit integer range")
    return value


SqliteId = Annotated[int, AfterValidator(_check_sqlite_id)]


class SubjectSummary(BaseModel):
    """One subject entry returned by GET /api/subjects."""

    subject: str
    title: str
    has_tree: bool
    error: str | None = None


class SourceItem(BaseModel):
    """One cited material chunk attached to a topic or answer."""

    chunk_id: int
    material_id: int
    filename: str
    page: int | None
    heading_path: str | None
    text: str
    pdf: bool


class TopicProgress(BaseModel):
    """Mastery and bank counters for a topic node."""

    questions: int
    answered: int
    recent: int
    recent_correct: int
    state: Literal["not_started", "in_progress", "mastered"]
    subtopics: int | None
    subtopics_mastered: int | None


class TopicNode(BaseModel):
    """One node in a subject's topic tree."""

    id: int
    title: str
    summary: str
    sources: list[SourceItem]
    subtopics: list["TopicNode"]
    progress: TopicProgress | None


class TreeResponse(BaseModel):
    """Topic map and mastery payload returned by GET /api/subjects/{subject}/tree."""

    title: str
    tree: list[TopicNode]


class QuestionOption(BaseModel):
    """One answer option shown in a drawn test (never includes correctness)."""

    id: int
    text: str


class DrawnQuestion(BaseModel):
    """One question in a drawn test, stripped of keys and reference answers."""

    id: int
    topic_id: int
    kind: Literal["single", "multiple", "open"]
    stem: str
    code: str | None
    options: list[QuestionOption]


class DrawTestResponse(BaseModel):
    """Drawn test payload returned by GET /api/subjects/{subject}/test."""

    questions: list[DrawnQuestion]


class SubmitAttemptRequest(BaseModel):
    """Strict request body for POST /api/subjects/{subject}/attempts."""

    model_config = ConfigDict(strict=True)

    token: str
    topic_id: SqliteId | None = None
    answers: list[dict[str, Any]]

    @field_validator("token")
    @classmethod
    def _validate_token(cls, value: str) -> str:
        if not _TOKEN.fullmatch(value):
            raise ValueError("token must match [A-Za-z0-9-]{8,64}")
        return value


class SubmitAttemptResponse(BaseModel):
    """Created attempt identifier returned by POST /api/subjects/{subject}/attempts."""

    id: int


class AttemptScore(BaseModel):
    """Graded and awaiting answer counts for an attempt."""

    correct: int
    graded: int
    awaiting: int


class AttemptSummary(BaseModel):
    """One past attempt entry returned by GET /api/subjects/{subject}/attempts."""

    id: int
    submitted_at: str
    scope_topic_id: int | None
    scope_title: str | None
    score: AttemptScore
    provisional: bool


class AttemptOption(QuestionOption):
    """One option in a graded attempt result, with key and student selection."""

    correct: bool
    selected: bool


class AttemptAnswer(BaseModel):
    """One graded or awaiting answer in an attempt result."""

    answer_id: int
    question_id: int
    kind: Literal["single", "multiple", "open"]
    state: Literal["correct", "wrong", "awaiting", "unreviewable"]
    removed: bool
    stem: str | None
    code: str | None
    options: list[AttemptOption]
    text: str | None
    reference_answer: str | None
    comment: str | None
    sources: list[SourceItem]


class AttemptTopicSummary(TopicProgress):
    """Topic mastery summary for one topic touched by an attempt."""

    id: int
    title: str


class AttemptResultResponse(BaseModel):
    """Detailed attempt result returned by GET /api/subjects/{subject}/attempts/{id}."""

    id: int
    submitted_at: str
    scope_topic_id: int | None
    score: AttemptScore
    provisional: bool
    answers: list[AttemptAnswer]
    topics: list[AttemptTopicSummary]


class RunErrorDetail(BaseModel):
    """Structured error reason and detail for a failed page run."""

    reason: str
    detail: str


class RunSnapshot(BaseModel):
    """In-memory state snapshot of the current or most recent headless page run."""

    id: int
    kind: Literal["write", "grade"]
    subject: str
    topic_id: int | None
    status: Literal["running", "done", "failed", "stopped"]
    phase: Literal["starting", "thinking", "reading", "writing"] | None
    active_tool: str | None
    thought: str
    thought_channel: Literal["thinking", "text"] | None
    steps: list[str]
    summary: str | None
    error: RunErrorDetail | None
    started_at: str


class RunsInfoResponse(BaseModel):
    """Host availability and active run snapshot returned by GET /api/runs."""

    available: bool
    reason: str | None
    target: int
    run: RunSnapshot | None


class StartRunRequest(BaseModel):
    """Strict request body for POST /api/subjects/{subject}/runs."""

    model_config = ConfigDict(strict=True)

    kind: Literal["write", "grade"]
    topic_id: SqliteId | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> "StartRunRequest":
        if self.kind == "write" and self.topic_id is None:
            raise ValueError('topic_id is required when kind is "write"')
        if self.kind == "grade" and self.topic_id is not None:
            raise ValueError('topic_id must not be provided when kind is "grade"')
        return self


class StopRunRequest(BaseModel):
    """Strict request body for POST /api/runs/stop (empty JSON object allowed)."""

    model_config = ConfigDict(strict=True)


class StopRunResponse(BaseModel):
    """Run state returned by POST /api/runs/stop."""

    run: RunSnapshot | None
