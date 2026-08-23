"""Pushes a live-update signal to the web frontend's Durable Object
(web/server/routes/ws.ts) whenever something the conversation sidebar
shows actually changes — a new session starting, or a session's
summary finishing. Best-effort only: the sidebar's own fetch-on-load
path (web/app/components/ConversationSidebar.vue) is still correct
without this, just not instant — a failed notify here costs a browser
tab a manual refresh, never a booking or a conversation. Unset
WEB_NOTIFY_URL/WEB_NOTIFY_SECRET (e.g. local dev without the web app
running under wrangler) is a silent no-op, not a warning — see
web/server/api/internal/notify.post.ts's own "off Cloudflare" branch,
the same shape one level up.
"""

import logging
import os

import httpx

logger = logging.getLogger("sarjy-agent.web_notify")

_TIMEOUT_S = 3.0


async def notify(user_id: str) -> None:
    url = os.getenv("WEB_NOTIFY_URL")
    secret = os.getenv("WEB_NOTIFY_SECRET")
    if not url or not secret:
        return
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            await client.post(
                url,
                json={"identity": user_id},
                headers={"x-internal-secret": secret},
            )
    except Exception:
        logger.warning("web_notify: failed to notify web app", exc_info=True)
