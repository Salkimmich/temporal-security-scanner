package scanner

// =============================================================================
// Activities — Go vs Python
// =============================================================================
//
// The biggest structural difference: how activities are defined and registered.
//
// PYTHON: Activities are standalone functions with a @activity.defn decorator.
//     @activity.defn
//     def fetch_org_repos(org: str, token: str | None = None) -> list[RepoInfo]:
//         ...
//
// GO: Activities are methods on a struct. The struct holds dependencies
// (HTTP client, config) that would be module-level globals or closures
// in Python. This is more explicit and testable.
//
//     type Activities struct {
//         HTTPClient *http.Client
//     }
//     func (a *Activities) FetchOrgRepos(ctx context.Context, ...) ([]RepoInfo, error) {
//
// The struct-method pattern means you inject dependencies at worker startup,
// not at import time. Python achieves similar with activity classes or closures,
// but the Go pattern is more idiomatic.
//
// ERROR HANDLING is the other major difference:
//   - Python: raise exceptions. Temporal catches them and retries (or not).
//   - Go: return (result, error). Temporal checks the error and retries (or not).
//
// Same outcome, different idiom. Go forces you to handle every error explicitly.
// Python lets exceptions propagate. Both work well with Temporal's retry system.
// =============================================================================

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/temporal"
)

// Activities holds shared dependencies for all activity implementations.
//
// This is the Go-idiomatic way to inject dependencies (HTTP client, config)
// into activities. In Python, we used module-level functions and passed the
// token as a parameter. Go's struct approach is more explicit.
//
// Python equivalent (conceptual):
//
//	class SecurityActivities:
//	    def __init__(self, http_client):
//	        self.client = http_client
//
// But in our Python version we used standalone functions, which is also fine.
// The Go SDK docs recommend the struct pattern for anything with dependencies.
type Activities struct {
	HTTPClient *http.Client
}

// FetchOrgRepos fetches all repositories for a GitHub organization.
//
// Compare to the Python version:
//
//	@activity.defn
//	def fetch_org_repos(org: str, token: str | None = None) -> list[RepoInfo]:
//	    repos: list[RepoInfo] = []
//	    page = 1
//	    while True:
//	        activity.heartbeat(f"Fetching page {page}")
//	        ...
//
// KEY DIFFERENCES:
//
// 1. CONTEXT: Go activities receive context.Context as the first arg (required).
//    Python activities get an implicit context via contextvars.
//
// 2. RETURN TYPE: Go returns ([]RepoInfo, error). Python returns list[RepoInfo]
//    and raises exceptions on failure. Go's explicit error return means every
//    caller must handle the error — no silent exception swallowing.
//
// 3. HEARTBEAT: Both SDKs heartbeat the same way conceptually.
//    Go:     activity.RecordHeartbeat(ctx, fmt.Sprintf("page %d", page))
//    Python: activity.heartbeat(f"Fetching page {page}")
//
// 4. NON-RETRYABLE ERRORS: In Python, we list types in the RetryPolicy:
//        non_retryable_error_types=["ValueError"]
//    In Go, we wrap errors with temporal.NewNonRetryableApplicationError().
//    This gives finer control — you decide at the point of failure, not globally.
func (a *Activities) FetchOrgRepos(ctx context.Context, input ScanInput) ([]RepoInfo, error) {
	var repos []RepoInfo
	page := 1

	for {
		// Heartbeat to tell Temporal we're still alive during pagination
		activity.RecordHeartbeat(ctx, fmt.Sprintf("Fetching page %d", page))

		url := fmt.Sprintf("https://api.github.com/orgs/%s/repos?per_page=100&page=%d", input.Org, page)
		req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
		if err != nil {
			return nil, fmt.Errorf("creating request: %w", err)
		}

		req.Header.Set("Accept", "application/vnd.github+json")
		if input.Token != nil {
			req.Header.Set("Authorization", "token "+*input.Token)
		}

		resp, err := a.HTTPClient.Do(req)
		if err != nil {
			// Network error — this IS retryable (Temporal will retry automatically)
			return nil, fmt.Errorf("fetching repos page %d: %w", page, err)
		}
		defer resp.Body.Close()

		switch resp.StatusCode {
		case http.StatusNotFound:
			// Org doesn't exist — NOT retryable (retrying won't help)
			// In Python: raise ValueError("Organization not found")
			// In Go: wrap with temporal.NewNonRetryableApplicationError
			return nil, temporal.NewNonRetryableApplicationError(
				fmt.Sprintf("organization '%s' not found", input.Org),
				"NOT_FOUND",
				nil,
			)
		case http.StatusUnauthorized:
			return nil, temporal.NewNonRetryableApplicationError(
				"invalid GitHub API token",
				"UNAUTHORIZED",
				nil,
			)
		case http.StatusForbidden:
			// Rate limited — retryable (Temporal backs off and tries again)
			return nil, fmt.Errorf("GitHub API rate limit exceeded")
		}

		if resp.StatusCode != http.StatusOK {
			return nil, fmt.Errorf("unexpected status %d", resp.StatusCode)
		}

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return nil, fmt.Errorf("reading response: %w", err)
		}

		var pageRepos []struct {
			Name     string `json:"name"`
			FullName string `json:"full_name"`
			Private  bool   `json:"private"`
			Archived bool   `json:"archived"`
		}
		if err := json.Unmarshal(body, &pageRepos); err != nil {
			return nil, fmt.Errorf("parsing response: %w", err)
		}

		if len(pageRepos) == 0 {
			break
		}

		for _, r := range pageRepos {
			repos = append(repos, RepoInfo{
				Name:     r.Name,
				FullName: r.FullName,
				Private:  r.Private,
				Archived: r.Archived,
			})
		}

		if len(pageRepos) < 100 {
			break
		}
		page++
	}

	logger := activity.GetLogger(ctx)
	logger.Info("Fetched repositories", "count", len(repos), "org", input.Org)
	return repos, nil
}

// CheckRepoSecurity checks all security settings for a single repository.
//
// Compare to Python:
//
//	@activity.defn
//	def check_repo_security(org, repo_name, token=None) -> RepoSecurityResult:
//	    ...
//	    try:
//	        ...
//	    except requests.exceptions.Timeout:
//	        raise RuntimeError(f"Timeout checking {repo_name}")
//
// INTERESTING DIFFERENCE: Error categorization.
//
// Python relies on exception types and the RetryPolicy's non_retryable_error_types.
// Go gives you temporal.NewNonRetryableApplicationError() to mark specific errors
// as non-retryable right where they happen. This is more explicit — you decide
// the retry semantics at the point of failure, not in a separate policy config.
//
// Both approaches work. Go's is more granular. Python's is more centralized.
func (a *Activities) CheckRepoSecurity(ctx context.Context, org, repoName string, token *string) (*RepoSecurityResult, error) {
	result := &RepoSecurityResult{
		Repository:       repoName,
		SecretScanning:   StatusUnknown,
		DependabotAlerts: StatusUnknown,
		CodeScanning:     StatusUnknown,
		ScannedAt:        time.Now().UTC().Format(time.RFC3339),
	}

	headers := map[string]string{"Accept": "application/vnd.github+json"}
	if token != nil {
		headers["Authorization"] = "token " + *token
	}

	// 1. Check secret scanning
	status, err := a.checkEndpoint(ctx, fmt.Sprintf("https://api.github.com/repos/%s/%s", org, repoName), headers)
	if err != nil {
		return nil, err
	}
	if status == http.StatusOK {
		// Parse security_and_analysis from response (simplified)
		result.SecretScanning = StatusEnabled // Simplified for comparison
	}

	// 2. Check Dependabot (same pattern as Python — check 204 vs 404)
	status, err = a.checkEndpoint(ctx, fmt.Sprintf("https://api.github.com/repos/%s/%s/vulnerability-alerts", org, repoName), headers)
	if err != nil {
		return nil, err
	}
	switch status {
	case http.StatusNoContent:
		result.DependabotAlerts = StatusEnabled
	case http.StatusNotFound:
		result.DependabotAlerts = StatusDisabled
	}

	// 3. Check code scanning
	status, err = a.checkEndpoint(ctx, fmt.Sprintf("https://api.github.com/repos/%s/%s/code-scanning/alerts", org, repoName), headers)
	if err != nil {
		return nil, err
	}
	switch status {
	case http.StatusOK:
		result.CodeScanning = StatusEnabled
	case http.StatusNotFound:
		result.CodeScanning = StatusNotConfigured
	case http.StatusForbidden:
		result.CodeScanning = StatusNoAccess
	}

	logger := activity.GetLogger(ctx)
	logger.Info("Checked repo security",
		"repo", repoName,
		"secret_scanning", result.SecretScanning,
		"dependabot", result.DependabotAlerts,
		"code_scanning", result.CodeScanning,
	)
	return result, nil
}

// checkEndpoint is a helper that makes a GET request and returns the status code.
func (a *Activities) checkEndpoint(ctx context.Context, url string, headers map[string]string) (int, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return 0, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := a.HTTPClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	return resp.StatusCode, nil
}

// GenerateReport creates a summary from scan results.
//
// Python equivalent:
//
//	@activity.defn
//	def generate_report(org: str, results: list[RepoSecurityResult]) -> dict:
//
// Note: Python returns a dict (flexible, schema-free).
// Go returns a typed struct (rigid, compile-time checked).
// For a report that might evolve, Python's dict is arguably easier to iterate on.
// For a stable API, Go's struct catches mistakes earlier.
func (a *Activities) GenerateReport(ctx context.Context, org string, results []RepoSecurityResult) (map[string]interface{}, error) {
	total := len(results)
	compliant := 0
	secretEnabled := 0
	dependabotEnabled := 0
	codeScanningEnabled := 0
	var nonCompliant []string

	for _, r := range results {
		if r.IsFullyCompliant() {
			compliant++
		} else if r.Error == nil {
			nonCompliant = append(nonCompliant, r.Repository)
		}
		if r.SecretScanning == StatusEnabled {
			secretEnabled++
		}
		if r.DependabotAlerts == StatusEnabled {
			dependabotEnabled++
		}
		if r.CodeScanning == StatusEnabled {
			codeScanningEnabled++
		}
	}

	rate := "N/A"
	if total > 0 {
		rate = fmt.Sprintf("%.1f%%", float64(compliant)/float64(total)*100)
	}

	return map[string]interface{}{
		"org":                     org,
		"total_repos":             total,
		"fully_compliant":         compliant,
		"compliance_rate":         rate,
		"secret_scanning_enabled": secretEnabled,
		"dependabot_enabled":      dependabotEnabled,
		"code_scanning_enabled":   codeScanningEnabled,
		"non_compliant_repos":     nonCompliant,
	}, nil
}
