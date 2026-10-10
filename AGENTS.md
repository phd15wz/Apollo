# AGENTS.md

Conventions for the owner and AI agents working in this repository.

This repository is public. Never commit secrets, credentials, tokens, private keys, or personal data.

## Naming

- Branches use lowercase `area/short-description` with hyphens, for example `docs/agent-conventions` or `feat/quote-helper`.
- Files and directories use lowercase hyphenated names unless a tool requires otherwise.
- Skills use `skills/<slug>/`, where `<slug>` is lowercase and hyphenated.

## Commit style

- Write the subject in the imperative present tense: `Add skill layout`, `Document PR workflow`.
- Keep the subject short and specific. Explain why in the body when the change is not obvious.
- Put one logical change in each commit.

## Pull requests

- Open a pull request into `main` from a branch. Do not push to `main`.
- Fill in `.github/pull_request_template.md`.
- Keep each pull request focused on one change.
- The owner reviews and merges. Agents do not merge their own pull requests.

## Secrets

Never commit secrets. That includes API keys, tokens, passwords, private keys, `.env` files with real values, and personal data. If a secret lands in the history, tell the owner so it can be rotated.

## Skills

Each skill lives in `skills/<slug>/SKILL.md`.

- `SKILL.md` states what the skill is for, when to use it, and the steps an agent should follow.
- Keep files that belong to that skill in the same directory.
- Add a one-line entry to `skills/README.md` when you add a skill.
