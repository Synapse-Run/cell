# Synapse Cell — Commercial License

**Issuer:** Freshfield AI Inc. (Canada) · **Contact:** sales@synapserun.dev

This document describes the **commercial dual-licensing option** for organizations that need to use Synapse Cell's Pro or Hub features without complying with the AGPL v3 distribution requirements.

---

## When you need a commercial license

You **do NOT** need a commercial license if any of these is true:

- You're an individual developer building a hobby project
- You're a non-profit, academic institution, or research group
- You're a commercial organization that runs Cell internally and your usage stays internal (AGPL allows this — internal use does not trigger distribution)
- You're a commercial organization willing to release any modifications you make to Cell Pro or Hub source code under AGPL v3

You **may need** a commercial license if any of these is true:

- You want to offer Cell as a hosted service to third parties (the AGPL "SaaS clause" requires you to release your entire deployment stack as AGPL — usually undesirable for commercial offerings)
- You want to embed Cell Pro or Hub into a proprietary product without releasing the proprietary product's source code
- You want to combine Cell Pro or Hub with other AGPL-incompatible code (e.g., a proprietary plugin system)
- Your enterprise legal team has a blanket policy against AGPL-licensed dependencies

If you're not sure whether you need one, email sales@synapserun.dev — we'll talk it through honestly. We will not pressure you into a license you don't need.

---

## What a commercial license grants

The commercial license is a separate, signed agreement that **replaces** the AGPL v3 obligations for the licensed components, granting you:

1. **Permission to combine Cell Pro / Hub with proprietary code** without triggering AGPL's source-disclosure requirements.
2. **Permission to offer Cell-derived hosted services** to third parties without open-sourcing your deployment infrastructure.
3. **Standard commercial terms**: warranty disclaimers, limitation of liability, indemnification (mutual), term and termination clauses, governing law (Province of Ontario, Canada by default; negotiable for cross-border deals).

The commercial license does **not** change Apache 2.0–licensed Core components — those remain freely usable under their original Apache 2.0 grant regardless of any commercial agreement.

---

## Indicative pricing

Final pricing is set by a signed agreement, but indicative ranges (CAD):

| Tier | What's included | Price band |
|------|-----------------|------------|
| **Cell Pro Commercial** | AGPL opt-out for Pro FFIs (`load_weights`, `host_fetch`, `host_fs_*`, `host_mcp_call`, GPU ops); per-node deployment; standard email support | **$12,000–24,000 / year per organization** (volume discounts available) |
| **Synapse Hub Commercial** | AGPL opt-out for Hub fleet dashboard, cryptographic receipts UI, Atlantic Handshake federation, RBAC, license server; SLA; quarterly business review; air-gap deployment support | **$50,000–250,000 / year** depending on scale, sector, and SLA tier |
| **Cell Custom Engineering** | Custom FFI development, sector-specific compliance work (OSFI E-23, EU AI Act, HIPAA), security audits, integration consulting | Time-and-materials at **$300–500 CAD / hour**, or fixed-bid by scope |

These ranges deliberately overlap with what enterprise software vendors charge for comparable products (GitLab EE, Mattermost Enterprise, Sourcegraph Cloud). We are not a discount play — Cell's price reflects its compliance-grade differentiators (sovereign jurisdiction, cryptographic receipts, AGPL-protected commons, audit-grade trust registry membership).

---

## What we will NOT do

To keep faith with the [Sovereign Compute Manifesto](MANIFESTO.md):

- We **will not** license Cell to fossil fuel majors (oil and gas exploration / production), surveillance-state intelligence agencies, weapons manufacturers, or organizations subject to enforced sanctions for human-rights violations under the UN framework. This is an editorial policy of Freshfield AI Inc., not an AGPL clause — see the [Tiered Trust Registry exclusion list](docs/2026-04-15-cell-commercial-setup-plan.md) for the full reasoning.
- We **will not** issue silent patches for security vulnerabilities, NDA security researchers, or delay public disclosure beyond the [SECURITY.md](SECURITY.md) timelines for marketing reasons.
- We **will not** introduce vendor lock-in through proprietary file formats, undocumented APIs, or closed protocols. Customer data is always portable.
- We **will not** charge individuals, non-profits, or academic users to access Cell. AGPL v3 grants those rights freely; a commercial license is opt-in for organizations that need it.

---

## How to get a commercial license

1. Email **sales@synapserun.dev** with: your organization name, intended use case (one paragraph), expected scale (number of nodes, regions, sandbox volume), and any specific compliance requirements (jurisdiction, sector, certification needs).
2. We respond within **2 business days** with: a recommended tier, indicative price, draft terms, and any clarifying questions.
3. Mutual NDA (if requested) → final terms → signed agreement → license certificate issued (Ed25519 signed, see [SECURITY.md](SECURITY.md) §10.3 of the plan for the technical architecture).
4. Onboarding: kickoff call, deployment review, support contact established.

Typical time from first email to license issued: **1–4 weeks**, depending on legal review on your side.

---

## Why this dual-license model exists

Synapse Cell is open source (Apache 2.0 Core, AGPL v3 Pro/Hub) because [our manifesto](MANIFESTO.md) commits us to "compute for the commons" and rejects "the privatization of human knowledge." We genuinely want individuals, non-profits, and self-hosting organizations to use Cell freely, forever.

The commercial license exists because Freshfield AI Inc. is bootstrapped, solo-founder, and needs revenue to keep developing the commons. The asymmetry is deliberate: **community gets the software for free; organizations that want to extract value without contributing back pay for the privilege**. This is the same model used by Plausible Analytics, Mattermost, Matomo, MariaDB Enterprise, and others.

If you find this approach reasonable, **buying a commercial license is the most direct way to support continued development of the open-source commons**. Thank you.

---

## Pointers

- **Free tier (Apache 2.0 + AGPL v3):** [LICENSE](LICENSE) and [LICENSE-AGPL.md](LICENSE-AGPL.md)
- **Trademark policy:** [TRADEMARK.md](TRADEMARK.md) — Apache 2.0 grants source rights; trademark protects the name
- **Manifesto:** [MANIFESTO.md](MANIFESTO.md) — what we believe and why we license this way
- **Security:** [SECURITY.md](SECURITY.md) — non-negotiable security rules, vulnerability disclosure
- **Roadmap:** [ROADMAP.md](ROADMAP.md) — where Cell is going
