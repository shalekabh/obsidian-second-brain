"""What's wrong with my work, what changed, and what keeps going wrong — in one command.

Built 2026-09-14 after a check found the vault 10 notes behind memory, still
asserting overturned claims, and a scheduled task one run away from
double-counting P&L because a corrected rule had four hand-made copies and
only two got updated. Nothing detected any of that automatically.

Checks (stdlib only, no network, a few seconds):
  1. drift        memory notes missing / changed vs the vault (via sync_vault.plan)
  2. retractions  overturned claims (retractions.json) still asserted anywhere:
                  memory, vault, scheduled tasks, skills, CLAUDE.md files.
                  HIGH when found in something that drives actions (task/skill)
  3. copies       control files restating facts that memory owns — the exact
                  shape of the double-count near-miss; they go stale silently
  4. versioning   repos with uncommitted / unpushed work, and any project
                  .claude/skills directory not under git
  5. experiments  live experiments owed a verdict, or with stale probe numbers
  6. inbox        notes sync_vault could not classify
  7. changes      what was added / edited / removed since the last check
  8. patterns     recurring failure themes mined from the correction language
                  in memory — the lessons that keep having to be relearned

    python work_health.py            # full report -> vault Reviews/Work Health — Latest.md
    python work_health.py --sync     # mirror memory -> vault first, then check
    python work_health.py --hook     # SessionStart: JSON additionalContext, brief
    python work_health.py --commit   # afterwards, commit + push the vault if dirty
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys

HOME = pathlib.Path.home()
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sync_vault  # noqa: E402

VAULT = sync_vault.VAULT
MEMORY_DIRS = [root for root, _ in sync_vault.STORES if root.is_dir()]
REPORT = VAULT / "Reviews" / "Work Health — Latest.md"
STATE = HOME / ".config" / "obsidian-second-brain" / "work_health_state.json"
# Windows consoles default to cp1252, which can't print £/−/— and crashed the brief.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONTROL_SOURCES = [
    (HOME / ".claude" / "scheduled-tasks", "*/SKILL.md"),
    (HOME / "AI_TRADING_BOT_FIXED" / ".claude" / "skills", "*/SKILL.md"),
    (HOME / ".claude" / "skills", "*/SKILL.md"),
    (HOME / ".claude", "CLAUDE.md"),
    (HOME / "AI_TRADING_BOT_FIXED", "CLAUDE.md"),
]
REPOS = {
    "second-brain-vault": VAULT,
    "obsidian-second-brain (fork)": HOME / ".claude" / "skills" / "obsidian-second-brain",
    "claude-skills": HOME / ".claude" / "skills",
    "trading-claude-config": HOME / "AI_TRADING_BOT_FIXED" / ".claude",
    "NEXEVO": HOME / "Downloads" / "NEXEVO",
}
PROJECT_SKILL_GLOBS = ["*/.claude/skills", "*/*/.claude/skills"]

# A line that negates or corrects the claim is acknowledging it, not asserting it.
# Mirrors the convention in memory/superseded_claims.md.
GUARD = re.compile(r"(?i)\b(not|never|no longer|wrong|false|superseded|retract\w*|incorrect|"
                   r"corrected|old claim|was the|do not|don't|isn't|stale|out of date|"
                   r"pre-?correction|double.?count\w*)\b|~~")
MONEY = re.compile(r"[−-]?£\d[\d,]*(?:\.\d+)?")
# Only decimal percentages (1.95%, 0.75%) are specific enough to be a copied fact.
# Whole ones (0%, 50%, 100%) matched unrelated design skills and were pure noise.
PCT = re.compile(r"(?<![\w.])\d+\.\d+%")
CORRECTION = re.compile(r"(?i)lesson|wrong|correct|mistake|skipped|caught|false|superseded|"
                        r"missed|failure|should have|was not|wasn't|bug\b|near-miss|badly")
THEMES = {
    "Measured the wrong number (gross vs net, bad journal P&L)":
        r"(?i)\bgross\b|pnl_net|double.?count|journal (pnl|p&l)|overstat|understat|never matched",
    "Judged against the wrong comparison":
        r"(?i)wrong (comparison|yardstick|test)|baseline|isolat|absolute (floor|threshold)|own criterion",
    "Stale copies / drift between stores":
        r"(?i)\bstale\b|drift|out of date|hand.?copied|copies|restat",
    "Skipped a pre-flight step":
        r"(?i)skipped|pre-?flight|before (building|writing|touching)|without (checking|looking|running)",
    "Unversioned or unbacked work":
        r"(?i)untracked|not (a|in any) git|version control|no backup|one disk",
    "Cross-strategy isolation bugs":
        r"(?i)another strategy|isolation|orphan|config\.MARKETS|weekend.?flatten",
    "A premise that turned out false":
        r"(?i)premise|was (wrong|false)|turned out|overturn",
}
SEV_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}


class F:
    def __init__(self, sev, area, msg, where=""):
        self.sev, self.area, self.msg, self.where = sev, area, msg, where


def read(p: pathlib.Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def git(path: pathlib.Path, *args) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=20)
        return r.returncode, r.stdout.strip()
    except Exception as e:
        return 1, str(e)


def memory_files():
    for d in MEMORY_DIRS:
        yield from sorted(d.glob("*.md"))


def vault_files():
    for p in VAULT.rglob("*.md"):
        if sync_vault.SKIP_PARTS & set(p.parts) or p == REPORT:
            continue
        yield p


def control_files():
    seen = set()
    for root, pattern in CONTROL_SOURCES:
        if not root.is_dir():
            continue
        for p in root.glob(pattern):
            if ".bak" in p.name or p in seen:
                continue
            seen.add(p)
            yield p


# ---------------------------------------------------------------- checks
def check_drift(findings, synced):
    p = sync_vault.plan()
    if synced:
        findings.append(F("LOW", "drift", f"Synced this run: {len(synced['new'])} new, "
                                          f"{len(synced['changed'])} refreshed"))
    if p["new"]:
        findings.append(F("MED", "drift", f"{len(p['new'])} memory notes missing from the vault",
                          ", ".join(s for s, _, _ in p["new"][:6])))
    if p["changed"]:
        findings.append(F("MED", "drift", f"{len(p['changed'])} vault copies behind memory",
                          ", ".join(s for s, _, _ in p["changed"][:6])))
    for o in p["orphans"]:
        findings.append(F("LOW", "drift", "Vault copy whose memory source is gone", o))


def load_superseded() -> list[dict]:
    """Parse the canonical registry: memory/superseded_claims.md.

    Memory is canonical, so the list of overturned beliefs lives there as a
    markdown table rather than in a second copy next to this script.
    Columns: id | wrong-claim regex | what is true now | since | canonical.
    `\\|` inside a cell is a literal alternation pipe.
    """
    src = next((d / "superseded_claims.md" for d in MEMORY_DIRS
                if (d / "superseded_claims.md").exists()), None)
    if src is None:
        raise FileNotFoundError("memory/superseded_claims.md not found")
    rules = []
    for line in read(src).splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 5:
            continue
        rid, pat, why, since, canon = cells[:5]
        pat = pat.strip("`").replace("\\|", "|")
        try:
            re.compile(pat)
        except re.error:
            continue
        rules.append({"id": rid.strip("`"), "pattern": "(?i)" + pat, "why": why,
                      "since": since, "canonical": canon})
    return rules


def check_retractions(findings):
    try:
        rules = load_superseded()
    except Exception as e:
        findings.append(F("HIGH", "retractions", f"Registry unreadable, so drift can't be detected: {e}"))
        return
    if not rules:
        findings.append(F("HIGH", "retractions", "superseded_claims.md parsed to zero rules — check the table"))
        return
    controls = set(control_files())
    for path in [*memory_files(), *vault_files(), *controls]:
        if path.stem == "superseded_claims":
            continue  # the registry quotes every wrong claim on purpose
        text = read(path)
        for rule in rules:
            if f"acknowledges: {rule['id']}" in text:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if re.search(rule["pattern"], line) and not GUARD.search(line):
                    sev = "HIGH" if path in controls else "MED"
                    findings.append(F(sev, "retractions",
                                      f"Overturned claim `{rule['id']}` still asserted — {rule['why']}",
                                      f"{path}:{n}"))
                    break


def check_copies(findings):
    owned = set()
    for p in memory_files():
        t = read(p)
        owned |= {m.replace("−", "-") for m in MONEY.findall(t)}
        owned |= set(PCT.findall(t))
    where_seen: dict[str, set] = {}
    for p in control_files():
        t = read(p)
        toks = {m.replace("−", "-") for m in MONEY.findall(t)} | set(PCT.findall(t))
        restated = sorted(toks & owned)
        for tok in restated:
            where_seen.setdefault(tok, set()).add(p)
        if len(restated) >= 6:
            findings.append(F("MED", "copies",
                              f"Restates {len(restated)} facts memory owns — these go stale silently. "
                              f"Point to the canonical note instead of copying numbers",
                              f"{p}  e.g. {', '.join(restated[:5])}"))
    multi = {t: s for t, s in where_seen.items() if len(s) >= 3}
    if multi:
        findings.append(F("LOW", "copies", f"{len(multi)} facts are copied into 3+ control files",
                          ", ".join(sorted(multi)[:8])))


def check_versioning(findings):
    for name, path in REPOS.items():
        if not path.is_dir():
            findings.append(F("MED", "versioning", f"{name}: expected repo path missing", str(path)))
            continue
        rc, top = git(path, "rev-parse", "--show-toplevel")
        if rc != 0 or pathlib.Path(top).resolve() != path.resolve():
            findings.append(F("HIGH", "versioning", f"{name}: not its own git repo", str(path)))
            continue
        _, porcelain = git(path, "status", "--porcelain")
        dirty = [l for l in porcelain.splitlines() if l and "Work Health" not in l]
        if dirty:
            sev = "LOW" if name == "second-brain-vault" else "MED"
            findings.append(F(sev, "versioning", f"{name}: {len(dirty)} uncommitted change(s)", str(path)))
        rc, ahead = git(path, "rev-list", "--count", "@{u}..HEAD")
        if rc != 0:
            findings.append(F("MED", "versioning", f"{name}: branch has no upstream — never pushed?", str(path)))
        elif ahead.isdigit() and int(ahead) > 0:
            findings.append(F("MED", "versioning", f"{name}: {ahead} commit(s) not pushed", str(path)))
    for pattern in PROJECT_SKILL_GLOBS:
        for d in HOME.glob(pattern):
            if d.resolve() == (HOME / ".claude" / "skills").resolve():
                continue
            rc, _ = git(d, "rev-parse", "--is-inside-work-tree")
            if rc != 0:
                n = sum(1 for _ in d.glob("*/SKILL.md"))
                findings.append(F("HIGH", "versioning",
                                  f"Project skills not under version control ({n} skills)", str(d)))


def fm_fields(text: str) -> dict:
    m = sync_vault.FM_RE.match(text)
    out = {}
    if m:
        for line in m.group(0).splitlines():
            k, sep, v = line.partition(":")
            if sep and not line.startswith(" "):
                out[k.strip()] = v.strip()
    return out


def check_experiments(findings):
    today = dt.date.today()
    for p in (VAULT / "Projects" / "Experiments").glob("*.md"):
        f = fm_fields(read(p))
        if f.get("type") != "experiment" or f.get("status") != "live":
            continue
        due = f.get("review_due", "")
        try:
            overdue = dt.date.fromisoformat(due) <= today
        except ValueError:
            overdue = False
        if f.get("verdict_ready") == "true" or overdue:
            findings.append(F("HIGH", "experiments", f"Owed a verdict (review_due {due or '—'})", p.stem))
        synced = f.get("last_synced", "")
        try:
            if synced and (today - dt.date.fromisoformat(synced)).days > 8:
                findings.append(F("MED", "experiments",
                                  f"Probe numbers {(today - dt.date.fromisoformat(synced)).days} days old — "
                                  f"run sync_experiments.py", p.stem))
        except ValueError:
            pass


def check_inbox(findings):
    inbox = VAULT / sync_vault.INBOX
    if inbox.is_dir():
        notes = [p.stem for p in inbox.glob("*.md") if "Index" not in p.stem]
        if notes:
            findings.append(F("MED", "inbox", f"{len(notes)} note(s) no folder rule matched — "
                                              f"file them and add a rule in sync_vault.RULES",
                              ", ".join(notes[:8])))


def snapshot() -> dict:
    snap = {}
    for group, files in (("memory", memory_files()), ("vault", vault_files()), ("control", control_files())):
        for p in files:
            snap[f"{group}|{p}"] = hashlib.sha1(read(p).encode("utf-8")).hexdigest()
    return snap


def check_changes(save: bool) -> dict:
    now = snapshot()
    try:
        prev = json.loads(read(STATE))
    except Exception:
        prev = {}
    before = prev.get("files", {})
    out = {
        "since": prev.get("at"),
        "added": sorted(k for k in now if k not in before),
        "modified": sorted(k for k in now if k in before and now[k] != before[k]),
        "removed": sorted(k for k in before if k not in now),
    }
    if save:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"at": dt.datetime.now().isoformat(timespec="minutes"),
                                     "files": now}), encoding="utf-8")
    return out


def check_patterns(findings) -> list:
    recent_cut = dt.datetime.now().timestamp() - 30 * 86400
    rows = []
    for theme, pat in THEMES.items():
        rx = re.compile(pat)
        hits, recent = [], 0
        for p in memory_files():
            lines = [l for l in read(p).splitlines() if CORRECTION.search(l) and rx.search(l)]
            if lines:
                hits.append(p.stem)
                recent += p.stat().st_mtime >= recent_cut
        if hits:
            rows.append((theme, len(hits), recent, hits))
    rows.sort(key=lambda r: (-r[2], -r[1]))
    for theme, n, recent, hits in rows:
        if n >= 3 and recent >= 2:
            findings.append(F("MED", "patterns",
                              f"Recurring: {theme} — {n} notes, {recent} in the last 30 days. "
                              f"Candidate for an automated check or standing rule",
                              ", ".join(hits[:5])))
    return rows


# ---------------------------------------------------------------- output
def write_report(findings, changes, patterns):
    today = dt.date.today().isoformat()
    counts = {s: sum(f.sev == s for f in findings) for s in SEV_ORDER}
    L = ["---", f"date: {today}", "type: review", "tags:", "  - work-health", "  - review",
         "ai-first: true", "vault-authored: true", "---", "",
         "# Work Health — Latest", "",
         "## For future agent", "",
         "Generated by `work_health.py` (obsidian-second-brain fork, scripts/local). Overwritten every run — "
         "do not edit. HIGH items drive wrong actions if left; fix those first.", "",
         f"**{today}** — {counts['HIGH']} HIGH · {counts['MED']} MED · {counts['LOW']} LOW", ""]
    for sev in SEV_ORDER:
        items = [f for f in findings if f.sev == sev]
        if not items:
            continue
        L += [f"## {sev}", ""]
        for f in items:
            L.append(f"- **[{f.area}]** {f.msg}" + (f"  \n  `{f.where}`" if f.where else ""))
        L.append("")
    L += ["## What changed since last check", "",
          f"Previous check: {changes['since'] or 'none (first run)'}", ""]
    for kind in ("added", "modified", "removed"):
        items = changes[kind]
        L.append(f"- **{kind}**: {len(items)}")
        for k in items[:15]:
            group, _, path = k.partition("|")
            L.append(f"  - {group}: `{pathlib.Path(path).name}`")
    L += ["", "## Recurring themes in corrections", "",
          "| Theme | Notes | Last 30 days |", "|---|---|---|"]
    for theme, n, recent, _ in patterns:
        L.append(f"| {theme} | {n} | {recent} |")
    L.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L), encoding="utf-8")
    return counts


def brief(findings, counts, changes) -> str:
    parts = [f"WORK HEALTH {dt.date.today()}: {counts['HIGH']} HIGH, {counts['MED']} MED."]
    for f in [x for x in findings if x.sev == "HIGH"][:5] + [x for x in findings if x.sev == "MED"][:3]:
        # where = "C:\...\file.md:12" or "C:\...\SKILL.md  e.g. ..." — split(':') cut at the
        # drive letter and printed "(C)". Strip only a trailing :line, keep parent for SKILL.md.
        if f.where:
            wp = pathlib.Path(re.sub(r":\d+$", "", f.where.split("  ")[0]))
            name = f"{wp.parent.name}/{wp.name}" if wp.name in ("SKILL.md", "CLAUDE.md") else wp.name
            loc = f" ({name})"
        else:
            loc = ""
        parts.append(f"- [{f.sev} {f.area}] {f.msg[:160]}{loc}")
    n = len(changes["added"]) + len(changes["modified"]) + len(changes["removed"])
    if changes["since"]:
        parts.append(f"{n} file(s) changed since {changes['since']}.")
    parts.append("Full report: vault Reviews/Work Health — Latest.md. Fix HIGH items before acting on "
                 "anything they touch.")
    return "\n".join(parts)


def commit_vault():
    rc, porcelain = git(VAULT, "status", "--porcelain")
    if rc != 0 or not porcelain.strip():
        return "vault clean"
    git(VAULT, "add", "-A")
    git(VAULT, "-c", "user.email=shalekabh@gmail.com", "-c", "user.name=Shy", "commit", "-q", "-m",
        f"Auto-sync memory -> vault + work health ({dt.date.today()})\n\n"
        f"Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>")
    rc, out = git(VAULT, "push", "-q", "origin", "HEAD")
    return "vault committed and pushed" if rc == 0 else f"vault committed; push failed: {out[:120]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true", help="mirror memory -> vault before checking")
    ap.add_argument("--hook", action="store_true", help="SessionStart JSON output")
    ap.add_argument("--commit", action="store_true", help="commit + push the vault afterwards")
    ap.add_argument("--no-save", action="store_true", help="don't advance the change snapshot")
    a = ap.parse_args()

    synced = sync_vault.sync(quiet=True) if a.sync else None
    findings: list[F] = []
    for check in (lambda: check_drift(findings, synced), lambda: check_retractions(findings),
                  lambda: check_copies(findings), lambda: check_versioning(findings),
                  lambda: check_experiments(findings), lambda: check_inbox(findings)):
        try:
            check()
        except Exception as e:  # a broken check must never kill session start
            findings.append(F("MED", "internal", f"check failed: {e}"))
    patterns = check_patterns(findings)
    changes = check_changes(save=not a.no_save)
    findings.sort(key=lambda f: SEV_ORDER[f.sev])
    counts = write_report(findings, changes, patterns)
    note = commit_vault() if a.commit else ""

    if a.hook:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": brief(findings, counts, changes)}}))
    else:
        print(brief(findings, counts, changes))
        if note:
            print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
