# AGENTS.md

Guide for AI agents navigating this repo.

## What this is

Single-file Python CLI (`steam_chat.py`) that logs into Steam as a user, listens for chat messages from one named friend, and auto-replies via an LLM (Anthropic Claude or local Ollama). README.md is the user-facing doc; this file is the maintainer-oriented map.

## Layout

- `steam_chat.py` — the entire app. No package, no submodules. Edit this file for nearly any change.
- `requirements.txt` — `steam[client]`, `anthropic`, `ollama`, `gevent`.
- `README.md` — install/run/flags/personas. Keep in sync when CLI flags or runtime commands change.
- `.presets/presets.json` — user-saved personas (gitignored). Created on first `/preset save`.
- `.claude/settings.local.json` — Claude Code harness settings, not app config.
- `.venv/`, `__pycache__/` — gitignored.

There are no tests, no CI, no build step. `python steam_chat.py <FriendName>` is the only entry point.

## Mental model of `steam_chat.py`

Roughly four layers, top-to-bottom in the file:

1. **Constants & personas** (lines ~21–71): `BASE_PROMPT`, `PERSONAS` dict, defaults (`DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"`, `DEFAULT_OLLAMA_MODEL = "gemma4"`, buffer seconds, paths).
2. **Backends** (`ClaudeBackend`, `OllamaBackend`, ~74–130): same shape — `describe()`, `generate(system_prompt, history) -> str`, `error_type` property. Add a backend by mirroring this interface and wiring it in `build_backend()`.
3. **State** (`ChatSession`, `MessageBuffer`, ~133–236): `ChatSession` owns persona text/label, target friend name, conversation history (capped at 40 turns), and a lock so the stdin command thread can mutate persona/friend safely while the Steam thread reads. `MessageBuffer` debounces inbound messages (default 2.5s) so a flurry coalesces into one LLM call.
4. **App glue** (`main`, `login`, `_command_loop`, helpers, ~239–end):
   - `login()` handles `login_key` caching (`~/.steam_chat/credentials/<user>.key`, chmod 600). On rejection it falls back to `cli_login()`. `--fresh-login` clears the cache.
   - `main()` parses args, builds the backend, registers Steam event handlers (`logged_on`, `friends.on("ready")`, `chat_message`), starts the stdin command loop in a daemon thread, then `steam.run_forever()`.
   - `_command_loop()` is the `/say /preset /persona /friend /reset /help /quit` dispatcher. `/preset save|delete <name>` persists to `.presets/presets.json`.

## Threading

Three threads matter:
- Main thread: `steam.run_forever()` (gevent under the hood).
- Stdin reader: `_command_loop` daemon thread.
- `MessageBuffer` timer thread (one-shot, replaced on each new message).

`ChatSession._lock` guards `_persona`, `persona_label`, and `_friend`. `history` is mutated only on the Steam/buffer-flush path so it's effectively single-threaded; if you add another writer, lock it.

**Login must happen before the stdin thread starts.** `cli_login()` reads password / Steam Guard from stdin and the reader thread would otherwise eat those keystrokes. Don't reorder this in `main()`.

## Conventions worth keeping

- Single file. Don't split into a package unless there's a real reason — README, requirements, and the run command all assume `steam_chat.py`.
- Backend classes share a duck-typed interface (`describe`, `generate`, `error_type`). Maintain it.
- History cap at 40 turns is enforced in two places (`reply` and `append_assistant`) — keep them in sync.
- Print-based UX: `[*]` info, `[+]` success, `[!]` warning/error, `<name> text` for chat lines. Match the existing style.
- Built-in preset names in `PERSONAS` are reserved — `/preset save` and `/preset delete` reject collisions. Preserve that check if you touch preset code.
- `claude-sonnet-4-6` is the current Claude default; `claude-opus-4-7` is commented above it as the "more capable" alternative. Both are real model IDs — don't "correct" them.

## When making changes

- Adding a CLI flag: update `argparse` in `main()`, then update README.md "Flags" section.
- Adding a runtime command: extend `_command_loop()` and the `/help` block inside it, then update README.md "Switch personas while the bot is running" table.
- Adding a built-in persona: append to `PERSONAS` and add a row to the README persona list.
- Adding a backend: new class with the same three members, extend `--backend` choices, branch in `build_backend()`, document in README.

No test suite to run. Smoke-test by `python steam_chat.py <FriendName>` against a real Steam account, or at minimum `python -c "import steam_chat"` after edits to catch syntax/import errors.
