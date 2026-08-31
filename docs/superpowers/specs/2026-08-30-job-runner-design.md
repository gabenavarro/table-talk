# Starting work from the wall — design

**Status:** built 2026-08-30 (decisions 0387, c7dd, 4b2a, d81f)
**Scope:** the dashboard starts a Claude session in a project it already knows,
streams it, and shows its work on the wall. It does NOT talk to sessions it
did not start.

> This is the second copy of this document. The first, with its implementation
> plan and four task commits, existed only on one machine and was destroyed by
> a shell accident before it was ever pushed. Everything that had gone through
> a pull request survived; everything that had not, did not. That is the whole
> lesson, and it is recorded here rather than in a postmortem nobody reads.

## Why the Agent SDK

| Route | Verdict |
|---|---|
| The per-session unix socket at `$XDG_RUNTIME_DIR/cc-socks/<pid>.sock` | **Rejected.** Real and locally reachable, but undocumented, token-gated per session, and free to change in any release. A malformed frame lands in a live session doing real work in a real repository. |
| `tmux send-keys` | **Rejected.** Requires the session to run under tmux; none do. |
| `claude-agent-sdk` (`ClaudeSDKClient`) | **Chosen.** Supported, and a live multi-turn session rather than one-shot `claude -p` runs. |

## Measured, not assumed

Run on this machine against `claude-agent-sdk` 0.2.148 and Claude Code 2.1.221,
on a subscription OAuth login with no `ANTHROPIC_API_KEY`.

1. **Live multi-turn on the subscription.** Two turns, same `session_id`,
   memory across them. Headless bills the plan allowance, not a separate path.
2. **Concurrent cross-project sessions** work, each with its own id.
3. **A dashboard-minted `session_id` is honoured** and `resume` recovers it.
4. **`PreToolUse` sees every tool call; `can_use_tool` does not.** On one prompt
   using three tools: the hook saw `['Read', 'Bash', 'Write']`, the callback saw
   `['Write']`. The callback is the LAST step of evaluation, so safe reads never
   reach it.
5. **Allow rules shadow the callback and cannot be switched off.** A whole-tool
   `allowed_tools` entry auto-approves first; settings-file allow rules do the
   same, and `setting_sources=[]` does not exclude them (SDK issue #215). This
   machine has 390 such rules. A `PreToolUse` hook runs at step 1 and is not
   shadowed — a hook deny applies even under `bypassPermissions`.
6. **Cost shape.** A trivial prompt cost 25,882 cache-creation input tokens.
   Few long jobs beat many short ones.

## The design

```
  user picks project + job text
            ▼
  dash mints uuid4  →  sid = first 4 hex, known BEFORE anything starts
            ▼
  ClaudeSDKClient(
    cwd=roots[project],          ← recorded by the CLI, never guessed
    session_id=<minted uuid>,
    permission_mode="dontAsk",   ← unlisted means denied, never prompted
    allowed_tools=[],            ← everything falls through to the hook
    hooks={"PreToolUse": [gate]},
    env={"CLAUDE_CODE_SESSION_ID": <uuid>},  ← so the job records under that sid
  )
```

**`allowed_tools=[]` with a hook, not a populated allowlist.** A whole-tool
entry auto-approves before the hook's decision is read for that tool. Keeping
the list empty forces every call through one place.

**Bash is gated per command.** `Bash` as a whole is never allowed; the command
is matched against the job's patterns. Chaining, redirection and substitution
are refused rather than parsed — once a permitted command can contain another,
an allowlist of prefixes means nothing. The scan tracks quote state, so a
commit message may contain `&` while `$(` is refused even inside double quotes,
where a shell still expands it.

**Patterns that cannot be made safe are refused as patterns.**
`find . -exec rm -rf / {} +` contains no metacharacter, so no command-time check
can catch it. Heads that run whatever they are handed (`find`, `xargs`, `env`,
`sudo`, `sh`, `python`, …) are refused outright; heads that are safe only with
a subcommand (`git`, `npm`, `make`, `uv`, …) are refused bare, because
`git -c alias.z='!id' z` runs anything while `git commit` cannot.

**Paths are confined.** A tool name is not enough: `Read` takes an absolute
path, so `cwd` confines nothing by itself. Every path-bearing key is resolved —
symlinks, `..` and `~` included — and must sit inside the job's project.
Without this, a job whose only tool is `Read` (the panel's default) could read
`~/.ssh/id_ed25519` and stream it into the browser.

## The hard interlock

**Job launching is refused unless the dashboard is bound to `127.0.0.1`.**

`server.host = "0.0.0.0"` exists so a phone can read the wall. This page has no
authentication. A dashboard that both listens on every interface and can run a
shell is a remote shell for anyone on the network, and shared wifi is a network.
The two features are individually reasonable and jointly unacceptable.

The decision is a pure function, `panel_state`, and the refusal and the form are
separate code paths — there is no start control on the refused path to
re-enable. Not configurable.

## Where the project's directory comes from

The wall renders a project **name**; a job needs a **directory**. Nothing
recorded one, and the first attempt resolved it through `link_roots`, which is a
link-*confinement* list (the data dir plus the process cwd). Four of five
projects therefore resolved to the dashboard's own directory, and the
`table-talk` project resolved to the event log store — where a job with `Edit`
could rewrite the files the wall renders.

`add_event` now stamps `root` when, and only when, the project was derived from
the working directory: then cwd **is** that project. With an explicit
`--project` the cwd proves nothing, so nothing is stamped. `project_roots` reads
the newest such record per project, and a project with no recorded root is
**absent** rather than guessed at.

## Non-goals

- Talking to a session the dashboard did not start.
- A free-text working directory.
- A browser approve/deny prompt per tool call. Proven to work, but a long job
  becomes a stream of dialogs and an unattended one stalls.
- Running when the dashboard is reachable off this machine.

## Known limits

- A job has no cancel path: `pytest --pdb` under an allowed `pytest` pattern
  hangs until the process is killed. `ClaudeSDKClient.interrupt()` exists and is
  the obvious fix.
- The suite drives `run_job` with a fake client and duck-typed option objects,
  so **no pin proves the real SDK accepts these options**. If a kwarg is
  renamed upstream, every pin stays green and the gate silently stops gating.
  The manual real-SDK check must be re-run whenever the SDK version moves;
  the last run denied `/etc/hostname` by confinement and `Bash` by tool list,
  and allowed only the in-project `Read` and `Write`.
- A pattern grants its whole argument surface. `git commit` permits
  `git commit --no-verify`.
