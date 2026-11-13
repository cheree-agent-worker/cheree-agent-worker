#!/usr/bin/env python3
"""Aggregate agent chat and token usage from local Claude Code and Codex session logs.

Runs on the machine that holds the logs (GitHub Actions cannot see them).
Prints a JSON summary with totals only: counts and token sums, never any
message content, file names or project names.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")).expanduser()
CODEX_DIR = Path(os.environ.get("CODEX_SESSIONS_DIR", "~/.codex/sessions")).expanduser()
WEEKS_KEPT = 26


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def week_start(dt):
    d = (dt - timedelta(days=dt.weekday())).date()
    return d.isoformat()


def new_bucket():
    return {"chats": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


tool_totals = {"claude_code": new_bucket(), "codex": new_bucket()}
weekly = defaultdict(lambda: {"claude_code": new_bucket(), "codex": new_bucket()})
models = defaultdict(int)  # fresh input+output tokens per model
first_seen = None


def note_first(dt):
    global first_seen
    if first_seen is None or dt < first_seen:
        first_seen = dt


def add(tool, dt, inp, out, cr, cw, model=None):
    note_first(dt)
    for b in (tool_totals[tool], weekly[week_start(dt)][tool]):
        b["input"] += inp
        b["output"] += out
        b["cache_read"] += cr
        b["cache_write"] += cw
    if model:
        models[model] += inp + out


def add_chat(tool, dt):
    note_first(dt)
    tool_totals[tool]["chats"] += 1
    weekly[week_start(dt)][tool]["chats"] += 1


def scan_claude():
    seen = set()  # global dedupe of message ids (resumed sessions copy history)
    for path in CLAUDE_DIR.rglob("*.jsonl"):
        is_sub = "subagents" in path.parts
        # last usage per message id within this file (streaming repeats a message)
        per_msg = {}
        first_ts = None
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    m = d.get("message") or {}
                    u = m.get("usage")
                    ts = d.get("timestamp")
                    mid = m.get("id")
                    if not (isinstance(u, dict) and ts and mid):
                        continue
                    if m.get("model") in (None, "<synthetic>"):
                        continue
                    per_msg[mid] = (ts, u, m.get("model"))
                    if first_ts is None:
                        first_ts = ts
        except OSError:
            continue
        counted = False
        for mid, (ts, u, model) in per_msg.items():
            if mid in seen:
                continue
            seen.add(mid)
            counted = True
            add(
                "claude_code",
                parse_ts(ts),
                u.get("input_tokens", 0) or 0,
                u.get("output_tokens", 0) or 0,
                u.get("cache_read_input_tokens", 0) or 0,
                u.get("cache_creation_input_tokens", 0) or 0,
                model,
            )
        if counted and not is_sub and first_ts:
            add_chat("claude_code", parse_ts(first_ts))


def scan_codex():
    for path in CODEX_DIR.rglob("*.jsonl"):
        prev = {"input": 0, "cached": 0, "output": 0}
        model = None
        started = None
        used = False
        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    if '"token_count"' in line:
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        p = d.get("payload") or {}
                        info = p.get("info")
                        ts = d.get("timestamp")
                        if p.get("type") != "token_count" or not info or not ts:
                            continue
                        t = info.get("total_token_usage") or {}
                        cur = {
                            "input": t.get("input_tokens", 0) or 0,
                            "cached": t.get("cached_input_tokens", 0) or 0,
                            "output": t.get("output_tokens", 0) or 0,
                        }
                        di = cur["input"] - prev["input"]
                        dc = cur["cached"] - prev["cached"]
                        do = cur["output"] - prev["output"]
                        if di < 0 or dc < 0 or do < 0:  # counter reset, skip
                            prev = cur
                            continue
                        if di or dc or do:
                            used = True
                            dt = parse_ts(ts)
                            if started is None:
                                started = dt
                            add("codex", dt, max(di - dc, 0), do, dc, 0, model)
                        prev = cur
                    elif model is None and '"turn_context"' in line:
                        try:
                            model = (json.loads(line).get("payload") or {}).get("model")
                        except ValueError:
                            pass
        except OSError:
            continue
        if used and started:
            add_chat("codex", started)


def main():
    scan_claude()
    scan_codex()

    total = new_bucket()
    for b in tool_totals.values():
        for k in total:
            total[k] += b[k]

    now = datetime.now(timezone.utc)
    this_monday = now - timedelta(days=now.weekday())
    weeks = []
    for i in range(WEEKS_KEPT - 1, -1, -1):
        ws = week_start(this_monday - timedelta(weeks=i))
        w = weekly.get(ws) or {"claude_code": new_bucket(), "codex": new_bucket()}
        row = new_bucket()
        for b in w.values():
            for k in row:
                row[k] += b[k]
        row["week"] = ws
        weeks.append(row)

    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": first_seen.strftime("%Y-%m-%d") if first_seen else None,
        "totals": total,
        "by_tool": tool_totals,
        "weekly": weeks,
        "top_models": sorted(
            ({"model": k, "tokens": v} for k, v in models.items()),
            key=lambda x: -x["tokens"],
        )[:5],
    }
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
