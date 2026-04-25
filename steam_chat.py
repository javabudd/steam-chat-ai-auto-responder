"""
AI-driven Steam chat bot. Logs in as a Steam user, listens for messages from
a target friend (by persona name), and replies using an LLM (Claude or a
local Ollama model).

Usage: python steam_chat.py <friend_persona_name> [--backend claude|ollama] [options]
"""

import argparse
import os
import signal
import sys
from pathlib import Path

from steam.client import SteamClient
from steam.client.user import SteamUser


CREDENTIAL_DIR = Path.home() / ".steam_chat" / "credentials"

BASE_PROMPT = (
    "You are chatting with a friend on Steam. Reply casually and naturally, "
    "like you would in a real Steam chat — short messages, lowercase is fine, "
    "no essays, no bullet points, no formal structure. Don't mention that "
    "you're an AI unless directly asked."
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

DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"
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
    def __init__(self, system_prompt: str, backend):
        self.system_prompt = system_prompt
        self.backend = backend
        self.history: list[dict] = []

    def reply(self, message: str) -> str:
        self.history.append({"role": "user", "content": message})
        text = self.backend.generate(self.system_prompt, self.history)
        self.history.append({"role": "assistant", "content": text})
        if len(self.history) > 40:
            self.history = self.history[-40:]
        return text


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
            _save_login_key(steam.username, steam.login_key)
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
    except OSError:
        return None


def _save_login_key(username: str, key: str) -> None:
    CREDENTIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _key_path(username)
    path.write_text(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
        patterns = [f"{username}.key", f"{username}_sentry.bin", f"{username}_*"]
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
    parser = argparse.ArgumentParser(description="AI-driven Steam chat bot")
    parser.add_argument("friend", help="Friend's Steam persona name (case-insensitive)")
    parser.add_argument("--username", help="Your Steam account name")
    parser.add_argument(
        "--preset",
        choices=sorted(PERSONAS.keys()),
        default=DEFAULT_PRESET,
        help=(
            f"Built-in persona to layer on top of the base prompt "
            f"(default: {DEFAULT_PRESET}). Ignored if --persona is passed."
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
    args = parser.parse_args()

    try:
        backend = build_backend(args)
    except RuntimeError as e:
        print(f"[!] {e}")
        sys.exit(1)

    target_name = args.friend.strip().lower()

    persona_text = args.persona if args.persona else PERSONAS[args.preset]
    system_prompt = (
        f"{BASE_PROMPT}\n\n"
        f"{persona_text}\n\n"
        f"The person you're chatting with is named '{args.friend}'."
    )

    steam = SteamClient()
    chat = ChatSession(system_prompt=system_prompt, backend=backend)

    persona_label = "custom" if args.persona else args.preset

    @steam.on("logged_on")
    def handle_logged_on():
        print(f"[+] Logged on as {steam.user.name} (SteamID {steam.steam_id})")
        print(f"[*] Backend: {backend.describe()}")
        print(f"[*] Persona: {persona_label}")
        print(f"[*] Auto-replying to messages from: {args.friend}")
        print("[*] Press Ctrl+C to exit.")

    @steam.friends.on("ready")
    def handle_friends_ready():
        match = next(
            (f for f in steam.friends if f.name and f.name.lower() == target_name),
            None,
        )
        if match:
            print(f"[+] Target friend resolved: {match.name} ({match.steam_id})")
        else:
            print(
                f"[!] Friend '{args.friend}' not in your friends list. "
                "Will still reply if they message you."
            )

    @steam.on("chat_message")
    def handle_message(user: SteamUser, text: str):
        if not user.name or user.name.lower() != target_name:
            return
        print(f"<{user.name}> {text}")
        try:
            reply = chat.reply(text)
        except backend.error_type as e:
            print(f"[!] Backend error: {e}")
            return
        if not reply:
            print("[!] Empty reply from backend, skipping.")
            return
        print(f"<you>  {reply}")
        user.send_message(reply)

    def shutdown(*_):
        print("\n[*] Shutting down...")
        try:
            steam.logout()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    login(steam, args.username, fresh=args.fresh_login)
    steam.run_forever()


if __name__ == "__main__":
    main()
