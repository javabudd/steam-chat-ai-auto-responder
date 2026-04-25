# steam-autochat

AI-driven Steam chat bot. Logs into your Steam account, listens for messages
from a specific friend (by persona name), and auto-replies using an LLM —
either Anthropic's Claude API or a local model served by
[Ollama](https://ollama.com).

## Install

Requires Python 3.10+.

### Windows (PowerShell or cmd)

```
cd C:\path\to\steam-test
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### macOS

```
cd /path/to/steam-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `python3` is missing: `brew install python`.

### Linux (Debian/Ubuntu)

```
sudo apt install python3 python3-venv python3-pip
cd /path/to/steam-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Linux (Fedora/RHEL)

```
sudo dnf install python3 python3-pip
cd /path/to/steam-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Linux (Arch)

```
sudo pacman -S python python-pip
cd /path/to/steam-test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure a backend

Pick one of the two LLM backends.

### Option A — Claude (default)

Set your Anthropic API key:

**Windows (persistent, PowerShell or cmd)**

```
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Reopen the terminal after `setx`. For the current session only, use
`$env:ANTHROPIC_API_KEY="sk-ant-..."` in PowerShell.

**macOS / Linux**

Current shell only:

```
export ANTHROPIC_API_KEY="sk-ant-..."
```

Persistent — append to `~/.zshrc` (macOS default), `~/.bashrc` (most Linux),
or `~/.bash_profile`:

```
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

### Option B — Ollama (local, no API key)

Install Ollama from [ollama.com/download](https://ollama.com/download), then
pull a model and make sure the server is running:

```
ollama pull gemma4:26b
ollama serve        # usually auto-starts; run if not
```

By default the bot connects to `http://localhost:11434`. Override with
`--ollama-host http://other-host:11434` if you run Ollama elsewhere.

## Run

With Claude (default):

```
python steam_chat.py "FriendsPersonaName"
```

With Ollama:

```
python steam_chat.py "FriendsPersonaName" --backend ollama
```

First launch prompts for Steam username, password, and a **Steam Guard code**
(either the code emailed to you OR the current code from the Steam Mobile
app — whichever your account uses). Session is cached so subsequent runs
skip the login:

- Windows: `%USERPROFILE%\.steam_chat\credentials`
- macOS / Linux: `~/.steam_chat/credentials`

If the cached session is rejected (Steam expires them periodically, or you
signed in elsewhere), the program automatically falls back to the full Steam
Guard login. To force a fresh login manually, pass `--fresh-login`.

## What it does

- Listens for `chat_message` events; filters to only your target friend by
  persona name (case-insensitive).
- Maintains conversation history (last 40 turns) so the LLM has context.
- Messages from other friends are ignored; only the named friend gets
  auto-replies.
- Claude default: `claude-sonnet-4-6` with thinking disabled for fast replies.
  Add `--thinking` for smarter-but-slower answers, or `--model claude-opus-4-7`
  for a more capable model.
- Ollama default: `gemma4:26b` at `http://localhost:11434`. Any model you have
  pulled will work (`--model llama3.2`, `--model qwen2.5`, etc.).

## Flags

- `--backend {claude,ollama}` — choose the LLM backend (default: `claude`)
- `--model <id>` — override the model. Claude default: `claude-sonnet-4-6`.
  Ollama default: `gemma4:26b`.
- `--ollama-host <url>` — Ollama server URL (default: `http://localhost:11434`)
- `--thinking` — enable adaptive thinking (claude only)
- `--username <steam-user>` — pass your Steam account name (skips one prompt)
- `--preset <name>` — pick a built-in persona (see below; default: `chill`)
- `--persona "..."` — custom persona text, replaces the preset layer (the
  base "keep replies short, don't reveal AI" rules still apply)
- `--fresh-login` — clear cached session and force a Steam Guard login

## Personas

The system prompt is layered: a fixed base (short replies, casual tone, no
AI disclosure) plus a swappable persona. Pick one with `--preset`:

- `chill` (default) — laid back, low effort, matches energy
- `snark` — sarcastic, dry-witted, lightly roasts the friend
- `hype` — high-energy, enthusiastic, "lets gooo"
- `sweat` — competitive tryhard, talks ranks/meta/builds
- `quiet` — minimal one-or-two-word replies
- `dad` — corny dad jokes, supportive, dorky

```
python steam_chat.py "FriendName" --preset snark
python steam_chat.py "FriendName" --preset hype --backend ollama
```

Need something specific? `--persona "You're a laconic DOTA player who only
talks about TI"` overrides the preset entirely while keeping the base rules.

### Switch personas while the bot is running

The terminal where the bot is running accepts slash-commands. Type one and
press Enter:

| Command | Effect |
|---|---|
| `/say <message>` | Send a message to the current friend yourself (added to history so the bot stays in sync) |
| `/preset <name>` | Switch to a built-in persona |
| `/preset` | Show current preset and list available |
| `/persona <text>` | Set a custom persona |
| `/persona` | Print the current persona |
| `/friend <name>` | Switch which friend the bot auto-replies to (clears history) |
| `/friend` | Print the current target friend |
| `/reset` | Clear conversation history |
| `/help` | Show runtime commands |
| `/quit` | Shut down |

Persona switches affect future replies; existing conversation history is
preserved. Switching the target friend clears history automatically since
it's a different conversation. Use `/reset` to clear history without
changing persona or friend.

## Caveats

1. Persona name matching is case-insensitive but must match exactly. If your
   friend's Steam name changes, restart with the new name.
2. This uses the unofficial
   [ValvePython/steam](https://github.com/ValvePython/steam) library
   (reverse-engineered Steam client protocol). Valve could theoretically flag
   automated accounts — low risk for personal use, but worth noting.
