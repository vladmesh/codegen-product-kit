# tg-channels 0.1.0

In-process public Telegram channel reading through the platform's `tg-reader` Product API v1.
There is no model, translation or content extraction. Text is delivered as plain text.

Install the independently released package with `kit add tg-channels`, then `kit bind tg-channels`.
It requires core façade 2.4 and a backend,tg_bot product. The shipped binding v2 uses the
product-scope `language` setting, explicitly set to `ru` or `en` through `/settings/set`.
No language is assumed. Commands are `/channel <username>`, `/channels` (remove buttons)
and `/digest`. Names are stripped, lowercased and validated; `@name` and `t.me/name` links work.
Pending channels are saved and get a localized checking reply. Posts arrive once available.

The package declares required `PLATFORM_KEY`, `PLATFORM_BASE_URL` and `REDIS_URL` in
`codegen_kit_tg_channels/package.yaml`. Generation copies the two platform sources into the
product's environment contract; they are not user secrets. The package's `.env.example`
documents all required values. The platform
issues the key for `tg-reader:read` and supplies the base URL. No stand or production setup
is performed by installing this package. There are no required-setting application defaults.

The starting list is supplied through the existing product setting-seed contract, by writing
the product setting through `/settings/set` with the core settings write capability:

```json
{"contract_version": 1, "key": "tg_channels.starting_channels", "scope": "product",
 "value": ["cyproplan", "kipr_podslushano_limasol"]}
```

This «Кипр» list is copied into each user's subscriptions on their first successful list,
add, remove or digest operation. The seed is optional; an unseeded user starts with an empty
list. Initialization is recorded durably, so removing a seeded channel or emptying the list
does not restore it on the next request. The seed snapshots the first supplied list, not a live
synchronization of later setting edits. Seeded channels are leased and checked lazily by
the platform on the first read; manually entered names always get an explicit resolve check.
The product union is limited to 50 distinct channels, matching the declared quota.

The core fires `tg-channels.tick` every 60 seconds. A single locked database row serializes
polls and subscription changes across backend processes. Each poll reads one page (200
changes), persists its cursor with seen post ids and delivery outbox rows, and continues
the backlog on later ticks. The cursor covers the union of all users' channels. HTTP 409
restarts from the saved `since` instant (initially 72 hours ago, clipped to the 29-day
retention window); stored `(channel, id)` identities prevent repeat notifications. HTTP 429
sets a durable retry deadline from `Retry-After` and retains the cursor. HTTP 401/403 stops
future platform polls durably and writes a fixed credential-free error log. Correcting the
deployment credential also requires an operator to clear `tg_channels.poll_state.stopped`;
the bot has no administrative credential or recovery command.

Edits and tombstones mark their identities seen but never emit a new-post event. Each new
post has one durable `tg-channels.post` delivery row per subscribed user, addressed by the
core recipient `user_ref`. Publication occurs after commit, with a deterministic UUID;
crash recovery may retry the same transport event and the generated relay deduplicates that
identity. This is one logical notification, not exactly-once transport. Committed pending
deliveries survive a platform outage and may still arrive after a user removes a channel.
Removing a channel changes only that user's list. Platform reads renew union leases;
unused leases expire after the platform's seven-day TTL, rather than one user's removal
dropping another user's lease. The client uses only resolve and post reads.

Digests read the caller's own channels over the last 72 hours, fold edits and tombstones by
sequence, and return the latest 20 surviving posts by publication date. Work is bounded at
20 pages; a larger outstanding change feed asks the caller to try later rather than showing
a partial, possibly deleted snapshot. Text is stripped and capped at 2500 characters so
the channel, ISO timestamp and canonical `https://t.me/<channel>/<id>` URL fit one Telegram
message. Content is never translated. Digest cursors are ephemeral and independent of the
single product delivery cursor.

`contracts/openapi.json` is vendored byte-for-byte from `codegen-platform-services` commit
`070dfb35`, `services/tg-reader/openapi.json` (API 0.1.0). Contract tests verify used operations,
parameters, response projections, required fields, defaults, enums, security and errors.
401/403 for resolve are ingress authentication failures; the upstream operation lists
200/400/429/503 and post reads explicitly list 401/403. Error bodies and transport diagnostics
are discarded. The client stores the credential as `SecretStr` and returns only fixed
status diagnostics without chained httpx/validation errors.

Package tests run in the kit unit/tooling lanes through `test_channel_package.py`.
Copier tests bind the actual installed package in a backend,tg_bot product, test every command,
declared creation error and recipient event in RU/EN, and run the product's unchanged drift,
ruff and xenon checks. The slow CI lane adds the product's exact `make typecheck`; it is not
run on the control host.
