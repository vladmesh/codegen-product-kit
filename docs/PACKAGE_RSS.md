# Reminders package idle RSS observation

Product A's incremental idle backend RSS was 584 KiB in the local CI-equivalent compose run on
2026-09-05. This is an observation, not a limit or performance promise.

The slow Copier proof generates product A and preserves its package-free baseline before running
`kit add reminders`. It starts each backend separately with the existing
`infra/compose.tests.integration.yml` stack, waits for Compose health, and reads `VmRSS` for PID 1
from `/proc/1/status` inside the backend container. The package-free process measured 98,308 KiB and
the otherwise unchanged product A process measured 98,892 KiB, producing the recorded 584 KiB
difference.

This is a single pair of Linux container measurements. Allocator behavior, import timing, and host
noise can change either reading, so CI repeats and reports the measurement without asserting this
recorded value or smoothing multiple samples.
