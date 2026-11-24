export type RunPhase = "starting" | "thinking" | "reading" | "writing" | null;
export type RunKind = "write" | "grade";
export type RunStatus = "running" | "done" | "failed" | "stopped";
export type ThoughtChannel = "thinking" | "text" | null;

export interface RunErrorDetail {
  reason: string;
  detail: string;
}

export interface RunSnapshot {
  id: number;
  kind: RunKind;
  subject: string;
  topic_id: number | null;
  status: RunStatus;
  phase: RunPhase;
  active_tool: string | null;
  thought: string;
  thought_channel: ThoughtChannel;
  steps: string[];
  summary: string | null;
  error: RunErrorDetail | null;
  started_at: string;
}

export interface RunsInfo {
  available: boolean;
  reason: string | null;
  target: number;
  run: RunSnapshot | null;
}

export type StartRunBody = { kind: "write"; topic_id: number } | { kind: "grade" };

export interface StopRunResponse {
  run: RunSnapshot | null;
}

export interface SubjectSummary {
  subject: string;
  title: string;
  has_tree: boolean;
  error?: string | null;
}

export interface SourceItem {
  chunk_id: number;
  material_id: number;
  filename: string;
  page: number | null;
  heading_path: string | null;
  text: string;
  pdf: boolean;
}

export type MasteryState = "not_started" | "in_progress" | "mastered";

export interface TopicProgress {
  questions: number;
  answered: number;
  recent: number;
  recent_correct: number;
  state: MasteryState;
  subtopics: number | null;
  subtopics_mastered: number | null;
}

export interface TopicNode {
  id: number;
  title: string;
  summary: string;
  sources: SourceItem[];
  subtopics: TopicNode[];
  progress: TopicProgress | null;
}

export interface TreeResponse {
  title: string;
  tree: TopicNode[];
}

export type QuestionKind = "single" | "multiple" | "open";

export interface QuestionOption {
  id: number;
  text: string;
}

export interface DrawnQuestion {
  id: number;
  topic_id: number;
  kind: QuestionKind;
  stem: string;
  code: string | null;
  options: QuestionOption[];
}

export interface DrawTestResponse {
  questions: DrawnQuestion[];
}

export interface SubmitAttemptResponse {
  id: number;
}

export interface AttemptScore {
  correct: number;
  graded: number;
  awaiting: number;
}

export interface AttemptSummary {
  id: number;
  submitted_at: string;
  scope_topic_id: number | null;
  scope_title: string | null;
  score: AttemptScore;
  provisional: boolean;
}

export interface AttemptOption extends QuestionOption {
  correct: boolean;
  selected: boolean;
}

export type AnswerState = "correct" | "wrong" | "awaiting" | "unreviewable";

export interface AttemptAnswer {
  answer_id: number;
  question_id: number;
  kind: QuestionKind;
  state: AnswerState;
  removed: boolean;
  stem: string | null;
  code: string | null;
  options: AttemptOption[];
  text: string | null;
  reference_answer: string | null;
  comment: string | null;
  sources: SourceItem[];
}

export interface AttemptTopicSummary extends TopicProgress {
  id: number;
  title: string;
}

export interface AttemptResult {
  id: number;
  submitted_at: string;
  scope_topic_id: number | null;
  score: AttemptScore;
  provisional: boolean;
  answers: AttemptAnswer[];
  topics: AttemptTopicSummary[];
}

export interface SSEPhaseEvent {
  id: number;
  phase: RunPhase;
  active_tool: string | null;
}

export interface SSEDeltaEvent {
  id: number;
  channel: Exclude<ThoughtChannel, null>;
  text: string;
  reset: boolean;
}

export interface SSEStepEvent {
  id: number;
  step: string;
  refresh: boolean;
}
