"""
Tests for the Security Scanner Temporal Application.

TEMPORAL CONCEPT — TESTING FRAMEWORK:
    Temporal provides a built-in test server that runs IN-PROCESS:
    - No Docker required
    - No external Temporal server needed
    - Time-skipping: timers and timeouts resolve instantly
    - Activity mocking: test workflow logic without hitting GitHub

    The test server is a real Temporal server (written in Go, embedded
    via the temporalio Python package). It downloads a small binary on
    first run and caches it at ~/.cache/temporalio/.

HOW ACTIVITY MOCKING WORKS:
    We define mock activities with @activity.defn(name="...") that match
    the real activity names. When the worker registers these mocks instead
    of the real activities, the workflow calls the mocks — it doesn't know
    the difference.

    This is cleaner than monkey-patching because it works through Temporal's
    normal dispatch mechanism. The workflow code is unchanged.

KNOWN TEST QUIRKS:
    1. First run downloads the test server binary (~10-30 seconds)
    2. Time-skipping makes all sleeps/timers instant, so tests run fast
       but timing-dependent behavior (like signal delivery) may race
    3. pytest-asyncio 0.23+ changed asyncio_mode handling — if tests fail
       with "event loop is closed", pin to pytest-asyncio>=0.23,<0.25

Run with:
    pytest tests/ -v
"""

import concurrent.futures
import dataclasses
import uuid

import pytest
import temporalio.converter
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from temporal.encryption import EncryptionCodec
from temporal.models import RepoInfo, RepoSecurityResult, ScanInput, SecurityStatus
from temporal.workflows import SecurityScanWorkflow

TASK_QUEUE = "test-security-scanner"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOCK ACTIVITIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# These replace the real GitHub API calls. Each mock has the same
# function signature and @activity.defn(name="...") as the real
# activity, so the workflow dispatches to them without modification.
#
# The mock data is designed to produce predictable results:
# - 5 repos total
# - 2 fully compliant (repo-a, repo-b)
# - 2 non-compliant (repo-c, repo-d)
# - 1 with an error (repo-e)
# - Expected compliance rate: 40.0%

MOCK_REPOS = [
    RepoInfo(name="repo-a", full_name="test-org/repo-a"),
    RepoInfo(name="repo-b", full_name="test-org/repo-b"),
    RepoInfo(name="repo-c", full_name="test-org/repo-c"),
    RepoInfo(name="repo-d", full_name="test-org/repo-d"),
    RepoInfo(name="repo-e", full_name="test-org/repo-e"),
]


@activity.defn(name="fetch_org_repos")
def mock_fetch_org_repos(org: str, token: str | None = None) -> list[RepoInfo]:
    """Returns a fixed set of 5 test repositories."""
    return MOCK_REPOS


@activity.defn(name="check_repo_security")
def mock_check_repo_security(
    org: str, repo_name: str, token: str | None = None
) -> RepoSecurityResult:
    """
    Returns predictable security results.

    repo-a, repo-b: all three features enabled (fully compliant)
    repo-c, repo-d: secret scanning only (not compliant)
    repo-e:         partial scan with error field set
    """
    if repo_name in ("repo-a", "repo-b"):
        return RepoSecurityResult(
            repository=repo_name,
            secret_scanning=SecurityStatus.ENABLED,
            dependabot_alerts=SecurityStatus.ENABLED,
            code_scanning=SecurityStatus.ENABLED,
        )
    elif repo_name == "repo-e":
        return RepoSecurityResult(
            repository=repo_name,
            secret_scanning=SecurityStatus.ENABLED,
            dependabot_alerts=SecurityStatus.DISABLED,
            code_scanning=SecurityStatus.NOT_CONFIGURED,
            error="Partial scan",
        )
    else:
        return RepoSecurityResult(
            repository=repo_name,
            secret_scanning=SecurityStatus.ENABLED,
            dependabot_alerts=SecurityStatus.DISABLED,
            code_scanning=SecurityStatus.NOT_CONFIGURED,
        )


@activity.defn(name="generate_report")
def mock_generate_report(org: str, results: list[RepoSecurityResult]) -> dict:
    """Generates a real summary from mock results (same logic as production)."""
    total = len(results)
    compliant = sum(1 for r in results if r.is_fully_compliant)
    return {
        "org": org,
        "total_repos": total,
        "fully_compliant": compliant,
        "compliance_rate": f"{(compliant / total * 100):.1f}%" if total > 0 else "N/A",
        "secret_scanning_enabled": sum(
            1 for r in results if r.secret_scanning == SecurityStatus.ENABLED
        ),
        "dependabot_enabled": sum(
            1 for r in results if r.dependabot_alerts == SecurityStatus.ENABLED
        ),
        "code_scanning_enabled": sum(
            1 for r in results if r.code_scanning == SecurityStatus.ENABLED
        ),
        "errors": sum(1 for r in results if r.error is not None),
        "non_compliant_repos": [
            r.repository for r in results if not r.is_fully_compliant and r.error is None
        ],
    }


def _make_worker(client: Client, **kwargs) -> Worker:
    """Create a worker with mock activities for testing."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    return Worker(
        client,
        task_queue=kwargs.pop("task_queue", TASK_QUEUE),
        workflows=[SecurityScanWorkflow],
        activities=[
            mock_fetch_org_repos,
            mock_check_repo_security,
            mock_generate_report,
        ],
        activity_executor=executor,
        **kwargs,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 1: Full workflow execution
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     The workflow correctly orchestrates the entire pipeline:
#     fetch repos → scan each repo → generate report.
#
#     With 5 mock repos (2 compliant, 2 non-compliant, 1 error),
#     the expected compliance rate is 40.0%.
#
# WHAT COULD BREAK:
#     - Dataclass serialization between workflow and activities
#     - Batch processing logic (all 5 repos fit in one batch of 10)
#     - Report generation with mixed compliant/non-compliant results

@pytest.mark.asyncio
async def test_full_scan_workflow():
    """Workflow correctly orchestrates fetch → scan → report."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with _make_worker(env.client):
            result = await env.client.execute_workflow(
                SecurityScanWorkflow.run,
                ScanInput(org="test-org"),
                id=f"test-scan-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )

            assert result["org"] == "test-org"
            assert result["total_repos"] == 5
            assert result["fully_compliant"] == 2  # repo-a and repo-b
            assert result["compliance_rate"] == "40.0%"
            assert "repo-c" in result["non_compliant_repos"]
            assert "repo-d" in result["non_compliant_repos"]
            # repo-e has error field set, so it's excluded from non_compliant_repos
            assert "repo-e" not in result["non_compliant_repos"]
            assert "cancelled" not in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 2: Progress query
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     Queries return the correct final state after the workflow completes.
#     In a time-skipping environment, the workflow runs to completion
#     almost instantly, so we query the final state.
#
# WHAT COULD BREAK:
#     - Query handler not returning updated state
#     - ScanProgress dataclass property calculations

@pytest.mark.asyncio
async def test_progress_query():
    """Progress query returns correct final state."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with _make_worker(env.client):
            handle = await env.client.start_workflow(
                SecurityScanWorkflow.run,
                ScanInput(org="test-org"),
                id=f"test-query-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )
            await handle.result()

            progress = await handle.query(SecurityScanWorkflow.progress)
            assert progress.org == "test-org"
            assert progress.total_repos == 5
            assert progress.scanned_repos == 5
            assert progress.compliant_repos == 2
            assert progress.status == "completed"
            assert progress.percent_complete == 100.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 3: Signal-based cancellation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     The cancel_scan signal stops the workflow gracefully and produces
#     a partial report with cancellation metadata.
#
# TIMING CAVEAT:
#     In the time-skipping environment, activities execute nearly
#     instantly. The signal sent immediately after start_workflow
#     RACES with activity execution. Possible outcomes:
#
#     a) Signal arrives before any batch starts → 0 repos scanned
#     b) Signal arrives during first batch → up to 5 repos scanned
#     c) Signal arrives after all batches → all 5 repos scanned, but
#        cancellation metadata is still set (flag was set, just too late
#        to break the loop)
#
#     We don't assert on exact repo count — only that cancellation
#     metadata is present. This makes the test timing-robust.
#
# WHAT COULD BREAK:
#     - Signal handler not setting the flag
#     - Batch loop not checking the flag
#     - Report not including cancellation metadata

@pytest.mark.asyncio
async def test_cancel_scan_signal():
    """Cancel signal stops scan and produces partial report."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with _make_worker(env.client):
            handle = await env.client.start_workflow(
                SecurityScanWorkflow.run,
                ScanInput(org="test-org"),
                id=f"test-cancel-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )

            # Send cancel signal immediately (races with execution)
            await handle.signal(SecurityScanWorkflow.cancel_scan, "Test cancellation")

            result = await handle.result()

            # These assertions are timing-robust:
            assert result.get("cancelled") is True
            assert result["cancel_reason"] == "Test cancellation"

            is_cancelled = await handle.query(SecurityScanWorkflow.is_cancelled)
            assert is_cancelled is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 4: Encryption roundtrip
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     The EncryptionCodec correctly encrypts and decrypts payloads.
#     Data survives the encode → decode roundtrip intact. The encrypted
#     form does NOT contain the plaintext token.
#
# WHAT COULD BREAK:
#     - Protobuf serialization of Payload objects
#     - Fernet encrypt/decrypt with the encoding metadata marker

@pytest.mark.asyncio
async def test_encryption_codec_roundtrip():
    """Encrypt → decrypt preserves data; token not visible in ciphertext."""
    from cryptography.fernet import Fernet
    from temporalio.api.common.v1 import Payload

    key = Fernet.generate_key()
    codec = EncryptionCodec(key=key)

    original = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"org": "test-org", "token": "ghp_supersecret123"}',
    )

    # Encrypt
    encrypted_payloads = await codec.encode([original])
    assert len(encrypted_payloads) == 1
    encrypted = encrypted_payloads[0]

    # Verify encryption markers
    assert encrypted.metadata["encoding"] == b"binary/encrypted"
    assert encrypted.data != original.data
    assert b"ghp_supersecret123" not in encrypted.data  # Token is hidden

    # Decrypt
    decrypted_payloads = await codec.decode(encrypted_payloads)
    assert len(decrypted_payloads) == 1
    decrypted = decrypted_payloads[0]

    # Verify roundtrip fidelity
    assert decrypted.data == original.data
    assert decrypted.metadata["encoding"] == b"json/plain"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 5: Wrong encryption key fails
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     Someone with access to the Temporal database but NOT the encryption
#     key cannot read the data. This is the core security guarantee.
#
# WHAT COULD BREAK:
#     - Nothing, really. This is testing the cryptography library's
#       guarantee. But it's valuable documentation of the security model.

@pytest.mark.asyncio
async def test_encryption_wrong_key_fails():
    """Decrypting with wrong key raises InvalidToken."""
    from cryptography.fernet import Fernet, InvalidToken
    from temporalio.api.common.v1 import Payload

    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()

    codec_encrypt = EncryptionCodec(key=key1)
    codec_wrong = EncryptionCodec(key=key2)

    original = Payload(
        metadata={"encoding": b"json/plain"},
        data=b'{"token": "secret"}',
    )

    encrypted = await codec_encrypt.encode([original])

    with pytest.raises(InvalidToken):
        await codec_wrong.decode(encrypted)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 6: Full workflow with encryption (integration)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     The full pipeline works end-to-end WITH encryption enabled.
#     This catches serialization issues where a dataclass might not
#     survive the encrypt → serialize → transmit → deserialize → decrypt
#     pipeline.
#
# WHY THIS MATTERS:
#     It's possible for encryption to break dataclass serialization
#     (e.g., if the protobuf serialization of Payload changes format).
#     This test catches that before it bites you in production.
#
# WHAT COULD BREAK:
#     - Client constructor API changes (namespace kwarg)
#     - DataConverter with PayloadCodec not wrapping correctly
#     - Dataclass serialization through the encrypted pipeline

@pytest.mark.asyncio
async def test_workflow_with_encryption():
    """Full workflow works with payload encryption enabled."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        encrypted_converter = dataclasses.replace(
            temporalio.converter.default(),
            payload_codec=EncryptionCodec(key=key),
        )

        # Create a new Client with encryption, using the test env's connection
        encrypted_client = Client(
            env.client.service_client,
            namespace=env.client.namespace,
            data_converter=encrypted_converter,
        )

        async with Worker(
            encrypted_client,
            task_queue=TASK_QUEUE,
            workflows=[SecurityScanWorkflow],
            activities=[
                mock_fetch_org_repos,
                mock_check_repo_security,
                mock_generate_report,
            ],
            activity_executor=concurrent.futures.ThreadPoolExecutor(max_workers=10),
        ):
            result = await encrypted_client.execute_workflow(
                SecurityScanWorkflow.run,
                ScanInput(org="test-org", token="ghp_test_token_123"),
                id=f"test-encrypted-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )

            assert result["org"] == "test-org"
            assert result["fully_compliant"] == 2
            assert result["total_repos"] == 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 7: No token in result model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     Defense in depth: even if encryption is disabled, activity results
#     don't contain the GitHub token. There's no field that could
#     accidentally store it.
#
# WHY THIS EXISTS:
#     It's easy to add a "token" or "api_key" field to a result dataclass
#     during development and forget to remove it. This test catches that.

def test_repo_security_result_no_token_leak():
    """Result model has no fields that could store secrets."""
    import dataclasses as dc

    result = RepoSecurityResult(
        repository="test-repo",
        secret_scanning=SecurityStatus.ENABLED,
        dependabot_alerts=SecurityStatus.ENABLED,
        code_scanning=SecurityStatus.ENABLED,
    )

    fields = {f.name for f in dc.fields(result)}
    assert "token" not in fields
    assert "api_key" not in fields
    assert "secret" not in fields


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEST 8: Compliance logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# WHAT THIS PROVES:
#     The is_fully_compliant property requires ALL THREE features enabled.
#     A repo with 2 of 3 is NOT compliant. A repo with no data (all
#     UNKNOWN) is NOT compliant.
#
# WHY THIS MATTERS:
#     This is the business logic that drives the compliance rate. Getting
#     it wrong means the report lies. Pure unit test — no Temporal needed.

def test_compliance_calculation():
    """is_fully_compliant requires all three features enabled."""
    compliant = RepoSecurityResult(
        repository="good",
        secret_scanning=SecurityStatus.ENABLED,
        dependabot_alerts=SecurityStatus.ENABLED,
        code_scanning=SecurityStatus.ENABLED,
    )
    assert compliant.is_fully_compliant is True

    partial = RepoSecurityResult(
        repository="partial",
        secret_scanning=SecurityStatus.ENABLED,
        dependabot_alerts=SecurityStatus.DISABLED,
        code_scanning=SecurityStatus.ENABLED,
    )
    assert partial.is_fully_compliant is False

    unknown = RepoSecurityResult(repository="new")
    assert unknown.is_fully_compliant is False
