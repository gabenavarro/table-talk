#!/usr/bin/env python3
"""Every decision the job runner makes, with no dependency on the SDK.

Kept stdlib-only and pure on purpose: the whole suite must run offline, in CI,
with no credentials and no cost, and a gate that can only be exercised by
starting a real session is a gate nobody tests.
"""
import argparse
import uuid
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class JobSpec:
    """One job: where it runs, what it may do, and who it will be on the wall."""
    project: str
    cwd: str
    text: str
    tools: tuple
    bash: tuple
    session_id: str


def mint_job(project, cwd, text, tools=(), bash=()):
    """A job with its session id chosen HERE, before anything starts.

    The dashboard minting the id is what lets the wall show the job from its
    first event: table-talk stamps the first four characters of the session id
    on every record, so the sid is known before the session exists.
    """
    return JobSpec(project=project, cwd=str(cwd), text=text,
                   tools=tuple(tools), bash=tuple(bash),
                   session_id=str(uuid.uuid4()))


def job_sid(spec):
    """The code table-talk will stamp on this job's records."""
    return spec.session_id[:4]


def launch_allowed(host):
    """(ok, reason) - may a job be started at all, given the bind address?

    This page has no authentication and a job may run git and a shell. A
    dashboard that both listens on every interface and can run commands is a
    remote shell for anyone on the network, and shared wifi is a network. The
    two features are individually reasonable and jointly unacceptable, so this
    is structural rather than a warning the user can wave away.
    """
    if host == "127.0.0.1":
        return True, ""
    return False, (f"jobs are disabled while the dashboard is bound to {host}: "
                   "this page has no password and a job can run commands. "
                   'set server.host = "127.0.0.1" to enable them')


# Metacharacters that turn one permitted command into an arbitrary one.
# Split by where they still bite: a shell expands $(...) and backticks INSIDE
# double quotes, so those are unsafe wherever they appear; the rest are inert
# once quoted, which is what lets `git commit -m "fix: a & b"` through.
_ALWAYS = ("`", "$(", "${")
_OUTSIDE = (";", "&&", "||", "|", ">", "<", "&", "\n", "\r")

# Heads that run whatever they are handed. `find . -exec rm -rf / {} +` needs
# no metacharacter at all, so no argument scan can make these safe as a bare
# pattern - they are refused as patterns rather than gated as commands.
_MULTIPLEXERS = frozenset((
    "find", "xargs", "env", "sudo", "doas", "nohup", "timeout", "watch",
    "sh", "bash", "zsh", "dash", "ksh", "eval", "exec", "command",
    "python", "python3", "perl", "ruby", "node", "ssh", "docker", "podman"))

# Heads safe only with a subcommand: `git -c alias.z='!id' z` runs anything,
# while `git commit` cannot.
_NEEDS_SUBCOMMAND = frozenset(("git", "npm", "pnpm", "yarn", "make", "uv",
                               "cargo", "poetry", "just", "task"))


def pattern_problem(pattern):
    """Why this allow-pattern is unsafe to offer at all, or None.

    Checked when a job is STARTED, not when a command arrives: a pattern like
    `find` cannot be made safe by inspecting arguments, so the honest place to
    refuse it is before the job exists.
    """
    head, _, rest = pattern.strip().partition(" ")
    if not head:
        return "an empty pattern would match nothing"
    if head in _MULTIPLEXERS:
        return (f"{head!r} runs whatever it is given, so no argument check "
                f"can make it safe - name the command you actually want")
    if head in _NEEDS_SUBCOMMAND and not rest.strip():
        return (f"{head!r} alone permits its whole option surface "
                f"(git -c alias.z='!id' z runs anything) - add a subcommand, "
                f"like '{head} status'")
    return None


def unquoted_meta(command):
    """The first metacharacter that survives quoting, or None.

    Walks the string tracking quote state rather than scanning it flat, so a
    commit message may contain & or > while $( and a backtick are refused even
    inside double quotes, which is exactly where a flat scan gets it wrong in
    both directions.
    """
    q, i = None, 0
    while i < len(command):
        c = command[i]
        for m in _ALWAYS:
            if command.startswith(m, i) and q != "'":
                return m
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in "'\"":
            q = c
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        for m in _OUTSIDE:
            if command.startswith(m, i):
                return m
        i += 1
    return "an unbalanced quote" if q else None


def bash_allowed(patterns, command):
    """(ok, reason) - may this shell command run under these patterns?

    A pattern permits a command it PREFIXES on a token boundary, so
    "git commit" permits `git commit -m x` and not `git commitfoo`. Chaining
    and substitution are refused rather than parsed: the moment a command can
    contain another command, an allowlist of prefixes means nothing.
    """
    if not isinstance(command, str) or not command.strip():
        return False, "no command"
    if (bad := unquoted_meta(command)):
        return False, (f"refused: {bad!r} could chain, redirect or substitute "
                       f"into something no pattern allows")
    cmd = command.strip()
    for p in patterns:
        if pattern_problem(p):
            continue                     # never honour a pattern we would refuse
        if cmd == p or cmd[len(p):len(p) + 1] in (" ", "\t") and cmd.startswith(p):
            return True, ""
    return False, f"no allowed pattern matches: {cmd}"


_PATH_KEYS = ("file_path", "path", "notebook_path")


def path_inside(root, value):
    """Is `value` a path inside `root` once symlinks are resolved?"""
    try:
        base = Path(root).resolve()
        target = Path(value).expanduser().resolve()
    except (OSError, ValueError, TypeError):
        return False
    return target == base or base in target.parents


def gate_decision(spec, tool_name, tool_input):
    """(allow, reason) for one tool call. DENY is the default, always.

    This is the whole permission model. It runs from a PreToolUse hook, which
    is step 1 of evaluation - ahead of deny rules, ask rules, permission mode
    and allow rules - so its answer cannot be shadowed by the ambient allow
    rules in the user's settings, of which this machine has 390.

    A tool name alone is not enough. Read takes an ABSOLUTE path, so the job's
    cwd confines nothing by itself: without the path check below, a job whose
    only tool is Read - the panel's default - can read ~/.ssh/id_ed25519 and
    stream it into the browser.
    """
    if not isinstance(tool_name, str) or tool_name not in spec.tools:
        return False, f"this job does not allow {tool_name!r}"
    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
        return bash_allowed(spec.bash, cmd)
    if isinstance(tool_input, dict):
        for key in _PATH_KEYS:
            if (val := tool_input.get(key)) is not None:
                if not isinstance(val, str) or not path_inside(spec.cwd, val):
                    return False, (f"{val!r} is outside this job's project "
                                   f"({spec.cwd})")
    return True, ""


def hook_output(allow, reason):
    """The PreToolUse payload the SDK expects."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if allow else "deny",
        "permissionDecisionReason": reason or ("allowed by the job's tool list"
                                               if allow else "denied")}}


def selftest():
    spec = mint_job("proj", "/tmp/proj", "do a thing",
                    tools=("Read", "Edit", "Bash"), bash=("pytest", "git commit"))
    assert spec.project == "proj" and spec.cwd == "/tmp/proj", \
        "the job carries the project it belongs to as well as the path: the " \
        "panel names both, because a bare absolute path does not tell the " \
        "reader which project's wall the job's rows will land on"
    assert len(spec.session_id) == 36 and job_sid(spec) == spec.session_id[:4], \
        "the sid must be the first four characters of the minted id: it is " \
        "what table-talk stamps, and the wall cannot show the job before its " \
        "first event unless the dashboard knows it up front"
    assert mint_job("p", "/c", "t").session_id != mint_job("p", "/c", "t").session_id, \
        "two jobs must never share a session id or their records merge"

    ok, why = launch_allowed("127.0.0.1")
    assert ok and why == "", "jobs run when the dashboard is on this machine"
    ok, why = launch_allowed("0.0.0.0")
    assert not ok and "no password" in why, \
        "a dashboard reachable from the network must NOT start jobs: it has " \
        "no authentication and a job can run commands, which together is a " \
        "remote shell for anyone on shared wifi"

    assert gate_decision(spec, "Read", {})[0], "a listed tool is allowed"
    assert not gate_decision(spec, "Write", {})[0], \
        "a tool the job did not list is DENIED - the default must never be " \
        "allow, or a job quietly gains whatever the model decides to reach for"
    assert not gate_decision(spec, None, {})[0], "a missing tool name is denied"
    assert not gate_decision(spec, "WebFetch", {})[0], "so is an unknown one"

    assert gate_decision(spec, "Bash", {"command": "pytest -k foo"})[0], \
        "a pattern permits the command it prefixes on a token boundary"
    assert not gate_decision(spec, "Bash", {"command": "pytestfoo"})[0], \
        "but only on a BOUNDARY: a prefix match without one turns `git` into " \
        "permission for `gitleaks`, `git-anything`, and worse"
    ok, why = gate_decision(spec, "Bash", {"command": "git commit -m x && git push"})
    assert not ok and "chain" in why, \
        "chaining must be refused OUTRIGHT: the moment one permitted command " \
        "can contain another, an allowlist of prefixes means nothing"
    for evil in ("git commit; rm -rf /", "pytest | sh", "pytest > /etc/x",
                 "pytest `whoami`", "pytest $(id)", "pytest\nrm -rf /"):
        assert not gate_decision(spec, "Bash", {"command": evil})[0], \
            f"refused shell construction leaked through: {evil!r}"
    assert not gate_decision(spec, "Bash", {"command": "rm -rf /"})[0], \
        "an unmatched command is denied"
    assert not gate_decision(spec, "Bash", {})[0], "a Bash call with no command is denied"
    assert not gate_decision(spec, "Bash", None)[0], "and so is one with no input at all"
    assert not gate_decision(spec, "Bash", "a string")[0], \
        "a tool_input that is not a dict must DENY, not raise: an exception " \
        "inside the hook is not a refusal, and what the SDK does with one is " \
        "not something this gate should be betting the filesystem on"
    assert not gate_decision(spec, "Bash", {"command": ["pytest"]})[0], \
        "nor may a non-string command slip past the metacharacter scan"
    assert not gate_decision(spec, "Bash", {"command": "PYTEST"})[0], \
        "matching is case-SENSITIVE: a shell is, and a gate that is not " \
        "would allow commands the user never listed"

    # Path confinement. A tool NAME is not enough: Read takes an absolute path,
    # so the job's cwd confines nothing on its own - and Read is a default.
    import tempfile as _tf
    _root = _tf.mkdtemp()
    open(_root + "/ok.txt", "w").write("x")
    conf = mint_job("p", _root, "t", tools=("Read", "Write", "Grep"), bash=())
    assert gate_decision(conf, "Read", {"file_path": _root + "/ok.txt"})[0], \
        "a file inside the project is readable, or the job cannot work at all"
    assert gate_decision(conf, "Read", {"file_path": _root + "/sub/../ok.txt"})[0], \
        "and it stays readable through a traversal that lands back inside"
    for outside in ("/etc/shadow", "~/.ssh/id_ed25519", _root + "/../escape"):
        ok, why = gate_decision(conf, "Read", {"file_path": outside})
        assert not ok and "outside" in why, \
            f"a path outside the project must be DENIED ({outside}): without " \
            f"this a job whose only tool is Read - the panel's default - " \
            f"reads private keys and streams them into the browser"
    assert path_inside(Path.home(), "~/anything"), \
        "a leading ~ must be EXPANDED, not treated as a directory literally " \
        "named ~: unexpanded it resolves under the process cwd, so a path " \
        "genuinely inside the project would be refused"
    assert not gate_decision(conf, "Grep", {"path": "/"})[0], \
        "the check covers every path-bearing key, not just file_path"
    assert not gate_decision(conf, "Write", {"file_path": 12})[0], \
        "a non-string path is denied rather than coerced"

    # Patterns that cannot be made safe by inspecting arguments.
    assert pattern_problem("find") and pattern_problem("xargs") and \
           pattern_problem("sudo") and pattern_problem("sh"), \
        "a head that runs whatever it is handed must be refused as a PATTERN: " \
        "`find . -exec rm -rf / {} +` needs no metacharacter at all, so no " \
        "command-time check can catch it"
    assert pattern_problem("git") and not pattern_problem("git commit"), \
        "bare `git` permits `git -c alias.z='!id' z`, which runs anything; " \
        "with a subcommand it does not"
    assert not pattern_problem("pytest"), "an ordinary command is fine"
    risky = mint_job("p", "/t", "t", tools=("Bash",), bash=("find", "git"))
    assert not gate_decision(risky, "Bash", {"command": "find . -exec rm -rf /x {} +"})[0], \
        "and a refused pattern must not be honoured at command time either, " \
        "or the validation is advice rather than a gate"

    # Quoting. A flat metacharacter scan is wrong in BOTH directions.
    quoted = mint_job("p", "/t", "t", tools=("Bash",), bash=("git commit", "pytest"))
    assert gate_decision(quoted, "Bash", {"command": 'git commit -m "fix: a & b"'})[0], \
        "a commit message may contain & or > - a shell does not expand them " \
        "inside quotes, and refusing them makes the feature useless on day one"
    assert not gate_decision(quoted, "Bash", {"command": 'git commit -m "$(id)"'})[0], \
        "but $( survives DOUBLE quotes and a shell expands it there, so it " \
        "must be refused wherever it appears outside single quotes"
    assert gate_decision(quoted, "Bash", {"command": "pytest\t-k foo"})[0], \
        "a tab is a token boundary to a shell, so it must be one here too"
    assert not gate_decision(quoted, "Bash", {"command": 'pytest "unbalanced'})[0], \
        "an unbalanced quote is refused rather than guessed at"

    out = hook_output(*gate_decision(spec, "Write", {}))
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse" and h["permissionDecision"] == "deny" \
        and h["permissionDecisionReason"], \
        "a deny must carry its REASON in the shape the SDK reads: a job that " \
        "stopped because the dashboard refused something reads as the model " \
        "failing unless the refusal says so"
    assert hook_output(True, "")["hookSpecificOutput"]["permissionDecision"] == "allow"
    print("ok")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selftest", action="store_true")
    if p.parse_args().selftest:
        selftest()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
