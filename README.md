# Sudo CLI

Coding on a phone is hostile. Screen space is limited, touch keyboards are slow, and switching contexts is painful. 

Sudo CLI brings desktop-grade language models directly into your Android terminal to solve this. Unlike a thin chat client, Sudo understands your local workspace. It actively reads your file system, navigates your projects, and writes code for you, all from within a secure sandbox.

---

## Why Termux?

Mobile hardware has plenty of compute, but the bottleneck is ergonomics.

You don't need a heavy IDE on a 6-inch screen. You need an assistant that can execute commands, read logs, and modify files. Sudo CLI turns a raw Linux environment into a capable engineering workstation, exactly when you are away from your desk.

---

## Why not VS Code?

VS Code is unmatched for deep, focused engineering work. Sudo CLI solves a different problem: the mobile-first workflow. 

When you need to debug a server failure from your phone, apply a quick fix over SSH, or write a script while commuting, a heavy UI is a liability. Sudo CLI is built for transient environments where you need intelligent assistance, but only have access to a raw terminal.

---

## Example Session

```bash
$ sudo

Workspace detected.
Provider: Anthropic (claude-3.5-sonnet)

> The authentication tests are failing. Can you find why?

[Sentinel] Reading tests/test_auth.py...
[Sentinel] Reading src/auth/token.py...
[Sudo] The tests expect a 401 status code, but `verify_token` raises a 500 when the token is malformed. I can catch the ValueError and return 401.

> Fix it.

[Sentinel] Modifying src/auth/token.py...

Done. The tests should pass now.
```

---

## Philosophy

**Secure by Default**  
We don't trust the AI, and we don't trust the network. API keys are encrypted at rest using AES-128 and PBKDF2. Execution boundaries are strictly defined.

**Terminal Native**  
No webviews. No heavy background daemons. Everything goes through standard streams (`stdout` / `stdin`). When you need to script it, it gracefully pipes data.

**Graceful Degradation**  
Mobile networks drop. Connections timeout. State is aggressively summarized to prevent context-window blowouts. When the network fails, your working state remains intact.

---

## Architecture

The system operates as a strict pipeline. Input is parsed, constrained by rules, and handed to the execution core. 

```text
User Input
   │
   ▼
[ CLI Router ] ───── Parses flags and handles routing
   │
   ▼
[ Sandbox ] ──────── Validates paths and blocks risky actions
   │
   ▼
[ Context ] ──────── Tokenizes and summarizes session history
   │
   ▼
[ LLM ] ──────────── Streams responses back to the terminal
```

---

## The Security Model (Sentinel)

Giving an LLM access to your shell is a significant risk. 

Sudo mitigates this through **Sentinel**, a declarative security layer. Instead of checking permissions haphazardly throughout the codebase, file access is constrained at the lowest possible layer.

By default, operations outside your current working directory are explicitly blocked. If an agent attempts to read a sensitive file, Sentinel intercepts and kills the request before it reaches the filesystem.

---

## Plugin System & MCP

Plugins exist because an AI assistant cannot anticipate every toolchain. If you need to query a local SQLite database or interface with a specialized build system, you write a plugin. 

Sudo provides native integration with the Model Context Protocol (MCP). It dynamically discovers plugins in the `sudo/core/plugins` namespace, injects them into the execution context, and allows the LLM to call them seamlessly. Core features and external tools are treated equally.

---

## Repository Layout

```text
sudo/
├── cli.py             # Intercepts CLI requests before execution.
├── commands/          # Subcommands (chat, find, grep) separated to keep the core lean.
├── core/              # The execution engine.
│   ├── config.py      # Manages state securely using Fernet encryption.
│   ├── mcp.py         # Injects external MCP tools into the LLM context.
│   ├── provider.py    # Standardizes streaming responses across different backends.
│   ├── session.py     # Tracks tokens and persists conversational state to prevent context bloat.
│   └── tools.py       # Implements file operations, strictly guarded by the Sentinel sandbox.
└── mcp_servers/       # Embedded tools that provide local file system capabilities.
```

---

## Commands

```bash
# Start an interactive chat, context-aware of the current folder
sudo

# Analyze a stack trace piped from another process
cat error.log | sudo --pipe "Why did this crash?"

# Continue the previous session
sudo -c

# Manage LLM providers securely
sudo provider set anthropic
sudo provider key sk-...
```

---

## Engineering Decisions

**Stateless Operations**  
Commands are stateless by default. The only stateful component is the session context, which is serialized, aggressively summarized by `tiktoken`, and persisted to disk.

**Dependency Injection**  
Components like configuration and memory receive dependencies at runtime. This allows us to swap local storage for GCS synchronization seamlessly and makes testing trivial.

**Evidence-Based Correctness**  
With over 200 unit tests, every core mechanism is verified. The CLI requires a passing test suite before any PR is accepted. If it isn't tested, it doesn't merge.

---

## Future

Right now, Sudo is a chat assistant. The next phase introduces autonomous agent loops. We are building a system designed to independently debug stack traces, write fixes, and run tests—asking for permission only when necessary.

---

## Vision

Sudo CLI is a step toward making any Android device a capable engineering workstation. Because your terminal should be just as powerful in your pocket as it is on your desk.
