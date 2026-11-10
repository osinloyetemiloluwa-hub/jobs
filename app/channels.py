import secrets
import httpx

def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(24)

def parse_incoming(channel_type: str, body: dict):
    """Returns (payload, reply_to) or None."""

    if channel_type == "telegram":
        msg = body.get("message") or body.get("edited_message")
        if not msg or "text" not in msg:
            return None
        return (
            {"text": msg["text"], "from_user": str(msg.get("from", {}).get("id", "")),
             "from_username": msg.get("from", {}).get("username"),
             "chat_id": str(msg["chat"]["id"]), "chat_type": msg["chat"].get("type"), "raw": body},
            {"chat_id": msg["chat"]["id"]},
        )

    if channel_type == "discord":
        if body.get("type") != 2:
            return None
        data = body.get("data", {})
        text = next((o.get("value", "") for o in data.get("options", []) if o.get("name") == "message"), "")
        member = body.get("member", {}) or {}
        user = member.get("user", {}) or {}
        return (
            {"text": text, "from_user": str(user.get("id", "")), "from_username": user.get("username"),
             "command": data.get("name"), "channel_id": str(body.get("channel_id", "")),
             "guild_id": str(body.get("guild_id", "")), "raw": body},
            {"application_id": body.get("application_id"), "interaction_token": body.get("token")},
        )

    if channel_type == "slack":
        ev = body.get("event", {})
        if ev.get("type") != "message" or ev.get("bot_id"):
            return None
        return (
            {"text": ev.get("text", ""), "from_user": ev.get("user", ""),
             "channel_id": ev.get("channel"), "raw": body},
            {"channel_id": ev.get("channel"), "response_url": body.get("response_url")},
        )

    if channel_type == "whatsapp":
        try:
            change = body["entry"][0]["changes"][0]["value"]
            msg = change["messages"][0]
        except (KeyError, IndexError):
            return None
        if msg.get("type") != "text":
            return None
        return (
            {"text": msg["text"]["body"], "from_user": msg["from"],
             "message_id": msg["id"], "phone_number_id": change["metadata"]["phone_number_id"],
             "raw": body},
            {"to": msg["from"], "phone_number_id": change["metadata"]["phone_number_id"]},
        )

    if channel_type == "generic":
        return (body.get("payload", body), body.get("reply_to"))

    return None

def matches_filter(event_payload: dict, event_filter: dict) -> bool:
    if not event_filter:
        return True
    for key, expected in event_filter.items():
        if key == "contains":
            if expected.lower() not in (event_payload.get("text") or "").lower():
                return False
            continue
        if str(event_payload.get(key, "")) != str(expected):
            return False
    return True

async def send_reply(channel, reply_to: dict | None, text: str) -> dict:
    try:
        cfg = channel.config or {}
        if channel.type == "telegram":
            return await _send_telegram(cfg, reply_to, text)
        if channel.type == "discord":
            return await _send_discord(cfg, reply_to, text)
        if channel.type == "slack":
            return await _send_slack(cfg, reply_to, text)
        if channel.type == "whatsapp":
            return await _send_whatsapp(cfg, reply_to, text)
        if channel.type == "twitter":
            return await _send_twitter(cfg, text)
        if channel.type == "generic":
            return await _send_generic(cfg, reply_to, text)
        return {"ok": False, "error": f"unknown type: {channel.type}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

async def _send_telegram(cfg, reply_to, text):
    chat_id = (reply_to or {}).get("chat_id") or cfg.get("default_chat_id")
    if not chat_id:
        return {"ok": False, "error": "no chat_id"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                         json={"chat_id": chat_id, "text": text[:4000]})
        return {"ok": r.is_success, "response": r.text[:300]}

async def _send_discord(cfg, reply_to, text):
    # Interaction reply (deferred) takes priority
    if reply_to and reply_to.get("application_id") and reply_to.get("interaction_token"):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                f"https://discord.com/api/v10/webhooks/{reply_to['application_id']}/{reply_to['interaction_token']}/messages/@original",
                json={"content": text[:2000]},
            )
            return {"ok": r.is_success, "response": r.text[:300]}
    # Fallback: channel webhook URL for scheduled monitors
    url = cfg.get("webhook_url")
    if url:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json={"content": text[:2000]})
            return {"ok": r.is_success, "response": r.text[:300]}
    return {"ok": False, "error": "no discord target (need interaction or webhook_url)"}

async def _send_slack(cfg, reply_to, text):
    url = (reply_to or {}).get("response_url") or cfg.get("webhook_url")
    if not url:
        return {"ok": False, "error": "no slack webhook"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"text": text})
        return {"ok": r.is_success, "response": r.text[:300]}

async def _send_whatsapp(cfg, reply_to, text):
    to = (reply_to or {}).get("to") or cfg.get("default_to")
    pnid = (reply_to or {}).get("phone_number_id") or cfg.get("phone_number_id")
    if not (to and pnid):
        return {"ok": False, "error": "no whatsapp target"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://graph.facebook.com/v21.0/{pnid}/messages",
            headers={"Authorization": f"Bearer {cfg['access_token']}"},
            json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}},
        )
        return {"ok": r.is_success, "response": r.text[:300]}

async def _send_twitter(cfg, text):
    token = cfg.get("bearer_token")
    if not token:
        return {"ok": False, "error": "no twitter bearer_token"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://api.twitter.com/2/tweets",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"text": text[:280]})
        return {"ok": r.is_success, "response": r.text[:300]}

async def _send_generic(cfg, reply_to, text):
    url = (reply_to or {}).get("callback_url") or cfg.get("callback_url")
    if not url:
        return {"ok": False, "error": "no callback_url"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"text": text})
        return {"ok": r.is_success, "response": r.text[:300]}