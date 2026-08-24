"""Sync live bot metrics into the vault's Experiment Register.

Why this exists
---------------
Every strategy change on these bots is deployed with a pre-registered kill
criterion ("judge after ~15-20 firings", "re-check Friday"). Those criteria
were scattered across seven memory notes with hand-copied counts that went
stale, so experiments sailed past their own thresholds unjudged. On
2026-08-24 three of them had: EARLY_CUT (19 vs 10-15), Half-Lock (21 vs
15-20), carry_ledger (22 vs 15-20).

This script reads the experiment notes, pulls the real number off the VPS,
and writes it back into each note's frontmatter so a Dataview query can
surface "due for verdict" automatically.

Safety
------
Probes are NAMED and defined HERE, in code. Notes select a probe by name and
pass a simple argument. Notes never supply shell to run on the trading VPS —
a note is data, and letting note text execute on the box that holds live
positions would be an obvious injection hole. Unknown probe name = skipped.

All probes are strictly READ-ONLY (they cat/parse journal files). Nothing in
this script can touch the trading path.

Usage
-----
    python sync_experiments.py            # sync all
    python sync_experiments.py --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

VAULT = Path("C:/Users/shale/Documents/SecondBrain")
EXP_DIR = VAULT / "Projects" / "Experiments"
SSH_HOST = "root@178.104.233.189"

FX = "/home/botuser/bot"
COMM = "/home/botuser/comm"

# ---------------------------------------------------------------- remote probe
# One round trip: compute every metric on the VPS, return JSON. Read-only.
REMOTE = r'''
import csv, collections, json, os, re

FX = "%(fx)s"
COMM = "%(comm)s"
out = {}

# --- FX close_reason buckets: n, sum, avg -----------------------------------
buckets = collections.defaultdict(list)
tpath = os.path.join(FX, "journal", "trades.csv")
try:
    for r in csv.DictReader(open(tpath)):
        cr = (r.get("close_reason") or "").strip()
        if not cr:
            continue
        try:
            buckets[cr].append(float(r["pnl"]))
        except (TypeError, ValueError):
            continue
except Exception as e:
    out["_trades_error"] = str(e)

for k, v in buckets.items():
    out["fx_close_reason:" + k] = {
        "n": len(v),
        "sum_pnl": round(sum(v), 2),
        "avg_pnl": round(sum(v) / len(v), 2),
    }
out["fx_closed_total"] = {"n": sum(len(v) for v in buckets.values())}

# --- FX log pattern counts --------------------------------------------------
for label, pat in [("Partial exit", "Partial exit")]:
    n = 0
    try:
        with open(os.path.join(FX, "bot_out.log"), errors="replace") as fh:
            for line in fh:
                if pat in line:
                    n += 1
    except Exception:
        n = -1
    out["fx_log_pattern:" + label] = {"n": n}

# --- FX simple row counts ---------------------------------------------------
for label, rel in [("carry_ledger", "journal/carry_ledger.csv")]:
    try:
        with open(os.path.join(FX, rel), errors="replace") as fh:
            n = max(0, sum(1 for _ in fh) - 1)
    except Exception:
        n = -1
    out["fx_file_rows:" + label] = {"n": n}

# --- FX carry sleeve: completed entry->exit cycles ---------------------------
opens = closes = 0
try:
    for r in csv.DictReader(open(os.path.join(FX, "journal", "carry_sleeve_trades.csv"))):
        ev = (r.get("event") or "").strip()
        if ev == "OPEN":
            opens += 1
        elif "CLOSE" in ev:
            closes += 1
except Exception:
    opens = closes = -1
out["fx_carry_cycles"] = {"n": closes, "opens": opens, "closes": closes}

# --- comm reconciliation gap (latest go-forward line) ------------------------
gap = resid = bal = None
try:
    lines = [l for l in open(os.path.join(COMM, "cron_reconcile.log"), errors="replace")
             if "go-forward" in l]
    if lines:
        last = lines[-1]
        m = re.search(r"unexplained_gap=(-?[\d.]+)", last)
        gap = float(m.group(1)) if m else None
        m = re.search(r"residual_after_financing=(-?[\d.]+)", last)
        resid = float(m.group(1)) if m else None
        m = re.search(r"balance=(-?[\d.]+)", last)
        bal = float(m.group(1)) if m else None
except Exception:
    pass
out["comm_reconcile_gap"] = {"n": len([1 for _ in ()]), "gap": gap,
                             "residual": resid, "balance": bal}

# --- FX trades closed since a given date, for post-change monitoring --------
# ERAS is injected by the caller and contains ONLY validated ISO dates.
ERAS = %(eras)s
if ERAS:
    try:
        allrows = list(csv.DictReader(open(tpath)))
    except Exception:
        allrows = []
    for since in ERAS:
        n = tp = ec = 0
        ecpnl = []
        for r in allrows:
            if not (r.get("close_reason") or "").strip():
                continue
            if (r.get("timestamp") or "")[:10] < since:
                continue
            n += 1
            cr = r["close_reason"].strip()
            if cr == "tp":
                tp += 1
            elif cr == "early_cut":
                ec += 1
                try:
                    ecpnl.append(float(r["pnl"]))
                except (TypeError, ValueError):
                    pass
        out["fx_since:" + since] = {
            "n": n, "tp": tp, "early_cut": ec,
            "early_cut_avg": round(sum(ecpnl) / len(ecpnl), 2) if ecpnl else None,
        }

print(json.dumps(out))
'''


def collect_eras() -> list:
    """ISO dates requested by `probe: fx_since:<date>` notes.

    Each is validated as a real date before it is interpolated into the remote
    script — the probe name is the only note-supplied value that reaches the
    VPS, so it must not be able to carry anything but a date.
    """
    eras = set()
    for note in EXP_DIR.glob("*.md"):
        m = FM_RE.match(note.read_text(encoding="utf-8"))
        if not m:
            continue
        probe = get_field(m.group(1), "probe") or ""
        if probe.startswith("fx_since:"):
            raw = probe.split(":", 1)[1].strip()
            try:
                eras.add(datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d"))
            except ValueError:
                print(f"  ignoring bad date in {note.name}: {raw!r}")
    return sorted(eras)


def fetch_metrics() -> dict:
    """Run the read-only probe script on the VPS, return parsed JSON."""
    script = REMOTE % {"fx": FX, "comm": COMM, "eras": repr(collect_eras())}
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=20", SSH_HOST, "python3 -"],
        input=script, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh failed ({proc.returncode}): {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- frontmatter
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def get_field(fm: str, key: str):
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", fm, re.M)
    return m.group(1).strip() if m else None


def set_field(fm: str, key: str, value) -> str:
    line = f"{key}: {value}"
    if re.search(rf"^{re.escape(key)}:", fm, re.M):
        return re.sub(rf"^{re.escape(key)}:.*$", line, fm, count=1, flags=re.M)
    return fm.rstrip("\n") + "\n" + line


def to_int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not EXP_DIR.is_dir():
        print(f"No experiment dir: {EXP_DIR}")
        return 1

    print("Fetching live metrics from VPS (read-only)...")
    metrics = fetch_metrics()

    today = date.today().isoformat()
    rows, changed = [], 0

    for note in sorted(EXP_DIR.glob("*.md")):
        text = note.read_text(encoding="utf-8")
        m = FM_RE.match(text)
        if not m:
            continue
        fm, body = m.group(1), text[m.end():]
        # The folder also holds the register index and verdict write-ups.
        # Only `type: experiment` notes are experiments.
        if (get_field(fm, "type") or "").strip() != "experiment":
            continue

        probe = get_field(fm, "probe")
        if not probe or probe == "manual":
            rows.append((note.stem, get_field(fm, "status"), "manual", "", ""))
            continue

        data = metrics.get(probe)
        if data is None:
            rows.append((note.stem, get_field(fm, "status"), "NO DATA", "", ""))
            continue

        n = to_int(data.get("n"), 0)
        min_n = to_int(get_field(fm, "min_n"), 0)
        status = (get_field(fm, "status") or "live").strip()

        fm = set_field(fm, "n_current", n)
        fm = set_field(fm, "last_synced", today)
        # Only a LIVE experiment can become due; decided ones stay decided.
        ready = "true" if (status == "live" and min_n and n >= min_n) else "false"
        fm = set_field(fm, "verdict_ready", ready)

        extra = {k: v for k, v in data.items() if k != "n"}
        if extra:
            fm = set_field(fm, "metric_detail", json.dumps(extra, separators=(",", ":")))

        new = f"---\n{fm}\n---\n{body}"
        if new != text:
            changed += 1
            if not args.dry_run:
                note.write_text(new, encoding="utf-8")

        rows.append((note.stem, status, f"{n}/{min_n or '-'}",
                     "DUE" if ready == "true" else "", json.dumps(extra) if extra else ""))

    w = max((len(r[0]) for r in rows), default=20)
    print(f"\n{'experiment':<{w}}  {'status':<8} {'n/min':<9} {'':<4} detail")
    print("-" * (w + 40))
    for name, status, n, due, detail in rows:
        print(f"{name:<{w}}  {(status or '-'):<8} {n:<9} {due:<4} {detail[:60]}")

    due_now = [r[0] for r in rows if r[3] == "DUE"]
    print(f"\n{changed} note(s) updated{' (dry run)' if args.dry_run else ''}.")
    if due_now:
        print(f"DUE FOR VERDICT ({len(due_now)}): " + ", ".join(due_now))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"sync failed: {exc}")
        sys.exit(1)
