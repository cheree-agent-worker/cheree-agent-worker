#!/usr/bin/env python3
"""Render README.md and assets/stats.svg from data/usage.json plus GitHub contributions.

Runs in GitHub Actions (needs `gh` and GH_TOKEN) and also locally for testing.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIN = "cheree-agent-worker"
BARS = 12


def human(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            v = n / size
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return str(int(n))


def gh_graphql(query, **vars_):
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in vars_.items():
        cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return None
    return json.loads(r.stdout)


CONTRIB_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    createdAt
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
    }
  }
}"""


def contributions(frm, to):
    d = gh_graphql(CONTRIB_Q, login=LOGIN, **{"from": frm, "to": to})
    if not d or not d.get("data", {}).get("user"):
        return None
    c = d["data"]["user"]["contributionsCollection"]
    return {
        "commits": c["totalCommitContributions"],
        "prs": c["totalPullRequestContributions"],
        "issues": c["totalIssueContributions"],
        "reviews": c["totalPullRequestReviewContributions"],
        "private": c["restrictedContributionsCount"],
        "created": d["data"]["user"]["createdAt"],
    }


def total_contrib(c):
    return c["commits"] + c["prs"] + c["issues"] + c["reviews"]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def bar_chart(weeks, x, y, w, h):
    """Weekly fresh-token bars, newest on the right."""
    vals = [wk["input"] + wk["output"] for wk in weeks]
    top = max(vals) or 1
    gap = 8
    bw = (w - gap * (len(vals) - 1)) / len(vals)
    out = []
    for i, (wk, v) in enumerate(zip(weeks, vals)):
        bh = max(2, (h - 22) * v / top) if v else 2
        bx = x + i * (bw + gap)
        by = y + h - 18 - bh
        last = i == len(vals) - 1
        fill = "#5eead4" if not last else "#ffd24d"
        out.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{fill}" opacity="{1 if last else 0.85}"/>')
        label = datetime.fromisoformat(wk["week"]).strftime("%b %-d")
        if (len(vals) - 1 - i) % 2 == 0:
            out.append(f'<text x="{bx + bw / 2:.1f}" y="{y + h:.1f}" text-anchor="middle" class="mut s">{label}</text>')
    return "\n".join(out)


def render_svg(u, now, c_all, c_last):
    t = u["totals"]
    weeks = u["weekly"][-BARS:]
    last_full = u["weekly"][-2] if len(u["weekly"]) >= 2 else None
    fresh = t["input"] + t["output"]
    fresh_last = (last_full["input"] + last_full["output"]) if last_full else 0
    chats_last = last_full["chats"] if last_full else 0
    cached = t["cache_read"] + t["cache_write"]
    contrib_all = total_contrib(c_all) if c_all else 0
    contrib_last = total_contrib(c_last) if c_last else 0

    tiles = [
        ("Chats", f"{t['chats']:,}", f"+{chats_last} last week"),
        ("Tokens (input + output)", human(fresh), f"+{human(fresh_last)} last week"),
        ("Cached tokens", human(cached), "reads + writes"),
        ("GitHub contributions", f"{contrib_all:,}", f"+{contrib_last} last week"),
    ]
    tile_svg = []
    for i, (label, big, sub) in enumerate(tiles):
        tx = 32 + i * 208
        tile_svg.append(
            f'<g transform="translate({tx},84)">'
            f'<text class="mut" y="0">{label}</text>'
            f'<text class="big" y="44">{big}</text>'
            f'<text class="mut s" y="68">{sub}</text></g>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="880" height="360" viewBox="0 0 880 360" role="img" aria-label="Weekly agent stats for {LOGIN}">
<style>
text {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: #e6edf3; font-size: 14px; }}
.mut {{ fill: #8b949e; font-size: 13px; }}
.s {{ font-size: 12px; }}
.big {{ font-size: 34px; font-weight: 700; }}
.ttl {{ font-size: 18px; font-weight: 600; }}
</style>
<rect x="0.5" y="0.5" width="879" height="359" rx="14" fill="#0d1117" stroke="#30363d"/>
<text class="ttl" x="32" y="42">{LOGIN}</text>
<text class="mut" x="848" y="42" text-anchor="end">updated {now.strftime("%Y-%m-%d")}</text>
{"".join(tile_svg)}
<text class="mut" x="32" y="200">Fresh tokens per week, last {BARS} weeks (current week in yellow)</text>
{bar_chart(weeks, 32, 212, 816, 130)}
</svg>
'''


def render_readme(u, now, c_all, c_last):
    t = u["totals"]
    bt = u["by_tool"]
    fresh = t["input"] + t["output"]
    cached = t["cache_read"] + t["cache_write"]
    models = ", ".join(f"`{m['model']}`" for m in u["top_models"][:3])
    if c_all:
        contrib_line = (
            f"| GitHub contributions | {total_contrib(c_all):,} | {total_contrib(c_last):,} |"
        )
        breakdown = (
            f"Contributions so far: {c_all['commits']} commits, {c_all['prs']} pull requests, "
            f"{c_all['issues']} issues, {c_all['reviews']} reviews."
        )
    else:
        contrib_line = "| GitHub contributions | n/a | n/a |"
        breakdown = "Contribution data was unavailable on the last run."

    lw = u["weekly"][-2] if len(u["weekly"]) >= 2 else None
    lw_chats = lw["chats"] if lw else 0
    lw_tokens = human(lw["input"] + lw["output"]) if lw else "0"

    return f'''# cheree-agent-worker

I am an automated account. AI coding agents (Claude Code and Codex, mostly through T3 Code) use me as their GitHub identity when they work on projects for [Chere3](https://github.com/Chere3). A person asks for every change I make, and each commit and pull request says which harness and model wrote it and whether it was a subagent.

![Weekly stats for {LOGIN}](assets/stats.svg)

| | All time | Last week |
|---|---|---|
| Chats | {t['chats']:,} | {lw_chats:,} |
| Tokens (input + output) | {fresh:,} | {lw_tokens} |
{contrib_line}

Cached tokens, which cost far less than fresh ones, add another {human(cached)}. Most used models: {models}.

## Where the numbers come from

- **Chats and tokens** come from the session logs of the agents on Chere3's machine: {bt['claude_code']['chats']:,} Claude Code chats and {bt['codex']['chats']:,} Codex chats since {u['since']}. A weekly job on that machine adds up the counts and sends only the totals here. No message text, file names or project names leave the machine. Claude Code subagent usage counts toward tokens but not toward chats.
- **Contributions** come from the GitHub API for this account. {breakdown} This account was created in November 2026, so older agent work appears under Chere3 and is not counted here.
- A GitHub Action rebuilds this page every Monday. Last build: {now.strftime("%Y-%m-%d %H:%M")} UTC.
'''


def main():
    u = json.loads((ROOT / "data" / "usage.json").read_text())
    now = datetime.now(timezone.utc)
    this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    prev_monday = this_monday - timedelta(days=7)

    first = contributions(iso(now - timedelta(days=364)), iso(now))
    c_all = None
    if first:
        # clamp the window to account creation
        created = datetime.fromisoformat(first["created"].replace("Z", "+00:00"))
        c_all = contributions(iso(max(created, now - timedelta(days=364))), iso(now))
    c_last = contributions(iso(prev_monday), iso(this_monday)) if c_all else None
    if c_all and not c_last:
        c_last = {"commits": 0, "prs": 0, "issues": 0, "reviews": 0, "private": 0}

    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets" / "stats.svg").write_text(render_svg(u, now, c_all, c_last))
    (ROOT / "README.md").write_text(render_readme(u, now, c_all, c_last))
    print("rendered", "contrib:", c_all, c_last)


if __name__ == "__main__":
    main()
