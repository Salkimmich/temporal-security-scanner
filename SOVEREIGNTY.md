# Sovereignty Architecture with Temporal

**When to use this doc:** You need to guarantee that sensitive data from one country or region is never stored or processed on another nation's servers. This page explains **how to do that with Temporal** — which levers to use (Cloud region, self-hosted, workers, namespaces) and in what order. For *why* encryption alone isn't enough, see [ENCRYPTION.md](ENCRYPTION.md).

This document describes **how to keep Temporal workflow data and processing within the same region or country** so you can meet data sovereignty and residency requirements. It complements [ENCRYPTION.md](ENCRYPTION.md) (which explains why encryption alone is not a substitute for residency).

**Goal:** Sensitive data from one country or region must not be stored or processed on another nation's or region's servers. Temporal gives you several levers to achieve this.

---

## Why residency first

Sovereignty and residency rules are usually about **where data lives** and **who has jurisdiction**. Encryption protects **who can read** the data; it does not change **where the bits are stored**. For strict "no data in another nation" guarantees, you need to run Temporal (and its persistence) in the same jurisdiction as the data. See [ENCRYPTION.md — Data sovereignty: is encryption a good or bad way to fix it?](ENCRYPTION.md#data-sovereignty-is-encryption-a-good-or-bad-way-to-fix-it) for the full argument.

This page focuses on **architectural options** to keep everything in-region.

---

## Ways to keep Temporal in the same region

### 1. Temporal Cloud: choose the namespace region

With **Temporal Cloud**, each **Namespace** is created in a specific **region**. All workflow execution and data for that namespace stay in that region; Temporal does not share data processing or storage across regional boundaries for a given namespace.

- **What you do:** When creating a namespace (or when signing up), select the region that matches your residency requirement (e.g. EU Frankfurt for EU data, Sydney for Australian data).
- **Result:** Workflow history, visibility data, and all Temporal-managed state for that namespace are stored and processed only in that region.
- **Regions:** Temporal Cloud supports multiple AWS and GCP regions (e.g. `aws-eu-central-1`, `aws-ap-southeast-2`, `gcp-europe-west3`). See [Temporal Cloud — Service regions](https://docs.temporal.io/cloud/regions) for the current list.
- **Replication:** If you enable multi-region or multi-cloud replication for failover, replicated data will exist in other regions. For **strict single-region sovereignty**, create the namespace in the desired region and **do not enable replication** to other regions (or confirm with Temporal Cloud that a non-replicated namespace is available for your use case).

**Best for:** Teams using Temporal Cloud who need a clear "this namespace is EU" or "this namespace is Australia" guarantee without operating their own cluster.

---

### 2. Self-hosted Temporal in your region

With **self-hosted Temporal**, you deploy the Temporal server (frontend, history, matching, workers optional) and its **persistence store** (PostgreSQL, MySQL, or Cassandra) entirely within your chosen region or data center.

- **What you do:** Deploy Temporal using the [self-hosted guide](https://docs.temporal.io/self-hosted-guide) (e.g. Kubernetes in EU-west-1 or in your on-prem EU DC). Use a database that also runs in that same region.
- **Result:** No Temporal data leaves your region; you control the entire stack and its location.
- **Workers:** Run workers in the same region (same VPC or network as the Temporal service, or in the same country) so that activity execution and any data they touch also stay in-region.

**Best for:** Organizations that need full control over where every component runs (e.g. government, regulated industry, on-prem-only policies).

---

### 3. Worker placement in the same region as the server

Even if the Temporal server is in the right region, **workers** execute your activity code and may call external APIs or databases. For sovereignty you typically want workers to run in the **same region** as the Temporal server (and as any data sources or sinks they use).

- **What you do:** Deploy workers in the same cloud region (or same country) as your Temporal namespace. Use the same VPC or private connectivity where possible.
- **Result:** Workflow orchestration (Temporal server) and activity execution (workers) both run in-region; no cross-border traffic for execution or data.
- **Temporal Cloud:** Your application can connect to Temporal Cloud from anywhere; for lowest latency and clearest residency story, run workers in the same region as the namespace. See [Temporal Cloud regions](https://docs.temporal.io/cloud/regions): "You will reduce latency by creating Namespaces in a region close to where you host your Workers."

**Best for:** Ensuring that not only workflow state but also the code that reads/writes external systems stays in-region.

---

### 4. Persistence (database) in-region

For both Temporal Cloud and self-hosted deployments, **where the database lives** defines where the data lives.

- **Temporal Cloud:** The namespace region determines where Temporal stores persistence; you don't manage the DB yourself. Picking the right region is sufficient.
- **Self-hosted:** Run PostgreSQL, MySQL, or Cassandra in the same region (or same country) as the Temporal services. Backups should also stay in-region if your policy requires it.

**Best for:** Meeting "data at rest in country X" requirements; often combined with (1) or (2).

---

### 5. Namespace-per-region for multi-region organizations

If your organization operates in **multiple regions** (e.g. EU and US) and each region has its own residency rules, use **one namespace per region** and route traffic accordingly.

- **What you do:** Create namespace `security-scanner-eu` in EU and `security-scanner-us` in US. Your application (or a router) chooses the namespace based on where the data originates or where the user is.
- **Result:** EU data stays in the EU namespace (and thus in the EU region); US data in the US namespace. No mixing in a single namespace.
- **Workers:** Run separate worker pools per region, each polling the namespace for that region.

**Best for:** Global products with regional data boundaries (e.g. GDPR + US data in separate namespaces).

---

### 6. Private connectivity (reduce exposure)

Keeping data in-region also means reducing the path it takes over the network. Temporal Cloud supports **AWS PrivateLink** (and similar patterns) so that traffic between your VPC and Temporal does not traverse the public internet in an uncontrolled way.

- **What you do:** Configure PrivateLink (or equivalent) to the Temporal Cloud endpoint for your namespace's region. Run workers and starters in the same VPC.
- **Result:** All traffic to Temporal stays on private links within the same cloud region; no data on the public internet in transit.

**Best for:** Defense in depth and compliance with "no data on public internet" or "private connectivity only" policies.

---

### 7. No cross-border replication (strict single-region)

If you need **strict single-region** (no copy of data in another country at all):

- **Temporal Cloud:** Create the namespace in the desired region and **do not enable** multi-region or multi-cloud replication for that namespace. Confirm with Temporal that the namespace is single-region only.
- **Self-hosted:** Run a single cluster in one region; do not set up replication to another region or country.

**Best for:** Jurisdictions or contracts that explicitly prohibit any copy of data outside the country/region.

---

## Summary: sovereignty architecture checklist

| Lever | What to do | Outcome |
|-------|------------|---------|
| **Namespace region (Cloud)** | Create namespace in the target region (e.g. EU, APAC). | Workflow data and processing stay in that region. |
| **Self-hosted in-region** | Deploy Temporal + DB in your region/DC. | Full control; no data in another region. |
| **Workers in same region** | Run workers in same region as namespace (and as data sources). | Execution and data access stay in-region. |
| **Persistence in-region** | (Cloud: implied by namespace. Self-hosted: run DB in-region.) | Data at rest in the right jurisdiction. |
| **Namespace-per-region** | One namespace per region; route by data origin or user. | Clear separation for multi-region orgs. |
| **No cross-region replication** | Disable replication for strict single-region namespaces. | No copy of data in another region. |
| **Private connectivity** | Use PrivateLink / private endpoints to Temporal. | No sovereignty data on public internet in transit. |
| **Encryption (defense in depth)** | Use PayloadCodec + key in-region. See [ENCRYPTION.md](ENCRYPTION.md). | If data or backup ever left the region, it would be unreadable. |

---

## Best practices

1. **Decide residency first.** Choose the region (or country) where data is allowed to live, then pick the deployment model (Cloud in that region, or self-hosted there).
2. **Align workers and persistence.** Run workers and the Temporal persistence store in the same region as the namespace. Document this in your architecture and runbooks.
3. **Namespace-per-region when boundaries matter.** For multi-region orgs, do not mix EU and US (or other sovereign) data in one namespace; use separate namespaces and worker pools.
4. **Encryption as defense in depth.** After residency is in place, add payload encryption with keys held only in-region (see [ENCRYPTION.md](ENCRYPTION.md)). This protects against backups or mistakes that might copy data elsewhere.
5. **Validate with legal/compliance.** Sovereignty and residency are legal and contractual concepts. Get sign-off that your architecture (region choice, no replication, worker placement) satisfies your jurisdiction and contracts.

---

## References

- [Temporal Cloud — Service regions](https://docs.temporal.io/cloud/regions) — List of regions and replication options.
- [Self-hosted Temporal Service guide](https://docs.temporal.io/self-hosted-guide) — Deploy Temporal in your own environment.
- [Worker deployments](https://docs.temporal.io/production-deployment/worker-deployments) — Where and how to run workers.
- [ENCRYPTION.md — Data sovereignty](ENCRYPTION.md#data-sovereignty-is-encryption-a-good-or-bad-way-to-fix-it) — Why encryption alone doesn't fix residency; when it helps.
