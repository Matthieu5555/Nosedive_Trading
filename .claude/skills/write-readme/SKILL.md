---
name: write-readme
description: Write or revise a README for a software project. Use when creating a new project, when the user asks for a README, or when an existing README has drifted from reality. Produces a README that leads with TL;DR, makes the fastest run path obvious, and documents configuration explicitly.
---

# Process

## 1. Confirm the audience

Ask (or infer from the project) who reads this: a collaborator, a future you, an open-source user, a hiring manager. The level of detail and the tone shift accordingly. If unclear, ask.

## 2. Read the project first

Before writing a single line, skim: entry points, config files, test commands, `pyproject.toml` or equivalent, CI config. The README describes what the code actually does, not what you imagine it does. If documented behavior diverges from real behavior, fix the code or fix the doc, but do not ship a lie.

## 3. Structure

Produce these sections in order. Omit a section only if it is genuinely irrelevant; do not pad.

### TL;DR (50 to 150 words)

What the project is, who it is for, why it exists, what it does not do. One paragraph. Reader decides here whether to keep reading.

### Quickstart

The fastest path from zero to running. For a Python project:

```
uv sync
uv run <entry point>
```

Actual commands the reader can paste. If the project needs credentials or data first, say so in one line and link to the Configuration section; do not derail the quickstart.

### What it does

One or two paragraphs describing the problem and the approach. Not an exhaustive feature list. A reader should walk away knowing the mental model.

### Configuration

Every environment variable and config field:
- Name.
- What it controls.
- Default (or "required, no default").
- What happens if it is missing or malformed.

A `.env.example` committed to the repo with variable names and dummy values is preferred over prose. The real `.env` stays local.

### Data flow and key interfaces

For a non-trivial project, one diagram or one paragraph describing how data moves through the system and which modules are the public interface. Readers should know which file to open first.

### Testing

How to run the tests. What requires external resources (databases, APIs) and how to gate those.

### Common errors

The two or three errors that will actually happen on first run, with the fix. "Missing credentials" → set X. "Database not reachable" → check Y. Do not write a generic troubleshooting section; list the real ones.

### Known limitations

What this project does not handle, what assumptions it makes, what is out of scope. Be explicit so a reader does not have to discover limitations through bugs.

## 4. Style

Prose, not bullet soup. Every sentence connects to the next. Code blocks for commands, config, and file layouts only. No screenshots unless the project has a UI and the screenshot is actually informative.

Do not write "Welcome to [project]!" or similar ceremonial openings. Start with content.

## 5. Verify the commands

Run every command the README tells the reader to run, on a fresh environment if possible. If you cannot run them in this session, say so explicitly in the reply to the user; do not claim verification you did not perform.
