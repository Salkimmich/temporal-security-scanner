"""
Temporal Worker for the Security Scanner — Security-Hardened Configuration.

The worker is a long-running process that polls the Temporal server
for tasks (workflow tasks and activity tasks) and executes them.

SECURITY CONFIGURATION:
This worker configures end-to-end payload encryption so that all
workflow/activity inputs and outputs (including GitHub tokens, private
repo names, and scan results) are encrypted before reaching the
Temporal server. The server never sees plaintext data.

Run this before starting any workflows:
    python -m temporal.worker

You can run multiple worker instances for horizontal scaling.
Each worker must use the same encryption key to read shared workflow state.

Environment variables:
    TEMPORAL_ENCRYPTION_KEY  — Fernet encryption key (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    TEMPORAL_HOST            — Temporal server address (default: localhost:7233)
"""

import asyncio
import concurrent.futures
import dataclasses
import logging
import os

import temporalio.converter
from temporalio.client import Client
from temporalio.worker import Worker

from .activities import check_repo_security, fetch_org_repos, generate_report
from .encryption import EncryptionCodec
from .workflows import SecurityScanWorkflow

TASK_QUEUE = "security-scanner"


def build_encrypted_data_converter() -> temporalio.converter.DataConverter:
    """
    Build a DataConverter with end-to-end encryption enabled.

    This wraps the default converter with our EncryptionCodec, which means:
    - All activity inputs (including GitHub tokens) are encrypted
    - All activity outputs (scan results) are encrypted
    - All workflow inputs and return values are encrypted
    - The Temporal server only stores ciphertext

    The Temporal Web UI will show "[binary/encrypted]" for all payloads.
    To view decrypted data in the UI, you'd deploy a Codec Server
    (see Temporal docs for the pattern — it's an HTTP endpoint that
    decrypts payloads on demand, behind your auth layer).

    IMPORTANT: Search attributes bypass the codec entirely. Never store
    sensitive data in search attributes — they're meant for indexing, not
    for carrying secrets.

    NOTE ON FAILURE ENCRYPTION:
    By default, exception messages and stack traces are stored in Failure
    objects which are NOT encrypted by the PayloadCodec. For a security
    scanner, error messages could leak repo names or infrastructure details.
    In production, you would also configure the failure converter to encode
    common attributes through the codec:

        failure_converter_class=DefaultFailureConverterWithEncodedAttributes

    This ensures stack traces and error messages are also encrypted at rest.
    We omit it here because it makes the Temporal Web UI harder to use
    during the demo (you can't read error messages without a Codec Server).
    """
    return dataclasses.replace(
        temporalio.converter.default(),
        payload_codec=EncryptionCodec(),
    )


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("security-scanner.worker")

    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")

    # Build the encrypted data converter.
    # Both the worker AND the starter must use the same converter
    # (and therefore the same encryption key) to communicate.
    data_converter = build_encrypted_data_converter()

    logger.info("Payload encryption: ENABLED")
    logger.info(
        "All workflow/activity data will be encrypted before reaching the Temporal server"
    )

    # Connect to the Temporal server with encryption enabled
    client = await Client.connect(
        temporal_host,
        data_converter=data_converter,
    )

    # ── Activity Executor ──
    #
    # TEMPORAL CONCEPT — SYNC ACTIVITY EXECUTION:
    #     Activities use `requests` (blocking I/O), so they're sync functions
    #     (`def`, not `async def`). The SDK runs them in this ThreadPoolExecutor.
    #
    #     max_workers=20 means up to 20 concurrent activity executions per
    #     worker instance. With BATCH_SIZE=10 in the workflow, we need at
    #     least 10 threads for a single batch. The extra 10 allow for
    #     overlapping batches or concurrent workflows.
    #
    #     If you set max_workers too low, activities will queue on the worker
    #     even though the thread pool is the bottleneck — not Temporal.
    #     If too high, you risk GitHub rate limits (20 activities × 3 API
    #     calls = 60 concurrent requests).
    #
    #     For multiple worker instances: Temporal distributes tasks across
    #     workers automatically. Each worker's thread pool is independent.
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[SecurityScanWorkflow],
            activities=[fetch_org_repos, check_repo_security, generate_report],
            activity_executor=executor,
        )
        logger.info(f"Worker started on task queue '{TASK_QUEUE}' (host: {temporal_host})")
        logger.info("Registered workflows: SecurityScanWorkflow")
        logger.info("Registered activities: fetch_org_repos, check_repo_security, generate_report")
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
