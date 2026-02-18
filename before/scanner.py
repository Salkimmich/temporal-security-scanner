#!/usr/bin/env python3
"""
GitHub Organization Security Scanner (Original Version)

Scans all repositories in a GitHub organization and checks whether
key security features are enabled: secret scanning, Dependabot alerts,
and code scanning (via GitHub Advanced Security).

This is a straightforward script that works — until it doesn't.
See the README for the failure modes that motivated the Temporal version.
"""

import argparse
import json
import os
import sys
import time

import requests


DEFAULT_ORG = "eclipse-bci"


def fetch_repositories(org: str, token: str | None) -> list[dict]:
    """Fetch all repositories for the given organization using GitHub REST API."""
    repos = []
    page = 1
    headers = {"Authorization": f"token {token}"} if token else {}

    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}"
        response = requests.get(url, headers=headers)

        if response.status_code == 404:
            print(f"Error: Organization '{org}' not found or access denied.")
            sys.exit(1)
        if response.status_code == 401:
            print("Error: Invalid API token.")
            sys.exit(1)
        if response.status_code == 403 and "rate limit" in response.text.lower():
            # Rate limited — wait and hope for the best
            print("Rate limited by GitHub API. Waiting 60 seconds...")
            time.sleep(60)
            continue
        if response.status_code != 200:
            print(f"Error fetching repositories: {response.status_code}")
            sys.exit(1)

        data = response.json()
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1

    return repos


def check_repo_security(org: str, repo_name: str, token: str | None) -> dict:
    """Check security settings for a single repository."""
    headers = {"Authorization": f"token {token}"} if token else {}
    result = {
        "repository": repo_name,
        "secret_scanning": "unknown",
        "dependabot_alerts": "unknown",
        "code_scanning": "unknown",
    }

    # Check repository settings (includes secret scanning status)
    url = f"https://api.github.com/repos/{org}/{repo_name}"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        secret_status = data.get("security_and_analysis", {})
        result["secret_scanning"] = (
            secret_status.get("secret_scanning", {}).get("status", "disabled")
        )

    # Check Dependabot alerts
    url = f"https://api.github.com/repos/{org}/{repo_name}/vulnerability-alerts"
    headers_accept = {**headers, "Accept": "application/vnd.github.dorian-preview+json"}
    resp = requests.get(url, headers=headers_accept)
    if resp.status_code == 204:
        result["dependabot_alerts"] = "enabled"
    elif resp.status_code == 404:
        result["dependabot_alerts"] = "disabled"

    # Check code scanning alerts (GHAS)
    url = f"https://api.github.com/repos/{org}/{repo_name}/code-scanning/alerts"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        result["code_scanning"] = "enabled"
    elif resp.status_code == 404:
        result["code_scanning"] = "not configured"
    elif resp.status_code == 403:
        result["code_scanning"] = "no access (GHAS required)"

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Check security settings across a GitHub organization."
    )
    parser.add_argument(
        "--org",
        default=DEFAULT_ORG,
        help="GitHub organization to scan",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (or set GITHUB_TOKEN env var)",
    )
    args = parser.parse_args()

    org = args.org
    token = args.token or os.getenv("GITHUB_TOKEN")

    if not token:
        print("No token provided. Scanning public repos only (unauthenticated).\n")

    print(f"Fetching repositories from '{org}'...\n")
    repos = fetch_repositories(org, token)
    if not repos:
        print(f"No repositories found for '{org}'.")
        sys.exit(1)

    print(f"Found {len(repos)} repositories. Scanning security settings...\n")

    results = []
    for i, repo in enumerate(repos, 1):
        name = repo.get("name", "(unknown)")
        print(f"  [{i}/{len(repos)}] Checking {name}...")
        result = check_repo_security(org, name, token)
        results.append(result)

    # Summary
    enabled_count = sum(1 for r in results if r["secret_scanning"] == "enabled")
    print(f"\n--- Summary ---")
    print(f"Total repositories: {len(results)}")
    print(f"Secret scanning enabled: {enabled_count}/{len(results)}")

    # Save results
    output_file = f"security_scan_{org}.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to '{output_file}'.")


if __name__ == "__main__":
    main()
