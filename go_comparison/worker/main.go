package main

// =============================================================================
// Worker — Go vs Python
// =============================================================================
//
// The worker is where the SDK differences are smallest. Both languages:
// 1. Connect to the Temporal server
// 2. Register workflows and activities
// 3. Start polling for tasks
//
// The main structural difference: Go activities are struct methods, so you
// register an *instance* of the struct. Python activities are standalone
// functions, so you register the functions directly.
//
// PYTHON:
//     worker = Worker(
//         client,
//         task_queue=TASK_QUEUE,
//         workflows=[SecurityScanWorkflow],
//         activities=[fetch_org_repos, check_repo_security, generate_report],
//         activity_executor=executor,  # ThreadPoolExecutor for sync activities
//     )
//     await worker.run()
//
// GO: (below)
//     w := worker.New(c, TaskQueue, worker.Options{})
//     w.RegisterWorkflow(scanner.SecurityScanWorkflow)
//     w.RegisterActivity(&activities)  // Register the struct instance
//     w.Run(worker.InterruptCh())
//
// NOTABLE DIFFERENCE: Python needs a ThreadPoolExecutor for synchronous
// activities (because they block the event loop). Go activities are natively
// synchronous — the Go SDK handles concurrency internally via goroutines.
// This is one area where Go's concurrency model is genuinely simpler.
// =============================================================================

import (
	"log"
	"net/http"
	"time"

	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/worker"

	scanner "github.com/salkimmich/temporal-security-scanner/go_comparison"
)

const TaskQueue = "security-scanner"

func main() {
	// Connect to Temporal server
	// Python: client = await Client.connect("localhost:7233")
	c, err := client.Dial(client.Options{
		HostPort: client.DefaultHostPort, // localhost:7233
	})
	if err != nil {
		log.Fatalln("Unable to create Temporal client:", err)
	}
	defer c.Close()

	// Create worker
	// Python: Worker(client, task_queue=TASK_QUEUE, ...)
	w := worker.New(c, TaskQueue, worker.Options{})

	// Register workflow
	// Python: workflows=[SecurityScanWorkflow]
	w.RegisterWorkflow(scanner.SecurityScanWorkflow)

	// Create activity struct with dependencies and register it.
	//
	// This is the key difference: Go registers a *struct instance*.
	// All methods on that struct become available as activities.
	// Python registers individual functions:
	//     activities=[fetch_org_repos, check_repo_security, generate_report]
	//
	// Go's approach:
	//   - Dependencies (HTTP client) are injected once at startup
	//   - All activities on the struct share them
	//   - Easy to swap in a mock client for testing
	//
	// Python's approach:
	//   - Each function is independent
	//   - Dependencies passed as parameters or via module globals
	//   - For testing, you register different functions entirely
	activities := &scanner.Activities{
		HTTPClient: &http.Client{Timeout: 30 * time.Second},
	}
	w.RegisterActivity(activities)

	log.Printf("Worker started on task queue '%s'", TaskQueue)

	// Run the worker until interrupted.
	//
	// Python: await worker.run()
	//
	// worker.InterruptCh() returns a channel that closes on SIGINT/SIGTERM.
	// This is Go's idiomatic signal handling. Python's asyncio.run() handles
	// this via its event loop.
	err = w.Run(worker.InterruptCh())
	if err != nil {
		log.Fatalln("Worker failed:", err)
	}
}
