"""A page button starts the student's own Claude Code headless on one of the skill's flows.

Groundly holds no key and builds no client: the run is the student's `claude` on the student's
login. What the run may touch is fixed here, never by the student's settings: only Groundly's
MCP server, no built-in tools, a per-flow tool allowlist and the packaged skill. The prompt
carries only the flow, the subject name and a topic id; titles, summaries and other layer-3
text reach the run through tools, as data. The page learns what the run did from tool names
and counts, never from model text, except the final summary, which it shows as text.
"""

import json
import shutil
import sys
from importlib.resources import files

TARGET_QUESTIONS = 10  # a subtopic is filled to the mastery window
RUN_TIMEOUT_S = 20 * 60
MAX_STEPS = 50
MAX_SUMMARY_CHARS = 2000

WRITE_TOOLS = tuple(
    f"mcp__groundly__{name}"
    for name in ("list_subjects", "progress", "search", "get_page", "submit_questions")
)
GRADE_TOOLS = ("mcp__groundly__review_answers", "mcp__groundly__get_page")
_TOOLS = {"write": WRITE_TOOLS, "grade": GRADE_TOOLS}


class RunError(Exception):
    """A run that cannot start, with the reason and status the page route answers with."""

    def __init__(self, reason: str, detail: str, status: int) -> None:
        super().__init__(detail)
        self.reason, self.detail, self.status = reason, detail, status


def availability() -> tuple[str | None, str | None]:
    """The `claude` to run, or why there is none. Resolved per call: PATH may change."""
    if sys.platform == "win32":
        # an npm-installed claude is claude.cmd, which Python runs through cmd.exe
        return None, "unsupported_platform"
    path = shutil.which("claude")
    return (path, None) if path else (None, "no_host")


def mcp_config(mcp_url: str | None) -> dict:
    """Groundly only: the running process's /mcp under `start`, else this installation's stdio."""
    if mcp_url:
        server = {"type": "http", "url": mcp_url}
    else:
        server = {"command": sys.executable, "args": ["-m", "groundly", "mcp"]}
    return {"mcpServers": {"groundly": server}}


_ENDING = (
    " Finish with at most three plain sentences for the student: what was stored or graded,"
    " and what was rejected. Do not tell them to run any command, and do not call any"
    " question, answer key or verdict verified."
)
_WRITE_LEAF = (
    'Follow "Write questions for a topic" for subject "{subject}", topic id {topic_id}:'
    " bring that topic to {target} questions."
)
_WRITE_BIG = (
    'Follow "Write questions for a topic" for subject "{subject}": for each subtopic of topic id'
    " {topic_id} that has fewer than {target} questions (see `progress`), bring it to"
    " {target} questions."
)
_GRADE = (
    'Follow "Grade a test" for subject "{subject}": review every written answer awaiting review,'
    " calling `review_answers` until `remaining` is 0."
)


def build_prompt(kind: str, subject: str, topic_id: int | None, big: bool) -> str:
    """The fixed prompt for a flow. `subject` is a validated subject name, `topic_id` an int."""
    if kind == "grade":
        template = _GRADE
    else:
        template = _WRITE_BIG if big else _WRITE_LEAF
    return template.format(subject=subject, topic_id=topic_id, target=TARGET_QUESTIONS) + _ENDING


def _skill_path() -> str:
    return str(files("groundly").joinpath("skill/SKILL.md"))


def build_argv(claude: str, kind: str, prompt: str, mcp_url: str | None) -> list[str]:
    """The isolated run: none of the student's settings, no built-in tools, this flow's tools."""
    return [
        claude,
        "-p",
        prompt,
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp_config(mcp_url)),
        "--setting-sources",
        "",
        "--tools",
        "",
        "--allowedTools",
        ",".join(_TOOLS[kind]),
        "--permission-mode",
        "dontAsk",
        "--append-system-prompt-file",
        _skill_path(),
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]


def check_init(event: dict) -> tuple[str, str] | None:
    """None when the run holds only Groundly's tools and a connected Groundly server; else the
    failure reason and what appeared. Checked rather than assumed: another `claude` build may
    honour the isolation flags differently."""
    extra = [t for t in event.get("tools", []) if not str(t).startswith("mcp__groundly__")]
    if extra:
        return "isolation_failed", "the run had tools other than Groundly's: " + ", ".join(extra)
    servers = event.get("mcp_servers", [])
    others = [s.get("name") for s in servers if s.get("name") != "groundly"]
    if others:
        return "isolation_failed", "the run had MCP servers other than Groundly: " + ", ".join(
            map(str, others)
        )
    status = next((s.get("status") for s in servers if s.get("name") == "groundly"), "missing")
    if status != "connected":
        return "groundly_not_connected", f"Groundly's MCP server did not connect (status: {status})"
    return None


def _payload(content) -> dict | None:
    """A tool result's JSON object: a string or text blocks, possibly wrapped as {"result": …}."""
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        data = data["result"]
    return data if isinstance(data, dict) else None


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 0


_USE_WORDS = {
    "search": "Reading the course",
    "get_page": "Opening a cited page",
    "progress": "Checking question counts",
    "list_subjects": "Checking the subject",
}


class Steps:
    """Turns stream-json events into plain-word steps from tool names and counts only."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._last: str | None = None

    def _emit(self, step: str | None, out: list[str]) -> None:
        if step and step != self._last:
            out.append(step)
            self._last = step

    def _use(self, block: dict) -> str | None:
        name = str(block.get("name", "")).removeprefix("mcp__groundly__")
        self._names[str(block.get("id"))] = name
        tool_input = block.get("input") or {}
        if name == "submit_questions":
            return f"Submitting {_count(tool_input.get('questions'))} questions"
        if name == "review_answers":
            reviews = tool_input.get("reviews")
            if reviews:
                return f"Recording {_count(reviews)} verdicts"
            return "Reading answers awaiting review"
        return _USE_WORDS.get(name)

    def _result(self, block: dict) -> str | None:
        if block.get("is_error"):
            return "A tool call failed"
        name = self._names.get(str(block.get("tool_use_id")))
        data = _payload(block.get("content"))
        if data is None:
            return None
        if name == "submit_questions":
            accepted, rejected = _count(data.get("accepted")), _count(data.get("rejected"))
            return f"{accepted} stored, {rejected} rejected"
        if name == "review_answers" and "recorded" in data:
            return f"{_count(data.get('recorded'))} verdicts recorded"
        return None

    def feed(self, event: dict) -> list[str]:
        out: list[str] = []
        if event.get("type") not in ("assistant", "user"):
            return out
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self._emit(self._use(block), out)
            elif block.get("type") == "tool_result":
                self._emit(self._result(block), out)
        return out


def summary_of(event: dict) -> tuple[str | None, tuple[str, str] | None]:
    """The final `result` event: the summary shown to the student, or the host's error."""
    text = str(event.get("result") or "")[:MAX_SUMMARY_CHARS]
    if event.get("is_error") or event.get("subtype") != "success":
        return None, ("host_error", text or str(event.get("subtype") or "the run failed"))
    return text, None
