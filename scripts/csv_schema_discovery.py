#!/usr/bin/env python3
"""
Discover CSV files and CSV usage across a codebase.

Outputs:
- Markdown report with CSV inventory, headers, and code references
- JSON report with structured data for downstream processing
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Iterable


DEFAULT_CODE_EXTENSIONS = {
    ".py",
    ".ipynb",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".scala",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".sql",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".md",
    ".txt",
}


CSV_REF_PATTERNS = [
    re.compile(r"\.csv\b", re.IGNORECASE),
    re.compile(r"\bread_csv\b", re.IGNORECASE),
    re.compile(r"\bto_csv\b", re.IGNORECASE),
    re.compile(r"\bcsv\.", re.IGNORECASE),
    re.compile(r"\bDictReader\b", re.IGNORECASE),
    re.compile(r"\bDictWriter\b", re.IGNORECASE),
]


def is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except Exception:
        return True


def safe_read_text(path: Path) -> str | None:
    if is_probably_binary(path):
        return None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="strict")
        except Exception:
            continue
    return None


def discover_csv_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.csv") if p.is_file())


def read_csv_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            return next(reader, [])
    except Exception:
        try:
            with path.open("r", encoding="latin-1", newline="") as f:
                reader = csv.reader(f)
                return next(reader, [])
        except Exception:
            return []


def guess_delimiter(path: Path) -> str | None:
    try:
        sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:2048]
        dialect = csv.Sniffer().sniff(sample)
        return dialect.delimiter
    except Exception:
        return None


def gather_code_files(root: Path, include_ext: set[str]) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in include_ext:
            yield p


def find_csv_references(text: str) -> list[str]:
    refs: list[str] = []
    for pattern in CSV_REF_PATTERNS:
        if pattern.search(text):
            refs.append(pattern.pattern)
    return refs


def line_matches(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in CSV_REF_PATTERNS):
            out.append((idx, line.strip()))
    return out


def to_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)


def write_markdown(report_path: Path, root: Path, data: dict) -> None:
    lines: list[str] = []
    lines.append("# CSV Schema Discovery Report")
    lines.append("")
    lines.append(f"- Root: `{root}`")
    lines.append(f"- CSV files found: **{len(data['csv_files'])}**")
    lines.append(f"- Code files scanned: **{data['code_files_scanned']}**")
    lines.append(f"- Files with CSV references: **{len(data['csv_references'])}**")
    lines.append("")

    lines.append("## CSV Inventory")
    lines.append("")
    if not data["csv_files"]:
        lines.append("_No CSV files found._")
        lines.append("")
    else:
        for item in data["csv_files"]:
            lines.append(f"### `{item['path']}`")
            lines.append(f"- Size bytes: {item['size_bytes']}")
            lines.append(f"- Rows (estimated): {item['row_count_estimate']}")
            lines.append(f"- Delimiter guess: `{item['delimiter_guess']}`")
            if item["header"]:
                lines.append("- Header columns:")
                for col in item["header"]:
                    lines.append(f"  - `{col}`")
            else:
                lines.append("- Header columns: _unreadable or empty_")
            lines.append("")

    lines.append("## CSV References In Code")
    lines.append("")
    if not data["csv_references"]:
        lines.append("_No CSV references found in scanned code files._")
        lines.append("")
    else:
        for ref in data["csv_references"]:
            lines.append(f"### `{ref['path']}`")
            lines.append(f"- Matched patterns: `{', '.join(ref['patterns'])}`")
            lines.append("- Matching lines:")
            for ln, content in ref["matches"][:200]:
                lines.append(f"  - `{ln}`: `{content}`")
            if len(ref["matches"]) > 200:
                lines.append("  - `_truncated after 200 lines_`")
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def estimate_rows(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            count = sum(1 for _ in f)
        return max(0, count - 1)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover CSV files and usage references.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to scan (default: current directory).",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=Path("csv_schema_discovery_report.md"),
        help="Output Markdown report path.",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        default=Path("csv_schema_discovery_report.json"),
        help="Output JSON report path.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    out_md = args.out_md.resolve()
    out_json = args.out_json.resolve()

    csv_paths = discover_csv_files(root)
    csv_files = []
    for p in csv_paths:
        csv_files.append(
            {
                "path": to_rel(p, root),
                "abs_path": str(p),
                "size_bytes": p.stat().st_size,
                "delimiter_guess": guess_delimiter(p),
                "header": read_csv_header(p),
                "row_count_estimate": estimate_rows(p),
            }
        )

    refs = []
    scanned = 0
    for file_path in gather_code_files(root, DEFAULT_CODE_EXTENSIONS):
        scanned += 1
        text = safe_read_text(file_path)
        if text is None:
            continue
        matched_patterns = find_csv_references(text)
        if not matched_patterns:
            continue
        matches = line_matches(text)
        refs.append(
            {
                "path": to_rel(file_path, root),
                "abs_path": str(file_path),
                "patterns": matched_patterns,
                "matches": matches,
            }
        )

    report_data = {
        "root": str(root),
        "csv_files": csv_files,
        "code_files_scanned": scanned,
        "csv_references": refs,
    }

    out_json.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
    write_markdown(out_md, root, report_data)

    print(f"Wrote Markdown report: {out_md}")
    print(f"Wrote JSON report: {out_json}")
    print(f"CSV files found: {len(csv_files)}")
    print(f"Code files scanned: {scanned}")
    print(f"Files with CSV references: {len(refs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
