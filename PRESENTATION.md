# Presentation Talking Points

## "What breaks when your script goes to production: Temporalizing a GitHub Security Scanner"

Target: 14-17 minute walkthrough for Temporal engineers, presented as if speaking to a developer meetup/conference audience. Can trim Go Lens section to hit 15 minutes.

---

## 1. The Pain (2-3 minutes)

**Open with the relatable hook:**

"I maintain security scanning tools for open source foundations. I wrote a Python script that checks whether repos in a GitHub org have secret scanning, Dependabot, and code scanning turned on. It's about 100 lines. It works great — on my laptop, against a 20-repo org. Then I pointed it at an organization with 400 repositories."

**Show `before/scanner.py` and highlight the fragility:**

- "Here's the core loop. For each repo, three API calls. Sequential. No retry logic. If the network blips at repo 150, I start over."
- "See this `sys.exit(1)`? One bad repo kills the entire scan."
- "This `time.sleep(60)` for rate limits? That's hope-driven development."
- "And while this runs — which takes 20 minutes for a large org — I have zero visibility into progress."

**The real story:** "In open source foundations, security compliance isn't optional. When you're scanning hundreds of repos across CNCF, Eclipse, Apache — you need something that survives a laptop lid closing, a network drop, or a worker node recycling."

---

## 2. The Decomposition (3-4 minutes)

**Show the mental model:**

"I asked myself one question: *what has side effects?* Everything that touches the network becomes an Activity. Everything else — the orchestration, the looping, the progress tracking — is the Workflow."

**Walk through the mapping:**

- `fetch_repositories()` → `fetch_org_repos` activity
  - "This paginates through the GitHub API. It can fail, it can rate-limit, it can time out. Perfect activity."

- `check_repo_security()` → `check_repo_security` activity
  - "Three API calls per repo. Each repo is independent. If repo X fails, repo Y shouldn't care."

- The for-loop and progress tracking → Workflow logic
  - "This is pure orchestration. Given these activity results, what do I do next? That's deterministic."

**Show `models.py` briefly:**

"I use dataclasses for all inputs and outputs — that's what the SDK recommends. Clean serialization, easy to extend without breaking compatibility."

---

## 3. The Temporal Version (4-5 minutes)

**Walk through `workflows.py`:**

- "The `@workflow.defn` decorator — this is my workflow class."
- "The `@workflow.run` method — this is the entry point. It's `async def` because Temporal's Python SDK is built on asyncio."
- "Step 1: fetch repos. One activity call with a retry policy."
- **Highlight the retry policy:** "Two seconds initial, exponential backoff, max 5 attempts. But — and this is important — `ValueError` is non-retryable. If the org doesn't exist, retrying won't fix it."
- "Step 2: scan in parallel batches. `asyncio.gather` on 10 repos at a time. Each activity retries independently."
- "Step 3: generate the report."

**Show `@workflow.query`:**

"This is one of my favourite features. At any point — mid-scan — I can query the workflow for progress. From the CLI, from another service, from a dashboard. The workflow maintains this state naturally as it runs."

**Walk through `activities.py`:**

- "These are regular Python functions with a `@activity.defn` decorator."
- "They use `requests` — a blocking library. That's fine. The worker runs them in a `ThreadPoolExecutor`."
- "Notice the `activity.heartbeat()` in `fetch_org_repos` — for large orgs, pagination takes time. Heartbeating tells Temporal I'm still alive."

**Walk through `worker.py`:**

- "This is the worker process. It registers the workflow and activities, connects to the Temporal server, and starts polling for work."
- "It's about 30 lines. I can run multiple instances of this for horizontal scaling."

---

## 4. The Demo (3-4 minutes)

**The Kill Test** (most powerful visual):

1. Show Temporal Web UI at `http://localhost:8233`
2. Start a scan (set GITHUB_TOKEN first): `python -m temporal.starter --org temporalio --no-wait`
3. Show events appearing in Web UI
4. Query progress: `python -m temporal.starter --org temporalio --query`
5. **Kill the worker hard**: `kill -9 $(pgrep -f temporal.worker)`
6. Show Web UI — workflow still "Running" (tasks timing out, waiting for a worker)
7. Restart worker: `python -m temporal.worker`
8. Watch it resume from where it left off — no repos re-scanned

"That right there is the value proposition. The workflow survived a worker crash. Completed activities weren't re-executed. The scan resumed exactly where it left off."

**The Signal Test** (shows graceful cancellation):

1. Start a scan against a larger org: `python -m temporal.starter --org temporalio --no-wait`
2. While it's running: `python -m temporal.starter --org temporalio --cancel "Demo signal"`
3. Watch it finish the current batch and produce a partial report
4. "This is a signal — a message TO the running workflow. Unlike `workflow cancel`, which is abrupt, this gives the workflow a chance to wrap up cleanly."

**If time permits — show the tests:**

"The SDK includes a testing framework with an in-memory Temporal server. I mock the activities and test the workflow logic in isolation — including the cancellation signal. These tests run in seconds, no external server needed."

---

## 5. The Security Story (2-3 minutes)

**Transition:** "Now here's where my cybersecurity background starts raising flags. Temporalizing this code made it more reliable. But did it make it more *secure*?"

**Show the problem:**

"Look at the Temporal Web UI. Click into the workflow. Expand the input payload. There's the GitHub token. In plaintext. Stored in the event history database. And it's not just the token — every scan result, every private repo name, every compliance finding is right there."

"By default, Temporal doesn't encrypt payloads at rest. That's a reasonable default for many use cases. But for a security scanner handling API tokens and compliance data? We need to fix that."

**Show `encryption.py`:**

"Temporal's architecture has a clean extension point: the PayloadCodec. It's a bytes-to-bytes transform that sits between your application and the server. Encode on the way out, decode on the way in. The server is agnostic — it stores whatever bytes you give it."

"I'm using Fernet, which is AES-128-CBC with HMAC-SHA256. In production you'd use envelope encryption with a KMS. The pattern is the same."

**Show the worker configuration:**

"Both the worker AND the starter use the same encryption key. If they don't match, the worker can't decrypt the inputs. This is important — the key never leaves your infrastructure."

**Show the Web UI again (with encryption):**

"Now look at the payloads. `[binary/encrypted]`. The Temporal server, the database, anyone with read access to the DB — they see ciphertext. The token is protected at rest."

**The nuance that shows depth:**

"Two things the encryption DOESN'T cover by default. First: search attributes bypass the codec entirely — they're for indexing, not for carrying secrets. Second: exception messages and stack traces are stored in Failure objects, not Payloads. You need to configure the failure converter separately to encrypt those. I've documented both in the code."

---

## 6. The Go Lens (2 minutes)

**Transition:** "Now, I built the primary implementation in Python because that's where my original code lived. But for teams running Go — and a lot of Temporal's user base runs Go — I wanted to show how these same patterns translate."

**Pull up `go_comparison/SDK_COMPARISON.md` or the Go files:**

"Three things stand out when you compare them:"

1. **Workflow shape.** "Python uses a class with decorators. Go uses a plain function with closure state. Neither is wrong — they reflect the language. But if you're teaching Go developers, they need to know: workflow state lives in local variables, not struct fields."

2. **Parallel execution.** "This is the most revealing difference. Python: `asyncio.gather()` — three lines, fan-out and fan-in. Go: `workflow.Go()` plus a channel to collect results — about 15 lines. Go developers will recognize the goroutine-channel pattern immediately. Python developers will find `gather` more natural. Same durability guarantees either way."

3. **Error handling.** "Python centralizes retry decisions in the RetryPolicy — 'don't retry ValueErrors.' Go decentralizes them — each activity wraps its own errors with `NewNonRetryableApplicationError`. Go's is more explicit. Python's is more concise. Both are valid."

**Close with:** "The Temporal *concepts* — activities, retries, queries, durability — are identical across SDKs. The expression differs to match the language. Choosing between them is a team and ecosystem decision."

---

## 7. Trade-offs and Reflections (1-2 minutes)

**Be honest about complexity:**

"The original is one file, 150 lines. The Temporal version is seven files across a proper package structure — workflow, activities, models, encryption, worker, starter, tests. That's more code. But it's more code that *handles failure* and *protects sensitive data* — and that's the code I was never going to write well in a script."

**What I'd tell developers choosing:**

"If you're running a one-off scan on your laptop, use the script. If you're running this as a service that scans 50 organizations weekly and pages your security team on regressions — that's where Temporal changes the equation. The question isn't 'can my code handle the happy path?' It's 'what happens when things go wrong at 3am?'"

**What I'd do next:**

- "Use `continue_as_new` for recurring weekly scans (prevent unbounded history growth)"
- "Add a notification activity — Slack the security team when compliance drops"
- "Child workflows per-org if scanning multiple organizations"
- "Deploy a Codec Server so the security team can view decrypted data in the Web UI, behind auth"
- "Envelope encryption with a KMS for key rotation without re-encrypting history"

---

## 8. Audience Q&A Prep

**"Why not just use Celery/Airflow?"**
"Temporal gives me durable state, queryable progress, and automatic retry with history replay — not just task distribution. If my Celery worker dies, the task is lost or at best retried from scratch. With Temporal, it resumes."

**"Is this over-engineering for a security scanner?"**
"For a one-off script? Absolutely. For a compliance service that runs continuously across multiple orgs with SLAs? It's the minimum viable reliability."

**"Why Python and not Go?"**
"My 'before' code was Python. The transformation story is clearest when the language stays the same. But I've included the full Go comparison in the repo — the same workflow, activities, and worker with inline annotations. The concepts are identical; the idioms differ. For a platform team running Go, the patterns map directly."

**"Why compare two languages instead of showing all four SDKs?"**
"Staff DevRel judgment call. Going deep on one with an informed comparison to a second demonstrates more than going shallow on four. I chose Python as primary because it's the fastest-growing SDK, and Go because it's Temporal's home turf and where the biggest chunk of the user base lives. TypeScript and Java follow the same core patterns — the concepts transfer."

**"How does this work in production?"**
"Temporal Cloud, or self-hosted Temporal cluster. The development experience with `temporal server start-dev` is the same API — you just change the connection address."

**"Why Fernet and not AES-256-GCM directly?"**
"Fernet is a high-level recipe: AES-128-CBC + HMAC-SHA256, with an authenticated timestamp. It's hard to misuse — no nonce management, no mode selection. For a production service, I'd use envelope encryption with AWS KMS or GCP KMS generating data encryption keys. The PayloadCodec pattern is the same either way."

**"What about the token being in the workflow input?"**
"With encryption enabled, it's encrypted at rest. But there's an alternative pattern: don't pass the token through Temporal at all. Use an environment variable on the worker, and have the activities read it directly. That way the token never enters the event history. Trade-off: you lose the ability to start scans with different tokens per workflow. Both are valid depending on your threat model."

**"Do search attributes get encrypted too?"**
"No, and this is important. Search attributes bypass the PayloadCodec — they're stored unencrypted for indexing. This is by design. You should never put sensitive data in search attributes. I'd use them for things like org name or scan status, not tokens or compliance details."
