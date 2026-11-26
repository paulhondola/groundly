"""The bench's DuckDB store: every run's raw facts, one file for all sessions.

Only the parent bench process opens it, once per run, after the run ends; children never
import duckdb. Rows are raw facts: aggregates (per-cell scores, energy, CPU %) are queries.
`page_text` is the one table holding course text and is never exported.
"""

from pathlib import Path

import duckdb

from bench.common.results import RESULTS_DIR

SCHEMA_VERSION = 1
STORE = RESULTS_DIR / "bench.duckdb"


class StoreBusy(RuntimeError):
    """Another bench process holds the store open for writing."""


_SCHEMA = """
create table if not exists meta (schema_version integer not null);
create table if not exists session (
    id varchar primary key, started_at timestamp, machine varchar, os varchar,
    git_sha varchar, note varchar);
create table if not exists session_package (
    session varchar, package varchar, version varchar, primary key (session, package));
create sequence if not exists run_id;
create table if not exists run (
    id bigint primary key default nextval('run_id'), session varchar not null,
    bench varchar not null, arm varchar not null, subject varchar, run_no integer,
    started_at timestamp, ended_at timestamp, returncode integer, wall_s double,
    load_s double, memray boolean default false, timeout_s double, manifest_version integer,
    device varchar, items integer, idle_combined_mw double, power_sampler varchar,
    sampler_note varchar);
create table if not exists page (
    run bigint, sha256 varchar, page integer, kind varchar, lang varchar, ref boolean,
    ms double, capped boolean, error varchar, f1 double, precision double, recall double,
    bigram_f1 double, diacritic_recall double, scorer_version integer);
create table if not exists page_text (run bigint, sha256 varchar, page integer, text varchar);
create table if not exists file (
    run bigint, filename varchar, pages integer, extract_s double, embed_s double,
    status varchar, error varchar);
create table if not exists query (
    run bigint, query_id varchar, latency_ms double, hit_within1_at8 boolean);
create table if not exists query_hit (
    run bigint, query_id varchar, rank integer, filename varchar, page integer, score double);
create table if not exists agreement (
    run_a bigint, run_b bigint, item_kind varchar, item_id varchar, dense_cos double,
    sparse_top10_overlap double, rerank_top5_overlap double, max_score_diff double);
create table if not exists sample_proc (
    run bigint, t_s double, rss_mb double, footprint_mb double, cpu_s double,
    gpu_mem_mb_system double);
create table if not exists sample_power (
    run bigint, t_s double, cpu_mw double, gpu_mw double, ane_mw double, combined_mw double,
    gpu_active double, gpu_freq_mhz double);
create table if not exists sample_task (
    run bigint, t_s double, pid integer, name varchar, cpu_ms_per_s double,
    gpu_ms_per_s double, energy_impact double);
create table if not exists artifact (
    run bigint, kind varchar, path varchar, process varchar, peak_heap_mb double);
"""

_RUN_COLUMNS = (
    "session", "bench", "arm", "subject", "run_no", "started_at", "ended_at", "returncode",
    "wall_s", "load_s", "memray", "timeout_s", "manifest_version", "device", "items",
    "idle_combined_mw", "power_sampler", "sampler_note",
)  # fmt: skip

TABLES: dict[str, tuple[str, ...]] = {
    "page": ("run", "sha256", "page", "kind", "lang", "ref", "ms", "capped", "error", "f1",
             "precision", "recall", "bigram_f1", "diacritic_recall", "scorer_version"),
    "page_text": ("run", "sha256", "page", "text"),
    "file": ("run", "filename", "pages", "extract_s", "embed_s", "status", "error"),
    "query": ("run", "query_id", "latency_ms", "hit_within1_at8"),
    "query_hit": ("run", "query_id", "rank", "filename", "page", "score"),
    "agreement": ("run_a", "run_b", "item_kind", "item_id", "dense_cos",
                  "sparse_top10_overlap", "rerank_top5_overlap", "max_score_diff"),
    "sample_proc": ("run", "t_s", "rss_mb", "footprint_mb", "cpu_s", "gpu_mem_mb_system"),
    "sample_power": ("run", "t_s", "cpu_mw", "gpu_mw", "ane_mw", "combined_mw", "gpu_active",
                     "gpu_freq_mhz"),
    "sample_task": ("run", "t_s", "pid", "name", "cpu_ms_per_s", "gpu_ms_per_s",
                    "energy_impact"),
    "artifact": ("run", "kind", "path", "process", "peak_heap_mb"),
}  # fmt: skip


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the store; refuse a schema newer than this bench knows."""
    path = path or STORE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(path))
    except duckdb.IOException as e:
        if "lock" in str(e).lower():
            raise StoreBusy(f"{path.name} is in use by another bench run") from e
        raise
    con.execute(_SCHEMA)
    row = con.execute("select schema_version from meta").fetchone()
    if row is None:
        con.execute("insert into meta values (?)", [SCHEMA_VERSION])
    elif row[0] > SCHEMA_VERSION:
        con.close()
        raise SystemExit(f"{path.name} is schema v{row[0]}, this bench knows v{SCHEMA_VERSION}")
    return con


def ensure_session(con: duckdb.DuckDBPyConnection, session: dict) -> None:
    """Insert a session and its package versions once; later calls for the same id do nothing."""
    if con.execute("select 1 from session where id = ?", [session["id"]]).fetchone():
        return
    con.execute(
        "insert into session values (?, ?, ?, ?, ?, ?)",
        [session[k] for k in ("id", "started_at", "machine", "os", "git_sha", "note")],
    )
    for package, version in session.get("packages", {}).items():
        con.execute(
            "insert into session_package values (?, ?, ?)", [session["id"], package, version]
        )


def _check(table: str, rows: list[dict]) -> tuple[str, ...]:
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}")
    cols = TABLES[table]
    for r in rows:
        extra = set(r) - set(cols)
        if extra:
            raise ValueError(f"{table}: unknown columns {sorted(extra)}")
    return cols


def insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> None:
    """Insert rows into one child table; missing columns are NULL."""
    cols = _check(table, rows)
    if rows:
        # ponytail: executemany, fine for ~10k rows per run; an Arrow batch if it ever shows up
        con.executemany(
            f"insert into {table} ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
            [[r.get(c) for c in cols] for r in rows],
        )


def write_run(con: duckdb.DuckDBPyConnection, run: dict, children: dict[str, list[dict]]) -> int:
    """Insert one run and all its child rows in one transaction; return the run id.

    Child rows get the new run id in their `run` column. An unknown table or column raises
    ValueError before anything is written; any insert failure rolls the whole run back.
    """
    extra = set(run) - set(_RUN_COLUMNS)
    if extra:
        raise ValueError(f"run: unknown columns {sorted(extra)}")
    for table, rows in children.items():
        _check(table, rows)
    con.execute("begin")
    try:
        cols = [c for c in _RUN_COLUMNS if c in run]
        (rid,) = con.execute(
            f"insert into run ({', '.join(cols)}) values ({', '.join('?' * len(cols))}) "
            "returning id",
            [run[c] for c in cols],
        ).fetchone()
        for table, rows in children.items():
            if "run" in TABLES[table]:
                rows = [{**r, "run": rid} for r in rows]
            insert(con, table, rows)
        con.execute("commit")
    except BaseException:
        con.execute("rollback")
        raise
    return rid
