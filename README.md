# Mini Runner

Stateless tick system for running user-submitted monitors:

- **Render Free web service** = control plane
- **Neon Postgres** = durable state
- **E2B** = isolated sandbox execution
- **External cron** = calls `POST /tick` every minute

Each run is short-lived, so E2B's 1-hour continuous-runtime limit is never
approached.

## Deploy

1. Create a Neon project and copy the **pooled** connection string.
   Convert it to `postgresql+asyncpg://...` and keep `sslmode=require`.
2. Create an E2B account and copy your API key.
3. Push this repo to GitHub, then create a Render Blueprint from `render.yaml`.
4. In the Render dashboard, set:
   - `DATABASE_URL`
   - `E2B_API_KEY`
5. Copy the generated values for:
   - `RUNNER_API_KEYS` (give this to your users)
   - `TICK_SECRET` (used only by your cron)
6. Create a free cron on cron-job.org (or GitHub Actions):
   - URL: `https://<your-service>.onrender.com/tick`
   - Method: `POST`
   - Header: `X-Tick-Secret: <your TICK_SECRET>`
   - Schedule: every 1 minute
7. Open the service URL, paste the API key, and create a monitor.

## Why the cron is every minute

Render Free sleeps after 15 minutes without inbound traffic. A request every
minute keeps the control plane awake and processes due monitors. Render gives
750 instance-hours per month, enough for 24/7 on a single free service.

## Security

Scripts run in E2B Firecracker microVMs, not inside the Render container.
Never pass exchange API keys or other long-lived secrets into a script. If a
monitor needs to call an authenticated API, proxy that call through the control
plane or use a short-lived token.

## Scaling

Increase `TICK_BATCH_SIZE` (default `3`) carefully. E2B free tier has concurrent
sandbox limits. If you need more throughput, raise the batch size and move to a
paid Render plan or a paid E2B plan.