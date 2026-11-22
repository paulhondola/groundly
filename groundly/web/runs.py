"""A page button starts the student's own Claude Code headless on one flow's packaged skill.

Groundly holds no key and builds no client: the run is the student's `claude` on the student's
login. What the run may touch is fixed here, never by the student's settings: only Groundly's
MCP server, no built-in tools, a per-flow tool allowlist and that flow's skill. The prompt
carries only the flow, the subject name and a topic id; titles, summaries and other layer-3
text reach the run through tools, as data. The page learns what the run did from tool names
and counts, never from model text, except the final summary, which it shows as text.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files

from groundly.agents.quiz import MASTERY_WINDOW
from groundly.core.paths import groundly_home

TARGET_QUESTIONS = MASTERY_WINDOW  # a subtopic is filled to the mastery window
RUN_TIMEOUT_S = 20 * 60
MAX_STEPS = 50
MAX_SUMMARY_CHARS = 2000
MAX_STREAM_CHARS = 2000
DELTA_COALESCE_S = 0.04
SUBSCRIBER_QUEUE_MAX = 256
SSE_KEEPALIVE_S = 15.0

WRITE_TOOLS = tuple(
    f"mcp__groundly__{name}"
    for name in ("list_subjects", "progress", "topic", "search", "get_page", "submit_questions")
)
GRADE_TOOLS = ("mcp__groundly__review_answers", "mcp__groundly__get_page")
_TOOLS = {"write": WRITE_TOOLS, "grade": GRADE_TOOLS}
_SKILLS = {"write": "groundly-questions", "grade": "groundly-grade"}


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
        # -P: the run directory never lands on sys.path, so nothing there shadows groundly
        server = {"command": sys.executable, "args": ["-P", "-m", "groundly", "mcp"]}
    return {"mcpServers": {"groundly": server}}


_ENDING = (
    " Finish with at most three plain sentences for the student: what was stored or graded,"
    " and what was rejected. Do not tell them to run any command, and do not call any"
    " question, answer key or verdict verified."
)
_PAGE_RUN = "This is a page run. "
_WRITE_LEAF = (
    'Follow the groundly-questions skill for subject "{subject}", topic id {topic_id}:'
    " bring that topic to {target} questions."
)
_WRITE_BIG = (
    'Follow the groundly-questions skill for subject "{subject}": for each subtopic of topic id'
    " {topic_id} that has fewer than {target} questions (see `progress`), bring it to"
    " {target} questions."
)
_GRADE = (
    'Follow the groundly-grade skill for subject "{subject}": review every written answer awaiting'
    " review, calling `review_answers` until `remaining` is 0."
)


def build_prompt(kind: str, subject: str, topic_id: int | None, big: bool) -> str:
    """The fixed prompt for a flow. `subject` is a validated subject name, `topic_id` an int."""
    if kind == "grade":
        template = _GRADE
    else:
        template = _WRITE_BIG if big else _WRITE_LEAF
    prompt = template.format(subject=subject, topic_id=topic_id, target=TARGET_QUESTIONS)
    return _PAGE_RUN + prompt + _ENDING


def _skill_path(kind: str) -> str:
    return str(files("groundly").joinpath("skills", _SKILLS[kind], "SKILL.md"))


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
        _skill_path(kind),
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--no-session-persistence",
    ]


def check_init(event: dict) -> tuple[str, str] | None:
    """None when the run holds only Groundly's tools and a connected Groundly server; else the
    failure reason and what appeared. Checked rather than assumed: another `claude` build may
    honour the isolation flags differently, or report them in another shape."""
    tools, servers = event.get("tools"), event.get("mcp_servers")
    if not (
        isinstance(tools, list)
        and isinstance(servers, list)
        and all(isinstance(s, dict) for s in servers)
    ):
        return "isolation_failed", "the run's init event did not list its tools and MCP servers"
    extra = [str(t) for t in tools if not str(t).startswith("mcp__groundly__")]
    if extra:
        return "isolation_failed", "the run had tools other than Groundly's: " + ", ".join(extra)
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
    "topic": "Reading the topic",
}


class Steps:
    """Turns stream-json events into plain-word steps from tool names and counts only."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._last: str | None = None

    def _emit(self, step: str | None, out: list[tuple[str, bool]], refresh: bool) -> None:
        if step and step != self._last:
            out.append((step, refresh))
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

    def _result(self, block: dict) -> tuple[str | None, bool]:
        if block.get("is_error"):
            return "A tool call failed", False
        name = self._names.get(str(block.get("tool_use_id")))
        data = _payload(block.get("content"))
        if data is None:
            return None, False
        if name == "submit_questions":
            accepted, rejected = _count(data.get("accepted")), _count(data.get("rejected"))
            return f"{accepted} stored, {rejected} rejected", accepted > 0
        if name == "review_answers" and "recorded" in data:
            recorded = _count(data.get("recorded"))
            return f"{recorded} verdicts recorded", recorded > 0
        return None, False

    def feed_items(self, event: dict) -> list[tuple[str, bool]]:
        """Consume one stream-json event and return ``(step_text, refresh)`` pairs."""
        out: list[tuple[str, bool]] = []
        if event.get("type") not in ("assistant", "user"):
            return out
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                self._emit(self._use(block), out, False)
            elif block.get("type") == "tool_result":
                step, block_refresh = self._result(block)
                self._emit(step, out, block_refresh)
        return out

    def feed(self, event: dict) -> tuple[list[str], bool]:
        items = self.feed_items(event)
        return [step for step, _ in items], any(refresh for _, refresh in items)


@dataclass(frozen=True)
class PhaseEvent:
    """Live activity phase and plain-language tool label emitted by StreamTracker."""

    phase: str | None
    active_tool: str | None


@dataclass(frozen=True)
class DeltaEvent:
    """Live plain-text stream delta (or reset marker) emitted by StreamTracker."""

    channel: str
    text: str
    reset: bool = False


def _tool_phase(name: str, tool_input: dict | None = None) -> tuple[str, str | None]:
    """Map a stripped Groundly tool name (and optional input) to (phase, active_tool)."""
    if name == "submit_questions":
        return "writing", "Drafting questions"
    if name == "review_answers":
        reviews = (tool_input or {}).get("reviews")
        return ("writing" if reviews else "reading"), "Reviewing answers"
    return "reading", _USE_WORDS.get(name)


class StreamTracker:
    """Tracks live run phase, active tool, and tail-capped rolling current-turn thought."""

    def __init__(self) -> None:
        self.phase: str | None = "starting"
        self.active_tool: str | None = None
        self.thought: str = ""
        self.thought_channel: str | None = None
        self._saw_stream_for_turn = False

    def on_init(self) -> PhaseEvent:
        """Transition from starting to initial thinking after system/init passes."""
        self.phase = "thinking"
        self.active_tool = None
        return PhaseEvent(self.phase, self.active_tool)

    def reset_settled(self) -> None:
        """Clear live streaming state once the run reaches any terminal status."""
        self.phase = None
        self.active_tool = None
        self.thought = ""
        self.thought_channel = None
        self._saw_stream_for_turn = False

    def _append_thought(self, channel: str, text: str) -> DeltaEvent | None:
        if not text:
            return None
        self.thought = (self.thought + text)[-MAX_STREAM_CHARS:]
        self.thought_channel = channel
        return DeltaEvent(channel, text)

    def _feed_stream(self, event: dict) -> list[PhaseEvent | DeltaEvent]:
        inner = event.get("event") or {}
        if not isinstance(inner, dict):
            return []
        etype = inner.get("type")
        if etype == "content_block_start":
            block = inner.get("content_block") or {}
            if not isinstance(block, dict):
                return []
            btype = block.get("type")
            if btype in ("thinking", "text"):
                self._saw_stream_for_turn = True
                self.phase = "thinking"
                self.active_tool = None
                self.thought = ""
                self.thought_channel = btype
                return [PhaseEvent("thinking", None), DeltaEvent(btype, "", reset=True)]
            if btype == "tool_use":
                self._saw_stream_for_turn = True
                name = str(block.get("name", "")).removeprefix("mcp__groundly__")
                self.phase, self.active_tool = _tool_phase(name)
                return [PhaseEvent(self.phase, self.active_tool)]
            return []
        if etype == "content_block_delta":
            delta = inner.get("delta") or {}
            if not isinstance(delta, dict):
                return []
            dtype = delta.get("type")
            if dtype == "thinking_delta":
                self._saw_stream_for_turn = True
                ev = self._append_thought("thinking", str(delta.get("thinking") or ""))
                return [ev] if ev is not None else []
            if dtype == "text_delta":
                self._saw_stream_for_turn = True
                ev = self._append_thought("text", str(delta.get("text") or ""))
                return [ev] if ev is not None else []
            if dtype == "input_json_delta":
                self._saw_stream_for_turn = True
                return []
        return []

    def _feed_assistant(self, event: dict) -> list[PhaseEvent | DeltaEvent]:
        out: list[PhaseEvent | DeltaEvent] = []
        content = (event.get("message") or {}).get("content")
        saw_stream = self._saw_stream_for_turn
        self._saw_stream_for_turn = False
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if not saw_stream and btype in ("thinking", "text"):
                text = block.get(btype)
                if isinstance(text, str) and text:
                    self.phase = "thinking"
                    self.active_tool = None
                    self.thought = text[-MAX_STREAM_CHARS:]
                    self.thought_channel = btype
                    out.append(PhaseEvent("thinking", None))
                    out.append(DeltaEvent(btype, self.thought, reset=True))
            elif btype == "tool_use":
                name = str(block.get("name", "")).removeprefix("mcp__groundly__")
                if saw_stream and name != "review_answers":
                    continue
                tool_input = block.get("input")
                phase, active_tool = _tool_phase(
                    name, tool_input if isinstance(tool_input, dict) else None
                )
                if not saw_stream or (phase, active_tool) != (self.phase, self.active_tool):
                    self.phase, self.active_tool = phase, active_tool
                    out.append(PhaseEvent(phase, active_tool))
        return out

    def _feed_user(self, event: dict) -> list[PhaseEvent | DeltaEvent]:
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self.phase = "thinking"
                self.active_tool = None
                return [PhaseEvent("thinking", None)]
        return []

    def feed(self, event: dict) -> list[PhaseEvent | DeltaEvent]:
        """Consume one stream-json event and return any PhaseEvent or DeltaEvent updates."""
        etype = event.get("type")
        if etype == "stream_event":
            return self._feed_stream(event)
        if etype == "assistant":
            return self._feed_assistant(event)
        if etype == "user":
            return self._feed_user(event)
        return []


def summary_of(event: dict) -> tuple[str | None, tuple[str, str] | None]:
    """The final `result` event: the summary shown to the student, or the host's error."""
    text = str(event.get("result") or "")[:MAX_SUMMARY_CHARS]
    if event.get("is_error") or event.get("subtype") != "success":
        return None, ("host_error", text or str(event.get("subtype") or "the run failed"))
    return text, None


_UNAVAILABLE = {
    "no_host": "Claude Code (`claude`) is not on PATH",
    "unsupported_platform": "page runs need macOS or Linux",
}
_MAX_LINE_BYTES = 1_000_000  # a longer line is read in pieces, none of which parses
_STDERR_TAIL = 500


def _limit_words(seconds: float) -> str:
    if seconds >= 60:
        return f"{seconds / 60:g} minute"
    return f"{seconds:g} second"


def _kill(proc: subprocess.Popen) -> None:
    """End the run's whole process group: `claude` and the stdio `groundly mcp` it started,
    which may outlive `claude` itself."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(5)
    except (ProcessLookupError, PermissionError):  # no group left; macOS: only zombies left
        return
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def _clear_live(run: dict) -> None:
    run["phase"] = None
    run["active_tool"] = None
    run["thought"] = ""
    run["thought_channel"] = None


_Subscriber = tuple[
    asyncio.AbstractEventLoop,
    asyncio.Queue[tuple[str, dict | None] | None],
]


class Runner:
    """At most one run at a time across the page. State lives in memory only."""

    def __init__(self, mcp_url: str | None = None) -> None:
        self.mcp_url = mcp_url
        self._lock = threading.Lock()
        self._run: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._subscribers: set[_Subscriber] = set()
        self._pending_delta: tuple[str, str, bool] | None = None
        self._delta_timer: threading.Timer | None = None

    def view(self) -> dict | None:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict | None:
        if self._run is None:
            return None
        error = self._run["error"]
        return {**self._run, "steps": list(self._run["steps"]), "error": error and dict(error)}

    def subscribe(
        self, loop: asyncio.AbstractEventLoop
    ) -> tuple[dict | None, asyncio.Queue[tuple[str, dict | None] | None] | None]:
        """Snapshot the current run and, if running, register an async subscriber queue."""
        with self._lock:
            if self._run is not None and self._run["status"] == "running":
                self._flush_deltas_locked(self._run)
            snapshot = self._snapshot()
            if snapshot is not None and snapshot["status"] == "running":
                queue: asyncio.Queue[tuple[str, dict | None] | None] = asyncio.Queue(
                    maxsize=SUBSCRIBER_QUEUE_MAX
                )
                self._subscribers.add((loop, queue))
                return snapshot, queue
            return snapshot, None

    def unsubscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, dict | None] | None],
    ) -> None:
        """Detach a subscriber queue from future notifications."""
        with self._lock:
            self._subscribers.discard((loop, queue))

    @staticmethod
    def _drain_queue(
        queue: asyncio.Queue[tuple[str, dict | None] | None],
    ) -> list[tuple[str, dict | None] | None]:
        drained: list[tuple[str, dict | None] | None] = []
        while True:
            try:
                drained.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    @staticmethod
    def _enqueue(
        queue: asyncio.Queue[tuple[str, dict | None] | None],
        event_name: str,
        data: dict | None,
        snapshot_fallback: dict | None = None,
    ) -> None:
        item: tuple[str, dict | None] | None = None if data is None else (event_name, data)
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        drained = Runner._drain_queue(queue)
        if item is None:
            for old in drained:
                if (
                    old is not None
                    and old[0] == "done"
                    and (queue.maxsize <= 0 or queue.qsize() < queue.maxsize - 1)
                ):
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(old)
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
            return
        if event_name == "done":
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(item)
            return
        kept = [x for x in drained if x is None or x[0] == "done"]
        if kept:
            for old in kept:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(old)
            return
        if snapshot_fallback is not None:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(("snapshot", snapshot_fallback))

    def _publish_locked(
        self,
        event_name: str,
        data: dict | None,
        *,
        snapshot_fallback: dict | None = None,
    ) -> None:
        if not self._subscribers:
            return
        if snapshot_fallback is None and data is not None and event_name != "done":
            snapshot_fallback = self._snapshot()
        for loop, queue in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, event_name, data, snapshot_fallback)
            except RuntimeError:
                self._subscribers.discard((loop, queue))

    def _publish(
        self,
        event_name: str,
        data: dict | None,
        *,
        snapshot_fallback: dict | None = None,
    ) -> None:
        """Schedule an event (or None sentinel when data is None) to all subscribers."""
        with self._lock:
            self._publish_locked(event_name, data, snapshot_fallback=snapshot_fallback)

    def _cancel_delta_timer_locked(self) -> None:
        if self._delta_timer is not None:
            self._delta_timer.cancel()
            self._delta_timer = None

    def _flush_deltas_locked(self, run: dict) -> None:
        self._cancel_delta_timer_locked()
        if self._pending_delta is None:
            return
        channel, text, reset = self._pending_delta
        self._pending_delta = None
        self._publish_locked(
            "delta",
            {"id": run["id"], "channel": channel, "text": text, "reset": reset},
        )

    def _arm_delta_timer_locked(self, run: dict) -> None:
        if self._delta_timer is not None:
            return
        timer: threading.Timer

        def fire() -> None:
            with self._lock:
                if self._delta_timer is not timer:
                    return
                self._delta_timer = None
                if self._run is not run or run["status"] != "running":
                    self._pending_delta = None
                    return
                self._flush_deltas_locked(run)

        timer = threading.Timer(DELTA_COALESCE_S, fire)
        timer.daemon = True
        self._delta_timer = timer
        timer.start()

    def _apply_tracker_events_locked(
        self, run: dict, events: list[PhaseEvent | DeltaEvent]
    ) -> None:
        for ev in events:
            if isinstance(ev, PhaseEvent):
                if (ev.phase, ev.active_tool) == (run["phase"], run["active_tool"]):
                    continue
                self._flush_deltas_locked(run)
                run["phase"] = ev.phase
                run["active_tool"] = ev.active_tool
                self._publish_locked(
                    "phase",
                    {"id": run["id"], "phase": ev.phase, "active_tool": ev.active_tool},
                )
            else:
                if ev.reset or (
                    self._pending_delta is not None and self._pending_delta[0] != ev.channel
                ):
                    self._flush_deltas_locked(run)
                run["thought"] = (
                    ev.text[-MAX_STREAM_CHARS:]
                    if ev.reset
                    else (run["thought"] + ev.text)[-MAX_STREAM_CHARS:]
                )
                run["thought_channel"] = ev.channel
                if self._pending_delta is None:
                    self._pending_delta = (ev.channel, ev.text, ev.reset)
                else:
                    channel, prev_text, prev_reset = self._pending_delta
                    self._pending_delta = (channel, prev_text + ev.text, prev_reset)
                self._arm_delta_timer_locked(run)

    def _apply_steps_locked(self, run: dict, step_items: list[tuple[str, bool]]) -> None:
        if not step_items:
            return
        self._flush_deltas_locked(run)
        for step_text, refresh in step_items:
            run["steps"] = (run["steps"] + [step_text])[-MAX_STEPS:]
            self._publish_locked(
                "step",
                {"id": run["id"], "step": step_text, "refresh": refresh},
            )

    def _settle_locked(
        self,
        run: dict,
        status: str,
        *,
        error: tuple[str, str] | None = None,
        summary: str | None = None,
        tracker: StreamTracker | None = None,
    ) -> bool:
        """Atomically settle ``run`` once, flush buffered deltas, and emit done + sentinel."""
        if self._run is not run or run["status"] != "running":
            return False
        self._flush_deltas_locked(run)
        if summary is not None:
            run["summary"] = summary
        run["status"] = status
        run["error"] = error and {"reason": error[0], "detail": error[1]}
        _clear_live(run)
        if tracker is not None:
            tracker.reset_settled()
        snapshot = self._snapshot()
        if snapshot is not None:
            self._publish_locked("done", snapshot, snapshot_fallback=snapshot)
        self._publish_locked("", None)
        self._subscribers.clear()
        return True

    def start(self, kind: str, subject: str, topic_id: int | None, prompt: str) -> dict:
        claude, reason = availability()
        if reason:
            raise RunError(reason, _UNAVAILABLE[reason], 503)
        assert claude is not None  # availability() gives a path whenever it gives no reason
        with self._lock:
            # The process, not the status: after its result event claude may still be exiting.
            if self._live_locked():
                raise RunError("run_in_progress", "a run is already working — wait or stop it", 409)
            # One fixed cwd: Claude Code makes a folder per working directory under
            # ~/.claude/projects, so a fresh tempdir per run would leave one per click.
            run_dir = groundly_home() / ".host-run"
            run_dir.mkdir(parents=True, exist_ok=True)
            # Anonymous, so claude's stderr is never a file left behind; the reader thread reads
            # back its tail and closes it.
            stderr = tempfile.TemporaryFile()  # noqa: SIM115 - outlives this block
            try:
                proc = subprocess.Popen(
                    build_argv(claude, kind, prompt, self.mcp_url),
                    cwd=run_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    start_new_session=True,  # its own process group, so Stop reaches children
                )
            except BaseException:
                stderr.close()
                raise
            run = {
                "id": self._next_id,
                "kind": kind,
                "subject": subject,
                "topic_id": topic_id,
                "status": "running",
                "phase": "starting",
                "active_tool": None,
                "thought": "",
                "thought_channel": None,
                "steps": [],
                "summary": None,
                "error": None,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            self._next_id += 1
            self._cancel_delta_timer_locked()
            self._pending_delta = None
            self._run, self._proc = run, proc
            snapshot = self._snapshot()
        assert snapshot is not None  # self._run was set just above
        timer = threading.Timer(
            RUN_TIMEOUT_S,
            self._end,
            (
                proc,
                run,
                "failed",
                ("timeout", f"the run passed the {_limit_words(RUN_TIMEOUT_S)} limit"),
            ),
        )
        timer.daemon = True
        timer.start()
        threading.Thread(target=self._read, args=(proc, run, stderr, timer), daemon=True).start()
        return snapshot

    def _end(self, proc, run, status, error=None) -> None:
        """Settle a running run (first settlement wins), then end its process."""
        with self._lock:
            self._settle_locked(run, status, error=error)
        _kill(proc)

    def _read(self, proc, run, stderr, timer) -> None:
        steps = Steps()
        tracker = StreamTracker()
        checked = False
        try:
            for raw in iter(lambda: proc.stdout.readline(_MAX_LINE_BYTES), b""):
                try:
                    event = json.loads(raw)
                except (ValueError, RecursionError):  # junk, or nesting too deep to parse
                    continue
                if not isinstance(event, dict):
                    continue
                with self._lock:
                    if self._run is not run or run["status"] != "running":
                        continue
                try:
                    if not checked:
                        # The init event must come first and list the run's tools; anything else
                        # before it is a run whose isolation cannot be shown.
                        is_init = event.get("type") == "system" and event.get("subtype") == "init"
                        fault = check_init(event) if is_init else None
                        if is_init and fault is None:
                            checked = True
                            phase_ev = tracker.on_init()
                            with self._lock:
                                if self._run is run and run["status"] == "running":
                                    self._apply_tracker_events_locked(run, [phase_ev])
                            continue
                        if not is_init and event.get("type") not in (
                            "stream_event",
                            "assistant",
                            "user",
                            "result",
                        ):
                            continue  # other system events (e.g. hooks) carry no tool use
                        self._end(
                            proc,
                            run,
                            "failed",
                            fault or ("isolation_failed", "the run did not report its tools first"),
                        )
                        break
                    step_items = steps.feed_items(event)
                    tracker_events = tracker.feed(event)
                    with self._lock:
                        if self._run is run and run["status"] == "running":
                            if event.get("type") == "user":
                                self._apply_steps_locked(run, step_items)
                                self._apply_tracker_events_locked(run, tracker_events)
                            else:
                                self._apply_tracker_events_locked(run, tracker_events)
                                self._apply_steps_locked(run, step_items)
                            if event.get("type") == "result":
                                summary, error = summary_of(event)
                                self._settle_locked(
                                    run,
                                    "failed" if error else "done",
                                    error=error,
                                    summary=summary,
                                    tracker=tracker,
                                )
                except Exception as e:  # noqa: BLE001 - an event shape it cannot read fails the run, never hangs it
                    reason = "host_error" if checked else "isolation_failed"
                    detail = f"unreadable output from claude ({type(e).__name__}: {e})"
                    self._end(proc, run, "failed", (reason, detail))
                    break
        finally:
            with self._lock:
                if self._run is run:
                    self._cancel_delta_timer_locked()
                    self._pending_delta = None
        proc.stdout.close()
        code = proc.wait()
        timer.cancel()
        _kill(proc)  # children claude left behind
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - _STDERR_TAIL))
        tail = stderr.read().decode("utf-8", "replace").strip()
        stderr.close()
        with self._lock:
            if self._run is run and run["status"] == "running":
                detail = f"claude exited with code {code}" + (f": {tail}" if tail else "")
                self._settle_locked(run, "failed", error=("host_exit", detail), tracker=tracker)

    def stop(self) -> dict | None:
        """Stop the running run, if any; what the gates already stored stays."""
        with self._lock:
            run, proc = self._run, self._proc
            live = self._live_locked()
        if run is not None and proc is not None and live:
            self._end(proc, run, "stopped")
        return self.view()

    def _live_locked(self) -> bool:
        """A run is working or its `claude` has not yet exited."""
        run, proc = self._run, self._proc
        return (
            run is not None
            and proc is not None
            and (run["status"] == "running" or proc.poll() is None)
        )

    def shutdown(self) -> None:
        """The page process is exiting: leave no `claude` behind."""
        self.stop()
        with self._lock:
            if self._subscribers:
                self._publish_locked("", None)
                self._subscribers.clear()
