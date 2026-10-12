# AGENTS.md — PvZ Online English Translation

These rules apply to every AI assistant, coding agent, and automated tool working in this repository.

## Project purpose

This repository is for the English localization and maintenance/debugging of the local PvZ Online game. Prefer small, targeted changes that support localization or fix a clearly identified defect. Do not turn a translation task into a refactor or gameplay redesign.

## Hard restrictions

### 1. Never edit the modding folder

**Do not modify anything inside `modding the game/`.** This includes creating, editing, deleting, renaming, moving, or reformatting files or folders there. Keep all changes outside this directory.

### 2. Do not change game balance

**Game balance must remain unchanged.** Do not intentionally alter or “improve” plant or zombie stats, damage, health, costs, attack speed, cooldowns, spawn rates, timers, XP, rewards, progression, difficulty, or any other balance-related values or formulas.

When fixing a bug, preserve the existing intended balance values and behavior. A bug fix is not permission to rebalance the game. If a proposed fix appears to require a balance change, stop and explain the conflict instead of making that change.

Do not change gameplay values merely because they appear unusual, inconsistent, weak, strong, or possibly accidental. Ask for explicit direction before making any change whose balance impact is unclear.

## Localization and data-file safety

- Preserve IDs, row order, field order, column count, and delimiters in structured localization files.
- For tab-separated files, preserve tabs exactly; do not replace them with spaces or convert the file to a different format.
- Preserve placeholders such as `{0}`, format strings, escape sequences, markup, control codes, and intentional line breaks.
- Do not translate identifiers, internal names, keys, filenames, or code unless the task explicitly requires it.
- Change only the user-facing text or the smallest code/data area needed for the requested task.
- Avoid rewriting or reformatting large files wholesale. Make narrow edits so unrelated strings and game behavior remain untouched.
- Keep localization natural and understandable rather than mechanically literal, while preserving the original meaning and any intentional jokes or references. Keep “Qin Shi Huang” in the relevant illusion/tacos line.
- If a field's purpose is unclear, do not guess that it is safe to translate or change. Investigate first, or leave it untouched and report the uncertainty.

## Debugging and code changes

- Establish the suspected cause before changing code or data.
- Prefer the smallest reversible fix. Do not bundle unrelated cleanup, refactoring, formatting, translation, or balance changes into a bug fix.
- Preserve existing behavior outside the specific defect being addressed.
- Do not delete files, records, fields, assets, or code simply because they appear unused without verifying that they are unused and obtaining explicit approval when deletion could affect the game.
- Review the final diff. Confirm that no path under `modding the game/` changed and that no balance-related values or formulas changed.
- Report the files changed, the issue addressed, any checks performed, and any uncertainty. Never claim a test passed unless it was actually run.

## AI tools currently used for this project

The project's current AI-assisted workflow uses:

- **Google AI Studio** — brainstorming, research, and working through large project context.
- **ChatGPT** — localization decisions, debugging analysis, review, and project planning.
- **Google Jules** — delegated repository coding/editing tasks; it must follow these repository rules.
- **GitHub Copilot** — in-editor coding assistance and suggestions.

These tools are assistants, not authorization to expand the task. All changes must follow the restrictions above regardless of which tool proposes or makes them.

## Default priority

When instructions conflict, prioritize the user's explicit request and the hard restrictions in this file. When uncertain, preserve the existing files and behavior, make no speculative changes, and explain what needs clarification.
