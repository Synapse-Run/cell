# Contributing to Synapse Cell

Thank you for considering a contribution. This document covers the practical mechanics: how to file issues, how to submit code, what we require for legal hygiene, and what to expect in review.

---

## TL;DR

1. **File issues first** for non-trivial changes. Discuss before coding.
2. **Sign your commits** with `git commit -s` (Developer Certificate of Origin).
3. **Run the tests** (`python3 cell/run_test.py` and `python3 cell/sdk/tests/stress_test.py --quick`) before opening a PR.
4. **One PR per logical change** with a clear conventional-commit title.
5. **Be patient** with review — Cell is bootstrapped and Mike + agents triage on a best-effort basis.

---

## Where to file what

| Type of contribution | Where it goes |
|----------------------|---------------|
| Bug report | GitHub issue with `[bug]` prefix and a minimal reproducer |
| Security vulnerability | **NOT** a public issue. Email security@synapserun.dev (see [SECURITY.md](SECURITY.md) §4-5) |
| Feature request | GitHub issue with `[feature]` prefix. Include use case, not just "wouldn't it be nice if" |
| Documentation fix | PR directly with `[docs]` conventional-commit prefix |
| Tiny code fix (typo, one-line bug) | PR directly with `[fix]` |
| New FFI or security-relevant change | Issue first → discussion → PR. See [SECURITY.md](SECURITY.md) Rule 7 |
| Trademark / branding question | Email trademark@synapserun.dev (see [TRADEMARK.md](TRADEMARK.md)) |
| Commercial license question | Email sales@synapserun.dev (see [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md)) |

---

## Legal hygiene — DCO is required, CLA is optional

### DCO (Developer Certificate of Origin) — required for every commit

Every commit must be signed with the `-s` flag:

```bash
git commit -s -m "fix(sdk): handle empty stdout in CellResult"
```

This adds a `Signed-off-by: Your Name <your.email@example.com>` line to your commit message. By doing this, you're certifying that **you have the right to submit your contribution** under the project's open-source license (Apache 2.0 or AGPL v3, whichever applies to the file you're modifying). The full DCO text is at [https://developercertificate.org/](https://developercertificate.org/) — it's about 200 words and worth reading.

**A GitHub Action enforces DCO on every PR.** Unsigned commits will block the merge.

### CLA (Contributor License Agreement) — optional, recommended for substantial contributions

For most contributions (typo fixes, single-file bug fixes, doc improvements), DCO is sufficient.

For **substantial contributions** (>100 lines, new modules, significant refactors), we recommend signing a CLA that assigns copyright to Freshfield AI Inc. Why?

1. It lets us continue offering the [commercial dual-license](COMMERCIAL_LICENSE.md) without breaking customer agreements.
2. It lets us relicense (for example, to upgrade AGPL v3 to a future AGPL v4 if one ever exists) without tracking down every contributor.
3. It protects you from being personally named in copyright suits later — Freshfield carries the legal exposure.

The CLA is standard text — we'll send it on request. Signing is a 2-minute electronic process. We will not require it retroactively for already-merged contributions.

If you prefer NOT to sign a CLA, that's fine — your contribution stays under the file's existing license (Apache 2.0 or AGPL v3) and we work within those rights.

---

## Code style and conventions

### Rust (`cell/gateway/src/`)

- `cargo fmt` before commit (rustfmt with default settings)
- `cargo clippy` should produce no new warnings on your changes
- Public functions get rustdoc comments (`///`)
- Security-relevant changes (host_* FFIs, license validation, sandbox boundaries) require a [SECURITY.md](SECURITY.md) Rule 7 review

### Python (`cell/sdk/synapse/`)

- Black formatter, line length 100
- Type hints required for public functions
- Docstrings for public functions and classes
- `python -m pytest cell/sdk/tests/` should stay green
- The supported Python subset for guest execution is documented at `cell/gtm/customer-docs/python-subset.md` (forthcoming) — do NOT silently expand it; that's a contract change

### TypeScript (`cell/sdk/js/`)

- Prettier with project config
- ESLint should produce no warnings
- Type-safety mandatory (no `any` without justification comment)

### Commit messages

Conventional commits required:

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation only
- `tests:` test additions / refactors
- `bench:` benchmark changes
- `sdk:` SDK-specific
- `launch:` launch-related (landing page, marketing, Show HN)
- `refactor:` no behavior change
- `chore:` build / tooling

Subject line under 70 characters. Body explains the **why**, not just the what.

Co-author line for AI-assisted contributions:

```
Co-Authored-By: <model name and version> <noreply@anthropic.com>
```

(or other model provider's noreply address, as appropriate.)

---

## Pull request process

1. **Fork the repo** and create a feature branch from `main`: `git checkout -b feat/your-thing`.
2. **Make your changes** with DCO-signed commits. Keep PRs focused — one logical change each.
3. **Run the tests locally**:
   ```bash
   # Rust gateway
   cd cell/gateway && cargo check && cargo test

   # Python SDK
   python3 cell/sdk/tests/stress_test.py --quick

   # Military Audit (security guards)
   python3 cell/run_test.py
   ```
4. **Open a PR** with:
   - A clear title (conventional commit format)
   - A description that explains the why
   - Cross-links to any related issue
   - A test plan section (what you tested and how)
5. **Respond to review comments** — we may push back, ask for changes, or request additional tests. Be patient and engage substantively.
6. **Squash before merge** if asked — we prefer clean, atomic commit history.

---

## What we will NOT accept (or will heavily push back on)

- Changes that **strip or weaken security guards** (path canonicalization, SSRF shields, OOM caps) — see [SECURITY.md](SECURITY.md) Rule 6
- Changes that **break the supported Python subset contract** without an explicit RFC discussion
- New `host_*` FFIs without a [SECURITY.md](SECURITY.md) Rule 7 threat model
- Reintroducing Z3 to the commercial Cell gateway (lives in research swarm; see [JC-001](journals/JC-001_z3_purge_and_wasmtime_drift.md))
- Changes that move us **away** from manifesto-aligned licensing (Apache 2.0 / AGPL v3) — those need a separate manifesto-level conversation
- Hardcoded secrets or test API keys committed to the repo (a pre-commit hook will block these)
- Changes that introduce new third-party dependencies without justification — minimize the dep tree

---

## Recognizing contributors

We credit contributors in:
- The git history (DCO signatures + commit authorship)
- A `CONTRIBUTORS.md` file (forthcoming) for substantial contributions
- Release notes when your work ships
- Public thank-yous on social media and in newsletters

If you'd prefer NOT to be publicly credited, say so in your PR and we'll respect it.

---

## A note on AI-assisted contributions

Most of Cell was developed with Claude Code (and other AI assistants) by Mike Mazur, a non-technical solo founder. We welcome AI-assisted contributions — just:

1. **You take responsibility** for what you submit. The model generated it; you're vouching for it.
2. **Run the tests.** AI-generated code that doesn't pass `cell/run_test.py` won't pass review.
3. **Use the Co-Authored-By line** so the model's contribution is honestly attributed.
4. **Don't submit volume for volume's sake.** A 50-line focused fix is worth more than a 500-line "I asked Claude to refactor this whole module" PR.

---

## Where to ask questions

- **General questions:** GitHub Discussions (when set up) or hello@synapserun.dev
- **Sales / commercial license:** sales@synapserun.dev
- **Security:** security@synapserun.dev (do NOT use public channels)
- **Trademark / brand:** trademark@synapserun.dev

---

## Pointers

- [README.md](README.md) — what Cell is
- [CLAUDE.md](CLAUDE.md) — full agent guide (also useful for human contributors)
- [SECURITY.md](SECURITY.md) — non-negotiable security rules
- [TRADEMARK.md](TRADEMARK.md) — brand policy
- [MANIFESTO.md](MANIFESTO.md) — values that inform our decisions
- [ROADMAP.md](ROADMAP.md) — where we're going
- [LICENSE](LICENSE) and [LICENSE-AGPL.md](LICENSE-AGPL.md) — the legal foundation
