"""Constants for the LSP subsystem."""

# RPC error codes
LSP_ERROR_CONTENT_MODIFIED: int = -32801
"""RPC error code: content has been modified (retryable)."""

# Retry configuration
MAX_RETRIES_FOR_CONTENT_MODIFIED: int = 3
"""Maximum number of retries for ContentModified errors."""

RETRY_BASE_DELAY_MS: int = 500
"""Initial exponential back-off delay in milliseconds."""

# Server startup
DEFAULT_STARTUP_TIMEOUT_MS: int = 45_000
"""Default server startup timeout in milliseconds."""

MAX_LSP_FILE_SIZE_BYTES: int = 10 * 1024 * 1024
"""Maximum file size for LSP operations (10 MB)."""

DEFAULT_GOPLS_TIMEOUT_MS: int = 60_000
"""gopls special startup timeout in milliseconds."""

# Crash recovery
MAX_CRASH_RECOVERY_ATTEMPTS: int = 3
"""Maximum number of crash recovery attempts before marking server as ERROR."""

# Request timeout
DEFAULT_REQUEST_TIMEOUT_MS: int = 15_000
"""Default timeout for a single LSP request (15 seconds)."""
