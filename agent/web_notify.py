"""Pushes live-update events to the web frontend's Durable Object
(web/server/routes/ws.ts) whenever something the conversation sidebar or
an open transcript shows actually changes — a session starting or
finishing, or a single message being added. Best-effort only: the
sidebar's own fetch-on-load path (web/app/components/
ConversationSidebar.vue) is still correct without this, just not
instant — a failed notify here costs a browser tab a manual refresh,
never a booking or a conversation. Unset WEB_NOTIFY_URL/WEB_NOTIFY_SECRET
(e.g. local dev without the web app running under wrangler) is a silent
no-op, not a warning — see web/server/api/internal/notify.post.ts's own
"off Cloudflare" branch, the same shape one level up.

The event dict shapes sent here are a contract with two places on the
web side: server/api/internal/notify.post.ts relays `event` verbatim
(it never inspects the shape), and useLiveUpdates.ts's LiveUpdateEvent
type is what actually has to agree with these field names.
"""

import logging
import os

import httpx

logger = logging.getLogger("sarjy-agent.web_notify")

_TIMEOUT_S = 3.0


async def _post(user_id: str, event: dict) -> None:
    url = os.getenv("WEB_NOTIFY_URL")
    secret = os.getenv("WEB_NOTIFY_SECRET")
    if not url or not secret:
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            await client.post(
                url,
                json={"identity": user_id, "event": event},
                headers={"x-internal-secret": secret},
            )
    except Exception:
        logger.warning("web_notify: failed to notify web app", exc_info=True)


async def notify_session_upserted(user_id: str, session: dict) -> None:
    """session is conversations.py's _session_dict() shape — a brand-new
    session (conversations.start_session) or one whose summary/ended_at
    just landed (conversations.end_session). The sidebar always places
    this at the top of the list rather than sorting it in: both call
    sites set updated_at to (essentially) now, so it's never anything
    but the most recently active conversation at the moment it's sent.
    """
    await _post(user_id, {"type": "session-upserted", "session": session})


async def notify_message_added(user_id: str, session_id: str, message: dict) -> None:
    """message is conversations.py's _message_dict() shape. sessionId
    travels alongside it (not embedded in message itself) so a tab
    looking at a *different* conversation can cheaply ignore this
    without inspecting the message.
    """
    await _post(user_id, {"type": "message-added", "sessionId": session_id, "message": message})
