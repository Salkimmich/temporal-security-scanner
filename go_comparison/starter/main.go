// Starter is the Go equivalent of Python's temporal/starter.py.
// It starts, queries, and cancels SecurityScanWorkflow on the "security-scanner-go" task queue.
//
// Usage:
//
//	go run ./go_comparison/starter --org temporalio
//	Set GITHUB_TOKEN to avoid rate limits. Then:
//	go run ./go_comparison/starter --org temporalio --no-wait
//	go run ./go_comparison/starter --org temporalio --query
//	go run ./go_comparison/starter --org temporalio --cancel "reason"
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"go.temporal.io/api/enums"
	"go.temporal.io/sdk/client"

	scanner "github.com/salkimmich/temporal-security-scanner/go_comparison"
)

const (
	taskQueue        = "security-scanner-go"
	executionTimeout = 30 * time.Minute
)

func main() {
	org := flag.String("org", "", "GitHub organization to scan (required)")
	token := flag.String("token", "", "GitHub PAT (or set GITHUB_TOKEN)")
	noWait := flag.Bool("no-wait", false, "Start workflow and exit without waiting")
	query := flag.Bool("query", false, "Query progress of a running scan")
	cancelReason := flag.String("cancel", "", "Cancel a running scan with this reason")
	flag.Parse()

	if *org == "" {
		fmt.Fprintln(os.Stderr, "Error: --org is required")
		flag.Usage()
		os.Exit(1)
	}

	if *token == "" {
		*token = os.Getenv("GITHUB_TOKEN")
	}
	if *token == "" {
		fmt.Println("Note: No GitHub token. Scanning public repos only (60 req/hr). Set GITHUB_TOKEN for higher limits.")
	}

	c, err := client.Dial(client.Options{HostPort: client.DefaultHostPort})
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create Temporal client: %v\n", err)
		os.Exit(1)
	}
	defer c.Close()

	workflowID := "security-scan-" + *org

	if *query {
		doQuery(c, workflowID, *org)
		return
	}
	if *cancelReason != "" {
		doCancel(c, workflowID, *cancelReason)
		return
	}

	// Start workflow
	input := scanner.ScanInput{Org: *org}
	if *token != "" {
		input.Token = token
	}

	fmt.Printf("Starting security scan for '%s'...\n", *org)
	fmt.Printf("  Workflow ID: %s\n", workflowID)
	fmt.Printf("  Task Queue:  %s\n", taskQueue)
	fmt.Printf("  Timeout:     %s\n\n", executionTimeout)

	options := client.StartWorkflowOptions{
		ID:                         workflowID,
		TaskQueue:                  taskQueue,
		WorkflowExecutionTimeout:   executionTimeout,
		WorkflowIDReusePolicy:      enums.WORKFLOW_ID_REUSE_POLICY_TERMINATE_EXISTING,
	}

	we, err := c.ExecuteWorkflow(context.Background(), options, scanner.SecurityScanWorkflow, input)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start workflow: %v\n", err)
		os.Exit(1)
	}

	if *noWait {
		fmt.Println("Workflow started.")
		fmt.Printf("  Query:  go run ./go_comparison/starter --org %s --query\n", *org)
		fmt.Printf("  Cancel: go run ./go_comparison/starter --org %s --cancel \"reason\"\n", *org)
		fmt.Printf("  UI:     http://localhost:8233/namespaces/default/workflows/%s\n", workflowID)
		return
	}

	fmt.Println("Scanning... (use --query in another terminal to check progress)\n")

	var result map[string]interface{}
	err = we.Get(context.Background(), &result)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Workflow failed: %v\n", err)
		os.Exit(1)
	}

	printReport(result)
	outPath := "security_scan_" + *org + ".json"
	b, _ := json.MarshalIndent(result, "", "  ")
	_ = os.WriteFile(outPath, b, 0644)
	fmt.Printf("\nReport saved to %s\n", outPath)
}

func doQuery(c client.Client, workflowID, org string) {
	ctx := context.Background()
	handle := c.GetWorkflowHandle(workflowID)

	var progress scanner.ScanProgress
	err := handle.Query(ctx, "progress", &progress)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Query failed: %v\n", err)
		fmt.Fprintf(os.Stderr, "Is a scan running? Start one with: go run ./go_comparison/starter --org %s\n", org)
		os.Exit(1)
	}

	fmt.Printf("Security Scan Progress: %s\n", org)
	fmt.Printf("  Status:       %s\n", progress.Status)
	fmt.Printf("  Progress:     %d/%d repos (%.1f%%)\n",
		progress.ScannedRepos, progress.TotalRepos, progress.PercentComplete())
	fmt.Printf("  Compliant:    %d\n", progress.CompliantRepos)
	fmt.Printf("  Non-compliant: %d\n", progress.NonCompliantRepos)
	fmt.Printf("  Errors:       %d\n", progress.Errors)
}

func doCancel(c client.Client, workflowID, reason string) {
	ctx := context.Background()
	handle := c.GetWorkflowHandle(workflowID)
	fmt.Printf("Sending cancel signal to workflow '%s'...\n", workflowID)
	fmt.Printf("  Reason: %s\n", reason)
	err := handle.Signal(ctx, "cancel_scan", reason)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Signal failed: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("\nSignal sent. The scan will stop after the current batch and produce a partial report.")
}

func printReport(result map[string]interface{}) {
	fmt.Println()
	fmt.Println("============================================================")
	if cancelled, _ := result["cancelled"].(bool); cancelled {
		fmt.Printf("  Security Scan CANCELLED: %v\n", result["org"])
		fmt.Printf("  Reason: %v\n", result["cancel_reason"])
		fmt.Printf("  Partial results (%v of %v repos scanned)\n",
			result["repos_scanned_before_cancel"], result["total_repos"])
	} else {
		fmt.Printf("  Security Scan Complete: %v\n", result["org"])
	}
	fmt.Println("============================================================")
	fmt.Printf("  Total repositories:   %v\n", result["total_repos"])
	fmt.Printf("  Fully compliant:      %v\n", result["fully_compliant"])
	fmt.Printf("  Compliance rate:      %v\n", result["compliance_rate"])
	fmt.Printf("  Secret scanning:      %v/%v\n", result["secret_scanning_enabled"], result["total_repos"])
	fmt.Printf("  Dependabot alerts:    %v/%v\n", result["dependabot_enabled"], result["total_repos"])
	fmt.Printf("  Code scanning (GHAS): %v/%v\n", result["code_scanning_enabled"], result["total_repos"])
	if errs, ok := result["errors"].(float64); ok && errs > 0 {
		fmt.Printf("  Errors:               %.0f\n", errs)
	}
	if repos, ok := result["non_compliant_repos"].([]interface{}); ok && len(repos) > 0 {
		fmt.Println("\n  Non-compliant repos:")
		for _, r := range repos {
			fmt.Printf("    - %v\n", r)
		}
	}
	fmt.Println("============================================================")
}
