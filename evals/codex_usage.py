#!/usr/bin/env python3
"""Attribute Codex usage and rate-limit consumption to one orchestrated run.

Codex writes one rollout file per thread under ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
Subagent threads carry the root thread id in `session_id`, so grouping files by that value gives
the whole run: per thread (role, nickname, model, effort, responses, tokens, duration), per model,
and the change of the account's rate-limit windows between the first and the last response.

Usage:
  codex_usage.py --list [--date YYYY-MM-DD] [--limit N]
  codex_usage.py --root <root-id-or-prefix> [--date YYYY-MM-DD] [--format md|json] [--include-guardian]
  codex_usage.py --latest [--date YYYY-MM-DD] [--format md|json]

Standard library only. Read-only. Guardian (auto-review) threads are listed but excluded from totals
unless --include-guardian is given. Rate-limit windows are labelled from their `window_minutes`, so a
7-day primary window is reported as 7d, not assumed to be 5h.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def empty_usage() -> dict[str, int]:
    return {k: 0 for k in USAGE_KEYS}


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_rollouts(sessions_dir: Path, date: str | None):
    if date:
        y, m, d = date.split("-")
        base = sessions_dir / y / m / d
        if base.is_dir():
            yield from sorted(base.glob("rollout-*.jsonl"))
        return
    yield from sorted(sessions_dir.rglob("rollout-*.jsonl"))


def read_meta(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            obj = json.loads(fh.readline())
    except (OSError, json.JSONDecodeError):
        return None
    if obj.get("type") != "session_meta":
        return None
    return obj.get("payload") or {}


def thread_role(meta: dict) -> tuple[str, str, str]:
    """Return (role, nickname, path) for a thread from its session_meta."""
    source = meta.get("source")
    if isinstance(source, dict):
        sub = source.get("subagent") or {}
        if isinstance(sub, str):
            return sub, "", ""
        spawn = sub.get("thread_spawn")
        if isinstance(spawn, dict):
            return (spawn.get("agent_role") or "subagent", spawn.get("agent_nickname") or "", spawn.get("agent_path") or "")
        if sub.get("other"):
            return str(sub["other"]), "", ""
    if meta.get("thread_source") == "guardian_review":
        return "guardian", "", ""
    if meta.get("id") == meta.get("session_id"):
        return "root", "", ""
    return str(meta.get("thread_source") or "unknown"), "", ""


def is_guardian(thread: dict) -> bool:
    return thread["role"] == "guardian" or thread["thread_source"] == "guardian_review"


def window_label(window: dict | None) -> str:
    if not window:
        return "-"
    minutes = int(window.get("window_minutes") or 0)
    if minutes >= 1440 and minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def analyze_thread(path: Path, meta: dict) -> dict:
    role, nickname, agent_path = thread_role(meta)
    usage = empty_usage()
    responses = 0
    model = effort = None
    first_ts = parse_ts(meta.get("timestamp"))
    last_ts = first_ts
    limits_first = limits_last = None
    plan = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(obj.get("timestamp"))
            if ts and (last_ts is None or ts > last_ts):
                last_ts = ts
            kind = obj.get("type")
            payload = obj.get("payload") or {}
            if kind == "turn_context":
                model = payload.get("model") or model
                effort = payload.get("effort") or effort
            elif kind == "token_usage_record":
                for k in USAGE_KEYS:
                    usage[k] += int((payload.get("usage") or {}).get(k) or 0)
                responses += 1
            elif kind == "event_msg" and payload.get("type") == "token_count":
                limits = payload.get("rate_limits") or {}
                if limits:
                    plan = limits.get("plan_type") or plan
                    snapshot = {"primary": limits.get("primary"), "secondary": limits.get("secondary"), "ts": ts}
                    if limits_first is None:
                        limits_first = snapshot
                    limits_last = snapshot
    return {"id": meta.get("id") or "", "session_id": meta.get("session_id") or "", "path": str(path),
            "role": role, "nickname": nickname, "agent_path": agent_path, "thread_source": meta.get("thread_source") or "",
            "cwd": meta.get("cwd") or "", "cli_version": meta.get("cli_version") or "", "model": model or "?",
            "effort": effort or "?", "responses": responses, "usage": usage, "first_ts": first_ts, "last_ts": last_ts,
            "limits_first": limits_first, "limits_last": limits_last, "plan": plan}


def collect(sessions_dir: Path, date: str | None) -> dict[str, list[tuple[Path, dict]]]:
    sessions: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for path in iter_rollouts(sessions_dir, date):
        meta = read_meta(path)
        if meta and meta.get("session_id"):
            sessions[meta["session_id"]].append((path, meta))
    return sessions


def fmt_dur(a, b) -> str:
    if not a or not b:
        return "-"
    seconds = int((b - a).total_seconds())
    return f"{seconds // 60}m{seconds % 60:02d}s"


def limit_delta(threads: list[dict], key: str) -> str:
    snaps = [(t["limits_first"], t["limits_last"]) for t in threads if t["limits_first"] and t["limits_last"]]
    if not snaps:
        return "-"
    firsts = sorted((f for f, _ in snaps), key=lambda s: s["ts"] or datetime.min)
    lasts = sorted((l for _, l in snaps), key=lambda s: s["ts"] or datetime.min)
    first, last = firsts[0].get(key), lasts[-1].get(key)
    if not first and not last:
        return "-"
    label = window_label(last or first)
    a = first.get("used_percent") if first else None
    b = last.get("used_percent") if last else None
    return f"{label} {a if a is not None else '-'}% -> {b if b is not None else '-'}%"


def report(root_id: str, threads: list[dict], include_guardian: bool) -> dict:
    counted = [t for t in threads if include_guardian or not is_guardian(t)]
    skipped = [t for t in threads if not include_guardian and is_guardian(t)]
    per_model: dict[str, dict] = defaultdict(lambda: {"threads": 0, "responses": 0, "usage": empty_usage()})
    total = {"threads": 0, "responses": 0, "usage": empty_usage()}
    for t in counted:
        for bucket in (per_model[t["model"]], total):
            bucket["threads"] += 1
            bucket["responses"] += t["responses"]
            for k in USAGE_KEYS:
                bucket["usage"][k] += t["usage"][k]
    starts = [t["first_ts"] for t in threads if t["first_ts"]]
    ends = [t["last_ts"] for t in threads if t["last_ts"]]
    inputs = total["usage"]["input_tokens"]
    cache_rate = round(100 * total["usage"]["cached_input_tokens"] / inputs, 1) if inputs else 0.0
    root = next((t for t in threads if t["role"] == "root"), threads[0])
    return {"root": root_id, "cwd": root["cwd"], "cli_version": root["cli_version"], "plan": root["plan"],
            "wall": fmt_dur(min(starts), max(ends)) if starts and ends else "-",
            "limits": {"primary": limit_delta(threads, "primary"), "secondary": limit_delta(threads, "secondary")},
            "threads": [{k: v for k, v in t.items() if k not in ("first_ts", "last_ts", "limits_first", "limits_last")}
                        | {"duration": fmt_dur(t["first_ts"], t["last_ts"])} for t in counted],
            "skipped_guardian": [t["id"][:8] for t in skipped],
            "per_model": {m: v for m, v in sorted(per_model.items())}, "total": total, "cache_hit_percent": cache_rate}


def render_md(r: dict) -> str:
    n = lambda v: f"{v:,}"
    out = [f"### Session `{r['root'][:8]}`", "", f"- cwd: `{r['cwd']}`", f"- codex: `{r['cli_version']}`, plan: {r['plan'] or '-'}",
           f"- threads counted: {r['total']['threads']} (guardian skipped: {len(r['skipped_guardian'])})", f"- wall time: {r['wall']}",
           f"- rate limits: primary {r['limits']['primary']}; secondary {r['limits']['secondary']}", "",
           "| Thread | Role | Model / effort | Responses | Uncached in | Cached in | Output | Reasoning | Duration |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for t in r["threads"]:
        u = t["usage"]
        role = t["role"] + (f" ({t['nickname']})" if t["nickname"] else "")
        out.append(f"| `{t['id'][:8]}` | {role} | {t['model']} / {t['effort']} | {t['responses']} | "
                   f"{n(u['input_tokens'] - u['cached_input_tokens'])} | {n(u['cached_input_tokens'])} | {n(u['output_tokens'])} | "
                   f"{n(u['reasoning_output_tokens'])} | {t['duration']} |")
    out += ["", "| Model | Threads | Responses | Uncached in | Cached in | Output | Reasoning | Total |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    rows = list(r["per_model"].items()) + [("all", r["total"])]
    for name, b in rows:
        u = b["usage"]
        out.append(f"| {name} | {b['threads']} | {b['responses']} | {n(u['input_tokens'] - u['cached_input_tokens'])} | "
                   f"{n(u['cached_input_tokens'])} | {n(u['output_tokens'])} | {n(u['reasoning_output_tokens'])} | {n(u['total_tokens'])} |")
    out += ["", f"Cache hit rate on input: {r['cache_hit_percent']}%"]
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sessions-dir", default=os.path.expanduser("~/.codex/sessions"))
    p.add_argument("--date", help="only scan rollouts of this day (YYYY-MM-DD)")
    p.add_argument("--list", action="store_true", help="list root sessions with their subagent counts")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--root", help="root thread id or unique prefix")
    p.add_argument("--latest", action="store_true", help="report on the newest session that spawned subagents")
    p.add_argument("--include-guardian", action="store_true")
    p.add_argument("--format", choices=("md", "json"), default="md")
    args = p.parse_args(argv)
    sessions = collect(Path(args.sessions_dir).expanduser(), args.date)
    if not sessions:
        print("no rollout files found", file=sys.stderr)
        return 1
    analyzed = {sid: [analyze_thread(path, meta) for path, meta in files] for sid, files in sessions.items()}
    ordered = sorted(analyzed.items(), key=lambda kv: min((t["first_ts"] for t in kv[1] if t["first_ts"]), default=datetime.min), reverse=True)
    if args.list:
        print("started (UTC)       root id   subagents  cwd")
        for sid, threads in ordered[: args.limit]:
            start = min((t["first_ts"] for t in threads if t["first_ts"]), default=None)
            subs = sum(1 for t in threads if t["role"] not in ("root",) and not is_guardian(t))
            print(f"{start.strftime('%Y-%m-%d %H:%M') if start else '?':<19} {sid[:8]}  {subs:>9}  {threads[0]['cwd']}")
        return 0
    if args.latest:
        candidates = [(sid, th) for sid, th in ordered if any(t["role"] != "root" and not is_guardian(t) for t in th)]
        if not candidates:
            print("no session with subagents found", file=sys.stderr)
            return 1
        sid, threads = candidates[0]
    elif args.root:
        matches = [(sid, th) for sid, th in analyzed.items() if sid.startswith(args.root)]
        if len(matches) != 1:
            print(f"{len(matches)} sessions match {args.root!r}", file=sys.stderr)
            return 1
        sid, threads = matches[0]
    else:
        p.print_usage()
        return 2
    r = report(sid, threads, args.include_guardian)
    print(json.dumps(r, indent=2, default=str) if args.format == "json" else render_md(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
