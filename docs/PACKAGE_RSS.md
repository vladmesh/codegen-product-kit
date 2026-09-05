# Reminders package idle RSS observations

GitHub Actions run `33996786092`, job `101388614893`, tested commit `9cfe1ce` on 2026-09-05 and
produced these five-read ranges from the same two running containers:

| Product | Emitted readings (KiB) | Observed range (KiB) |
|---|---|---|
| Package-free | 98,856, 98,856, 98,856, 98,856, 98,856 | 98,856 to 98,856 |
| Product A with reminders | 99,268, 99,268, 99,268, 99,268, 99,268 | 99,268 to 99,268 |
| Incremental | derived from the two ranges | +412 to +412 |

The immediately preceding attested CI run, `33995848608`, measured 99,112 KiB package-free and
98,940 KiB for product A, an incremental -172 KiB sample. The negative sample is reported as
negative. Its sign conflicts with the repeated run's +412 KiB range, placing the delta at or below
the noise floor of this `/proc/1/status` pair. This method does not resolve the reminders package's
idle memory cost.

Method: the slow Copier proof generates product A and preserves its package-free baseline before
running `kit add reminders`. In one test invocation it starts both backends with the existing
`infra/compose.tests.integration.yml` stack, waits for both Compose health checks, then reads PID 1's
`VmRSS` from `/proc/1/status` five times per already-running container without rebuilding or
restarting between samples. It emits both reading arrays, both observed ranges, and the incremental
range so the calculation is traceable from the CI log.

These observations set no limit and imply no performance promise.
