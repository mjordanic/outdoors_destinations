# CLAUDE.md

Claude Code entry point. Single source of truth for agent behavior is shared with Cursor, so edit the shared files, not copies.

@AGENTS.md

## Where things live (shared with Cursor)

- Rules / workflow / tooling: `AGENTS.md` (imported above). Change agent rules there, not here.
- Domain glossary: `GLOSSARY.md`; decisions: `docs/adr/`. Read before exploring (see `docs/agents/domain.md`).
- Issue tracker + triage labels: `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` (issues are markdown in `.scratch/<feature>/`).
- Skills: canonical in `.agents/skills/`. `.claude/skills` is a symlink to it. Add/edit skills in `.agents/skills/` only.

## Claude Code specifics

- Commits: no `Co-Authored-By` / agent trailer (see AGENTS.md and global CLAUDE.md).
- Python: `uv` only.
