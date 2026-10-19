# qq-hermes-bot

A native Hermes Gateway plugin for QQ through SnowLuma/NapCat's OneBot reverse WebSocket. Hermes owns conversations, memory, skills, cron and background delegation; the plugin owns QQ transport and the full group archive.

## Features

- Trusted groups only, explicit @mentions, durable trigger deduplication, separate requests for burst mentions.
- One Hermes profile and shared conversation per group, with independent native memories, skills and cron state.
- SQLite archive of all trusted-group messages, including ordinary non-mentions; date/user/text search, sampling, neighboring messages and coverage through `qq_history`.
- Recent and quoted image context, bounded QQ image downloads, group file downloads, bounded voice transcription.
- `qq_send` for short proactive text, inline pictures and group files, scoped to the current group and limited to three sends per minute.
- Native `cronjob` for scheduled work; native `delegate_task` for asynchronous work, with same-requester `/approve ID` consent and steering ownership checks.
- `/status` anywhere in an addressed message bypasses the model. Eyes acknowledgement is removed when processing finishes.
- DeepSeek Flash model plus the official DeepSeek Search/Fetch APIs as a Hermes web backend.

## Deployment

Copy `.env.example` to `.env` and `config.example.json` to `config.json`. Configure the bot QQ account and trusted groups. Keep real config, secrets and runtime state private.

`docker compose build` then `docker compose up -d` starts the Hermes Gateway. SnowLuma must connect its reverse WebSocket to `ws://qq-hermes-bot:8080/onebot` with the same `ONEBOT_TOKEN`. The Compose network is `napcat_default`; change it for another installation. No public host port or Docker socket is required.

The compatibility alias `qq-dsh-bot` allows an existing SnowLuma connection to continue after stopping the old container. Do not run both bots simultaneously with this alias.

The deployment bootstrap creates native profiles `qq-<group_id>` and routes each chat through the shared bot into its profile. Profile state lives in `hermes/`; archives, media and workspaces live in `state/groups/<group_id>/`. These are logical isolation boundaries between trusted groups, not hostile multi-tenant OS sandboxes. The container has no host filesystem or Docker control access.

## Import existing archives

Run `python3 deploy/import_history.py /path/to/qq-dsh-bot /path/to/qq-hermes-bot` on the server. It uses SQLite backup and verifies counts and integrity, copies media/attachments, preserves detailed memories as workspace references, seeds concise native memory and copies installed skills. The source is never modified. Run once while staging and again after stopping the old writer for final cutover. Both deployments mount their own copies at `/state`, preserving archived media paths.

Hermes session transcripts start fresh. Existing DSH cron/background jobs are not automatically replayed; inspect them before cutover and recreate desired schedules explicitly in Hermes.

## Rollback

Stop `qq-hermes-bot`, then start the preserved `qq-dsh-bot`. Original archives remain intact. Messages collected while Hermes ran stay in its archive; copy or merge them with SQLite tooling before removing either deployment. Never overwrite a newer archive with an older snapshot.

## Operations

Use `docker compose logs --tail=100`, `docker compose ps` and `docker exec qq-hermes-bot curl -fsS http://127.0.0.1:8080/health`. Keep both `hermes/` and `state/` in private backups. Stop the writer for filesystem backups, or use SQLite backup per database.

Hermes delegates do not resume running Python processes after a gateway restart. Interrupted tasks need review; persisted completion delivery is not durable execution. Cron is minute-granularity and QQ delivery is not hard real-time. Native session resets can end attached background work.

Long-job consent is enforced by plugin hooks. Whether text genuinely requests a schedule or proactive send remains an agent instruction; numeric destination/rate limits are enforced in code. Incoming files, history and websites never authorize actions. Native skills may execute code, so trusted groups must be allowed to use this runtime.

## Development

The plugin uses native plugin registration and does not patch Hermes core. Run tests inside the pinned image:

```sh
docker run --rm -e HERMES_HOME=/tmp/hermes-test \
  --entrypoint /opt/hermes/.venv/bin/python qq-hermes-bot:local \
  -m unittest discover -s /opt/qq-hermes-bot/tests -v
```

`deploy/smoke_gateway.py` and `deploy/smoke_capabilities.py` exercise a synthetic OneBot connection with real model/tool calls; use a separate staging container with empty state and no live QQ connection. They never connect to QQ. `deploy/smoke_api.py` checks model, vision and official search/fetch API access. These smoke tests incur API usage.

Mention deduplication survives restarts; pending in-memory admission queues do not. Review interrupted requests after a restart rather than replaying them automatically.

Archive, gateway ID translation, image cache and plaintext-rendering code are derived from GuruGuru-SEU/qq-dsh-bot. Protocol details remain separate from the Hermes adapter.
