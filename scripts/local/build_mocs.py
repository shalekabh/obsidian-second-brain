import re, pathlib, datetime
V = pathlib.Path("C:/Users/shale/Documents/SecondBrain")

# Harvest the one-line hooks the original MEMORY indexes already carried.
hooks = {}
for idx in ["Projects/MEMORY.md", "Projects/Netero Comm Bot/comm_MEMORY.md"]:
    p = V / idx
    if not p.exists(): continue
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*[-*]\s*\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*[—–-]\s*(.+)", line)
        if m:
            hooks.setdefault(m.group(1).strip(), m.group(2).strip().rstrip("."))

def blurb(p):
    if p.stem in hooks: return hooks[p.stem]
    txt = p.read_text(encoding="utf-8", errors="replace")
    txt = re.sub(r"^---.*?\n---\s*\n", "", txt, flags=re.DOTALL)
    txt = re.sub(r"##\s*For future agent.*?(?=\n#|\n##|\Z)", "", txt, flags=re.DOTALL)
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "|", "---", "```", "*Generated")): continue
        s = re.sub(r"^[-*]\s*", "", s)
        s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
        s = re.sub(r"\*\*|__|`", "", s)
        if len(s) > 12:
            return (s[:157] + "...") if len(s) > 160 else s
    return "—"

CLUSTERS = [
  ("Projects/Meruem FX Bot", "Meruem FX Bot — Index", "fx", "project",
   "The FX / spread-betting bot. Strategy experiments, exit-efficiency work, carry sleeves, and the live evidence ladder."),
  ("Projects/Netero Comm Bot", "Netero Comm Bot — Index", "comm", "project",
   "The commodities bot on Capital.com. Trade reviews, reconciliation history, and the flywheel."),
  ("Projects/Killua Trend Bot", "Killua Trend Bot — Index", "killua", "project",
   "The crypto trend / placement bot and the mean-reversion research sleeve feeding it."),
  ("Projects/Infrastructure", "Infrastructure — Index", "infra", "project",
   "Shared plumbing across all bots: dashboard, Telegram sync, access, and secret-exposure posture."),
  ("Knowledge/Operating Rules", "Operating Rules — Index", "rules", "knowledge",
   "How Claude must work on these bots. Read these before touching code — they encode past failures."),
  ("Knowledge/Frameworks", "Frameworks — Index", "frameworks", "knowledge",
   "Repeatable methods: how work improves, how bots are measured, how data is collected and audited."),
  ("Knowledge/Skills", "Skills — Index", "skills", "knowledge", "Reusable skills that apply beyond these bots."),
  ("Dev Logs/Meruem FX", "FX Session Log — Index", "fx", "devlog",
   "Chronological FX bot session archive, newest first. The change history behind current state."),
  ("Dev Logs/Netero Comm", "Comm Session Log — Index", "comm", "devlog",
   "Chronological comm bot session archive, newest first."),
  ("Projects/Findings", "Findings — Index", "findings", "project",
   "Investigation write-ups and verdict passes. Conclusions with their evidence, kept separate from "
   "Projects/Experiments so the live registry stays a registry."),
  ("Dev Logs/Enzo Ferrari 3D Render", "Enzo Ferrari Log — Index", "render", "devlog",
   "Session archive for the Enzo Ferrari 3D render work. Not a trading project."),
]

made = []
for folder, title, tag, ntype, desc in CLUSTERS:
    d = V / folder
    if not d.is_dir(): continue
    notes = sorted([p for p in d.glob("*.md") if "Index" not in p.stem],
                   key=lambda p: p.stem, reverse=(ntype == "devlog"))
    if not notes: continue
    rows = "\n".join(f"- [[{p.stem}]] — {blurb(p)}" for p in notes)
    body = f"""---
date: {datetime.date.today()}
type: {ntype}
tags:
  - trading-bot
  - {tag}
  - index
  - moc
ai-first: true
source: claude-memory-migration
---

# {title}

## For future agent

Map of content for `{folder}/`. Every note in that folder is linked below with a
one-line hook, so this is the cheapest way to find the right note without reading
the folder. Regenerate after adding notes. Parent: [[Home]].

{desc}

## Notes ({len(notes)})

{rows}
"""
    out = d / f"{title}.md"
    out.write_text(body, encoding="utf-8")
    made.append((str(out.relative_to(V)), len(notes)))

for f, n in made: print(f"  {f}  ({n} notes)")
print(f"built {len(made)} index notes")
