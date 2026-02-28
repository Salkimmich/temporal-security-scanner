# Encryption with Temporal: How and Why

This document explains how to set up payload encryption for a Temporal workflow, the decisions and considerations involved, and what is **good enough for testing** versus **good enough for production**. It is based on [Temporal’s Payload Codec](https://docs.temporal.io/payload-codec), [Python converters and encryption](https://docs.temporal.io/develop/python/converters-and-encryption), [production data encryption](https://docs.temporal.io/production-deployment/data-encryption), and [key management](https://docs.temporal.io/key-management) documentation. An **expert perspective** section covers threat model, authenticated encryption, and what encryption does *not* protect against.

---

## Why encrypt at all?

By default, Temporal stores all workflow and activity **inputs and outputs** in its event history in **plaintext**. Anyone with access to the Temporal server (or its database) can read:

- Workflow arguments (e.g. org name, GitHub token)
- Activity arguments and results (e.g. repo names, scan findings)
- Query results, signal payloads, memo, and more

For a security scanner, that means API tokens and compliance data are visible at rest. Temporal does **not** add encryption over payloads; encryption is the client’s responsibility. If you have sensitive data, you should implement a **Payload Codec** so that data is encrypted before it leaves your client and worker, and only decrypted on processes you control.

---

## How Temporal’s encryption fits in

Encryption is implemented as a **Payload Codec**: a component that transforms **bytes → bytes** (e.g. encrypt or compress) on the way to the server, and reverses it on the way back. It sits **between** your application and the wire:

```
Your code (Python objects)
    → Data Converter (objects → Payload bytes, e.g. JSON)
    → Payload Codec (bytes → encrypted bytes)   ← you implement this
    → gRPC → Temporal Server (stores only encrypted bytes)
```

The server never sees the key or the plaintext. Only the **client** (starter) and **worker** use the codec; they must share the same key and codec logic so that one can decrypt what the other encrypted.

**What gets encrypted** when you use a custom Payload Codec (with the default Data Converter):

- Workflow input and output
- Activity input and output
- Query inputs and results
- Signal inputs
- Memo
- Local activity and side-effect results (when applicable)

**What does *not* get encrypted** (by design):

- **Search attributes** — Used for server-side indexing and filtering. Never put secrets here; they bypass the codec.
- **Workflow type name, activity names, task queue** — Part of the “envelope” the server needs to route and execute.
- **Failure messages and stack traces** — Stored in Failure payloads. To encrypt these too, you must configure the **Failure Converter** with encoding for common attributes (see your SDK docs). Many teams leave failure details unencrypted in development for easier debugging and add a Codec Server or encrypted failure converter for production.

---

## Good enough for testing

For local development, demos, and tests, the goal is to **prove the pattern** without operating a KMS or Codec Server.

**What this project does (testing-style setup):**

- **Algorithm:** Fernet (AES-128-CBC + HMAC-SHA256). It’s a single, well-understood recipe from the `cryptography` library; hard to misuse and provides authenticated encryption.
- **Key:** A single symmetric key. Either:
  - Set `TEMPORAL_ENCRYPTION_KEY` in the environment (same value on client and worker), or
  - Omit it and use the **hardcoded dev key** in `temporal/encryption.py` so worker and starter work without any config.
- **Where the key lives:** Env var or the well-known dev key. No key rotation; one key for all data.
- **Web UI / CLI:** Payloads show as `[binary/encrypted]`. You do **not** run a Codec Server; you accept that the UI cannot show decrypted payloads.

This is **appropriate for testing** because:

- You can run the demo with zero config (dev key).
- You verify that the server only stores ciphertext and that client and worker can encrypt/decrypt.
- You do **not** need to manage keys, rotate them, or expose a Codec Server.

It is **not appropriate for production** because:

- The dev key is in source code; anyone with repo access can decrypt.
- No key rotation; key compromise means all history is readable if you don’t re-encrypt (which Temporal does not do for you).
- No separation of key management (e.g. per-environment or per-namespace keys).
- Failure messages (and similar) remain unencrypted unless you add a failure converter.

---

## Good enough for production

For production, Temporal’s docs and key-management guidance point to:

**1. Strong algorithm and key size**

- Use an AES-based algorithm (e.g. AES-256-GCM). Temporal’s samples often use AES-GCM with 256-bit keys.
- Symmetric encryption is typical; it’s faster and produces smaller payloads than asymmetric.

**2. Envelope encryption and a Key Management Service (KMS)**

- **Data Encryption Key (DEK):** Used to encrypt payloads (e.g. per-workflow or per-batch). Generated or cached in your app.
- **Key Encryption Key (KEK):** Stored in a KMS (e.g. AWS KMS, GCP KMS, HashiCorp Vault). The DEK is encrypted with the KEK and stored or passed alongside your infrastructure.
- Benefits: The KMS manages the KEK; you can rotate the KEK without re-encrypting all workflow history. Only DEKs need to be re-wrapped. NIST and Temporal recommend key rotation; envelope encryption makes it feasible.

**3. Key storage and loading**

- Store keys like other secrets (e.g. env vars in a secure runtime, or fetch from Vault/KMS at startup).
- Prefer loading the key (or DEK) into the process so you don’t need a network call on every encode/decode.
- Use different keys per environment or namespace when possible.

**4. Key rotation**

- NIST 800-38D suggests rotating AES-GCM keys before about 2^32 encryptions per key version. Plan rotation frequency from your encryption rate.
- Your Payload Codec must support **multiple key versions**: decode with old keys for existing history, encode with the current key. Temporal does not rotate payloads for you; the codec must accept “version” or “key id” in metadata and select the right key on decode.

**5. Codec Server (optional but common in production)**

- A **Codec Server** is an HTTP service you run that uses the **same** codec logic (and keys) as your workers. It exposes a `/decode` endpoint (and optionally `/encode`).
- The Temporal Web UI and CLI can call this endpoint to decode payloads on demand. Payloads stay encrypted on the server; decoding happens in your Codec Server, which you secure and operate.
- Use this when operators need to inspect workflow inputs/results in the UI or CLI without logging into workers. You must secure the Codec Server (auth, network, CORS) and consider latency and key access. See [Codec Server](https://docs.temporal.io/codec-server) and [production data encryption](https://docs.temporal.io/production-deployment/data-encryption).

**6. Failure converter**

- To avoid leaking sensitive data in failure messages and stack traces, configure the Failure Converter to encode (e.g. encrypt) common attributes. Without this, failures remain plaintext in event history.

**7. What to encrypt**

- Encrypt all payloads that contain secrets or PII (workflow/activity I/O, signals, queries, memo as needed). Rely on the codec for the main payload path; add failure converter encoding if you need failures encrypted too.
- Never put secrets in search attributes; they are not passed through the codec.

---

## Decisions and considerations (summary)

| Decision | Testing (this demo) | Production |
|----------|---------------------|------------|
| **Algorithm** | Fernet (AES-128-CBC + HMAC) | AES-256-GCM or similar; often via official samples |
| **Key source** | Env var or hardcoded dev key | KMS (Vault, AWS KMS, GCP KMS); envelope encryption with DEK/KEK |
| **Key rotation** | None | Supported in codec (versioned keys); rotate per NIST / policy |
| **Codec Server** | Not used | Use if operators need to view decoded payloads in UI/CLI |
| **Failure messages** | Plaintext (easier debugging) | Encode via Failure Converter if sensitive |
| **Search attributes** | Never contain secrets | Same; never put secrets in search attributes |

---

## How this repo implements it

- **Code:** `temporal/encryption.py` defines a `PayloadCodec` that uses Fernet. The worker and starter both use `dataclasses.replace(temporalio.converter.default(), payload_codec=EncryptionCodec())` when connecting.
- **Key:** Read from `TEMPORAL_ENCRYPTION_KEY` if set; otherwise the dev key. Worker and starter must use the same key.
- **Scope:** All workflow/activity payloads (inputs and results) go through the codec; search attributes and default failure payloads do not.

For a production-style implementation, you would:

- Replace the single Fernet key with an envelope scheme (DEK + KEK from a KMS).
- Add key versioning in the codec and rotate keys over time.
- Optionally run a Codec Server and point the Web UI/CLI at it.
- Optionally configure the Failure Converter to encode failure attributes.

---

## Expert perspective: threat model, AE, and caveats

This section is written for security and crypto reviewers: it makes the threat model explicit, justifies algorithm choices, and states what this encryption does *not* protect against.

### Threat model

**What we protect against:**

- **Storage / database access** — Anyone with read access to the Temporal server’s persistence (DB, logs, backups) sees only ciphertext. No key, no plaintext. This includes cloud provider staff, DBAs, and an attacker who exfiltrates the database.
- **Server-side inspection** — The Temporal server never has the key. It cannot decrypt payloads for logging, analytics, or support. Hold-your-own-key (HYOK) means the platform cannot read your data.

**What we do *not* protect against:**

- **Compromised client or worker** — If an attacker controls a process that has the key (starter or worker), they can decrypt any payload that process handles. Protect the key and the hosts that hold it (hardening, access control, secrets management).
- **Key theft** — If the key is exfiltrated (env dump, secrets manager breach, logs), all ciphertext encrypted under that key is at risk. Rotation and envelope encryption limit blast radius and allow recovery.
- **In-memory exposure** — Plaintext and key exist in process memory during encode/decode. Memory dumps, debuggers, or cold-boot attacks can expose them. This is a general limitation of application-level encryption; use secure defaults (e.g. lock memory where supported) and restrict who can attach to processes.
- **Traffic between client/worker and server** — The codec encrypts payloads at the application layer; transport security (TLS) is separate. Ensure gRPC uses TLS in production so the wire is also protected.

Defining this clearly avoids overclaiming (“encryption makes everything safe”) and underclaiming (“encryption is useless if the server is compromised” — the server never has the key).

### Authenticated encryption (AE)

We use **Fernet**, which provides **authenticated encryption**: confidentiality (AES-128-CBC) plus integrity (HMAC-SHA256 over the ciphertext). Decryption verifies the MAC before returning plaintext; tampered or corrupted ciphertext causes a failure instead of silent wrong output.

**Why this matters:**

- **Unauthenticated encryption** (e.g. raw AES-CBC without a MAC) is vulnerable to **tampering**: an attacker who can modify ciphertext can sometimes alter plaintext (e.g. bit-flipping in CBC) or learn information. Do not use encryption without authentication for sensitive data.
- Fernet’s **encrypt-then-MAC** construction (ciphertext then HMAC) is a standard, NIST-aligned approach. The Python `cryptography` library handles IV and MAC; you do not implement them yourself, which reduces misuse (e.g. IV reuse, forgetting to verify).

For **production**, Temporal’s samples often use **AES-256-GCM**, which is AEAD (authenticated encryption with associated data). GCM is widely used and hardware-accelerated; nonce (IV) reuse with the same key is catastrophic, so use a cryptographically secure nonce (e.g. random 96-bit) per encryption and never reuse. Fernet avoids that by baking a timestamp into the token and deriving IV from it in a structured way.

### IV / nonce and key handling

- **Fernet** — Each encrypted token includes a version, timestamp, and IV; the format ensures uniqueness per encryption. Keys are 32-byte (256-bit) URL-safe base64; they are used directly (no password-based KDF in this demo). For production, if you derive keys from a password or KMS response, use a proper KDF (e.g. HKDF) with a salt and context.
- **AES-GCM in production** — Use a unique 96-bit nonce per encryption under a given key. Many implementations use a random nonce; store or transmit it with the ciphertext (e.g. first 12 bytes). Key rotation and versioning in the codec let you support multiple keys; decode uses the key id in metadata to select the right key.

### What encryption does not do

- **Access control** — Encryption protects data at rest from the server and storage. It does not replace authentication and authorization for who can start workflows, query, or signal. Enforce those in your application and (for Codec Server) at the HTTP layer.
- **Audit** — Encrypted payloads cannot be audited by the Temporal server. If you need audit trails of “who saw what,” that must happen in your Codec Server or in applications that decode with proper logging and access checks.
- **Compliance by itself** — PCI-DSS, HIPAA, etc. often require encryption at rest *and* key management (rotation, access control, audit). A single long-lived key in an env var is usually insufficient for compliance; use a KMS and envelope encryption and document key lifecycle.

### Data sovereignty: is encryption a good or bad way to fix it?

**Sovereignty / residency requirement:** Sensitive data from one country must not be moved or stored on another nation's servers. Can encryption satisfy that?

**Short answer:** Encryption is a **useful tool** when combined with **key sovereignty** (keys never leave the originating jurisdiction), but it is **not a substitute** for data residency where the law or policy requires that the data — or any copy of it — must not be stored abroad. Whether encryption "fixes" sovereignty depends on how the jurisdiction defines "data" and "transfer," not on cryptography alone.

**Why encryption alone is a bad guarantee:**

1. **The bits still live in another country.** When you encrypt payloads and send them to a Temporal server (or any cloud) in nation B, the **ciphertext** is stored in nation B. Encryption does not move the storage location; it only changes whether the content is readable without the key. Many sovereignty regimes care about **where the data resides** — and in some jurisdictions, encrypted data is still considered "data" or "personal data" that is subject to residency rules. You cannot claim "we don't store Country A's data in Country B" if the ciphertext is in Country B, unless the law explicitly treats "encrypted and key-held-elsewhere" as not being a transfer or not being data in that location.

2. **Legal interpretation varies.** Some regulators or contracts accept that if the **key** never leaves the sovereign jurisdiction and only ciphertext is stored abroad, then "readable data" never leaves — so no sensitive data is "stored" in the other nation in a meaningful sense. Others take the view that any copy of the data (including ciphertext) is a transfer or storage. Relying on encryption without a clear legal/contractual basis is risky.

3. **Metadata and envelope are not encrypted.** Workflow names, activity names, task queues, timestamps, and (in our setup) search attributes are visible to the operator of the server. That can reveal which workflows ran, when, and sometimes enough to infer sensitive context. Sovereignty or "no data abroad" may still be violated in the eyes of a regulator if this metadata is considered data.

4. **Future risk.** Keys can be compromised, or a future legal order might require you to hand over keys. If ciphertext is in another nation, that nation could then gain access to readable data. Encryption does not eliminate that risk; it only keeps the *current* operator of the server from reading the data.

**When encryption helps (as part of a broader approach):**

1. **Key sovereignty.** If the **encryption key is held only in the sovereign jurisdiction** (e.g. KMS in Country A, workers and Codec Server in Country A), then the Temporal server in Country B only ever sees ciphertext and never has the key. You can argue that "readable sensitive data never leaves Country A" and that only unintelligible ciphertext is stored abroad. That is a **legal and policy argument**; get it validated for your jurisdiction and contracts.

2. **Defense in depth.** Even when you use data residency (Temporal and DB in the same country as the data), adding encryption means that if the database or backup is ever moved or exposed, the content is still protected. Sovereignty is then achieved by **where you store** plus **how you protect** the copy.

3. **Temporal's model fits key sovereignty.** With a PayloadCodec, the Temporal server (including Temporal Cloud in another region) never has your key. Client and workers in your jurisdiction do the encrypt/decrypt. So you *can* run Temporal in another country and still keep the **ability to read** the data only in your country — as long as you accept that **ciphertext** resides in that other country and that your legal/regulatory position allows that.

**What actually achieves sovereignty guarantees:**

| Approach | What it does | Limitation |
|----------|----------------|------------|
| **Data residency / localization** | Run Temporal (and its persistence) in the same country or region as the data. No ciphertext in another nation. | You must have or use a Temporal deployment in that jurisdiction. |
| **Encryption + key sovereignty** | Encrypt with a key that never leaves the sovereign jurisdiction; store ciphertext wherever. Argue that only ciphertext is abroad. | Depends on law and regulator; metadata still visible; not a substitute for residency where "any copy" is prohibited. |
| **No cross-border use** | Do not send the data to another country at all (no Temporal cluster, no cloud DB there). | Strong guarantee; may limit where you can run workloads. |

**Recommendation:** For strict "no sensitive data in another nation's server" guarantees:

- **First:** Prefer **data residency** — run Temporal and its storage in the same jurisdiction as the data. That satisfies "data is not stored in another nation" without relying on a legal interpretation of encrypted data. See [SOVEREIGNTY.md](SOVEREIGNTY.md) for how to achieve this with Temporal (namespace region, self-hosted, workers in-region).
- **Then:** Add **encryption with key sovereignty** (keys only in-region) as defense in depth and to support the argument that even if any copy were ever outside the region, it would be unreadable there.
- **Always:** Get legal or compliance approval for any "encryption only" or "key sovereignty" argument; do not assume encryption alone satisfies sovereignty in your context.

---

### Codec Server: security when decoding remotely

If you run a **Codec Server** so the Web UI or CLI can decode payloads:

- The server **holds decryption capability**. Treat it as a high-value target: authenticate and authorize every request, restrict the network (e.g. not on the public internet), and log access. Prefer short-lived tokens or mTLS.
- Use **CORS** and **Authorization** headers as required by Temporal’s Codec Server contract; do not expose `/decode` without auth.
- Keys used by the Codec Server should be the same as (or derived from) the worker’s key material, but access to the Codec Server should be limited to operators who are allowed to see decrypted payloads. Separation of duties: workers need keys to run workflows; operators need the Codec Server to inspect history.

### Decode pass-through and backwards compatibility

In this repo, `decode()` only decrypts payloads whose `encoding` metadata matches our marker (`binary/encrypted`). All other payloads are **passed through unchanged**. That allows workflows created before encryption was enabled (or from another codec) to still be read. For security reviewers: if event history contains a mix of encrypted and unencrypted payloads (e.g. after a partial rollout or a misconfiguration), unencrypted payloads will be returned as plaintext. In production, ensure all writers use the codec and consider failing closed (reject or log) on unexpected encoding values if you never expect legacy unencrypted data.

### Summary for reviewers

| Aspect | This demo (testing) | Production recommendation |
|--------|----------------------|---------------------------|
| **Threat model** | Protect payloads from Temporal server / DB access | Same; add protection for key (KMS, access control) and Codec Server |
| **Algorithm** | Fernet (AES-128-CBC + HMAC-SHA256), authenticated | AES-256-GCM or equivalent AEAD; avoid unauthenticated encryption |
| **IV/nonce** | Handled by Fernet (unique per token) | Unique per encryption; no nonce reuse under same key |
| **Key** | Single key; env or dev default | Envelope encryption; KEK in KMS; DEK per use or per namespace; rotation |
| **What we don’t claim** | No protection for compromised client/worker, key theft, or in-memory dumps | Same; document and mitigate via ops and key lifecycle |

---

## References

- [Payload Codec](https://docs.temporal.io/payload-codec) — What a codec is and how it fits in.
- [Converters and encryption (Python)](https://docs.temporal.io/develop/python/converters-and-encryption) — Custom Payload Codec and Codec Server in Python.
- [Production data encryption / Codec Server](https://docs.temporal.io/production-deployment/data-encryption) — What to encrypt and how to run a Codec Server.
- [Key management](https://docs.temporal.io/key-management) — Key storage, rotation, and KMS (e.g. Vault).
- [Temporal Python encryption sample](https://github.com/temporalio/samples-python/tree/main/encryption) — Reference implementation.
- NIST SP 800-38D (GCM), 800-38A (CBC) — Modes of operation; 800-57 (key management).
