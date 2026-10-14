# CLAUDE.md

Claude Code entry point. Single source of truth for agent behavior is shared with Cursor, so edit the shared files, not copies.

@AGENTS.md

## Where things live (shared with Cursor)

- Rules / workflow / tooling: `AGENTS.md` (imported above). Change agent rules there, not here.
- Domain glossary: `CONTEXT.md`; decisions: `docs/adr/`. Read before exploring (see `docs/agents/domain.md`).
- Issue tracker + triage labels: `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` (issues are markdown in `.scratch/<feature>/`).
- Skills: canonical in `.agents/skills/`. `.claude/skills` is a symlink to it. Add/edit skills in `.agents/skills/` only.
- Subagents: canonical in `.agents/agents/`. `.claude/agents/*` and `.cursor/agents/*` are symlinks. `/implement-issues` is the skill `.agents/skills/implement-issues`.

## Claude Code specifics

- Subagent frontmatter `model:` may hold Cursor slugs (e.g. `cursor-grok-4.6-xhigh`). Under Claude Code pass `opus`/`sonnet`/`haiku` (or omit) explicitly on the Agent call; translate via `.agents/skills/implement-issues/references/models.md`.
- Commits: no `Co-Authored-By` / agent trailer (see AGENTS.md and global CLAUDE.md).
- Python: `uv` only.
