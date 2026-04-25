"""
AI-driven Steam chat bot. Logs in as a Steam user, listens for messages from
a target friend (by persona name), and replies using Claude.

Usage: python steam_chat.py <friend_persona_name> [--username YOUR_STEAM] [--persona "..."]
"""

import argparse
import os
import signal
import sys
from pathlib import Path

import anthropic
from steam.client import SteamClient
from steam.client.user import SteamUser


CREDENTIAL_DIR = Path.home() / ".steam_chat" / "credentials"

DEFAULT_PERSONA = (
    "You are chatting with a friend on Steam. Reply casually and naturally, "
    "like you would in a real Steam chat — short messages, lowercase is fine, "
    "no essays, no bullet points, no formal structure. Don't mention that "
    "you're an AI unless directly asked."
)


class ChatSession:
    def __init__(self, system_prompt: str, model: str, disable_thinking: bool):
        self.client = anthropic.Anthropic()
        self.system_prompt = system_prompt
        self.model = model
        self.disable_thinking = disable_thinking
        self.history: list[dict] = []

    def reply(self, message: str) -> str:
        self.history.append({"role": "user", "content": message})

        kwargs = {
            "model": self.model,
            "max_tokens": 1024,
            "system": self.system_prompt,
            "messages": self.history,
        }
        if self.disable_thinking:
            kwargs["thinking"] = {"type": "disabled"}

        resp = self.client.messages.create(**kwargs)
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        ).strip()

        self.history.append({"role": "assistant", "content": text})
        if len(self.history) > 40:
            self.history = self.history[-40:]
        return text


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
    parser.add_argument("--persona", default=DEFAULT_PERSONA, help="System prompt for Claude")
    parser.add_argument("--model", default="claude-opus-4-7", help="Claude model ID")
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable adaptive thinking (slower replies, smarter answers)",
    )
    parser.add_argument(
        "--fresh-login",
        action="store_true",
        help="Ignore cached session and force a full Steam Guard login",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[!] ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    target_name = args.friend.strip().lower()

    system_prompt = (
        f"{args.persona}\n\n"
        f"The person you're chatting with is named '{args.friend}'."
    )

    steam = SteamClient()
    chat = ChatSession(
        system_prompt=system_prompt,
        model=args.model,
        disable_thinking=not args.thinking,
    )

    @steam.on("logged_on")
    def handle_logged_on():
        print(f"[+] Logged on as {steam.user.name} (SteamID {steam.steam_id})")
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
        except anthropic.APIError as e:
            print(f"[!] Claude API error: {e}")
            return
        if not reply:
            print("[!] Empty reply from Claude, skipping.")
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
