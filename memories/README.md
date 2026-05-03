# memories
Persistent and session memories for AI agents.

Structure:
- `memories/user/` — long-lived personal preferences and profile (non-sensitive).
- `memories/session/` — ephemeral conversation or session facts.
- `memories/repo/` — repository-scoped facts, conventions, snippets.

Guidelines:
- Use small YAML or JSON files for individual memory items.
- Avoid storing secrets or PII in plaintext — add sensitive files to `.gitignore`.
- Include `id`, `created`, `author`, and `tags` in memory files for easy filtering.
