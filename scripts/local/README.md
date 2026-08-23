# Local sync scripts (fork-only)

Not from upstream. These keep the SecondBrain vault in sync with the Claude Code
memory stores under `~/.claude/projects/*/memory/`.

**The memory stores are the source of truth.** The vault holds a copy. Edit the
originals for anything that must survive into the next Claude session.

| Script | Use |
|---|---|
| `migrate_memory.py` | First-time bulk copy of every memory note into the vault. Files into flat folders; run `build_mocs.py` after. |
| `refresh_vault.py` | Re-copy notes that changed upstream, **in place**, preserving the subject-folder layout. Pass filenames as args. |
| `build_mocs.py` | Rebuild the 9 `— Index` map-of-content notes. Run after any add/move. |

## Typical refresh

```bash
cd ~/.claude/projects/C--Users-shale-AI-TRADING-BOT-FIXED/memory
find . -name '*.md' -newermt '<last sync date>' -printf '%f\n'      # what drifted
python ~/.claude/skills/obsidian-second-brain/scripts/local/refresh_vault.py <files...>
python ~/.claude/skills/obsidian-second-brain/scripts/local/build_mocs.py
uv run python ~/.claude/skills/obsidian-second-brain/scripts/vault_health.py --path ~/Documents/SecondBrain
```

Note: `refresh_vault.py` only reads the FX store path. Comm-store notes are
prefixed `comm_` in the vault and need the rename map in `migrate_memory.py`.
