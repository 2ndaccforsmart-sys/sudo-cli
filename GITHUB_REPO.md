# GitHub Repository

**Repository:** https://github.com/2ndaccforsmart-sys/sudo-cli

---

## When to Push Changes

Push to GitHub when:
1. All 179+ tests pass (`pytest` passes cleanly)
2. You've verified the CLI runs without errors (`python -m sudo --help`, `python -m sudo status`, `python -m sudo provider list`)
3. You've manually tested the feature/fix works end-to-end
4. **You have explicit user approval** — ALWAYS ask before pushing

---

## Push Protocol

### Before Every Push:
1. Run `pytest` — must be 100% green
2. Run `python -m sudo --help` — verify CLI loads
3. Create a **HARD TRUTH REPORT** (see below)
4. Present report to user
5. Wait for explicit "yes, push" or "approve"

### Hard Truth Report Template:
```
## HARD TRUTH REPORT — Pre-Push Audit

### What Changed
- [List every file modified, added, deleted]

### Bugs Fixed (Real Ones)
- [Actual bugs with evidence, not wishful thinking]

### What's Still Fucked Up
- [Known issues, tech debt, hacks, workarounds]

### What's Better Now
- [Measurable improvements: speed, UX, reliability]

### What Could Be Better (But Isn't Yet)
- [Honest assessment of remaining gaps]

### Security Notes
- [Any key exposure risks, encryption status, data handling]

### Performance Impact
- [Latency changes, memory, startup time]

### Test Coverage
- [New tests added, coverage delta]

### Verdict
- [ ] Ready to push
- [ ] Needs more work
- [ ] Blocked by: [reason]
```

---

## Branch Strategy
- `main` — protected, only merge via PR after review
- Feature branches: `feat/<short-name>`, `fix/<short-name>`
- No force-push to main ever

---

## Commit Message Format
```
<type>(<scope>): <subject>

<body>

Closes #<issue>
```
Types: `feat`, `fix`, `refactor`, `perf`, `security`, `test`, `docs`, `chore`

---

## Never Push Without
- [ ] Tests passing
- [ ] Hard Truth Report written
- [ ] User explicitly said "push it" or "approved"
- [ ] No secrets/API keys in diff (check `git diff --cached`)

---

## Current Status
- **Last Verified Commit:** `235bafa` (HEAD)
- **Tests:** 211 passing (1 pre-existing MCP failure)
- **Streaming:** ✅ Implemented (OpenAI/Anthropic/Gemini)
- **Encrypted Config:** ✅ Fernet + PBKDF2
- **Sentinel Config:** ✅ Configurable via sentinel.json
- **Agent Loop:** Not implemented (P1)
- **Token Counter:** ✅ tiktoken with context management
- **list_dir tool:** ✅ Enabled with sentinel security