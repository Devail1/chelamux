"""``chela send-file`` — post a file from an agent into its own Telegram topic.

The bridge relays an agent's transcript, and only the images in it, so an agent asked
to "send me the zip" had no discoverable way to do it (CMX-370). This resolves the
window's topic from the same :class:`~chela.telegram.bindings.BindingRegistry` the
bridge routes on, loads the bot token and chat id the way the bridge does
(:func:`~chela.telegram.senders.bridge_env`: environment, then ``chela.env``, then
``secrets.env``), and posts through :meth:`~chela.telegram.relay.BotSender.send_document`.

Every failure is a :class:`SendFileError` with a message meant for the agent reading
it; the CLI prints it and exits 1. Nothing here returns a "maybe sent".
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chela.telegram.bindings import BindingRegistry, default_bindings_path
from chela.telegram.relay import BotSender
from chela.telegram.senders import bridge_env

# Telegram's cap on a file a bot uploads (https://core.telegram.org/bots/api#senddocument).
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class SendFileError(Exception):
    """A send that did not happen, with the reason worded for the caller."""


@dataclass(frozen=True)
class Sent:
    window_id: str
    thread_id: str
    message_id: int | None
    filename: str
    size: int


def normalize_window(token: str) -> str:
    """``@12`` or ``12`` → ``@12``."""
    token = token.strip()
    return "@" + token if token.isdigit() else token


def resolve_thread(registry: BindingRegistry, window_id: str) -> str:
    """The topic bound to ``window_id``; :class:`SendFileError` when it has none."""
    thread = registry.thread_for_window(window_id)
    if thread is None:
        raise SendFileError(
            f"no Telegram topic is bound to window {window_id} "
            f"(bindings: {default_bindings_path()}). Is chela-telegram running with "
            "auto-topics? Pass --to @N to send to another window's topic."
        )
    return thread


def _size_label(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{n} B"


def send_file(
    path: str | Path,
    window_id: str | None,
    caption: str | None = None,
    *,
    env: dict[str, str] | None = None,
    registry: BindingRegistry | None = None,
    sender_factory: Callable[[str, str], BotSender] = BotSender,
) -> Sent:
    """Send ``path`` as a document to ``window_id``'s topic and return what was sent.

    ``window_id`` is the caller's own window unless ``--to`` named another; None means
    neither was known. ``env``/``registry``/``sender_factory`` are injectable so tests
    never read the real secrets or reach the real Bot API.
    """
    if not window_id:
        raise SendFileError(
            "could not tell which window this is: $CHELA_WID is unset and this is not a "
            "tmux pane. Pass --to @N."
        )
    window_id = normalize_window(window_id)

    src = Path(path).expanduser()
    if not src.is_file():
        raise SendFileError(f"not a file: {src}")
    size = src.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise SendFileError(
            f"{src.name} is {_size_label(size)}, over Telegram's 50 MB limit for files "
            "a bot sends. Split or compress it first."
        )

    env = bridge_env() if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise SendFileError(
            "TELEGRAM_BOT_TOKEN is not set (looked in the environment, chela.env and "
            "secrets.env)."
        )
    registry = BindingRegistry.load() if registry is None else registry
    # The env chat wins, as it does for the bridge; the persisted one covers a shell
    # that has the token from secrets.env but never set the chat id.
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip() or registry.chat_id
    if not chat:
        raise SendFileError(
            "TELEGRAM_CHAT_ID is not set and the bindings file records no chat id."
        )
    thread = resolve_thread(registry, window_id)

    try:
        data = src.read_bytes()
    except OSError as e:
        raise SendFileError(f"could not read {src}: {e}") from e
    sender = sender_factory(token, chat)
    try:
        resp = sender.send_document(src.name, data, caption or None, thread)
    except (OSError, ValueError) as e:
        # The transport turns HTTP and URL errors into a response; a timeout mid-read
        # or a garbled body still raises, and that is a failed send too.
        raise SendFileError(f"Telegram request failed: {e}") from e
    if not resp.get("ok"):
        raise SendFileError(f"Telegram refused the file: {resp.get('description', resp)}")
    mid = (resp.get("result") or {}).get("message_id")
    return Sent(window_id, thread, mid if isinstance(mid, int) else None, src.name, size)


def describe(sent: Sent) -> str:
    """The one-line confirmation ``chela send-file`` prints."""
    mid = sent.message_id if sent.message_id is not None else "unknown"
    return (
        f"sent {sent.filename} ({_size_label(sent.size)}) to {sent.window_id}'s topic "
        f"{sent.thread_id}, message_id {mid}"
    )
