# steam-chat-ai-auto-responder

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
- Claude default: `claude-opus-4-7` with thinking disabled for fast replies.
  Add `--thinking` for smarter-but-slower answers, or `--model claude-sonnet-4-6`
  for a cheaper chat model.
- Ollama default: `gemma4:26b` at `http://localhost:11434`. Any model you have
  pulled will work (`--model llama3.2`, `--model qwen2.5`, etc.).

## Flags

- `--backend {claude,ollama}` — choose the LLM backend (default: `claude`)
- `--model <id>` — override the model. Claude default: `claude-opus-4-7`.
  Ollama default: `gemma4:26b`.
- `--ollama-host <url>` — Ollama server URL (default: `http://localhost:11434`)
- `--thinking` — enable adaptive thinking (claude only)
- `--username <steam-user>` — pass your Steam account name (skips one prompt)
- `--persona "..."` — override the system prompt
  (e.g. `"You're a laconic DOTA player, short replies only"`)
- `--fresh-login` — clear cached session and force a Steam Guard login

## Caveats

1. Persona name matching is case-insensitive but must match exactly. If your
   friend's Steam name changes, restart with the new name.
2. This uses the unofficial
   [ValvePython/steam](https://github.com/ValvePython/steam) library
   (reverse-engineered Steam client protocol). Valve could theoretically flag
   automated accounts — low risk for personal use, but worth noting.
