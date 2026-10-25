# qq-hermes-bot

A native Hermes Gateway plugin for QQ through SnowLuma/NapCat's OneBot reverse WebSocket. Hermes owns conversations, memory, skills, cron and background delegation; the plugin owns QQ transport and the full group archive.

## Features

- Trusted groups respond to @mentions; allowlisted QQ friends can DM without an @mention. Durable deduplication and independent ordered queues per conversation.
- One Hermes profile and shared conversation per group, with independent native memories, skills and cron state.
- SQLite archive of all trusted-group messages, including ordinary non-mentions; date/user/text search, sampling, neighboring messages and coverage through `qq_history`.
- Recent and quoted image context, bounded QQ image downloads, group file downloads, bounded voice transcription.
- `qq_send` for short proactive text, inline pictures and group files, scoped to the current group and limited to three sends per minute.
- Native `cronjob` for scheduled work; native `delegate_task` for asynchronous work, with same-requester `/approve ID` consent and steering ownership checks.
- `/status` anywhere in an addressed message bypasses the model. Eyes acknowledgement is removed when processing finishes.
- DeepSeek Flash model plus the official DeepSeek Search/Fetch APIs as a Hermes web backend.

## Deployment

Each unique incoming message gets its own turn, including file-only DMs. Queues preserve gateway arrival order; messages sharing QQ's second-resolution timestamp may arrive in a different order from the sender's display. The bridge waits through native startup restore and turn cleanup before admitting the next request. Turn context distinguishes pending messages and completed turns; already handled work is not evidence of duplicate delivery.

Copy `.env.example` to `.env` and `config.example.json` to `config.json`. Configure the bot QQ account, `trusted_groups`, and optional `trusted_users` QQ IDs (empty by default). Keep real config, secrets and runtime state private.

If the host uses Mihomo TUN fake-IP DNS, set `fake_ip_ranges` to its actual configured range, for example `["198.18.0.0/16"]`. Bootstrap declares it to Hermes's native URL checks and the QQ downloader. Only explicitly configured subnets of the benchmark range `198.18.0.0/15` are accepted; LAN, loopback and metadata addresses remain blocked. Leave the list empty on ordinary DNS.

`docker compose build` then `docker compose up -d` starts the Hermes Gateway. SnowLuma must connect its reverse WebSocket to `ws://qq-hermes-bot:8080/onebot` with the same `ONEBOT_TOKEN`. The Compose network is `napcat_default`; change it for another installation. No public host port or Docker socket is required.

The compatibility alias `qq-dsh-bot` allows an existing SnowLuma connection to continue after stopping the old container. Do not run both bots simultaneously with this alias.

The deployment bootstrap creates native profiles `qq-<group_id>` and routes each chat through the shared bot into its profile. Profile state lives in `hermes/`; archives, media and workspaces live in `state/groups/<group_id>/`. These are logical isolation boundaries between trusted groups, not hostile multi-tenant OS sandboxes. The container has no host filesystem or Docker control access.

## Personal assistants in DMs

Add QQ user IDs to `trusted_users` in the private deployment config, then restart the gateway. Both users must be QQ friends of the bot; temporary group-origin private sessions and automatic friend-request acceptance are not supported. Untrusted DMs are ignored before archiving or model invocation. Group membership alone does not authorize private access.

Each user has profile `qq-dm-<user_id>`, native memories (`MEMORY.md`/`USER.md`), skills, cron jobs and sessions under `hermes/profiles/`. Their archive, attachments and workspace live under `state/users/<user_id>/`. Personal replies can be more detailed than group replies. Tools operate on the current conversation; no default sharing with groups or other people. This remains logical isolation inside the shared container, not an OS sandbox per user.

DM chat IDs are `dm-<user_id>`; existing numeric group IDs and persisted group state are unchanged. Explicit native delivery targets are `qq_onebot:dm-<user_id>`, and cron should use `deliver=origin`. Hooks restrict sends and cron destinations to the current profile. Scheduling requires an explicit user request, defaults to Asia/Shanghai, and ambiguous times should be clarified. Native delegation keeps same-requester `/approve ID` consent and task ownership checks; no @mention is required in DMs. `/status` bypasses the model in both kinds of chat.

Private text/images use `send_private_msg`; files use `upload_private_file` and recorded incoming files use `get_private_file_url` (including the recorded file hash). Downloads and exports retain the existing size and path restrictions. New private messages are persisted from deployment onward; older DM history is not backfilled, and disconnected periods can leave gaps. Group backfill continues independently.

Back up both `hermes/` and `state/` before updating. To disable DMs, remove their IDs from `trusted_users` and restart; state is retained. Do not delete personal profiles until any saved schedules have been reviewed. The legacy ledger column named `group_id` stores integer group keys or distinct `dm-<user_id>` text keys, without rewriting existing group records.

## Import existing archives

Run `python3 deploy/import_history.py /path/to/qq-dsh-bot /path/to/qq-hermes-bot` on the server. It uses SQLite backup and verifies counts and integrity, copies media/attachments, preserves detailed memories as workspace references, seeds concise native memory and copies installed skills. The source is never modified. Run once while staging and again after stopping the old writer for final cutover. Both deployments mount their own copies at `/state`, preserving archived media paths.

Hermes session transcripts start fresh. Existing DSH cron/background jobs are not automatically replayed; inspect them before cutover and recreate desired schedules explicitly in Hermes.

## Rollback

Stop `qq-hermes-bot`, then start the preserved `qq-dsh-bot`. Original archives remain intact. Messages collected while Hermes ran stay in its archive; copy or merge them with SQLite tooling before removing either deployment. Never overwrite a newer archive with an older snapshot.

## Operations

Use `docker compose logs --tail=100`, `docker compose ps` and `docker exec qq-hermes-bot curl -fsS http://127.0.0.1:8080/health`. Keep both `hermes/` and `state/` in private backups. Stop the writer for filesystem backups, or use SQLite backup per database.

Hermes delegates do not resume running Python processes after a gateway restart. Interrupted tasks need review; persisted completion delivery is not durable execution. Cron is minute-granularity and QQ delivery is not hard real-time. Native session resets can end attached background work.

display.background_process_notifications is off in every served profile. Automatic terminal-process completion lines, raw commands and traceback tails stay out of group chat; process output remains available to backend inspection and the agent's process tool. Normal answers and native delegated-agent completion handling remain enabled. The agent should inspect its processes and give concise task-level results, showing raw diagnostics only on explicit request.

Long foreground turns occupy only their group's reply queue. Background delegation requires the same requester's confirmation and releases the queue after the foreground reply. In the pinned Hermes version, children have a 250-iteration budget; the configured 43,200-second child timeout measures inactivity, not total elapsed runtime. Keep task checkpoints in the workspace for recovery after a restart; no automatic foreground-to-background migration is implemented.

Startup extends HERMES_WRITE_SAFE_ROOT with each trusted group's workspace while retaining the existing roots and native credential/state protections. The whole /state directory is not made writable through file tools. These process-wide roots provide logical separation, not per-group OS isolation.

Long-job consent is enforced by plugin hooks. Whether text genuinely requests a schedule or proactive send remains an agent instruction; numeric destination/rate limits are enforced in code. Incoming files, history and websites never authorize actions. Native skills may execute code, so trusted groups must be allowed to use this runtime.

Successful qq_send results omit the error field: the pinned Hermes failure classifier incorrectly treats even error:null as a failure. File uploads return success:true and delivery:uploaded without requiring a message ID. Local send-rate limits expose error_kind:rate_limited, retry_after and delivery:not_attempted; they do not negate earlier successes. Ambiguous upload timeouts are reported as delivery_unknown and are never automatically retried.

## Development

The plugin uses native plugin registration and does not patch Hermes core. Run tests inside the pinned image:

```sh
docker run --rm -e HERMES_HOME=/tmp/hermes-test \
  --entrypoint /opt/hermes/.venv/bin/python qq-hermes-bot:local \
  -m unittest discover -s /opt/qq-hermes-bot/tests -v
```

`deploy/smoke_burst.py`, `deploy/smoke_dm.py`, `deploy/smoke_gateway.py` and `deploy/smoke_capabilities.py` exercise a synthetic OneBot connection with real model/tool calls; use a separate staging container with empty state and no live QQ connection. They never connect to QQ. `deploy/smoke_api.py` checks model, vision and official search/fetch API access. These smoke tests incur API usage.

Mention deduplication survives restarts; pending in-memory admission queues do not. Review interrupted requests after a restart rather than replaying them automatically.

Archive, gateway ID translation, image cache and plaintext-rendering code are derived from GuruGuru-SEU/qq-dsh-bot. Protocol details remain separate from the Hermes adapter.

## Preference and context reliability

Every served turn gets fresh group memory and the current sender's member preferences through the native pre_llm_call hook. Explicit preferences take precedence over inferred habits and learned skill suggestions. Stored text never authorizes tool use. The same guidance is maintained in SOUL.md without replacing other custom instructions. Skill writes repeating explicitly forbidden Chinese reply endings are rejected; broader semantic conflicts are handled by reconciliation instructions, not a universal conflict detector.

History calls require an explicit action; deferred-call parameters must live inside arguments. The observed flattened calls and unsupported filters fail instead of silently querying everything. Filtered search requires a selector; use action recent for intentional unfiltered retrieval. Responses default to 20 hits, echo applied_filters, and provide bounded previews with pagination. Full archives and file exports remain available.

Chat context supplies up to 12 new messages from the arrival snapshot, with roughly 4,000 characters of message content. Previously covered context is skipped after successful turns, with a five-minute expiry and reset on /new or /reset. Missing details remain queryable through qq_history. Current-message images, explicit reply targets, the newest image referenced by a visual question, or the latest unsupplied image from any sender in the bounded arrival snapshot may be attached (maximum three original images). Unrelated turns do not repeatedly attach old group pictures.

Native compression starts at 128,000 tokens (or Hermes's lower model-dependent threshold), with proactive tool-result pruning at 48,000. The former 64,000 trigger repeatedly fired while protected context and an existing handoff already occupied roughly 66–70K tokens, leaving no new middle window to summarize. A structural_backoff warning means automatic compression temporarily waits after such a no-op; it is not a task timeout or the model's context limit. These settings govern future summarization without deleting archives or resetting conversations.

For the learned-skill mistakes diagnosed on October 20, 2026, run deploy/reconcile_preferences.py with the deployment's hermes directory while the gateway is stopped. It repairs the known qq-group-chat-replies sentences, adds preference precedence, and creates dated private backups. It is idempotent and leaves other skills untouched.

## Image reliability

`qq_image` retrieves an archived image by message ID within the current conversation, refreshing its QQ download URL when necessary. It returns validated local image paths for native `vision_analyze`, with the original preserved for targeted crops. This is separate from `qq_file`, which handles file attachments. Image downloads finish before model dispatch; an omitted image is not "still loading".

DeepSeek request middleware fully decodes inline images, applies EXIF orientation, flattens transparency and prepares JPEG copies bounded to 4,096 pixels per side and four million pixels per part. Tall screenshots become at most four consecutive top-to-bottom sections to retain readable text. Animated images use their first frame. The same protection applies to inline images replayed from session history; original archives are unchanged. Undecodable inline images are replaced with an explicit retrieval notice rather than poisoning the entire request. These conservative bounds address the reproduced rejection of a valid 1050 × 8274 JPEG; they are not a claim about DeepSeek's documented maximum dimensions.
