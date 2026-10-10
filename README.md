# Apollo

Shared workspace where the owner and AI agents write code, docs, skills, and tools.

This repository is public. Do not commit secrets, credentials, or personal data.

## Layout

```
Apollo/
├── agents/        # Agent definitions and configuration
├── docs/          # Design notes, decisions, and how-to guides
├── projects/      # Longer-lived code and notes
├── skills/        # Reusable agent skills (skills/<slug>/SKILL.md)
├── tools/         # Custom tools, plugins, and helpers
├── workflows/     # Multi-agent workflow definitions
├── config/        # Framework and environment configuration
├── data/          # Shared data and knowledge bases
├── experiments/   # Experimental configs and sandboxes
├── notebooks/     # Prototyping notebooks
├── memories/      # Non-sensitive agent memory notes
├── logs/          # Log notes and analysis helpers
├── AGENTS.md      # Conventions for people and agents
└── README.md
```

## Workflow

Agents work on branches and open pull requests. The owner reviews and merges.

Do not push directly to `main`. See `AGENTS.md` for naming, commits, and pull request rules.
