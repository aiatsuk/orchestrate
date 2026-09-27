#!/usr/bin/env python3
"""Attribute Claude Code usage to one session, one workflow run, or explicit transcripts.

Claude Code writes every subagent transcript as JSON Lines under a session directory:
`<session-dir>/subagents/agent-<id>.jsonl` for Agent-tool subagents and
`<session-dir>/subagents/workflows/<runId>/agent-<id>.jsonl` for workflow agents. The main
transcript is the sibling `<session-dir>.jsonl`. Next to each agent transcript sits
`agent-<id>.meta.json` (agent type, description, workflow phase); a workflow run directory also
has `journal.jsonl`, whose `"type": "started"` lines carry the launch order, agent id, label and
phase.

Each transcript line with `"type": "assistant"` carries `message.id`, `message.model` and
`message.usage` (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`). One API response can span several lines sharing the same
`message.id`; this script counts each message id once, using the usage of the line with the
largest `output_tokens` for that id.

Usage:
  claude_usage.py --session-dir DIR [--main FILE] [--json]
  claude_usage.py --workflow-dir DIR [--main FILE] [--json]
  claude_usage.py --transcript FILE [--transcript FILE ...] [--main FILE] [--json]

Standard library only. Read-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def empty_usage() -> dict[str, int]:
    return {k: 0 for k in USAGE_KEYS}


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def agent_id_from_path(path: Path) -> str:
    stem = path.stem
    prefix = "agent-"
    return stem[len(prefix):] if stem.startswith(prefix) else stem


def meta_path_for(path: Path) -> Path:
    return path.with_name(path.stem + ".meta.json")


def read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_journal(run_dir: Path) -> dict[str, dict]:
    """Return agentId -> {label, phase} from a run directory's journal.jsonl, if any."""
    path = run_dir / "journal.jsonl"
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "started":
                continue
            agent_id = obj.get("agentId")
            if agent_id:
                out[agent_id] = {"label": obj.get("label"), "phase": obj.get("phase")}
    return out


def analyze_transcript(path: Path) -> dict:
    """Read one transcript file and return its deduplicated usage, grouped by model."""
    best: dict[str, tuple[int, dict, str]] = {}  # message id -> (output_tokens, usage, model)
    models: set[str] = set()
    first_ts = last_ts = None
    skipped = 0
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            mid = message.get("id")
            if not isinstance(usage, dict) or not mid:
                continue
            model = message.get("model") or "unknown"
            out_tokens = int(usage.get("output_tokens") or 0)
            ts = parse_ts(obj.get("timestamp"))
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            previous = best.get(mid)
            if previous is None or out_tokens > previous[0]:
                best[mid] = (out_tokens, {k: int(usage.get(k) or 0) for k in USAGE_KEYS}, model)
            models.add(model)
    totals = empty_usage()
    per_model: dict[str, dict] = {}
    for _, usage, model in best.values():
        for k in USAGE_KEYS:
            totals[k] += usage[k]
        bucket = per_model.setdefault(model, {"messages": 0, **empty_usage()})
        bucket["messages"] += 1
        for k in USAGE_KEYS:
            bucket[k] += usage[k]
    return {
        "usage": totals,
        "messages": len(best),
        "models": sorted(models),
        "per_model": per_model,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "skipped_lines": skipped,
    }


def build_record(agent_id: str, label: str, phase: str, agent_type: str, analysis: dict) -> dict:
    first_ts, last_ts = analysis["first_ts"], analysis["last_ts"]
    duration = (last_ts - first_ts).total_seconds() if first_ts and last_ts else None
    usage = analysis["usage"]
    return {
        "id": agent_id,
        "label": label,
        "phase": phase,
        "agent_type": agent_type,
        "models": analysis["models"],
        "messages": analysis["messages"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
        "cache_read_input_tokens": usage["cache_read_input_tokens"],
        "first_ts": first_ts.isoformat() if first_ts else None,
        "last_ts": last_ts.isoformat() if last_ts else None,
        "duration_seconds": duration,
        "skipped_lines": analysis["skipped_lines"],
    }


def merge_model_totals(per_model_totals: dict[str, dict], per_model: dict[str, dict]) -> None:
    for model, bucket in per_model.items():
        total_bucket = per_model_totals.setdefault(model, {"messages": 0, **empty_usage()})
        total_bucket["messages"] += bucket["messages"]
        for k in USAGE_KEYS:
            total_bucket[k] += bucket[k]


def collect_agents(paths: list[Path]) -> tuple[list[dict], dict[str, dict], int]:
    journal_cache: dict[str, dict] = {}
    records: list[dict] = []
    per_model_totals: dict[str, dict] = {}
    skipped_total = 0
    for path in paths:
        agent_id = agent_id_from_path(path)
        meta = read_json(meta_path_for(path)) or {}
        parent_key = str(path.parent)
        if parent_key not in journal_cache:
            journal_cache[parent_key] = read_journal(path.parent)
        journal_entry = journal_cache[parent_key].get(agent_id, {})
        label = journal_entry.get("label") or meta.get("description") or agent_id
        phase = journal_entry.get("phase") or meta.get("workflowPhase") or "-"
        agent_type = meta.get("agentType") or "-"
        analysis = analyze_transcript(path)
        skipped_total += analysis["skipped_lines"]
        records.append(build_record(agent_id, label, phase, agent_type, analysis))
        merge_model_totals(per_model_totals, analysis["per_model"])
    return records, per_model_totals, skipped_total


def cache_hit_rate(usage: dict) -> float:
    denom = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
    return (usage["cache_read_input_tokens"] / denom) if denom else 0.0


def fmt_num(v: int) -> str:
    return f"{v:,}"


def render_text(agents: list[dict], models: dict[str, dict], total: dict) -> str:
    headers = ["ID", "LABEL", "PHASE", "TYPE", "MODELS", "MSGS", "INPUT", "OUTPUT", "CACHE_W", "CACHE_R", "FIRST", "LAST", "DUR(s)"]
    rows = []
    for a in agents:
        rows.append([
            a["id"], a["label"], a["phase"], a["agent_type"], ",".join(a["models"]) or "-",
            str(a["messages"]), fmt_num(a["input_tokens"]), fmt_num(a["output_tokens"]),
            fmt_num(a["cache_creation_input_tokens"]), fmt_num(a["cache_read_input_tokens"]),
            a["first_ts"] or "-", a["last_ts"] or "-",
            "-" if a["duration_seconds"] is None else f"{a['duration_seconds']:.0f}",
        ])
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = [" ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    for r in rows:
        lines.append(" ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)))
    lines.append("")

    model_headers = ["MODEL", "MESSAGES", "INPUT", "OUTPUT", "CACHE_W", "CACHE_R", "CACHE_HIT%"]
    model_rows = []
    for name, b in sorted(models.items()):
        model_rows.append([
            name, str(b["messages"]), fmt_num(b["input_tokens"]), fmt_num(b["output_tokens"]),
            fmt_num(b["cache_creation_input_tokens"]), fmt_num(b["cache_read_input_tokens"]),
            f"{b['cache_hit_rate'] * 100:.1f}",
        ])
    mwidths = [max(len(h), *(len(r[i]) for r in model_rows)) if model_rows else len(h) for i, h in enumerate(model_headers)]
    lines.append(" ".join(h.ljust(mwidths[i]) for i, h in enumerate(model_headers)))
    for r in model_rows:
        lines.append(" ".join(cell.ljust(mwidths[i]) for i, cell in enumerate(r)))
    lines.append("")

    lines.append(
        f"TOTAL agents={total['agents']} messages={total['messages']} "
        f"input={fmt_num(total['input_tokens'])} output={fmt_num(total['output_tokens'])} "
        f"cache_creation={fmt_num(total['cache_creation_input_tokens'])} cache_read={fmt_num(total['cache_read_input_tokens'])} "
        f"cache_hit_rate={total['cache_hit_rate'] * 100:.1f}% skipped_lines={total['skipped_lines']}"
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--session-dir", help="a Claude Code session directory; scans every subagents/**/agent-*.jsonl below it")
    p.add_argument("--workflow-dir", help="one workflow run directory; scans its agent-*.jsonl files")
    p.add_argument("--transcript", action="append", default=[], help="an explicit agent transcript file; repeatable")
    p.add_argument("--main", help="also count this main session transcript as agent 'main'")
    p.add_argument("--json", action="store_true", help="print the report as JSON instead of a text table")
    return p


def fail(message: str) -> int:
    print(f"claude_usage.py: {message}", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (args.session_dir or args.workflow_dir or args.transcript):
        parser.print_usage(sys.stderr)
        return 2

    paths: list[Path] = []
    if args.session_dir:
        base = Path(args.session_dir).expanduser()
        if not base.is_dir():
            return fail(f"no such session directory: {base}")
        paths.extend(sorted((base / "subagents").rglob("agent-*.jsonl")))
    if args.workflow_dir:
        base = Path(args.workflow_dir).expanduser()
        if not base.is_dir():
            return fail(f"no such workflow directory: {base}")
        paths.extend(sorted(base.glob("agent-*.jsonl")))
    for t in args.transcript:
        tp = Path(t).expanduser()
        if not tp.is_file():
            return fail(f"no such transcript file: {tp}")
        paths.append(tp)

    records, per_model_totals, skipped_total = collect_agents(paths)

    if args.main:
        main_path = Path(args.main).expanduser()
        if not main_path.is_file():
            return fail(f"no such transcript file: {main_path}")
        analysis = analyze_transcript(main_path)
        skipped_total += analysis["skipped_lines"]
        records.append(build_record("main", "main", "-", "main", analysis))
        merge_model_totals(per_model_totals, analysis["per_model"])

    records.sort(key=lambda r: (r["first_ts"] is None, r["first_ts"] or ""))

    total = {
        "agents": len(records),
        "messages": sum(r["messages"] for r in records),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "cache_creation_input_tokens": sum(r["cache_creation_input_tokens"] for r in records),
        "cache_read_input_tokens": sum(r["cache_read_input_tokens"] for r in records),
        "skipped_lines": skipped_total,
    }
    total["cache_hit_rate"] = round(cache_hit_rate(total), 4)
    for bucket in per_model_totals.values():
        bucket["cache_hit_rate"] = round(cache_hit_rate(bucket), 4)

    if args.json:
        print(json.dumps({"agents": records, "models": per_model_totals, "total": total}, indent=2))
    else:
        print(render_text(records, per_model_totals, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
