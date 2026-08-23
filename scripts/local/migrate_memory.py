"""Migrate Claude memory files into the obsidian-second-brain vault.

Non-destructive: reads from ~/.claude/projects/*/memory/, writes into the vault.
Originals are left untouched so the existing memory system keeps working.

Rules applied per file:
  - filename preserved (so existing [[wikilinks]] keep resolving vault-wide)
  - comm-bot files prefixed `comm_` to avoid collisions with FX files of the
    same name (MEMORY.md, trust.md, session_discipline.md); wikilinks inside
    those files are rewritten to match
  - YAML frontmatter added if absent (vault_health requires it on >50b notes)
  - markdown index links `[Title](file.md)` -> `[[file|Title]]` so they still
    resolve once notes are distributed across folders
  - body content preserved verbatim
"""

import re
import sys
from datetime import datetime
from pathlib import Path

HOME = Path("C:/Users/shale")
VAULT = HOME / "Documents" / "SecondBrain"
FX_SRC = HOME / ".claude/projects/C--Users-shale-AI-TRADING-BOT-FIXED/memory"
COMM_SRC = HOME / ".claude/projects/c--Users-shale-AI-SPREAD-BETTING-BOT-COMM/memory"

DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)]+?)\.md\)")

# Knowledge = durable rules/frameworks. Reviews = running review log.
# Dev Logs = dated session changelogs. Ideas = not-yet-committed work.
# Projects = current state of a live workstream.
KNOWLEDGE = {
    "audit_checklist", "session_discipline", "improvement_framework",
    "per_bot_metrics_framework", "data_collection", "scheduled_routine",
    "trust", "skill_web_design_hunter_theme",
}
REVIEWS = {"weekly_review_log"}
IDEAS = {"project_trustmrr_clone_research"}
DEVLOG_EXTRA = {"bugs", "refactor_2026-03-21"}


def classify(stem: str) -> tuple[str, str, list[str]]:
    """Return (folder, type, extra_tags) for a memory filename stem."""
    if stem.startswith("session_") or stem in DEVLOG_EXTRA:
        return "Dev Logs", "devlog", ["session-log"]
    if stem.startswith("feedback_") or stem in KNOWLEDGE:
        return "Knowledge", "knowledge", ["operating-rule"]
    if stem in REVIEWS:
        return "Reviews", "review", ["weekly-review"]
    if stem in IDEAS:
        return "Ideas", "idea", []
    if stem == "MEMORY":
        return "Projects", "project", ["index"]
    return "Projects", "project", ["bot-state"]


def extract_date(stem: str, path: Path) -> str:
    m = DATE_RE.search(stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def first_heading(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def build(src: Path, bot: str, rename: dict[str, str]) -> tuple[Path, str]:
    raw = src.read_text(encoding="utf-8", errors="replace")
    stem = src.stem
    out_stem = rename.get(stem, stem)
    folder, ntype, extra = classify(stem)
    date = extract_date(stem, src)

    body = FM_RE.sub("", raw, count=1) if FM_RE.match(raw) else raw

    # `[Title](file.md)` -> `[[file|Title]]`, honouring comm renames.
    def to_wikilink(m: re.Match) -> str:
        title, target = m.group(1), Path(m.group(2)).name
        return f"[[{rename.get(target, target)}|{title}]]"

    body = MD_LINK_RE.sub(to_wikilink, body)

    # Rewrite [[wikilinks]] that point at renamed comm notes.
    if rename:
        def fix_wl(m: re.Match) -> str:
            inner = m.group(1)
            target, sep, alias = inner.partition("|")
            return f"[[{rename.get(target.strip(), target.strip())}{sep}{alias}]]"
        body = re.sub(r"\[\[([^\]]+)\]\]", fix_wl, body)

    title = first_heading(body, out_stem.replace("_", " "))
    tags = ["trading-bot", bot, *extra]
    fm = (
        "---\n"
        f"date: {date}\n"
        f"type: {ntype}\n"
        "tags:\n" + "".join(f"  - {t}\n" for t in tags) +
        "ai-first: true\n"
        f"bot: {bot}\n"
        "source: claude-memory-migration\n"
        f"migrated: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"original: {src.as_posix()}\n"
        "---\n\n"
    )

    agent_note = (
        "## For future agent\n\n"
        f"Migrated from the Claude Code memory store for the "
        f"{'FX (Meruem) / trading' if bot == 'fx' else 'commodities (Netero)'} bot. "
        "Original still lives at the path in `original:` above and is unchanged.\n\n"
    )

    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body.lstrip()}"
    lines = body.split("\n")
    body = lines[0] + "\n\n" + agent_note + "\n".join(lines[1:]).lstrip("\n")

    return VAULT / folder / f"{out_stem}.md", fm + body.rstrip() + "\n"


def main() -> int:
    if not FX_SRC.is_dir():
        print(f"FX memory dir not found: {FX_SRC}")
        return 1

    comm_files = sorted(COMM_SRC.glob("*.md")) if COMM_SRC.is_dir() else []
    comm_rename = {p.stem: f"comm_{p.stem}" for p in comm_files}
    comm_rename.update({f"{p.stem}.md": f"comm_{p.stem}" for p in comm_files})

    jobs = [(p, "fx", {}) for p in sorted(FX_SRC.glob("*.md"))]
    jobs += [(p, "comm", comm_rename) for p in comm_files]

    counts: dict[str, int] = {}
    for src, bot, rename in jobs:
        dest, content = build(src, bot, rename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        counts[dest.parent.name] = counts.get(dest.parent.name, 0) + 1

    print(f"Migrated {len(jobs)} notes into {VAULT}")
    for folder, n in sorted(counts.items()):
        print(f"  {folder:<12} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
