#!/usr/bin/env python3
"""Export Claude Code JSONL transcripts to readable markdown. Lazy one-shot."""
import json, glob, os, re, datetime

SRC = "/home/aff34/.claude/projects/-home-aff34-Downloads-Algo-Test-Software"
OUT = "/home/aff34/Downloads/Algo_Test_Software/claude_conversation"
os.makedirs(OUT, exist_ok=True)


def render_content(content):
    """content is str or list of blocks -> readable text."""
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if not isinstance(b, dict):
            parts.append(str(b)); continue
        t = b.get("type")
        if t == "text":
            parts.append(b.get("text", ""))
        elif t == "thinking":
            th = b.get("thinking", "").strip()
            if th:
                parts.append(f"_[thinking]_\n{th}")
        elif t == "tool_use":
            inp = json.dumps(b.get("input", {}), indent=2, ensure_ascii=False)
            parts.append(f"**[tool: {b.get('name')}]**\n```json\n{inp}\n```")
        elif t == "tool_result":
            c = b.get("content", "")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
            parts.append(f"**[tool result]**\n```\n{str(c)[:4000]}\n```")
        elif t == "image":
            parts.append("_[image]_")
    return "\n\n".join(p for p in parts if p and p.strip())


def first_user_text(rows):
    for r in rows:
        if r.get("type") == "user":
            msg = r.get("message", {})
            c = msg.get("content")
            txt = render_content(c) if c is not None else ""
            txt = txt.strip()
            # skip hook/system-reminder noise
            if txt and not txt.startswith("<") and "hookEventName" not in txt:
                return txt
    return ""


def slug(s, n=50):
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"[^A-Za-z0-9_-]", "", s)
    return s[:n] or "conversation"


count = 0
for path in sorted(glob.glob(os.path.join(SRC, "*.jsonl"))):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not rows:
        continue
    sid = os.path.splitext(os.path.basename(path))[0]
    ts = None
    for r in rows:
        if r.get("timestamp"):
            ts = r["timestamp"][:10]; break
    title = first_user_text(rows)
    fname = f"{ts or 'nodate'}__{slug(title)}__{sid[:8]}.md"

    out_lines = [f"# Conversation {sid}", ""]
    if title:
        out_lines += [f"**First prompt:** {title[:200]}", ""]
    out_lines += [f"**Date:** {ts}", "", "---", ""]

    for r in rows:
        typ = r.get("type")
        if typ not in ("user", "assistant"):
            continue
        msg = r.get("message", {})
        role = msg.get("role", typ)
        c = msg.get("content")
        body = render_content(c) if c is not None else ""
        if not body.strip():
            continue
        t = r.get("timestamp", "")[11:19]
        out_lines.append(f"### {role.upper()}  {t}".rstrip())
        out_lines.append("")
        out_lines.append(body)
        out_lines.append("")

    with open(os.path.join(OUT, fname), "w") as f:
        f.write("\n".join(out_lines))
    count += 1
    print(f"  {fname}")

print(f"\nExported {count} conversations to {OUT}")
