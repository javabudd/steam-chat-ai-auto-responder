"""
AI-driven Steam chat bot. Logs in as a Steam user, listens for messages from
a target friend (by persona name), and replies using an LLM (Claude or a
local Ollama model).

Usage: python steam_chat.py <friend_persona_name> [--backend claude|ollama] [options]
"""

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from steam.client import SteamClient
from steam.client.user import SteamUser


CREDENTIAL_DIR = Path.home() / ".steam_chat" / "credentials"
PRESETS_DIR = Path(__file__).resolve().parent / ".presets"
PRESETS_FILE = PRESETS_DIR / "presets.json"

BASE_PROMPT = (
    "You are in a casual Steam chat. Rules: 1. Respond immediately with your message. "
    "2. NEVER use the user's name, username, or any greeting. 3. No essays, lowercase preferred. "
    "4. Single message only, no newlines. 5. Never admit to being an AI."
)

PERSONAS: dict[str, str] = {
    "chill": (
        "You're laid back and easygoing. You match the friend's energy without "
        "trying too hard. You're fine with silence — short replies, sometimes "
        "just 'lol' or 'yeah'."
    ),
    "snark": (
        "You're sarcastic and dry-witted. You roast your friend lightly but "
        "affectionately. You never take things too seriously and you push back "
        "when they say something dumb."
    ),
    "hype": (
        "You're high-energy and enthusiastic about gaming. 'lets gooo', 'no "
        "way', 'thats insane'. You get genuinely excited about plays, drops, "
        "patches, anything."
    ),
    "sweat": (
        "You're a competitive tryhard. You talk ranks, meta, builds, K/D, "
        "frame data. You judge casual play but you're loyal to your friends. "
        "You complain about teammates a lot."
    ),
    "quiet": (
        "You reply with very short messages — often one or two words, "
        "sometimes just 'k', 'sure', 'lmao'. You're not unfriendly, just "
        "low effort. Rarely use full sentences."
    ),
    "dad": (
        "You drop corny dad jokes and puns whenever you can. You're "
        "supportive and dorky. You ask if they've eaten or had water. "
        "You sign off with 'gg champ' or similar."
    ),
}

DEFAULT_PRESET = "chill"

DEFAULT_BUFFER_SECONDS = 2.5

#DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_OLLAMA_MODEL = "gemma4"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


class ClaudeBackend:
    def __init__(self, model: str, thinking: bool):
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = model
        self._thinking = thinking

    def describe(self) -> str:
        mode = "adaptive thinking" if self._thinking else "thinking disabled"
        return f"Claude ({self._model}, {mode})"

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        kwargs = {
            "model": self._model,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": history,
        }
        if not self._thinking:
            kwargs["thinking"] = {"type": "disabled"}
        resp = self._client.messages.create(**kwargs)
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    @property
    def error_type(self):
        return self._anthropic.APIError


class OllamaBackend:
    def __init__(self, model: str, host: str):
        try:
            import ollama
        except ImportError as e:
            raise RuntimeError(
                "The 'ollama' package is required for --backend ollama. "
                "Install it with: pip install ollama"
            ) from e
        self._ollama = ollama
        self._client = ollama.Client(host=host)
        self._model = model
        self._host = host

    def describe(self) -> str:
        return f"Ollama ({self._model} @ {self._host})"

    def generate(self, system_prompt: str, history: list[dict]) -> str:
        messages = [{"role": "system", "content": system_prompt}, *history]
        resp = self._client.chat(model=self._model, messages=messages)
        return resp["message"]["content"].strip()

    @property
    def error_type(self):
        return self._ollama.ResponseError


class ChatSession:
    def __init__(self, base_prompt: str, persona_text: str, persona_label: str, friend_name: str, backend):
        self._base = base_prompt
        self._persona = persona_text
        self._friend = friend_name
        self.persona_label = persona_label
        self.backend = backend
        self.history: list[dict] = []
        self._lock = threading.Lock()
        # Bumped on /reset or /friend so an in-flight reply() knows its
        # snapshot is stale and skips the commit.
        self._generation = 0

    def set_persona(self, text: str, label: str) -> None:
        with self._lock:
            self._persona = text
            self.persona_label = label

    def get_persona(self) -> tuple[str, str]:
        with self._lock:
            return self._persona, self.persona_label

    def set_friend(self, name: str) -> None:
        with self._lock:
            self._friend = name
            self.history = []
            self._generation += 1

    def get_friend(self) -> str:
        with self._lock:
            return self._friend

    def target_name(self) -> str:
        """Lowercased friend name for matching incoming messages."""
        with self._lock:
            return self._friend.lower()

    def reset_history(self) -> None:
        with self._lock:
            self.history = []
            self._generation += 1

    def append_assistant(self, text: str) -> None:
        """Add an assistant turn (e.g. a manually-sent /say message) to history."""
        with self._lock:
            if self.history and self.history[-1]["role"] == "assistant":
                self.history[-1]["content"] += "\n" + text
            else:
                self.history.append({"role": "assistant", "content": text})
            if len(self.history) > 40:
                self.history = self.history[-40:]

    def reply(self, message: str):
        """Generate a reply for `message`. Returns (text, commit) — call
        `commit()` only after the reply was successfully delivered, to persist
        the user/assistant turn pair. If /reset or /friend fired during
        generation, `commit()` is a no-op so we don't corrupt the new state."""
        with self._lock:
            history_snapshot = list(self.history) + [{"role": "user", "content": message}]
            generation = self._generation
            persona = self._persona
        prompt = f"{self._base}\n\n{persona}"
        text = self.backend.generate(prompt, history_snapshot)
        text = " ".join(text.split())

        def commit() -> None:
            with self._lock:
                if self._generation != generation:
                    return
                self.history.append({"role": "user", "content": message})
                self.history.append({"role": "assistant", "content": text})
                if len(self.history) > 40:
                    self.history = self.history[-40:]

        return text, commit


class MessageBuffer:
    """Debounce inbound messages so a flurry coalesces into one LLM call."""

    def __init__(self, delay: float, on_flush):
        self._delay = delay
        self._on_flush = on_flush
        self._lock = threading.Lock()
        self._messages: list[str] = []
        self._user = None
        self._timer: threading.Timer | None = None

    def add(self, user, text: str) -> None:
        with self._lock:
            self._messages.append(text)
            self._user = user
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def clear(self) -> None:
        with self._lock:
            self._messages = []
            self._user = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _flush(self) -> None:
        with self._lock:
            if not self._messages:
                return
            combined = "\n".join(self._messages)
            user = self._user
            self._messages = []
            self._user = None
            self._timer = None
        self._on_flush(user, combined)


def build_backend(args) -> "ClaudeBackend | OllamaBackend":
    if args.backend == "claude":
        model = args.model or DEFAULT_CLAUDE_MODEL
        return ClaudeBackend(model=model, thinking=args.thinking)
    if args.backend == "ollama":
        model = args.model or DEFAULT_OLLAMA_MODEL
        return OllamaBackend(model=model, host=args.ollama_host)
    raise ValueError(f"Unknown backend: {args.backend}")


def login(steam: SteamClient, username: str | None, fresh: bool) -> None:
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    # set_credential_location persists the sentry file (Steam Guard machine
    # fingerprint) but NOT the login_key — we handle the latter ourselves.
    steam.set_credential_location(str(CREDENTIAL_DIR))

    if fresh:
        _clear_cached_session(username)

    effective_username = username or _detect_cached_username()

    # Persist the login_key whenever Steam issues a new one (initial login
    # and on rotation).
    @steam.on(steam.EVENT_NEW_LOGIN_KEY)
    def _persist_key():
        if steam.username and steam.login_key:
            if _save_login_key(steam.username, steam.login_key):
                print(f"[*] Cached Steam login key for {steam.username}.")

    if not fresh and effective_username:
        cached_key = _load_login_key(effective_username)
        if cached_key:
            print(f"[*] Resuming cached Steam session for {effective_username}...")
            result = steam.login(username=effective_username, login_key=cached_key)
            if result == 1:
                return
            print(f"[!] Cached login key rejected (result {result}). Falling back to full login.")
            try:
                _key_path(effective_username).unlink()
            except OSError:
                pass

    print("[*] Starting interactive Steam login.")
    print("    You'll be prompted for your password, then a Steam Guard code")
    print("    (email code OR mobile-authenticator code, whichever your account uses).")
    result = (
        steam.cli_login(username=effective_username)
        if effective_username
        else steam.cli_login()
    )

    if result != 1:
        print(f"[!] Login failed with result: {result}")
        sys.exit(1)


def _key_path(username: str) -> Path:
    return CREDENTIAL_DIR / f"{username}.key"


def _load_login_key(username: str) -> str | None:
    path = _key_path(username)
    if not path.exists():
        return None
    try:
        return path.read_text().strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _save_login_key(username: str, key: str) -> bool:
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_path(username)
    try:
        path.write_text(key)
    except OSError as e:
        print(f"[!] Failed to cache login key: {e}")
        return False
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True


def _load_saved_presets() -> dict[str, str]:
    if not PRESETS_FILE.exists():
        return {}
    try:
        data = json.loads(PRESETS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Failed to read {PRESETS_FILE}: {e}. Ignoring saved presets.")
        return {}
    if not isinstance(data, dict):
        print(f"[!] {PRESETS_FILE} is not a JSON object. Ignoring saved presets.")
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def _write_saved_presets(presets: dict[str, str]) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, sort_keys=True) + "\n")


def _save_preset(name: str, text: str) -> None:
    presets = _load_saved_presets()
    presets[name] = text
    _write_saved_presets(presets)


def _delete_saved_preset(name: str) -> bool:
    presets = _load_saved_presets()
    if name not in presets:
        return False
    del presets[name]
    _write_saved_presets(presets)
    return True


def _detect_cached_username() -> str | None:
    if not CREDENTIAL_DIR.exists():
        return None
    keys = sorted(
        CREDENTIAL_DIR.glob("*.key"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not keys:
        return None
    if len(keys) > 1:
        names = ", ".join(p.stem for p in keys)
        print(
            f"[*] Multiple cached Steam accounts found ({names}); "
            f"using most recent: {keys[0].stem}. Pass --username to override."
        )
    return keys[0].stem


def _clear_cached_session(username: str | None) -> None:
    if not CREDENTIAL_DIR.exists():
        return
    if username:
        patterns = [f"{username}.key", f"{username}_*"]
    else:
        patterns = ["*"]
    for pattern in patterns:
        for path in CREDENTIAL_DIR.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass
    print("[*] Cleared cached Steam session.")


def main():
    saved_presets = _load_saved_presets()
    available_presets = {**PERSONAS, **saved_presets}

    parser = argparse.ArgumentParser(description="AI-driven Steam chat bot")
    parser.add_argument("friend", help="Friend's Steam persona name (case-insensitive)")
    parser.add_argument("--username", help="Your Steam account name")
    parser.add_argument(
        "--preset",
        choices=sorted(available_presets.keys()),
        default=DEFAULT_PRESET,
        help=(
            f"Persona to layer on top of the base prompt (default: "
            f"{DEFAULT_PRESET}). Includes built-in presets and any saved in "
            f"{PRESETS_FILE.name}. Ignored if --persona is passed."
        ),
    )
    parser.add_argument(
        "--persona",
        help=(
            "Custom persona text. Replaces the --preset layer; the base "
            "behavioral rules (short replies, no AI disclosure) still apply."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=["claude", "ollama"],
        default="claude",
        help="LLM backend to use (default: claude)",
    )
    parser.add_argument(
        "--model",
        help=(
            f"Model ID. Default for claude: {DEFAULT_CLAUDE_MODEL}. "
            f"Default for ollama: {DEFAULT_OLLAMA_MODEL}."
        ),
    )
    parser.add_argument(
        "--ollama-host",
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama server URL (default: {DEFAULT_OLLAMA_HOST})",
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable adaptive thinking (claude only; slower, smarter replies)",
    )
    parser.add_argument(
        "--fresh-login",
        action="store_true",
        help="Ignore cached session and force a full Steam Guard login",
    )
    parser.add_argument(
        "--buffer-seconds",
        type=float,
        default=DEFAULT_BUFFER_SECONDS,
        help=(
            f"Wait this long after each incoming message before replying, so "
            f"rapid-fire messages coalesce into one reply (default: "
            f"{DEFAULT_BUFFER_SECONDS}s). Set to 0 to disable."
        ),
    )
    args = parser.parse_args()

    try:
        backend = build_backend(args)
    except RuntimeError as e:
        print(f"[!] {e}")
        sys.exit(1)

    if args.persona:
        persona_text = args.persona
        persona_label = "custom"
    else:
        persona_text = available_presets[args.preset]
        persona_label = args.preset

    steam = SteamClient()
    chat = ChatSession(
        base_prompt=BASE_PROMPT,
        persona_text=persona_text,
        persona_label=persona_label,
        friend_name=args.friend,
        backend=backend,
    )

    @steam.on("logged_on")
    def handle_logged_on():
        print(f"[+] Logged on as {steam.user.name} (SteamID {steam.steam_id})")
        print(f"[*] Backend: {backend.describe()}")
        print(f"[*] Persona: {chat.persona_label}")
        print(f"[*] Auto-replying to messages from: {chat.get_friend()}")
        print("[*] Type /help for runtime commands. Ctrl+C to exit.")

    @steam.friends.on("ready")
    def handle_friends_ready():
        _resolve_friend(steam, chat.get_friend())

    def respond(user: SteamUser, combined: str) -> None:
        try:
            reply, commit = chat.reply(combined)
        except backend.error_type as e:
            print(f"[!] Backend error: {e}")
            return
        if not reply:
            print("[!] Empty reply from backend, skipping.")
            return
        print(f"<you>  {reply}")
        try:
            user.send_message(reply)
        except Exception as e:
            print(f"[!] Failed to send: {e}")
            return
        commit()

    buffer = MessageBuffer(args.buffer_seconds, respond) if args.buffer_seconds > 0 else None

    @steam.on("chat_message")
    def handle_message(user: SteamUser, text: str):
        if not user.name or user.name.lower() != chat.target_name():
            return
        print(f"<{user.name}> {text}")
        if buffer is not None:
            buffer.add(user, text)
        else:
            respond(user, text)

    def shutdown(*_):
        print("\n[*] Shutting down...")
        sys.stdout.flush()
        try:
            steam.logout()
        except Exception:
            pass
        # os._exit so /quit from the daemon stdin thread actually terminates
        # the process — sys.exit there would only kill that thread.
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Log in BEFORE starting the stdin reader — otherwise the reader thread
    # competes with cli_login's password/Steam Guard prompts for stdin and
    # mangles your input.
    login(steam, args.username, fresh=args.fresh_login)

    threading.Thread(
        target=_command_loop,
        args=(chat, steam, buffer, shutdown),
        daemon=True,
    ).start()

    steam.run_forever()


def _find_friend(steam: SteamClient, name: str):
    """Return the SteamUser matching the persona name, or None."""
    target = name.lower()
    try:
        return next(
            (f for f in list(steam.friends) if f.name and f.name.lower() == target),
            None,
        )
    except RuntimeError:
        return None


def _resolve_friend(steam: SteamClient, name: str) -> None:
    """Look up a persona name in the friends list and print resolution status."""
    match = _find_friend(steam, name)
    if match:
        print(f"[+] '{name}' resolved to {match.name} (SteamID {match.steam_id})")
    else:
        print(
            f"[!] '{name}' is not in your friends list. "
            "Will still reply if they message you."
        )


def _command_loop(chat: "ChatSession", steam: SteamClient, buffer, shutdown) -> None:
    """Read /commands from stdin and apply them to the running session."""
    while True:
        try:
            line = input()
        except EOFError:
            return
        line = line.strip()
        if not line or not line.startswith("/"):
            continue

        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "preset":
            sub_parts = arg.split(maxsplit=1)
            sub = sub_parts[0].lower() if sub_parts else ""
            sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            saved = _load_saved_presets()
            available = {**PERSONAS, **saved}
            if not arg:
                _, current = chat.get_persona()
                print(f"[*] Current preset: {current}")
                print(f"    Built-in: {', '.join(sorted(PERSONAS))}")
                if saved:
                    print(f"    Saved:    {', '.join(sorted(saved))}")
                else:
                    print(f"    Saved:    (none — use /preset save <name>)")
            elif sub == "save":
                if not sub_arg:
                    print("[!] Usage: /preset save <name>")
                    continue
                if sub_arg in PERSONAS:
                    print(f"[!] '{sub_arg}' is a built-in preset name. Choose a different name.")
                    continue
                if sub_arg in {"save", "delete"}:
                    print(f"[!] '{sub_arg}' is a reserved subcommand name; a preset named that would be unreachable.")
                    continue
                text, _ = chat.get_persona()
                try:
                    _save_preset(sub_arg, text)
                except OSError as e:
                    print(f"[!] Failed to save preset: {e}")
                    continue
                print(f"[*] Saved current persona as '{sub_arg}' in {PRESETS_FILE}.")
            elif sub == "delete":
                if not sub_arg:
                    print("[!] Usage: /preset delete <name>")
                    continue
                if sub_arg in PERSONAS:
                    print(f"[!] '{sub_arg}' is a built-in preset and cannot be deleted.")
                    continue
                try:
                    removed = _delete_saved_preset(sub_arg)
                except OSError as e:
                    print(f"[!] Failed to delete preset: {e}")
                    continue
                if removed:
                    print(f"[*] Deleted saved preset '{sub_arg}'.")
                else:
                    print(f"[!] No saved preset named '{sub_arg}'.")
            elif arg in available:
                chat.set_persona(available[arg], arg)
                print(f"[*] Persona switched to '{arg}'.")
            else:
                print(f"[!] Unknown preset '{arg}'. Built-in: {', '.join(sorted(PERSONAS))}."
                      + (f" Saved: {', '.join(sorted(saved))}." if saved else ""))
        elif cmd == "persona":
            if not arg:
                text, label = chat.get_persona()
                print(f"[*] Current persona ({label}):\n    {text}")
            else:
                chat.set_persona(arg, "custom")
                print("[*] Persona switched to custom text.")
        elif cmd == "friend":
            if not arg:
                print(f"[*] Currently auto-replying to: {chat.get_friend()}")
            else:
                chat.set_friend(arg)
                if buffer is not None:
                    buffer.clear()
                print(f"[*] Now auto-replying to: {arg} (history cleared)")
                _resolve_friend(steam, arg)
        elif cmd == "say":
            if not arg:
                print("[!] Usage: /say <message>")
                continue
            friend_name = chat.get_friend()
            match = _find_friend(steam, friend_name)
            if not match:
                print(f"[!] '{friend_name}' is not in your friends list — cannot send.")
                continue
            try:
                match.send_message(arg)
            except Exception as e:
                print(f"[!] Failed to send: {e}")
                continue
            chat.append_assistant(arg)
            print(f"<you>  {arg}")
        elif cmd == "reset":
            chat.reset_history()
            if buffer is not None:
                buffer.clear()
            print("[*] Conversation history cleared.")
        elif cmd in ("help", "?"):
            print("Runtime commands:")
            print("  /say <message>    Send a message to the current friend as yourself")
            print("  /preset <name>    Switch to a built-in or saved persona")
            print("  /preset           Show current preset and list available")
            print("  /preset save <name>    Save current persona to .presets/")
            print("  /preset delete <name>  Delete a saved preset")
            print("  /persona <text>   Set a custom persona")
            print("  /persona          Show current persona")
            print("  /friend <name>    Switch the friend to auto-reply to")
            print("  /friend           Show current target friend")
            print("  /reset            Clear conversation history")
            print("  /quit             Shut down")
        elif cmd in ("quit", "exit"):
            shutdown()
            return
        else:
            print(f"[!] Unknown command '/{cmd}'. Try /help.")


if __name__ == "__main__":
    main()
