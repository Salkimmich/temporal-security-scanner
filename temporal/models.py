"""
Data models for the Security Scanner Temporal application.

TEMPORAL CONCEPT — SERIALIZATION:
    Every piece of data that flows through Temporal (workflow inputs,
    activity arguments, activity results, query responses) must be
    serializable to bytes. The Python SDK uses a DataConverter that
    handles this automatically for:
    - Primitive types (str, int, float, bool, None)
    - Dataclasses (the RECOMMENDED approach)
    - Lists, dicts, and other standard containers
    - Enums (including StrEnum)

    We use dataclasses for all our models because:
    1. Temporal's SDK serializes them cleanly (JSON-based by default)
    2. Adding an optional field with a default doesn't break running
       workflows (backward compatibility during deployments)
    3. They're standard Python — no ORM, no framework, just types
    4. Type hints make the workflow's data flow readable

    The serialization path is:
        Dataclass → DataConverter (to JSON) → PayloadCodec (encrypt) → Temporal Server

WHY NOT Pydantic / attrs / TypedDict:
    Temporal's Python SDK has first-class support for dataclasses. Pydantic
    works too (with some configuration), but dataclasses are simpler and
    have zero extra dependencies. For a demo, simplicity wins.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class SecurityStatus(StrEnum):
    """
    Possible states for a security feature check.

    Using StrEnum (Python 3.11+) so values are both enum members AND strings.
    This means `SecurityStatus.ENABLED == "enabled"` is True, which simplifies
    comparison with GitHub API responses that return plain strings.
    """
    ENABLED = "enabled"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not configured"
    NO_ACCESS = "no access"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class ScanInput:
    """
    Input for the security scan workflow.

    This is the WORKFLOW INPUT — it's serialized, encrypted, and stored in
    the workflow's event history. This is why we encrypt: the token would
    otherwise be visible in plaintext to anyone who can query the Temporal
    server's event history.

    DESIGN DECISION — token as Optional[str]:
        The token is optional so the scanner works for public-only scanning
        (unauthenticated, 60 requests/hour). When provided, it's passed
        through the workflow to each activity.

        Alternative: store the token as an env var on the worker. See
        workflows.py for the trade-off analysis.
    """
    org: str
    token: str | None = None


@dataclass
class RepoInfo:
    """
    Minimal repository information needed for scanning.

    This is the OUTPUT of fetch_org_repos and INPUT to check_repo_security.
    We extract only the fields we need rather than passing the full GitHub
    API response. This keeps the serialized payload small (important when
    it's stored in Temporal's event history for every activity call).
    """
    name: str
    full_name: str
    private: bool = False
    archived: bool = False


@dataclass
class RepoSecurityResult:
    """
    Security scan result for a single repository.

    This is the OUTPUT of check_repo_security. One instance per repo,
    stored in the workflow's _results list and serialized in the event
    history.

    IMPORTANT — NO TOKEN FIELD:
        This dataclass deliberately has no field for the token. Activity
        results are stored in the event history, and even with encryption,
        defense-in-depth says: don't store secrets you don't need to store.
        Test 7 verifies this constraint.
    """
    repository: str
    secret_scanning: str = SecurityStatus.UNKNOWN
    dependabot_alerts: str = SecurityStatus.UNKNOWN
    code_scanning: str = SecurityStatus.UNKNOWN
    error: str | None = None
    scanned_at: str = ""

    def __post_init__(self):
        if not self.scanned_at:
            self.scanned_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_fully_compliant(self) -> bool:
        """
        A repo is fully compliant when ALL THREE security features are enabled.
        This is the business logic that drives the compliance rate calculation.
        """
        return (
            self.secret_scanning == SecurityStatus.ENABLED
            and self.dependabot_alerts == SecurityStatus.ENABLED
            and self.code_scanning == SecurityStatus.ENABLED
        )


@dataclass
class ScanProgress:
    """
    Queryable progress of an in-flight scan.

    This dataclass is returned by the workflow's `progress` query handler.
    External clients (the starter, the CLI, the Web UI) can read this at
    any time without interrupting the scan.

    TEMPORAL CONCEPT — QUERY RETURN TYPES:
        Query handlers must return serializable types. Dataclasses work
        perfectly. The Temporal CLI displays the JSON-serialized form.
    """
    org: str
    total_repos: int = 0
    scanned_repos: int = 0
    compliant_repos: int = 0
    non_compliant_repos: int = 0
    errors: int = 0
    status: str = "starting"  # starting | scanning | completed | cancelled | failed

    @property
    def percent_complete(self) -> float:
        if self.total_repos == 0:
            return 0.0
        return round((self.scanned_repos / self.total_repos) * 100, 1)


@dataclass
class ScanReport:
    """
    Final output of a completed security scan.

    Not currently used by the workflow (which returns a plain dict for
    simplicity), but defined here for future use when the report becomes
    richer (e.g., adding trend data, recommendations, or multi-org support).

    DESIGN NOTE — WHY THE WORKFLOW RETURNS dict INSTEAD OF THIS:
        The workflow adds dynamic keys to the report (cancelled, cancel_reason)
        based on runtime state. A plain dict is more flexible for this.
        In a production system, you'd use this dataclass and include the
        cancellation fields as Optional attributes.
    """
    org: str
    results: list[RepoSecurityResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    completed_at: str = ""

    def __post_init__(self):
        if not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat()
