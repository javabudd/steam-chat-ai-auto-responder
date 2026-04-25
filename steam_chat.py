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

DEFAULT_PERSONA = (
    "You are chatting with a friend on Steam. Reply casually and naturally, "
    "like you would in a real Steam chat — short messages, lowercase is fine, "
    "no essays, no bullet points, no formal structure. Don't mention that "
    "you're an AI unless directly asked."
)

DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"
DEFAULT_OLLAMA_MODEL = "gemma4:26b"
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
    steam.set_credential_location(str(CREDENTIAL_DIR))

    if fresh:
        _clear_cached_session(username)

    if not fresh and steam.relogin_available:
        print("[*] Resuming cached Steam session...")
        result = steam.relogin()
        if result == 1:
            return
        print(f"[!] Cached session rejected (result {result}). Falling back to full login.")

    print("[*] Starting interactive Steam login.")
    print("    You'll be prompted for your password, then a Steam Guard code")
    print("    (email code OR mobile-authenticator code, whichever your account uses).")
    result = steam.cli_login(username=username) if username else steam.cli_login()

    if result != 1:
        print(f"[!] Login failed with result: {result}")
        sys.exit(1)


def _clear_cached_session(username: str | None) -> None:
    if not CREDENTIAL_DIR.exists():
        return
    patterns = [f"{username}_*"] if username else ["*"]
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
    parser.add_argument("--persona", default=DEFAULT_PERSONA, help="System prompt for the LLM")
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

    system_prompt = (
        f"{args.persona}\n\n"
        f"The person you're chatting with is named '{args.friend}'."
    )

    steam = SteamClient()
    chat = ChatSession(system_prompt=system_prompt, backend=backend)

    @steam.on("logged_on")
    def handle_logged_on():
        print(f"[+] Logged on as {steam.user.name} (SteamID {steam.steam_id})")
        print(f"[*] Backend: {backend.describe()}")
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
