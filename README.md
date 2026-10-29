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

Successful qq_send results omit the error field: older Hermes versions incorrectly treated even error:null as a failure; v0.22.5 fixes that upstream, and the plugin retains its explicit receipt format. File uploads return success:true and delivery:uploaded without requiring a message ID. Local send-rate limits expose error_kind:rate_limited, retry_after and delivery:not_attempted; they do not negate earlier successes. Ambiguous upload timeouts are reported as delivery_unknown and are never automatically retried.

## Development

The plugin uses native plugin registration. The pinned v0.22.5 image carries one narrow, build-time cron compatibility patch: a LOCAL-only satellite adapter map still uses the primary adapter through native exact profile routes. Profiles with real messaging adapters keep their own transport. The build fails if either upstream patch anchor changes; remove the patch when upgrading to a fixed release. Run tests inside the pinned image:

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

Chat context supplies up to 12 unseen messages from the last ten minutes of the arrival snapshot, with roughly 4,000 characters of message content; exact reply targets remain available even when older. Previously covered context is skipped after successful turns, with a five-minute deduplication expiry and reset on /new or /reset. Missing details remain queryable through qq_history. Image selection follows the explicit current-turn rules below.

Native compression starts at 128,000 tokens (or Hermes's lower model-dependent threshold), with proactive tool-result pruning at 48,000. The former 64,000 trigger repeatedly fired while protected context and an existing handoff already occupied roughly 66–70K tokens, leaving no new middle window to summarize. A structural_backoff warning means automatic compression temporarily waits after such a no-op; it is not a task timeout or the model's context limit. These settings govern future summarization without deleting archives or resetting conversations.

For the learned-skill mistakes diagnosed on October 20, 2026, run deploy/reconcile_preferences.py with the deployment's hermes directory while the gateway is stopped. It repairs the known qq-group-chat-replies sentences, adds preference precedence, and creates dated private backups. It is idempotent and leaves other skills untouched.

## Image reliability

`qq_image` retrieves an archived image by message ID within the current conversation, refreshing its QQ download URL when necessary. It returns validated local image paths for native `vision_analyze`, with the original preserved for targeted crops. This is separate from `qq_file`, which handles file attachments. Image downloads finish before model dispatch; an omitted image is not "still loading".

DeepSeek request middleware fully decodes inline images, applies EXIF orientation, flattens transparency and prepares JPEG copies bounded to 4,096 pixels per side and four million pixels per part. Tall screenshots become at most four consecutive top-to-bottom sections to retain readable text. Animated images use their first frame. The same protection applies to inline images replayed from session history; original archives are unchanged. Undecodable inline images are replaced with an explicit retrieval notice rather than poisoning the entire request. These conservative bounds address the reproduced rejection of a valid 1050 × 8274 JPEG; they are not a claim about DeepSeek's documented maximum dimensions.

## Answer quality (2026-10-25)

The runtime is pinned to Hermes v0.22.5, release commit bf0203a794a07aac3ff764972ab9087baa4e4f4e. DeepSeek Flash remains the model. Quality guidance separates tone from factual standards, resolves the current request before research, asks for neutral searches and dated evidence, and requires actual archive/computation tools for quantitative tasks. Search responses label snippets and preserve publication metadata when provided; retrieval timestamps never masquerade as publication dates.

A conversation-scoped request observer captures a bounded evidence snapshot. The native final-output hook optionally asks the SAME Flash model for one conservative review of quantitative, researched, corrected, or consequential text answers. Greetings/banter, image-bearing turns, very short replies and large outputs bypass it. The reviewer has no tools or send permissions. Accepted changes must stay close to the draft, remain bounded, and introduce no unseen numbers or URLs; transport/JSON/validation failures retain the original. This is a fallible additional check, not a fact oracle or a substitute for main-agent research. It cannot finish missing tool work. Set QQ_ANSWER_REVIEW=off to disable the extra pass. Selected reviews typically add seconds and incur one additional API call; requests have bounded connect/read timeouts and no retries. Logs record routing/timing, not prompts or private conversation text.

Automatic post-turn background memory/skill rewriting is disabled through auxiliary.background_review.enabled=false. Explicit memory requests, manual /refine, skill installation and existing skills remain available. Memory and process notifications are off. Deployment-owned auxiliary/display keys are merged with other settings.

While the gateway is stopped, run deploy/reconcile_quality.py /opt/data --apply inside the candidate container. Without --apply it previews affected files. It replaces only the observed stale image-loading/write-permission advice and overgeneralized group-chat skill recommendations, retaining user tone and naming preferences, with private file backups and before/after hashes. Archives and sessions are not rewritten.

Run deploy/smoke_quality.py only against an isolated staging gateway using bot_qq=99. It checks statistics, BO5 consistency, archive tools, and sender-name interpretation. deploy/evaluate_quality.py INPUT.jsonl OUTPUT.jsonl replays private drafts/evidence through the reviewer without sending QQ messages; keep its input/output outside Git. Replay acceptance is not an accuracy metric. Public tests use synthetic fixtures. For rollback, retain the prior Docker image AND a stopped-writer backup of hermes/ and state/: an upgraded Hermes may migrate session databases, so changing only the image is insufficient if schemas diverge. Preserve/merge any messages collected after cutover before restoring an older snapshot.

The Docker build validates the plugin dependency versions against the image, then removes redundant installer declarations from the bundled manifest only. The source manifest retains them for standalone installations. This keeps Hermes dependency checks honest without modifying its lockfile or runtime facts. First-contact profile-building offers are disabled; a native one-time /help introduction may still appear on a brand-new profile.

## Single-message replies and image scope (2026-10-29)

Each final text reply is sent as one QQ message in groups and DMs. The conversational splitter, its extra model call, pacing delays and character-based chunking have been removed. Obsolete reply_bubbles configuration is ignored, including previously enabled overrides. Answers are not silently truncated. Prompts ask for concise replies and files for genuinely large requested deliverables; requested images/files, explicitly authorized schedules and useful long-task progress remain available. Do not use qq_send to split an ordinary answer manually.

The per-conversation send lock and durable delivery receipts remain. Confirmed sends are not repeated after restart. Never-attempted tails from pre-upgrade bubble sequences are combined into one remaining message; an ambiguous attempted send still requires verification instead of automatic retry. The narrow Hermes recovery patch remains necessary to preserve that stable delivery identity.

An image is attached automatically only when it belongs to the triggering message, is the exact quoted reply target, or a clear image-specific request identifies the sole image-bearing message within the preceding five messages and two minutes. The proximity tests use the triggering message timestamp, so a queued request cannot borrow newer images. Multiple candidates, distant images and generic words such as 这个/刚才/评价 do not select a picture. Exact quoted older images and deliberate qq_history → qq_image retrieval remain supported.

Current context labels every selected image with its message ID, sender, timestamp and relationship to the request. Incidental background image records have no inline file path and are explicitly labeled as background. At request time, old QQ image payloads and attached-image path hints are removed from earlier session turns; current attachments and current-turn tool images remain. This is a request-only transformation: session text, archives, originals, memories and skills remain on disk. Old image descriptions in textual history remain historical context, and the prompt instructs the model not to treat them as the current topic.

Regression tests cover ordinary messages after images, generic pronouns, old/cross-sender pictures, ambiguous multiple images, current attachments, exact old quotes, arrival ordering, past-session image removal, group/DM single-send behavior, long answers and durable recovery. deploy/smoke_context.py runs a real-model regression with synthetic images in an isolated fake OneBot gateway; never point it at the live QQ connection.

While the gateway is stopped, deploy/reconcile_image_scope.py /opt/data --apply replaces the observed price-guess skill sentence that defaulted to the latest picture. It only changes that exact sentence, preserves calibration/style, saves a private backup, and is idempotent. Without --apply it previews the affected paths and hashes.

## Built-in agent-browser skill

The image includes agent-browser 0.40.0, its complete upstream CLI skill pack, Debian Chromium and Noto CJK/emoji fonts. The official npm archive is pinned and checked against its published SHA-512 integrity before extraction; no package installation or browser download is needed at runtime. The native executable avoids a Node daemon and Playwright dependency. Chromium follows the Debian security package available at build time. This Dockerfile currently selects the upstream Linux x86-64 binary, matching the deployed host.

Bootstrap installs the discoverable agent-browser skill into every trusted group/DM Hermes profile (and the primary profile used for maintenance). A previous customized entrypoint is backed up before an update. The skill invokes the installed CLI through Hermes terminal and loads version-matched upstream instructions with agent-browser skills get core. Extra guides remain available with skills list/get, but installing a guide does not configure cloud browser services or desktop applications. Ordinary research continues using DeepSeek web_search/web_extract; browser work is for JavaScript rendering, page interactions, downloads and screenshots. No new LLM provider or API key is required.

The launcher derives the namespace from the trusted conversation profile. Temporary sessions are turn-scoped; persistent sessions use a stable account identity. Browser HOME, screenshots and downloads are conversation-scoped; provider secrets are not inherited by the daemon. No CDP/dashboard port is published, and browser side effects still require authorization from the task.

One container-wide lease admits one active browser job at a time; busy callers receive a bounded retry hint and should not spin. Close releases it immediately; an expired lease closes the previous browser before allowing another job. A timed-out click/submission must be inspected before retrying. Browser profiles provide logical separation, not a security boundary against another process running under the same container UID. Compose increases /dev/shm to 512 MiB while retaining the 3 GiB memory limit, non-root runtime, dropped capabilities and no-new-privileges. Upstream agent-browser launches Chromium with --no-sandbox in this environment; containment relies on these Docker restrictions, not Chromium sandboxing. Do not treat this shared container as hostile multi-tenant isolation.

Run deploy/smoke_browser.py only in an isolated synthetic staging container with HERMES_SESSION_CHAT_ID=900001 and two synthetic profiles. It exercises the real CLI/browser, JavaScript, form entry/click, Chinese text, screenshot, download, cross-chat lease, persistent-profile separation and HTTPS, with PYTHONPATH removed like Hermes terminal does. deploy/smoke_browser_agent.py checks real-model skill discovery and browser interactions through a synthetic OneBot gateway. The normal unit suite covers profile routing, credential scrubbing, persistence opt-in, rejected session overrides, timeout handling, and native Hermes skill discovery.

## Persistent browser accounts and login handoffs

Use agent-browser --account NAME open URL for login/account workflows. Named profiles live under the mounted Hermes profile's browser-profiles/ and survive browser/container restarts. Existing personal profiles are retained in place; at first use, their Chromium History is read locally to adopt website origins without exporting URLs, query parameters, cookies or credentials. --persistent remains an alias for the selected account or personal. A job name selects an execution lane, not a different login identity: --job report --account engineering uses the same disk account as foreground engineering but cannot take its live lease.

Successful explicit account navigation remembers exact origins in private browser-state.json. A later turn's open URL reuses the account for a remembered origin; unrelated sites start temporary sessions. Within the current workflow, follow-up commands retain the selection without repeated flags. --temporary overrides it without deleting saved data. Multiple accounts on one origin require explicit selection; the most recently selected account is that origin's default. browser-status lists only the current conversation's accounts/selection and login-wait status, without starting a browser or claiming authentication.

Use login-wait after opening the intended persistent QR/login page, then return the foreground reply immediately. The same browser/tab can resume on the next message without the previous implementation's busy/sleep/blank-page cycle. Persistent browsers have a ten-minute idle timeout, including while waiting; temporary browsers retain two minutes. Website QR validity can be shorter. login-done ends the pending handoff without asserting login success. Verify the authenticated page, then close/reopen and verify before claiming the login persisted. login-cancel closes the waiting browser and retains saved data. close always preserves account storage. Only forget-account NAME --confirm removes that account's local profile and origin associations; it does not revoke remote sessions. No cookies are copied from a temporary profile automatically.

The resource lease still limits the container to one active browser job. The same foreground lane can switch closed/ordinary workflows immediately after cleanup, but a pending login blocks accidental switching until resumed/cancelled or idle expiry. Other conversations/background jobs cannot steal a live lease, including when sharing the same disk account. Keep account profiles private to the group or DM that created them. Group use implies an intentionally shared account, not private ownership within that group.

The revised skill was informed by real group trajectories: subsequent messages collided with turn-derived session IDs, then slept until the original QR tab expired; hidden pipeline exit codes, synthetic UI clicks without verifying selection, and batches of guessed private API endpoints added wasted work. Guidance now preserves handoffs, checks actual UI state, keeps failures visible and uses documented/observed interfaces instead of endpoint guessing.

In isolated staging, deploy/smoke_browser_persistence.py seed waits beyond the former two-minute boundary and verifies same-tab continuation, persistent-cookie reuse, temporary isolation, cross-chat separation and foreground/background contention. Restart the staging container and run its verify phase to check retention and explicit local forgetting. No real login credentials or QQ deliveries are used by this smoke test. deploy/smoke_browser_login_agent.py exercises a real DeepSeek/Hermes two-message login handoff through a fake QQ gateway, including closing/reopening to verify authentication.

## Natural scheduled messages

Deployment sets native Hermes cron.wrap_response=false in the primary and every group/DM profile. Scheduled delivery sends the result directly, without the Cronjob Response title, internal job ID, divider or management footer. This is a native delivery configuration, not a regex that strips arbitrary user text. It applies to existing schedules as well as new ones. Other cron configuration is preserved during bootstrap; schedules, prompts, repeat counts and next-run timestamps are unchanged.

Shared guidance asks scheduled agents to write natural plain text, retain useful reminder context, avoid duplicate manual sends, and return exactly [SILENT] for conditional monitors without relevant updates. New schedules should have descriptive names, self-contained prompts, origin delivery and a verified next run in Asia/Shanghai. The formatter regression test exercises the real pinned Hermes delivery path with a fake transport for group and DM targets.

## Verified browser form operations

The pinned agent-browser 0.40.0 can report success for fill on contenteditable without clearing its previous contents. The QQ launcher now handles fill SELECTOR TEXT itself under the existing browser lease: resolve a unique CSS target (or snapshot ref), focus it, verify the requested element owns that focus, select its contents, delete through real keyboard editing, confirm it cleared, insert text, and check readback twice. This supports normal text inputs, controlled inputs, textarea and contenteditable, including an empty replacement. Unsupported/noneditable, read-only, disabled, ambiguous or changed targets fail rather than editing the previously focused field. These checks are not a substitute for inspecting the application's validation/preview or confirming server-side saving.

Use replace-text SELECTOR --file /absolute/path/body.txt for multiline UTF-8 content; files and inline text are bounded to 100 KB. The text is passed as an argument, never evaluated as shell code. Readback normalizes CRLF and logical block boundaries because Chromium innerText adds extra separators around some paragraph structures; explicit blank paragraphs remain significant. The result reports paragraph_normalized when rendered innerText differs. Arbitrary iframe/closed-shadow editors and unusual formatting may be rejected; inspect the page rather than retrying blind. A failure after deletion/insertion can leave a modified editor, so no automatic mutation retry occurs.

Upload validates every local file before selecting any, preserves input order, and retains native failure exit codes. The successful result explicitly marks application_upload_verified=false: the agent must check the website's upload completion, preview count/order and errors. Files selected is not upload complete. The existing generic browser skill also covers current accessibility references, fresh screenshots and bounds for custom controls, separate navigation/save/submit actions, condition-based waits, and receipt verification before a submission retry. No XHS-specific skill or publishing automation is installed.

deploy/smoke_browser_forms.py runs only a synthetic local page in a disposable container, covering hidden multiple-file inputs, Chinese/emoji/multiline replacement and clearing, tracked controlled-input state, snapshot refs, invalid targets, and distinct save/submit buttons inside a closed shadow root. Unit tests cover fail-before-insert/readback failure, upload preflight/native errors and exact UTF-8 file handling. No real accounts or external posts are touched.
