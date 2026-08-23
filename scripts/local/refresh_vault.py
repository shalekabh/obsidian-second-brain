"""Refresh vault copies of memory notes that changed since migration.

Rewrites the note IN PLACE at its current vault location, so the subject-folder
regrouping is preserved. Only touches notes whose source is newer.
"""
import re, sys, pathlib, datetime

HOME = pathlib.Path("C:/Users/shale")
VAULT = HOME / "Documents/SecondBrain"
SRC = HOME / ".claude/projects/C--Users-shale-AI-TRADING-BOT-FIXED/memory"

FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)]+?)\.md\)")
DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")

# Map stem -> existing vault path (preserves the regrouped folders).
existing = {p.stem: p for p in VAULT.rglob("*.md") if ".obsidian" not in p.parts}

changed = [n.strip() for n in sys.argv[1:]]
updated = []

for name in changed:
    src = SRC / name
    stem = src.stem
    dest = existing.get(stem)
    if not src.exists() or dest is None:
        print(f"  SKIP {name} (src or vault note missing)")
        continue

    old_fm_match = FM_RE.match(dest.read_text(encoding="utf-8"))
    old_fm = old_fm_match.group(0) if old_fm_match else ""

    raw = src.read_text(encoding="utf-8", errors="replace")
    body = FM_RE.sub("", raw, count=1) if FM_RE.match(raw) else raw
    body = MD_LINK_RE.sub(
        lambda m: f"[[{pathlib.Path(m.group(2)).name}|{m.group(1)}]]", body)
    body = re.sub(r"\[\[([^\]|]+)\|\1\.md\]\]", r"[[\1]]", body)

    title = next((l[2:].strip() for l in body.splitlines() if l.startswith("# ")),
                 stem.replace("_", " "))
    agent = (f"## For future agent\n\nMigrated from the Claude Code memory store for the "
             f"FX (Meruem) / trading bot. Original still lives at the path in `original:` "
             f"above and is unchanged.\n\n")
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body.lstrip()}"
    lines = body.split("\n")
    body = lines[0] + "\n\n" + agent + "\n".join(lines[1:]).lstrip("\n")

    # Keep original frontmatter, stamp the refresh.
    fm = old_fm.rstrip("\n").removesuffix("---").rstrip("\n")
    fm = re.sub(r"\nrefreshed: [0-9-]+", "", fm)
    fm += f"\nrefreshed: {datetime.date.today()}\n---\n\n"

    dest.write_text(fm + body.rstrip() + "\n", encoding="utf-8")
    updated.append(str(dest.relative_to(VAULT)))

for u in updated: print(f"  refreshed {u}")
print(f"{len(updated)} notes refreshed")
