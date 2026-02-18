"""
Temporal Activities for the Security Scanner.

Activities are the side-effectful parts of our application — they make
network calls to the GitHub API. By wrapping them as Temporal activities,
we get automatic retries, timeouts, and heartbeating — all the things
that were missing from the original script.

TEMPORAL CONCEPT — WHY ACTIVITIES EXIST:
    Workflow code must be deterministic (same inputs → same execution path).
    Network calls are inherently non-deterministic (they can timeout, fail,
    return different data). So all I/O is extracted into activities.

    When a workflow calls `execute_activity()`, Temporal:
    1. Records "activity scheduled" in the event history
    2. Dispatches the task to a worker
    3. The worker executes the function and returns the result
    4. Temporal records "activity completed" with the result

    On replay (e.g., after a worker crash), Temporal doesn't re-execute
    completed activities — it replays the recorded results. This is how
    the Kill Test works: activities that finished before the crash aren't
    re-run; only the pending ones are retried.

SYNCHRONOUS vs ASYNCHRONOUS ACTIVITIES:
    These activities are synchronous (`def`, not `async def`) because they
    use the `requests` library (blocking I/O). The worker runs them in a
    ThreadPoolExecutor. This is the RECOMMENDED approach for the Python SDK.

    Alternative: use `async def` with `aiohttp` or `httpx`. That would
    give true async I/O but adds dependencies and complexity. The thread
    pool already gives us 20 concurrent activities per worker, which is
    plenty for a batch-of-10 scanning pattern.

    Temporal's Python SDK detects sync vs async activities automatically
    and routes them to the correct executor.
"""

import requests
from temporalio import activity

from .models import RepoInfo, RepoSecurityResult, SecurityStatus


def _github_headers(token: str | None) -> dict:
    """
    Build standard GitHub API headers.

    The Accept header tells GitHub which API version to use.
    'application/vnd.github+json' is the current stable version.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


@activity.defn
def fetch_org_repos(org: str, token: str | None = None) -> list[RepoInfo]:
    """
    Fetch all repositories for a GitHub organization.

    This activity handles pagination and is the FIRST step in the scan.
    If this fails (org not found, auth error), the workflow should fail fast
    — that's why ValueError is non-retryable in our retry policy.

    TEMPORAL CONCEPT — HEARTBEATING:
        We call activity.heartbeat() on each page fetch. This tells the
        Temporal server "I'm still alive and making progress." If the worker
        dies mid-fetch, the server will notice missed heartbeats (within the
        heartbeat_timeout configured by the workflow) and schedule a retry
        on another worker.

        Without heartbeating, the server would wait for the full
        start_to_close_timeout (120s) before retrying. With heartbeating
        (30s timeout), it detects failure in 30s instead.

        The heartbeat payload ("Fetching page N") is informational — it
        shows up in the Temporal Web UI for debugging.

    EDGE CASES:
        - GitHub returns max 100 repos per page. We paginate until empty.
        - For orgs with 1000+ repos, this activity could take minutes.
          Heartbeating keeps the server informed of progress.
        - Archived repos are included (they still have security settings).
    """
    repos: list[RepoInfo] = []
    page = 1
    headers = _github_headers(token)

    while True:
        activity.heartbeat(f"Fetching page {page}")
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=headers, timeout=30)

        # Non-retryable errors: the input is wrong, retrying won't help.
        # These raise ValueError, which our retry policy skips.
        if response.status_code == 404:
            raise ValueError(f"Organization '{org}' not found or access denied")
        if response.status_code == 401:
            raise ValueError("Invalid GitHub API token")

        # Retryable error: rate limits are transient, wait and retry.
        # This raises RuntimeError, which our retry policy DOES retry
        # with exponential backoff (2s → 4s → 8s → 16s → 32s).
        if response.status_code == 403 and "rate limit" in response.text.lower():
            raise RuntimeError("GitHub API rate limit exceeded")

        # Any other HTTP error — let requests raise it.
        # This becomes an ApplicationError which is retryable by default.
        response.raise_for_status()
        data = response.json()

        if not data:
            break

        for repo in data:
            repos.append(
                RepoInfo(
                    name=repo["name"],
                    full_name=repo["full_name"],
                    private=repo.get("private", False),
                    archived=repo.get("archived", False),
                )
            )

        # GitHub returns fewer than per_page items on the last page
        if len(data) < 100:
            break
        page += 1

    activity.logger.info(f"Found {len(repos)} repositories in '{org}'")
    return repos


@activity.defn
def check_repo_security(
    org: str, repo_name: str, token: str | None = None
) -> RepoSecurityResult:
    """
    Check all security settings for a single repository.

    This is the CORE scanning activity. Each repo check is independent,
    so if one repo's check fails, it doesn't affect the others. Temporal
    retries this automatically on transient failures.

    API CALLS MADE (3 per repo):
        1. GET /repos/{org}/{repo}              → secret scanning status
        2. GET /repos/{org}/{repo}/vulnerability-alerts → Dependabot status
        3. GET /repos/{org}/{repo}/code-scanning/alerts → code scanning status

    RATE LIMIT MATH:
        With batch size 10 and 3 calls per repo = 30 calls per batch.
        GitHub authenticated rate limit: 5,000/hour ≈ 83/minute.
        At ~30 calls per batch, we can do ~2.7 batches/minute safely.
        For 100 repos (10 batches), that's ~4 minutes. Comfortable.

    EDGE CASES HANDLED:
        - `security_and_analysis` can be None for repos without GitHub
          Advanced Security (GHAS). The `or {}` handles this.
        - 404 on the repo itself means it was deleted between the
          fetch_org_repos call and this check. We record the error
          and move on rather than crashing.
        - 403 on code scanning means GHAS is required but not enabled.
          This is a valid finding, not an error.

    TEMPORAL CONCEPT — ACTIVITY ISOLATION:
        Each invocation of this activity is independent. If checking
        repo-47 fails with a timeout, only repo-47 is retried. The
        other 9 repos in the batch complete normally. This is the
        fundamental advantage over the original script's sequential
        approach, where one failure killed the entire scan.
    """
    headers = _github_headers(token)
    result = RepoSecurityResult(repository=repo_name)

    try:
        # ── 1. Repository settings (includes secret scanning) ──
        #
        # The `security_and_analysis` field is only present for repos
        # with GHAS available. For public repos on free plans, it may
        # be null or absent entirely. The `or {}` fallback handles both.
        url = f"https://api.github.com/repos/{org}/{repo_name}"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            security = data.get("security_and_analysis", {}) or {}
            result.secret_scanning = (
                security.get("secret_scanning", {}).get("status", SecurityStatus.DISABLED)
            )
        elif resp.status_code == 404:
            result.error = "Repository not found"
            return result

        # ── 2. Dependabot vulnerability alerts ──
        #
        # This endpoint returns 204 (No Content) if alerts are enabled,
        # 404 if disabled. The preview header may be deprecated in favor
        # of the non-preview endpoint — if Dependabot results show
        # "disabled" for all repos, check GitHub's API changelog.
        url = f"https://api.github.com/repos/{org}/{repo_name}/vulnerability-alerts"
        dependabot_headers = {
            **headers,
            "Accept": "application/vnd.github.dorian-preview+json",
        }
        resp = requests.get(url, headers=dependabot_headers, timeout=30)
        if resp.status_code == 204:
            result.dependabot_alerts = SecurityStatus.ENABLED
        elif resp.status_code == 404:
            result.dependabot_alerts = SecurityStatus.DISABLED

        # ── 3. Code scanning (GitHub Advanced Security) ──
        #
        # This checks for CodeQL or third-party code scanning.
        # 200 = alerts endpoint exists (scanning is configured)
        # 404 = code scanning not set up for this repo
        # 403 = GHAS required but not available (free plan)
        url = f"https://api.github.com/repos/{org}/{repo_name}/code-scanning/alerts"
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            result.code_scanning = SecurityStatus.ENABLED
        elif resp.status_code == 404:
            result.code_scanning = SecurityStatus.NOT_CONFIGURED
        elif resp.status_code == 403:
            result.code_scanning = SecurityStatus.NO_ACCESS

    except requests.exceptions.Timeout:
        # Retryable: Temporal will retry with exponential backoff.
        # We re-raise as RuntimeError to distinguish from ValueError
        # (non-retryable). The retry policy checks error type names.
        raise RuntimeError(f"Timeout checking {repo_name}")
    except requests.exceptions.ConnectionError:
        # Retryable: network blip, DNS failure, etc.
        raise RuntimeError(f"Connection error checking {repo_name}")

    activity.logger.info(
        f"Checked {repo_name}: secret_scanning={result.secret_scanning}, "
        f"dependabot={result.dependabot_alerts}, code_scanning={result.code_scanning}"
    )
    return result


@activity.defn
def generate_report(
    org: str, results: list[RepoSecurityResult]
) -> dict:
    """
    Generate a summary report from scan results.

    WHY THIS IS AN ACTIVITY (not inline workflow logic):
        This is currently pure computation — no I/O. So why not just
        compute it inside the workflow?

        1. Separation of concerns: the workflow orchestrates, activities compute.
        2. Extensibility: in production, this would write to S3, post to Slack,
           update a dashboard. Each of those is a side effect that belongs in
           an activity.
        3. The report generation is recorded in the event history. If you
           change the report format, you can see both old and new formats
           in the workflow history.

    RETURNS a plain dict (not a dataclass) because the workflow returns this
    directly as its result, and dict is the simplest wire-compatible type.
    The workflow may add extra keys (cancelled, cancel_reason) before returning.
    """
    total = len(results)
    compliant = sum(1 for r in results if r.is_fully_compliant)
    secret_enabled = sum(1 for r in results if r.secret_scanning == SecurityStatus.ENABLED)
    dependabot_enabled = sum(1 for r in results if r.dependabot_alerts == SecurityStatus.ENABLED)
    code_scanning_enabled = sum(1 for r in results if r.code_scanning == SecurityStatus.ENABLED)
    errors = sum(1 for r in results if r.error is not None)

    summary = {
        "org": org,
        "total_repos": total,
        "fully_compliant": compliant,
        "compliance_rate": f"{(compliant / total * 100):.1f}%" if total > 0 else "N/A",
        "secret_scanning_enabled": secret_enabled,
        "dependabot_enabled": dependabot_enabled,
        "code_scanning_enabled": code_scanning_enabled,
        "errors": errors,
        "non_compliant_repos": [
            r.repository for r in results if not r.is_fully_compliant and r.error is None
        ],
    }

    activity.logger.info(
        f"Report: {compliant}/{total} repos fully compliant "
        f"({summary['compliance_rate']})"
    )
    return summary
