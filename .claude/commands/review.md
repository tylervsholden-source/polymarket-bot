Perform a comprehensive code review from 5 perspectives. Analyze all recently changed files (use `git diff` and `git diff --cached`).

## 5 Review Dimensions

### 1. Correctness
- Logic errors, off-by-one, wrong variable usage
- Edge cases not handled
- Race conditions in async code
- Math/formula errors (especially in Bayesian, Kelly, MACD calculations)

### 2. Security
- API key exposure, hardcoded secrets
- Injection risks in any user input paths
- Unsafe deserialization
- CLOB API authentication issues

### 3. Performance
- Unnecessary API calls or redundant fetches
- N+1 query patterns in market scanning
- Memory leaks (growing caches without eviction)
- Blocking calls in async context

### 4. Reliability
- Error handling: are exceptions caught and handled properly?
- Retry logic: are transient failures retried?
- Data integrity: can positions.json get corrupted?
- Graceful degradation when external APIs fail

### 5. Trading Logic
- Is the Bayesian model mathematically sound?
- Are signal weights justified by data?
- Position sizing: does Kelly formula have correct inputs?
- Are there scenarios where the bot could lose more than expected?
- Entry/exit timing: are windows correct?

## Output Format
For each dimension, provide:
- **Status**: PASS / WARN / FAIL
- **Findings**: Specific issues with file:line references
- **Severity**: Critical / High / Medium / Low
- **Fix suggestion**: One-liner on how to fix

End with an overall risk assessment.
