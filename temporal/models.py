"""
Data models for the Security Scanner Temporal application.

TEMPORAL CONCEPT — SERIALIZATION:
    Every piece of data that flows through Temporal (workflow inputs,
    activity arguments, activity results, query responses) must be
    serializable to bytes. The Python SDK uses a DataConverter that
    handles this automatically for dataclasses, primitives, lists,
    dicts, and enums.

    The serialization path is:
        Dataclass -> DataConverter (to JSON) -> PayloadCodec (encrypt) -> Server

TEMPORAL CONCEPT — SCHEMA EVOLUTION:
    Adding an optional field with a default to a dataclass is always safe.
    Running workflows that were started with the old schema will simply
    get the default value for the new field when deserialized.

    This is how we added continuation_state to ScanInput: old workflows
    still work, new workflows can use continue-as-new.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class SecurityStatus(StrEnum):
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

    TEMPORAL CONCEPT — CONTINUE-AS-NEW:
        When a workflow's event history grows too large, it "continues as
        new" — starts a fresh execution with a clean history. The
        continuation_state field carries accumulated results across.

        Adding this as an optional field preserves backward compatibility.
    """
    org: str
    token: str | None = None
    continuation_state: dict | None = None


@dataclass
class RepoInfo:
    """Minimal repository info. Kept small for serialized payload size."""
    name: str
    full_name: str
    private: bool = False
    archived: bool = False


@dataclass
class RepoSecurityResult:
    """
    Security scan result for a single repository.

    IMPORTANT — NO TOKEN FIELD: defense in depth. Activity results are
    stored in event history. Don't store secrets you don't need.
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
        return (
            self.secret_scanning == SecurityStatus.ENABLED
            and self.dependabot_alerts == SecurityStatus.ENABLED
            and self.code_scanning == SecurityStatus.ENABLED
        )


@dataclass
class ScanProgress:
    """
    Queryable progress returned by the workflow's progress query.

    Includes metadata about advanced features (batch_size, continuation
    count, timer state) so external clients can observe everything.
    """
    org: str
    total_repos: int = 0
    scanned_repos: int = 0
    compliant_repos: int = 0
    non_compliant_repos: int = 0
    errors: int = 0
    status: str = "starting"
    batch_size: int = 10
    continuation_count: int = 0
    timer_active: bool = False
    timer_remaining_secs: int = 0

    @property
    def percent_complete(self) -> float:
        if self.total_repos == 0:
            return 0.0
        return round((self.scanned_repos / self.total_repos) * 100, 1)


@dataclass
class ScanReport:
    """Final output (reserved for future use — workflow returns dict)."""
    org: str
    results: list[RepoSecurityResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    completed_at: str = ""

    def __post_init__(self):
        if not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat()
