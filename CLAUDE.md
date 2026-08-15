# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working style

- Keep responses terse. No trailing "here's what I did" summaries — the diff shows it.
- Default to no code comments. Only add one when the *why* is non-obvious.
- Don't create `.md` files, READMEs, or planning docs unless asked.
- Prefer editing existing files over creating new ones.
- No speculative abstraction. Solve what's asked; don't design for hypotheticals.
- Ask before destructive or shared-state actions (force push, `reset --hard`, dropping tables, deleting branches, sending anything external).
