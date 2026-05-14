# The Sovereign Compute Manifesto — Cell edition

Synapse Cell exists because of a set of values, not the other way around. Every decision we make — what we license, who we sell to, how we price, what we refuse — is downstream of **The Sovereign Compute Manifesto**, a values document maintained by Freshfield AI Inc.

This file captures the values that drive Cell specifically — what we'll license, who we'll sell to, what we'll refuse — so anyone landing here can see them without external lookups.

---

## The four lines that pin everything

> *"We reject the privatization of human knowledge and the ecological destruction of the corporate data center. We compute for the commons."*

> *"AI must be a ubiquitous, free public utility, as fundamental and accessible as mathematics."*

> *"Let them keep the syntax. We are taking the physics."*

> *"Physics and mathematics cannot be copyrighted."*

These aren't slogans. They drive concrete decisions:

| Decision | Driven by |
|----------|-----------|
| Cell is **AGPL v3** (not BSL or Elastic License) | "Compute for the commons" — billionaires must contribute back or pay |
| EdgeCell tier is **free, forever, with no usage cap on self-host** | "AI must be a ubiquitous free public utility" |
| Trust-registry exclusion list explicitly bans fossil-fuel majors and surveillance-state contractors | "Ecological destruction" + UN Universal Declaration of Human Rights |
| Cryptographic receipt fingerprinting (compiler hash + license id in every receipt) | "Physics and mathematics cannot be copyrighted" — the moat is verification, not gatekeeping |
| Server placement in Hetzner Helsinki + Hetzner Ashburn (NOT US-only AWS) | "Sovereign compute" — Canadian / EU jurisdiction, no US CLOUD Act exposure |
| Native `.syn` Wasm execution (NOT PyTorch / CUDA) | Direct rejection of Microsoft/Nvidia/cloud monopoly stack |
| Bootstrapped, no VC | "Reject the privatization of human knowledge" — VCs would pressure rent-extraction |

If you're asked to make a change to Cell that contradicts these driving principles, **escalate before doing it**. The manifesto outranks any individual ticket or feature request.

---

## What this means in practice for contributors

Apply the **Manifesto Test** to any monetization, licensing, or product idea:

> *"Is a small individual, non-profit, or community user charged for access? If yes, it violates the manifesto. If no, it's fine."*

Under this test:

- ✅ Charge for managed hosting (convenience, not access — they can self-host)
- ✅ Charge for support contracts (your time)
- ✅ Charge corporations a commercial license to opt out of AGPL (extraction without contribution)
- ✅ Charge enterprises for the trust registry (compliance-grade trust is a service)
- ✅ Charge for professional services and custom FFI work
- ❌ Free tier with crippling features designed to force upgrade
- ❌ Closed-source code that prevents self-hosting
- ❌ Rate limits that hurt non-commercial users
- ❌ Vendor lock-in via undocumented file formats

This filter keeps us honest as we scale.

---

## What we refuse, explicitly

From the Sovereign Compute Manifesto and the Synapse research arm's "What We Refuse" list:

- **No PyTorch in production.** Research and quantization only. The serving path is Rust → WGSL → Wasm.
- **No CUDA.** We use WGSL (WebGPU). Runs on Metal, Vulkan, and browsers. No NVIDIA lock-in.
- **No cloud-only architecture.** Must deploy on a single Hetzner box or a laptop.
- **No VC metrics.** Optimize for speed, efficiency, and integrity — not user count.
- **No marketing language in technical docs.** Data, methodology, and qualified claims only.
- **No silent security patches.** Vulnerabilities are disclosed publicly within 14 days of patch (high-severity) or 30 days (medium). See [SECURITY.md](SECURITY.md).
- **No NDA on security researchers.** Researchers who report vulnerabilities are credited (and paid bounties when material), not silenced.

---

## What this means for licensing in particular

Cell uses a **deliberate three-layer license split**:

| Layer | License | Why |
|-------|---------|-----|
| **Core** (runtime, .syn compiler, SDKs) | [Apache 2.0](LICENSE) | Maximum commons. Matches "physics cannot be copyrighted." |
| **Pro** (heavy FFIs) | [AGPL v3](LICENSE-AGPL.md) + [commercial dual](COMMERCIAL_LICENSE.md) | Corporations must contribute modifications or pay |
| **Hub** (fleet, dashboard, federation) | [AGPL v3](LICENSE-AGPL.md) + [commercial dual](COMMERCIAL_LICENSE.md) | Same — open to commons, paid opt-out for closed extraction |

We chose AGPL specifically because of its 2007 design intent: **close the SaaS loophole that GPL left open**. AWS literally cannot offer Cell as a hosted service without releasing their entire AWS-Cell deployment stack. That's the manifesto enforced through legal text.

---

## What this means for who we sell to

The trust registry (forthcoming) and [commercial license](COMMERCIAL_LICENSE.md) editorial policy together exclude:

- **Fossil fuel majors** (oil and gas exploration / production at scale)
- **Surveillance-state intelligence agencies** that violate UN Universal Declaration of Human Rights provisions
- **Weapons manufacturers** (excluded from compliance-grade trust registry; can still run AGPL Cell, just not earn certified trust badge)
- **Entities under enforced sanctions** for human-rights violations under the UN framework

These exclusions are NOT AGPL clauses (AGPL doesn't restrict use by users, only by distributors/SaaS-hosts). They're **editorial choices** by Freshfield AI Inc. about who we contract with, who we register, and who we lend our brand to. We make this distinction explicit because we want to be honest about what kind of company we are.

---

## What this means for our own behavior

This file commits us to:

1. **Be honest in technical claims.** No marketing inflation. Numbers come from reproducible benchmarks. Failures get journal entries.
2. **Respect security researchers.** Acknowledge within 48 hours, fix within 14-30 days, disclose publicly.
3. **Stay accountable to the commons.** When AGPL forces us to give back, we give back enthusiastically — not grudgingly.
4. **Decline business that contradicts the manifesto** even when it would make money.
5. **Document our values changes openly.** If we ever modify what's in this file or the manifestos it points to, that change is itself a public commit with a public rationale.

---

## Pointers

- **How licensing implements this:** [LICENSE](LICENSE), [LICENSE-AGPL.md](LICENSE-AGPL.md), [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md)
- **How brand policy implements this:** [TRADEMARK.md](TRADEMARK.md)
- **How security implements this:** [SECURITY.md](SECURITY.md)
- **How to contribute:** [CONTRIBUTING.md](CONTRIBUTING.md)
