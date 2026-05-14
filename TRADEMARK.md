# Synapse Cell — Trademark Policy

**Owner:** Freshfield AI Inc. (Canada) · **Contact:** trademark@synapserun.dev

The Synapse Cell software is freely available under [Apache 2.0](LICENSE) (Core) and [AGPL v3](LICENSE-AGPL.md) (Pro and Hub). Source-code licenses grant you rights to the **code**. They do **NOT** grant rights to the **names, logos, or brand identity** that we use to identify our official products. This document explains what's protected, what's allowed, and what requires permission.

This is the same separation Elastic, MongoDB, Redis, Mattermost, and Mozilla all use successfully. We're following well-established practice — not inventing a new restriction.

---

## What's a trademark of Freshfield AI Inc.

The following are our **protected marks**:

| Mark | Type | Scope |
|------|------|-------|
| **Synapse Cell** | Word mark | Code execution / sandboxing software, related services |
| **EdgeCell** | Word mark | Free-tier of Synapse Cell |
| **Synapse Hub** | Word mark | Enterprise tier of Synapse Cell |
| **Synapse** (in software / AI context) | Word mark | Broader product family |
| **The Synapse logo** | Design mark | All visual representations of the Synapse brand |
| **synapserun.dev** | Domain | Official online presence |

Status (as of 2026-04-15):
- CIPO (Canadian Intellectual Property Office) registration: **planned** — see [ROADMAP.md](ROADMAP.md) Horizon 2 milestone 2.8
- USPTO (United States Patent and Trademark Office) registration: **planned** — Horizon 3
- Common-law rights apply in jurisdictions where we've used the marks publicly since 2026-04-15

Even before formal registration, common-law trademark rights apply. Please respect them in good faith — and we'll respect yours.

---

## What you CAN do (no permission needed)

You may freely:

1. **Fork the source code** under its open-source license (Apache 2.0 or AGPL v3, as marked).
2. **Use Synapse Cell internally** at your organization without naming restrictions for your internal documentation.
3. **Refer to "Synapse Cell" by its proper name** in articles, blog posts, comparisons, academic papers, conference talks, social media, and other commentary — including critical commentary. Nominative use is fully permitted.
4. **Distribute unmodified binaries or source** of Synapse Cell using its name (you're distributing the official software — that's what the trademark refers to).
5. **State that your product "uses Synapse Cell"** or "is built on Synapse Cell" — this is descriptive use and is fine.
6. **Create educational content** (tutorials, courses, books, videos) that teach Synapse Cell using its proper name.
7. **Run a community fork or experimental fork** for personal or research use, under a different name (see "What requires renaming" below).

---

## What requires PERMISSION

You may NOT (without our written permission):

1. **Distribute a modified fork of Synapse Cell using the name "Synapse Cell," "EdgeCell," "Synapse Hub," or any confusingly similar name.** Forks must be renamed before public distribution. (Internal modified versions for your own use are fine.)
2. **Offer a hosted service** named "Synapse Cell," "Cell Cloud," "Synapse-as-a-Service," or any name implying official affiliation with Freshfield AI Inc.
3. **Use the Synapse logo** in any context other than referring to our official software (e.g., on your product packaging, your company website's homepage as a product offering, etc.).
4. **Sell merchandise** (t-shirts, mugs, stickers) bearing Synapse marks for commercial purposes.
5. **Register a domain name** containing "synapse-cell," "synapsecell," "synapserun," "synapse-run," "runsynapse," or close variants for purposes other than community resources clearly identified as unofficial.
6. **Imply endorsement, partnership, certification, or official affiliation** with Freshfield AI Inc. without an explicit written agreement.
7. **Claim "E2B parity certification" or "Synapse-verified" for your own products** without participating in our (forthcoming) certification program.

---

## What requires RENAMING (forks intended for public distribution)

If you fork Synapse Cell and modify it for public distribution (commercial or non-commercial), you must:

1. **Rename your fork** — pick a clearly distinct name. Examples that would NOT confuse: "AcmeBox," "WhaleSandbox," "TurtleCell" (just kidding), "SecureRunner-Plus." Examples that WOULD confuse: "Synapse Cell Plus," "EdgeCell-Pro," "Cell Premium."
2. **Remove the Synapse logo** from your fork's documentation, website, and binaries. Replace with your own branding.
3. **Update copyright headers** to reflect your modifications (Apache 2.0 §4(b) requires this anyway).
4. **State clearly** that your product is derived from Synapse Cell, but is not affiliated with or endorsed by Freshfield AI Inc. A line in your README like "Built on Synapse Cell, but not affiliated with Freshfield AI Inc." is a good model.

This is standard open-source practice. Mozilla requires Firefox forks to rebrand (Iceweasel was the most famous example). Elastic requires OpenSearch (the AWS fork) to use its own name. We're following the same playbook.

---

## Why we have a trademark policy at all

Source-code licenses solve part of the problem: anyone can use, modify, and redistribute the code. Trademarks solve the rest: **users need to be able to tell what software they're actually getting**.

If anyone could ship a modified fork called "Synapse Cell," users would have no way to know whether the binary they downloaded:
- Has the official security guards (path canonicalization, SSRF shields)
- Will produce receipts that verify against our canonical compiler fingerprint registry
- Is current with security patches
- Carries no spyware, backdoors, or malicious modifications

Trademark protection lets us guarantee: **anything called "Synapse Cell" came from Freshfield AI Inc. and meets our quality bar.** Forks are welcome — they just have to call themselves something else.

This is also our **primary defense against AWS-style rehosting** of the Apache-licensed Core. AWS can fork the code (Apache 2.0 allows it), but they cannot call their service "Synapse Cell" or use our logo. If they want to use our brand, they negotiate a trademark license. If they don't, they have to brand it as something distinctly their own — at which point they're a competitor, not a vendor capturing our mindshare.

---

## How to ask for permission

Email **trademark@synapserun.dev** with:

1. Your name / organization
2. Exactly which mark you want to use, where, and how
3. The duration (one-off article? ongoing product use? indefinite?)
4. Why you're asking (helps us understand context)

Typical response time: **3–5 business days**.

We grant permission generously for:
- Educational use (courses, books, conference talks, tutorials)
- Compatible-with-Cell badges (for genuinely compatible third-party products)
- Community resources (Discord servers, GitHub orgs, meetups) explicitly identified as unofficial

We are **stricter** about:
- Anything implying endorsement we haven't given
- Hosted services using our marks without a commercial license
- Fork distributions that don't rebrand

---

## If we get this wrong

We're a bootstrapped solo-founder company — we WILL make mistakes. If you think we've overstepped (sent an unwarranted cease-and-desist, etc.), email us first at **trademark@synapserun.dev** and we'll talk. We have no interest in adversarial trademark enforcement; we just want to keep the brand meaningful.

---

## Pointers

- [LICENSE](LICENSE) — Apache 2.0 (covers Core source rights — NOT trademark)
- [LICENSE-AGPL.md](LICENSE-AGPL.md) — AGPL v3 (covers Pro/Hub source rights — NOT trademark)
- [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) — buy out the AGPL distribution requirement
- [MANIFESTO.md](MANIFESTO.md) — values that inform this policy
- [SECURITY.md](SECURITY.md) — non-negotiable security rules forks should also respect
