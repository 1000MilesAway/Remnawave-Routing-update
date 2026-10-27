# CLAUDE.md — Remnawave-Routing-update (fork)

Fork of `lifeindarkside/Remnawave-Routing-update` (`upstream` remote). Single script `app.py`:
fetches the Happ deeplink from `hydraponique/roscomvpn-happ-routing`, applies our extras and
name override, and PATCHes `happRouting` in the panel's subscription settings and external squads.

Our additions over upstream: `ROUTING_NAME` / `SQUAD_N_NAME`, `ROUTING_EXTRA_*`, `ROUTING_REMOVE_*`,
`CRON_SCHEDULE`. Keep the README in sync when adding an env var — it documents every one.

## Where it runs

Container `remna-routing-updater` on the panel host `5.101.116.103`, `/opt/Remnawave-Routing-update`
(`ssh root@5.101.116.103`). Config lives in `.env` there; it **overwrites happRouting every few
minutes**, so manual edits of happRouting in the panel are lost — change `.env` instead and
`docker compose up -d`.

## Current production state (intentional)

- `ROUTING_REMOVE_*` are **not set** in prod `.env` (removed 2026-08-29). happRouting keeps
  `geosite:twitch-ads/whitelist/torrent`, `geoip:direct` — Happ uses roscomvpn geo bases, where
  those codes exist. This is not a regression.
- Extras only reach happRouting, never the XRAY_JSON templates (`/json` subscription). Those are
  a separate place — see the `remnawave-panel` skill.

## Checks

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim sh -c \
  "pip install -q -r requirements.txt ruff==0.12.1 && python -m compileall -q app.py && python -m ruff check --select E9,F app.py"
```

No tests. Before deploying a logic change, dry-run against the real deeplink and diff the
resulting JSON instead of pushing straight to the panel.

## Hygiene

`.env.bak*` next to the prod `.env` contain `REMNA_TOKEN`; they are gitignored — keep it so.
