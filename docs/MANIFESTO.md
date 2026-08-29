# The Manifesto

This project is not just a template; it is a philosophy of software development designed for the age of AI Agents. We prioritize rigidity, automation, and transparency over flexibility and human intuition.

## Core Principles

### 1. Rigidity is Freedom
Flexibility in an agent-based workflow is a liability. It introduces ambiguity, increases context size, and leads to drift.
- **We restrict choices:** A fixed stack, fixed patterns, and fixed file structures mean agents don't waste tokens "deciding" how to structure a project.
- **We enforce standardization:** Every service looks the same. Every Dockerfile follows the same template. Deviations are blocked by CI.

### 2. Token Economy (Generation > Context)
Tokens are a finite resource. We optimize for small context windows and high-throughput generation.
- **Don't read it, generate it:** If code can be deterministically generated from a spec (models, routers, boilerplate), it should be.
- **Spec-First:** The source of truth is always a compact YAML specification, not a sprawling codebase. Agents read the spec, and scripts generate the implementation.

### 3. Quality as a Gatekeeper
We do not tolerate technical debt. The pipeline is designed to fail fast and fail hard.
- **Zero Tolerance:** Linters, formatters, and type checkers are strictly configured. If a single check fails, the build fails.
- **Automated Best Practices:** We don't rely on code review to catch style issues; we rely on automated tools to prevent them from being committed.

### 4. Everything is a Template
Uniqueness is a bug.
- **Batteries Included:** Services are modular units (containers) that plug into the system.
- **No "Special Snowflakes":** If a service needs a custom build process, it probably shouldn't exist in this repo.
- **Runtime containers are the boundary:** Services run in Docker; deterministic developer tooling
  runs from project-owned uv environments.

### 5. Language Agnosticism as a Goal

The spec-first architecture is designed to be **language-agnostic in principle**. YAML specs define the *what* (models, operations, transports), not the *how* (Python classes, Rust structs).

- **Specs over code:** When modifying specs or the codegen pipeline, prefer language-neutral abstractions (JSON Schema types, transport-agnostic operation definitions)
- **Long-term vision:** The framework may evolve to support multiple target languages.
- **Practical guideline:** When writing new specs or generators, avoid embedding Python-specific assumptions where a generic approach works equally well

### 6. The Human Role: Product Owner
In this framework, the human moves up the abstraction ladder.
- **Humans define the "What":** Writing specs, defining business logic in specific slots, and setting the direction.
- **Agents handle the "How":** Generating boilerplate, writing tests, and connecting the dots.
- **Transparency:** The human can always inspect the code, but they shouldn't need to touch the plumbing.
