# steam-test

AI-driven Steam chat bot. Logs into your Steam account, listens for messages
from a specific friend (by persona name), and auto-replies using Claude.

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

## Set API key

### Windows (persistent, PowerShell or cmd)

```
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Reopen the terminal after `setx`. For the current session only, use
`$env:ANTHROPIC_API_KEY="sk-ant-..."` in PowerShell.

### macOS / Linux

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

## Run

```
python steam_chat.py "FriendsPersonaName"
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
- Maintains conversation history (last 40 turns) so Claude has context.
- Default model is `claude-opus-4-7` with thinking disabled for fast replies.
  Add `--thinking` for smarter-but-slower answers, or `--model claude-sonnet-4-6`
  for a cheaper chat model.
- Messages from other friends are ignored; only the named friend gets
  auto-replies.

## Flags

- `--username <steam-user>` — pass your Steam account name (skips one prompt)
- `--persona "..."` — override the system prompt
  (e.g. `"You're a laconic DOTA player, short replies only"`)
- `--model <id>` — swap models
- `--thinking` — enable adaptive thinking
- `--fresh-login` — clear cached session and force a Steam Guard login

## Caveats

1. Persona name matching is case-insensitive but must match exactly. If your
   friend's Steam name changes, restart with the new name.
2. This uses the unofficial
   [ValvePython/steam](https://github.com/ValvePython/steam) library
   (reverse-engineered Steam client protocol). Valve could theoretically flag
   automated accounts — low risk for personal use, but worth noting.
