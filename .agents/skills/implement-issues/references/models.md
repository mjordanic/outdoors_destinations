# Models

Resolve an alias to a **harness slug** you will actually pass to Task (Cursor) or Agent (Claude Code). Record both the alias and the slug in the report. Never pass a slug the current tool rejects.

## Default

- **Implementer**: `grok`. Fast thinking, extra-high effort. On Cursor that is `cursor-grok-4.6-xhigh` (Grok 4.6). On Claude Code, Grok is unavailable → `opus`.
- **Runner**: `inherit` (same model as the orchestrator). Git plumbing does not need a separate pin. `--runner-model grok` is valid if you want it explicit.

`Complexity: high` does **not** bump off `grok`. The default is already the strongest Grok. Use a `Model:` line or `--implementer-model` to pin Claude/GPT.

## Aliases

| Alias | Cursor Task `model` (preferred) | Claude Code Agent `model` |
|---|---|---|
| `grok` | `cursor-grok-4.6-xhigh` | `opus` |
| `inherit` | `inherit` (or omit if the tool treats omit as inherit) | omit the parameter |
| `opus` | `claude-opus-5-thinking-high` | `opus` |
| `opus-4` | `claude-opus-4-8-thinking-high` | `opus` |
| `composer` | `composer-2.5-fast` | `haiku` |
| `gpt` | `gpt-5.6-sol-medium` | `sonnet` |
| `sonnet` | `cursor-grok-4.6-xhigh` | `sonnet` |
| `haiku` | `composer-2.5-fast` | `haiku` |

A value that already looks like a harness slug (`cursor-grok-4.6-xhigh`, `claude-opus-5[effort=high]`, `opus`) is used as-is **if the current tool lists it**. Claude Code only accepts `opus` / `sonnet` / `haiku` (and omit). Cursor Task only accepts slugs listed on the Task tool in this session.

## Session list wins

Before the first dispatch, read the Task/Agent tool's allowed `model` values for this session.

1. Map the alias to the preferred slug in the table.
2. If that slug is in the allowed list, pass it.
3. If not, pick the closest listed slug in this order for `grok`: any `cursor-grok*` / `grok*` with `xhigh` or `high`, then any `grok`, then `inherit`.
4. Record the substitution in the report header (`implementer-model: grok (passed cursor-grok-4.6-xhigh)`). Unknown alias, or empty allowed list you cannot interpret → planning/preflight failure. Ask in Phase 0–1; never guess after that.

## Passing the parameter

- Cursor `Task`: `model` must be one of the listed slugs or `inherit`. Pass the resolved slug for every implementer. For the runner, pass the resolved slug unless the alias is `inherit`.
- Claude Code `Agent`: pass `opus` / `sonnet` / `haiku`, or omit for inherit. Translate Cursor slugs through the table before dispatch.
- `"default"` in a per-issue map means "pass the skill default (`grok`)", not "omit and hope frontmatter matches". Always pass an explicit implementer slug after translation so Claude Code frontmatter (`color`, old `sonnet`) cannot override the grok default.

## Per-issue `Model:` line

Valid: any alias in the table, or a raw slug. Invalid: ask in Phase 1.
