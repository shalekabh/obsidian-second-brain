"""Mirror the Claude Code memory stores into the Obsidian vault, automatically.

Memory is canonical. The vault is a readable, linked mirror of it. Before this,
the vault only updated when someone remembered to run refresh_vault.py, so it
kept drifting: on 2026-09-14 it was 10 notes behind and still holding claims
that had already been overturned.

What it does, idempotently (content-compared, not mtime-guessed):
  - NEW memory note  -> classified into the right vault folder and created
  - CHANGED note     -> body rebuilt in place. These survive the rebuild:
                        the vault frontmatter, the vault's own H1 and
                        "For future agent" paragraph, any Obsidian callouts
                        (> [!warning] ...) added in the vault, and any
                        <!-- vault-keep --> ... <!-- /vault-keep --> blocks
  - DELETED source   -> reported as an orphaned copy, never deleted
  - then rebuilds the folder index notes (build_mocs.py)

Notes it cannot classify land in Projects/Inbox, where work_health.py flags
them, so they are visible rather than silently misfiled.
The vault is a git repo, so every rewrite is recoverable.

    python sync_vault.py            # sync
    python sync_vault.py --dry-run  # report only
    python sync_vault.py --quiet    # no output (hooks)
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys

HOME = pathlib.Path.home()
HERE = pathlib.Path(__file__).resolve().parent
VAULT = HOME / "Documents" / "SecondBrain"
STORES = [
    (HOME / ".claude/projects/C--Users-shale-AI-TRADING-BOT-FIXED/memory", ""),
    (HOME / ".claude/projects/c--Users-shale-AI-SPREAD-BETTING-BOT-COMM/memory", "comm_"),
]
INBOX = "Projects/Inbox"

# First match wins. Order matters: specific before general.
RULES = [
    (r"^MEMORY$", "Projects"),
    (r"^comm_MEMORY$", "Projects/Netero Comm Bot"),
    (r"^(comm_)?(session_discipline|trust)$", "Knowledge/Operating Rules"),
    (r"^comm_session_", "Dev Logs/Netero Comm"),
    (r"^(session_|bugs$|refactor_)", "Dev Logs/Meruem FX"),
    (r"^feedback_.*(watch|model|artifact|live_example|page|nexevo|design|3d|render|visual|threejs)",
     "Knowledge/Design Rules"),
    (r"^feedback_", "Knowledge/Operating Rules"),
    (r"^skills?_", "Knowledge/Skills"),
    (r"^(experiment_|journal_accuracy|investigation_|superseded_)", "Projects/Findings"),
    (r"^(audit_checklist|per_bot_metrics|data_collection|improvement_framework|scheduled_routine)",
     "Knowledge/Frameworks"),
    (r"^weekly_review", "Reviews"),
    (r"^project_nexevo", "Projects/NEXEVO Systems"),
    (r"^project_(trustmrr|.*clone)", "Ideas"),
    (r"^crypto_", "Projects/Crypto Bot"),
    (r"^(killua_|research_mean_reversion)", "Projects/Killua Trend Bot"),
    (r"^(dashboard|infra_|security_|obsidian_|work_health|vault_|sync_)", "Projects/Infrastructure"),
    (r"^(state_current|project_goals)$", "Projects"),
    (r"^(comm_|data_integrity)", "Projects/Netero Comm Bot"),
    (r"^(fx_|carry_|journal_backup|live_evidence)", "Projects/Meruem FX Bot"),
]
BOT_BY_FOLDER = [("Meruem", "fx"), ("Netero", "comm"), ("Killua", "killua"), ("Crypto Bot", "crypto"),
                 ("NEXEVO", "nexevo"), ("Design Rules", "design"), ("Enzo", "render")]

FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
MD_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)]+?)\.md\)")
KEEP_RE = re.compile(r"<!-- vault-keep -->.*?<!-- /vault-keep -->", re.DOTALL)
CALLOUT_RE = re.compile(r"(?m)^> \[!.*(?:\n>.*)*")
# The vault's own "For future agent" section, up to the next heading. The original
# migration spliced it INTO the memory body, so it is stripped before comparing.
AGENT_BLOCK_RE = re.compile(r"## For future agent[ \t]*\n\s*.*?(?:\n[ \t]*\n|\Z)", re.DOTALL)
SKIP_PARTS = {".obsidian", ".git", "_trash"}


def classify(stem: str) -> str:
    for pat, folder in RULES:
        if re.search(pat, stem):
            return folder
    return INBOX


def meta_for(folder: str) -> tuple[str, str]:
    bot = next((v for k, v in BOT_BY_FOLDER if k in folder), "shared")
    if folder.startswith("Dev Logs"):
        ntype = "devlog"
    elif folder.startswith("Knowledge"):
        ntype = "knowledge"
    elif folder == "Reviews":
        ntype = "review"
    elif folder == "Ideas":
        ntype = "idea"
    else:
        ntype = "project"
    return bot, ntype


def vault_index() -> dict[str, pathlib.Path]:
    return {p.stem: p for p in VAULT.rglob("*.md") if not SKIP_PARTS & set(p.parts)}


def memory_notes():
    """Yield (source_path, vault_stem, rename_map) for every canonical note."""
    for root, prefix in STORES:
        if not root.is_dir():
            continue
        files = sorted(root.glob("*.md"))
        rename = {p.stem: prefix + p.stem for p in files} if prefix else {}
        for p in files:
            yield p, (prefix + p.stem if prefix else p.stem), rename


def body_from_memory(raw: str, rename: dict) -> tuple[str, str | None]:
    body = FM_RE.sub("", raw, count=1) if FM_RE.match(raw) else raw

    def md2wiki(m):
        target = pathlib.Path(m.group(2)).name
        return f"[[{rename.get(target, target)}|{m.group(1)}]]"

    body = MD_LINK.sub(md2wiki, body)
    body = re.sub(r"\[\[([^\]|]+)\|\1(?:\.md)?\]\]", r"[[\1]]", body)
    if rename:
        body = re.sub(r"\[\[([^\]|#]+)",
                      lambda m: "[[" + rename.get(m.group(1).strip(), m.group(1)), body)
    body = body.strip()
    title = None
    if body.startswith("# "):
        first, _, rest = body.partition("\n")
        title, body = first[2:].strip(), rest.strip()
    return body, title


def split_vault(text: str) -> dict:
    """Pull out the parts of a vault copy that are vault-authored and must survive."""
    fm_m = FM_RE.match(text)
    fm = fm_m.group(0) if fm_m else ""
    rest = text[len(fm):]
    h1 = re.search(r"(?m)^# (.+)$", rest)
    agent = re.search(r"## For future agent\s*\n\s*\n?(.*?)(?:\n\s*\n|\Z)", rest, re.DOTALL)
    return {
        "fm": fm,
        "title": h1.group(1).strip() if h1 else None,
        "agent": agent.group(1).strip() if agent else None,
        "callouts": CALLOUT_RE.findall(rest),
        "keeps": KEEP_RE.findall(rest),
    }


def render(fm: str, title: str, agent: str, callouts: list, body: str, keeps: list) -> str:
    parts = [fm.rstrip("\n") + "\n", f"# {title}\n", f"## For future agent\n\n{agent}\n"]
    parts += [c.strip() + "\n" for c in callouts if c.strip() not in body]
    parts.append(body + "\n")
    parts += [k + "\n" for k in keeps]
    return "\n".join(parts)


def set_fm(fm: str, key: str, value: str) -> str:
    inner = fm.strip()[3:-3].strip("\n") if fm else ""
    if re.search(rf"(?m)^{key}:", inner):
        inner = re.sub(rf"(?m)^{key}:.*$", f"{key}: {value}", inner)
    else:
        inner = inner + f"\n{key}: {value}"
    return f"---\n{inner.strip()}\n---\n\n"


def new_note(src: pathlib.Path, stem: str, body: str, title: str | None, folder: str) -> str:
    bot, ntype = meta_for(folder)
    today = datetime.date.today().isoformat()
    tags = ["trading-bot", bot] if bot not in ("design", "nexevo", "render") else [bot]
    fm = ("---\n" f"date: {today}\n" f"type: {ntype}\n" "tags:\n"
          + "".join(f"  - {t}\n" for t in tags)
          + "ai-first: true\n" f"bot: {bot}\n" "source: claude-memory-migration\n"
          f"migrated: {today}\n" f"original: {src.as_posix()}\n" "---\n\n")
    agent = (f"Mirrored automatically from the Claude Code memory store by sync_vault.py. "
             f"The memory file at `original:` is canonical; edit that, not this copy.")
    if folder == INBOX:
        agent += " **Filed in Inbox because no folder rule matched — move it and add a rule.**"
    return render(fm, title or stem.replace("_", " "), agent, [], body, [])


def _norm(s: str) -> str:
    """Whitespace-insensitive form for content comparison."""
    return re.sub(r"\s+", " ", s).strip()


def plan() -> dict:
    """Compute what a sync would do, without writing anything."""
    vidx = vault_index()
    out = {"new": [], "changed": [], "orphans": [], "unchanged": 0}
    canonical = set()
    for src, stem, rename in memory_notes():
        canonical.add(stem)
        body, mem_title = body_from_memory(src.read_text(encoding="utf-8", errors="replace"), rename)
        existing = vidx.get(stem)
        if existing is None:
            folder = classify(stem)
            out["new"].append((stem, folder, new_note(src, stem, body, mem_title, folder)))
            continue
        vtext = existing.read_text(encoding="utf-8", errors="replace")
        parts = split_vault(vtext)
        if "vault-authored: true" in parts["fm"]:
            continue
        if "claude-memory-migration" not in parts["fm"] and "original:" not in parts["fm"]:
            continue
        agent = parts["agent"] or "Mirrored from the Claude Code memory store; the memory file is canonical."
        title = parts["title"] or mem_title or stem.replace("_", " ")
        candidate = render(parts["fm"], title, agent, parts["callouts"], body, parts["keeps"])
        # Compare CONTENT, not layout. Older vault copies use a different layout
        # (no H1, agent block placed differently), so a whole-text comparison
        # reported ~50 untouched notes as changed. Only rewrite when the memory
        # body is no longer present in the vault copy.
        # The original migration spliced the agent paragraph INTO the body, so strip
        # vault-authored blocks before testing whether the memory body is present.
        vcore = AGENT_BLOCK_RE.sub("", vtext)
        vcore = CALLOUT_RE.sub("", KEEP_RE.sub("", vcore))
        if candidate.strip() == vtext.strip() or _norm(body) in _norm(vcore):
            out["unchanged"] += 1
            continue
        fm2 = set_fm(parts["fm"], "refreshed", datetime.date.today().isoformat())
        out["changed"].append((stem, existing,
                               render(fm2, title, agent, parts["callouts"], body, parts["keeps"])))
    for stem, p in vidx.items():
        if "Index" in stem or stem in canonical:
            continue
        head = p.read_text(encoding="utf-8", errors="replace")[:800]
        if "source: claude-memory-migration" in head and "original:" in head:
            out["orphans"].append(str(p.relative_to(VAULT)))
    return out


def sync(dry_run: bool = False, quiet: bool = False) -> dict:
    p = plan()
    if not dry_run:
        for stem, folder, text in p["new"]:
            d = VAULT / folder
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{stem}.md").write_text(text, encoding="utf-8")
        for stem, path, text in p["changed"]:
            path.write_text(text, encoding="utf-8")
        if p["new"] or p["changed"]:
            subprocess.run([sys.executable, str(HERE / "build_mocs.py")],
                           capture_output=True, text=True, timeout=120)
    if not quiet:
        tag = " (dry run)" if dry_run else ""
        print(f"sync_vault{tag}: {len(p['new'])} new, {len(p['changed'])} refreshed, "
              f"{p['unchanged']} unchanged, {len(p['orphans'])} orphaned copies")
        for stem, folder, _ in p["new"]:
            print(f"  + {folder}/{stem}")
        for stem, path, _ in p["changed"]:
            print(f"  ~ {path.relative_to(VAULT)}")
        for o in p["orphans"]:
            print(f"  ? orphan (memory source gone): {o}")
    return {"new": [(s, f) for s, f, _ in p["new"]],
            "changed": [str(pp.relative_to(VAULT)) for _, pp, _ in p["changed"]],
            "orphans": p["orphans"]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    sync(dry_run=a.dry_run, quiet=a.quiet)
