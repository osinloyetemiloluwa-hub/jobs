import os
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import Session, init_db
from app.models import Monitor, MonitorRun, Channel, Event
from app.schemas import (
    MonitorCreate, MonitorUpdate, MonitorOut, RunOut,
    ChannelCreate, ChannelOut, EventOut,
)
from app.auth import require_api_key
from app.executor import executor
from app.channels import (
    parse_incoming, matches_filter, send_reply, generate_webhook_secret,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Mini Runner", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ---------------- helpers ----------------

def _public_base(request: Request | None = None) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""

# ---------------- execution core ----------------

async def _run_script_for(
    monitor: Monitor,
    event: Event | None = None,
    channel: Channel | None = None,
) -> None:
    result = await executor.run(
        script=monitor.script,
        language=monitor.language,
        timeout_seconds=monitor.timeout_seconds,
        event_payload=event.payload if event else None,
        channel_type=channel.type if channel else None,
    )

    finished = datetime.now(timezone.utc)
    ok = result.exit_code == 0 and not result.error
    status = "succeeded" if ok else "failed"

    async with Session() as db:
        run = (
            await db.execute(
                select(MonitorRun)
                .where(MonitorRun.monitor_id == monitor.id, MonitorRun.status == "running")
                .order_by(MonitorRun.started_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

        if run is not None:
            run.status = status
            run.finished_at = finished
            run.exit_code = result.exit_code
            run.stdout = result.stdout
            run.stderr = result.stderr
            run.error = result.error

        mon = await db.get(Monitor, monitor.id)
        if mon is not None:
            mon.last_run_at = finished
            mon.last_status = status
            mon.last_exit_code = result.exit_code
            mon.last_stdout = result.stdout
            mon.last_stderr = result.stderr
            mon.last_error = result.error

        if event is not None:
            ev = await db.get(Event, event.id)
            if ev is not None:
                ev.status = "done" if ok else "failed"
                ev.processed_at = finished
                ev.error = result.error

        await db.commit()

    # Dispatch outbound replies
    if ok and result.replies and event is not None and event.reply_to is not None:
        for text in result.replies:
            await send_reply(channel, event.reply_to, text)

# ---------------- tick: schedules + events ----------------

async def _claim_due_monitors() -> list[dict]:
    now = datetime.now(timezone.utc)
    claimed = []
    async with Session() as db:
        async with db.begin():
            result = await db.execute(
                select(Monitor)
                .where(
                    Monitor.enabled.is_(True),
                    Monitor.trigger_type == "schedule",
                    Monitor.next_run_at <= now,
                )
                .order_by(Monitor.next_run_at)
                .with_for_update(skip_locked=True)
                .limit(settings.tick_batch_size)
            )
            for m in result.scalars().all():
                m.next_run_at = now + timedelta(seconds=m.interval_seconds)
                run = MonitorRun(monitor_id=m.id, status="running", started_at=now)
                db.add(run)
                await db.flush()
                claimed.append({"monitor_id": m.id, "run_id": run.id})
        await db.commit()
    return claimed

async def _claim_pending_events() -> list[dict]:
    now = datetime.now(timezone.utc)
    claimed = []
    async with Session() as db:
        async with db.begin():
            result = await db.execute(
                select(Event)
                .where(Event.status == "pending")
                .order_by(Event.created_at)
                .with_for_update(skip_locked=True)
                .limit(settings.tick_batch_size)
            )
            for e in result.scalars().all():
                e.status = "processing"
                claimed.append({
                    "event_id": e.id,
                    "channel_id": e.channel_id,
                    "payload": e.payload,
                    "reply_to": e.reply_to,
                })
        await db.commit()
    return claimed

async def _dispatch_event(item: dict, background: BackgroundTasks) -> None:
    async with Session() as db:
        channel = await db.get(Channel, item["channel_id"])
        if channel is None or not channel.enabled:
            await db.commit()
            return

        result = await db.execute(
            select(Monitor).where(
                Monitor.enabled.is_(True),
                Monitor.trigger_type == "event",
                Monitor.channel_id == item["channel_id"],
            )
        )
        for monitor in result.scalars().all():
            if not matches_filter(item["payload"], monitor.event_filter or {}):
                continue

            run = MonitorRun(
                monitor_id=monitor.id,
                event_id=item["event_id"],
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.commit()

            event = await db.get(Event, item["event_id"])
            background.add_task(_run_script_for, monitor, event, channel)

# ---------------- endpoints ----------------

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.post("/tick")
async def tick(background: BackgroundTasks, x_tick_secret: str = Header(...)):
    if x_tick_secret != settings.tick_secret:
        raise HTTPException(401, "Invalid tick secret")

    monitors = await _claim_due_monitors()
    for item in monitors:
        async with Session() as db:
            m = await db.get(Monitor, item["monitor_id"])
            background.add_task(_run_script_for, m)

    events = await _claim_pending_events()
    for item in events:
        await _dispatch_event(item, background)

    return {"monitors": len(monitors), "events": len(events)}

# ---------------- channel CRUD ----------------

@app.post("/v1/channels", response_model=ChannelOut, dependencies=[Depends(require_api_key)])
async def create_channel(body: ChannelCreate, request: Request):
    async with Session() as db:
        cfg = dict(body.config)
        if body.type in {"telegram", "discord", "slack", "generic"}:
            cfg.setdefault("webhook_secret", generate_webhook_secret())
        ch = Channel(name=body.name, type=body.type, config=cfg)
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return ChannelOut(
            id=ch.id, name=ch.name, type=ch.type,
            webhook_url=f"{_public_base(request)}/webhooks/{ch.id}/{cfg['webhook_secret']}",
            enabled=ch.enabled, created_at=ch.created_at,
        )

@app.get("/v1/channels", response_model=list[ChannelOut], dependencies=[Depends(require_api_key)])
async def list_channels(request: Request):
    async with Session() as db:
        result = await db.execute(select(Channel).order_by(Channel.created_at.desc()))
        out = []
        for ch in result.scalars().all():
            secret = (ch.config or {}).get("webhook_secret", "")
            out.append(ChannelOut(
                id=ch.id, name=ch.name, type=ch.type,
                webhook_url=f"{_public_base(request)}/webhooks/{ch.id}/{secret}" if secret else None,
                enabled=ch.enabled, created_at=ch.created_at,
            ))
        return out

# ---------------- webhook receivers ----------------

@app.post("/webhooks/{channel_id}/{secret}")
async def webhook(channel_id: str, secret: str, request: Request):
    async with Session() as db:
        ch = await db.get(Channel, channel_id)
        if ch is None or not ch.enabled:
            raise HTTPException(404)

        expected = (ch.config or {}).get("webhook_secret")
        if expected and secret != expected:
            raise HTTPException(401)

        body = await request.json()
    # Slack URL verification
    if ch.type == "slack" and body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    parsed = parse_incoming(ch.type, body)
    if parsed is None:
        return {"ok": True, "ignored": True}

    payload, reply_to = parsed
    async with Session() as db:
        db.add(Event(channel_id=channel_id, payload=payload, reply_to=reply_to, status="pending"))
        await db.commit()

    return {"ok": True}

# ---------------- monitor CRUD ----------------

@app.post("/v1/monitors", response_model=MonitorOut, dependencies=[Depends(require_api_key)])
async def create_monitor(body: MonitorCreate):
    async with Session() as db:
        m = Monitor(
            name=body.name, script=body.script, language=body.language,
            interval_seconds=body.interval_seconds, timeout_seconds=body.timeout_seconds,
            trigger_type=body.trigger_type, channel_id=body.channel_id,
            event_filter=body.event_filter,
            next_run_at=datetime.now(timezone.utc),
        )
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return MonitorOut.model_validate(m)

@app.get("/v1/monitors", response_model=list[MonitorOut], dependencies=[Depends(require_api_key)])
async def list_monitors():
    async with Session() as db:
        result = await db.execute(select(Monitor).order_by(Monitor.created_at.desc()))
        return [MonitorOut.model_validate(m) for m in result.scalars().all()]

@app.patch("/v1/monitors/{monitor_id}", response_model=MonitorOut, dependencies=[Depends(require_api_key)])
async def update_monitor(monitor_id: str, body: MonitorUpdate):
    async with Session() as db:
        m = await db.get(Monitor, monitor_id)
        if m is None:
            raise HTTPException(404)
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(m, k, v)
        if body.enabled is True:
            m.next_run_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(m)
        return MonitorOut.model_validate(m)

@app.delete("/v1/monitors/{monitor_id}", dependencies=[Depends(require_api_key)])
async def delete_monitor(monitor_id: str):
    async with Session() as db:
        m = await db.get(Monitor, monitor_id)
        if m is None:
            raise HTTPException(404)
        await db.delete(m)
        await db.commit()
    return {"ok": True}

@app.post("/v1/monitors/{monitor_id}/run-now", dependencies=[Depends(require_api_key)])
async def run_now(monitor_id: str):
    async with Session() as db:
        m = await db.get(Monitor, monitor_id)
        if m is None:
            raise HTTPException(404)
        m.next_run_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True}

@app.get("/v1/monitors/{monitor_id}/runs", response_model=list[RunOut], dependencies=[Depends(require_api_key)])
async def list_runs(monitor_id: str, limit: int = Query(20, ge=1, le=100)):
    async with Session() as db:
        result = await db.execute(
            select(MonitorRun).where(MonitorRun.monitor_id == monitor_id)
            .order_by(MonitorRun.started_at.desc()).limit(limit)
        )
        return [RunOut.model_validate(r) for r in result.scalars().all()]

# ---------------- frontend ----------------

if os.path.isdir("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")