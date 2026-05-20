# PR Sentinel Review Report

## Summary
- Risk Level: **High**
- Source: `file:samples\sample.diff`
- Base branch: `main`
- Reviewed at: 2026-05-20T09:54:06+00:00
- Agents: Security Agent
- 4 finding(s): 2 High, 2 Medium.

## Merge Verdict

Do not raise this PR or merge as-is. The review surfaced 2 High-severity issue(s) that represent real risk (e.g. exposed secrets, auth gaps, or unsafe data handling). Resolve every High finding and re-run the review before opening the PR.

## Key Findings

- **High** — `src/UserService.cs` (`AdminToken constant`) — Hardcoded API token committed in source code. _(from Security Agent)_
- **High** — `src/UserService.cs` (`CreateUser method`) — User password is written to standard output / logs in plaintext. _(from Security Agent)_
- **Medium** — `src/UserService.cs` (`CreateUser method`) — UserRequest fields (Email, Name, Password) are persisted without any validation or sanitization. _(from Security Agent)_
- **Medium** — `src/UserService.cs` (`CreateUser method`) — Password from the request is accepted but never hashed or stored, and is only emitted to logs. _(from Security Agent)_

## Key Recommendations

- `src/UserService.cs` — Remove the constant immediately, rotate the token at the provider, and load it at runtime from a secret store or environment variable (e.g., IConfiguration, Azure Key Vault, AWS Secrets Manager).
- `src/UserService.cs` — Never log credentials. Remove the password from the log statement entirely; if a creation event must be logged, log only a non-sensitive identifier (e.g., a hashed user id) at an appropriate log level.
- `src/UserService.cs` — Validate req.Email (format + length), req.Name (length + allowed characters), and enforce a password policy before calling _repo.Insert. Reject or sanitize invalid input at this boundary.
- `src/UserService.cs` — If passwords are required, hash with a modern KDF (e.g., PBKDF2/Argon2/bcrypt) before persisting and never log the plaintext. If passwords are not needed here, remove the field from UserRequest.

## All Findings

### High — Security Agent
- File: `src/UserService.cs`
- Location: `AdminToken constant`

**Issue:** Hardcoded API token committed in source code.

**Reasoning:** The constant 'sk-live-abc123-DO-NOT-COMMIT' appears to be a live API key embedded in source. Anyone with repo access (or anyone who sees the git history after rotation) can use it to impersonate an admin.

**Recommendation:** Remove the constant immediately, rotate the token at the provider, and load it at runtime from a secret store or environment variable (e.g., IConfiguration, Azure Key Vault, AWS Secrets Manager).

### High — Security Agent
- File: `src/UserService.cs`
- Location: `CreateUser method`

**Issue:** User password is written to standard output / logs in plaintext.

**Reasoning:** Console.WriteLine logs the password alongside the email. Plaintext credentials in logs are a serious leak: log aggregators, crash dumps, and operators all gain access to user passwords, and this likely violates GDPR/PCI/SOC2 obligations.

**Recommendation:** Never log credentials. Remove the password from the log statement entirely; if a creation event must be logged, log only a non-sensitive identifier (e.g., a hashed user id) at an appropriate log level.

### Medium — Security Agent
- File: `src/UserService.cs`
- Location: `CreateUser method`

**Issue:** UserRequest fields (Email, Name, Password) are persisted without any validation or sanitization.

**Reasoning:** Data crosses a trust boundary into the repository with no length, format, or content checks. Missing validation can enable malformed data, oversized inputs, or downstream injection depending on how IUserRepository handles the values.

**Recommendation:** Validate req.Email (format + length), req.Name (length + allowed characters), and enforce a password policy before calling _repo.Insert. Reject or sanitize invalid input at this boundary.

### Medium — Security Agent
- File: `src/UserService.cs`
- Location: `CreateUser method`

**Issue:** Password from the request is accepted but never hashed or stored, and is only emitted to logs.

**Reasoning:** The method receives a password yet discards it after logging — either authentication will silently break, or a future change will persist it. Either path is a security concern (logged plaintext credential, or a precedent for storing unhashed passwords).

**Recommendation:** If passwords are required, hash with a modern KDF (e.g., PBKDF2/Argon2/bcrypt) before persisting and never log the plaintext. If passwords are not needed here, remove the field from UserRequest.

