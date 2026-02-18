package scanner

// =============================================================================
// Models — Go vs Python
// =============================================================================
//
// PYTHON uses @dataclass for workflow/activity inputs and outputs:
//
//     @dataclass
//     class ScanInput:
//         org: str
//         token: str | None = None
//
// GO uses plain structs. The serialization story is different: Go's default
// data converter uses JSON, so struct field tags control the wire format.
// Python dataclasses serialize via json.dumps with type-hint awareness.
//
// KEY DIFFERENCE: Go has no None/nil for strings. You use a pointer (*string)
// to represent "optional." Python's `str | None = None` is more ergonomic here.
//
// SHARED PRINCIPLE: Both SDKs strongly recommend a single struct/dataclass as
// the parameter to workflows and activities, rather than multiple positional
// args. This makes it safe to add fields later without breaking compatibility.
// =============================================================================

// ScanInput is the input to the SecurityScanWorkflow.
//
// Python equivalent:
//
//	@dataclass
//	class ScanInput:
//	    org: str
//	    token: str | None = None
type ScanInput struct {
	Org   string  `json:"org"`
	Token *string `json:"token,omitempty"` // Pointer = optional (nil when absent)
}

// RepoInfo contains minimal repository data needed for scanning.
//
// Python equivalent:
//
//	@dataclass
//	class RepoInfo:
//	    name: str
//	    full_name: str
//	    private: bool = False
//	    archived: bool = False
type RepoInfo struct {
	Name     string `json:"name"`
	FullName string `json:"full_name"`
	Private  bool   `json:"private"`
	Archived bool   `json:"archived"`
}

// SecurityStatus represents the state of a security feature.
//
// Python uses StrEnum. Go uses typed string constants.
// Go's approach is more verbose but catches typos at compile time.
//
// Python:
//
//	class SecurityStatus(StrEnum):
//	    ENABLED = "enabled"
//	    DISABLED = "disabled"
type SecurityStatus string

const (
	StatusEnabled       SecurityStatus = "enabled"
	StatusDisabled      SecurityStatus = "disabled"
	StatusNotConfigured SecurityStatus = "not configured"
	StatusNoAccess      SecurityStatus = "no access"
	StatusUnknown       SecurityStatus = "unknown"
	StatusError         SecurityStatus = "error"
)

// RepoSecurityResult holds the scan result for one repository.
//
// Python equivalent uses a @property for is_fully_compliant.
// Go uses an explicit method — same concept, different syntax.
//
//	@dataclass
//	class RepoSecurityResult:
//	    repository: str
//	    secret_scanning: str = SecurityStatus.UNKNOWN
//	    ...
//	    @property
//	    def is_fully_compliant(self) -> bool:
//	        return (self.secret_scanning == SecurityStatus.ENABLED and ...)
type RepoSecurityResult struct {
	Repository      string         `json:"repository"`
	SecretScanning  SecurityStatus `json:"secret_scanning"`
	DependabotAlerts SecurityStatus `json:"dependabot_alerts"`
	CodeScanning    SecurityStatus `json:"code_scanning"`
	Error           *string        `json:"error,omitempty"`
	ScannedAt       string         `json:"scanned_at"`
}

// IsFullyCompliant checks whether all security features are enabled.
// In Python this is a @property; in Go it's an explicit method.
func (r *RepoSecurityResult) IsFullyCompliant() bool {
	return r.SecretScanning == StatusEnabled &&
		r.DependabotAlerts == StatusEnabled &&
		r.CodeScanning == StatusEnabled
}

// ScanProgress represents the queryable state of an in-flight scan.
//
// This struct is returned by the workflow's query handler.
// Both Go and Python use the same pattern: the workflow maintains
// an instance of this struct as internal state, and a query handler
// returns it on demand.
type ScanProgress struct {
	Org              string `json:"org"`
	TotalRepos       int    `json:"total_repos"`
	ScannedRepos     int    `json:"scanned_repos"`
	CompliantRepos   int    `json:"compliant_repos"`
	NonCompliantRepos int   `json:"non_compliant_repos"`
	Errors           int    `json:"errors"`
	Status           string `json:"status"`
}

// PercentComplete calculates completion percentage.
// Python uses a @property; Go uses a method.
func (p *ScanProgress) PercentComplete() float64 {
	if p.TotalRepos == 0 {
		return 0
	}
	return float64(p.ScannedRepos) / float64(p.TotalRepos) * 100
}
