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
    "sh", "bash", "zsh", "dash", "ksh", "eval", "exec", "command", "busybox",
    "nice", "stdbuf", "setsid", "flock", "script", "chroot", "unshare",
    "python", "python3", "perl", "ruby", "node", "ssh", "docker", "podman",
    "awk", "gawk", "sed", "tar", "rsync", "chmod", "chown", "rm", "dd",
    "install", "ln", "mv", "cp", "curl", "wget", "systemd-run", "at", "crontab"))

# `uv run python -c ...` is arbitrary execution, and so is every sibling.
_RUNNER_SUBCOMMANDS = frozenset(("run", "exec", "x", "dlx", "tool"))

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
    raw, _, rest = pattern.strip().partition(" ")
    if not raw:
        return "an empty pattern would match nothing"
    # Path(...).name so /bin/sh and /usr/bin/env are the same answer as sh and
    # env: a denylist matched on the literal head is one absolute path away
    # from being no denylist at all.
    head = Path(raw).name
    if rest.strip().split(" ")[0] in _RUNNER_SUBCOMMANDS and head in _NEEDS_SUBCOMMAND:
        return (f"{raw + ' ' + rest.strip()!r} runs whatever follows it - a "
                f"subcommand does not make a runner safe")
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
    """Is `value` a path inside `root` once symlinks are resolved?

    A RELATIVE value is anchored to `root`, never to whatever directory this
    process happens to be in. Resolving against the dashboard's own cwd let
    `../secret` escape one level for every level the dashboard sat below the
    job - and the dashboard's cwd has nothing to do with the job's.
    """
    try:
        base = Path(root).resolve()
        raw = Path(str(value)).expanduser()
        target = (raw if raw.is_absolute() else base / raw).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return target == base or base in target.parents


def looks_like_path(value):
    """Does this argument address the filesystem outside the project?

    Checked by SHAPE, not by key name. The first version listed three keys it
    knew about, so Glob's `pattern` - a DEFAULT tool - walked straight past it
    and could enumerate /home/**/.ssh. A tool this code has never heard of
    must not be a hole by default.
    """
    return isinstance(value, str) and (value.startswith("/")
                                       or value.startswith("~")
                                       or ".." in value.split("/"))


def usable_root(root):
    """Why this recorded directory must not be used as a job root, or None.

    The event log is append-only text anyone can hand-edit, and this field
    decides where an agent with Edit and Bash will run. One appended line
    carrying root "/" re-pointed the next job at the filesystem root, where
    path confinement then permits everything. Trust it as far as it can be
    checked and no further.
    """
    if not isinstance(root, str) or not root.strip():
        return "no directory recorded"
    try:
        r = Path(root).expanduser().resolve()
    except (OSError, ValueError):
        return f"{root!r} is not a usable path"
    if not r.is_dir():
        return f"{r} is not a directory (any more)"
    if r == Path.home():
        return "your home directory is not a project"
    if len(r.parts) < 3:
        return f"{r} is too close to the filesystem root to be a project"
    return None


def form_blocked(cfg_host, roots):
    """Why the job form must not open at all, or None.

    Separate from job_request because it answers a different question: this one
    decides whether there is a form, that one decides whether a particular job
    may start. Both are pure so both can be tested by calling them.
    """
    ok, why = launch_allowed(cfg_host)
    if not ok:
        return why
    if not roots:
        return ("no project has recorded its directory yet - run any "
                "table-talk command from inside a project first")
    return None


def job_request(cfg_host, roots, name, text, tools, bash):
    """(spec, None) if this job may start, else (None, why).

    EVERY check lives here, in one pure function, because the previous version
    put them in the dialog where no test could reach them: keeping the call to
    the interlock and overwriting its answer on the next line left the whole
    suite green. A rendering function that only renders cannot do that.
    """
    if (blocked := form_blocked(cfg_host, roots)):
        return None, blocked
    if name not in roots:
        return None, f"no recorded directory for {name!r}"
    if (bad := usable_root(roots[name])):
        return None, f"{name}: {bad}"
    for pat in bash:
        if (problem := pattern_problem(pat)):
            return None, f"{pat!r}: {problem}"
    if not str(text).strip():
        return None, "a job needs something to do"
    return mint_job(name, str(Path(roots[name]).expanduser().resolve()),
                    text, tools=tools, bash=bash), None


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
        for key, val in tool_input.items():
            # Two checks, on purpose. A key KNOWN to carry a path must hold a
            # string we can evaluate - anything else fails closed rather than
            # sailing past a shape test it does not match. Every other key is
            # judged by shape, so a tool this code has never heard of is not a
            # hole by default.
            if key in _PATH_KEYS and not isinstance(val, str):
                return False, f"{key}={val!r} is not a path this job can check"
            if (key in _PATH_KEYS or looks_like_path(val)) \
                    and not path_inside(spec.cwd, val):
                return False, (f"{key}={val!r} is outside this job's project "
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

    # job_request: EVERY check in one pure function, so a test can call the
    # thing that actually decides. The previous shape put these in the dialog,
    # where keeping the call and overwriting its answer left the suite green.
    import tempfile as _t, os as _os
    _d = _t.mkdtemp()
    _os.makedirs(_d + "/proj", exist_ok=True)
    _good = {"proj": _d + "/proj"}

    def _req(**kw):
        args = dict(cfg_host="127.0.0.1", roots=_good, name="proj",
                    text="do it", tools=("Read",), bash=())
        args.update(kw)
        return job_request(**args)

    assert _req()[0] is not None, "the ordinary case must actually start"
    assert _req(cfg_host="0.0.0.0")[0] is None, \
        "a dashboard on the network must not start a job at ALL: this is the " \
        "one control the whole design rests on, and it belongs where a test " \
        "can call it rather than inside a dialog"
    assert _req(roots={})[0] is None, "no known project, nothing to start"
    assert _req(name="nope")[0] is None, "an unknown project is refused"
    assert _req(text="   ")[0] is None, "an empty instruction is refused"
    assert _req(bash=("find",))[0] is None, \
        "a pattern that cannot be made safe stops the job BEFORE it exists"
    for hostile, label in ((("/"), "the filesystem root"),
                           (str(Path.home()), "the home directory"),
                           (_d + "/gone", "a directory that is not there")):
        spec_, why_ = _req(roots={"proj": hostile})
        assert spec_ is None and why_, \
            f"{label} must never become a job root: the event log is " \
            f"append-only text anyone can hand-edit, and one line carrying " \
            f"root '/' turns path confinement into permission for everything"
    assert form_blocked("0.0.0.0", _good) and form_blocked("127.0.0.1", {}), \
        "the form must not open on a networked dashboard, nor when no " \
        "project's directory is known - a select with nothing safe in it is " \
        "an invitation to guess"
    assert form_blocked("127.0.0.1", _good) is None, "otherwise it opens"
    # Behaviours verified by hand but not pinned until a mutation showed the
    # pins could not fail. Each of these caught nothing before.
    _os.makedirs(_d + "/proj/deep", exist_ok=True)
    _os.makedirs(_d + "/elsewhere", exist_ok=True)
    _cwd_before = _os.getcwd()
    # cwd OUTSIDE the job's project - the case that distinguishes anchoring
    # from resolving against whatever directory this process happens to be in.
    _os.chdir(_d + "/elsewhere")
    try:
        _rel = mint_job("p", _d + "/proj", "t", tools=("Read",), bash=())
        assert gate_decision(_rel, "Read", {"file_path": "deep/ok"})[0], \
            "a RELATIVE path must be anchored to the JOB's directory: " \
            "resolved against the dashboard's own cwd instead, a file plainly " \
            "inside the project reads as outside it, and the job cannot work"
        assert not gate_decision(_rel, "Read", {"file_path": "../escape"})[0], \
            "and anchoring must not become an escape hatch either"
    finally:
        _os.chdir(_cwd_before)
    _shape = mint_job("p", _d + "/proj", "t", tools=("Glob", "Grep"), bash=())
    assert not gate_decision(_shape, "Glob", {"pattern": "/home/**/.ssh/id_*"})[0], \
        "a path must be judged by SHAPE, not by a list of key names: Glob's " \
        "`pattern` is not `file_path`, and Glob is a DEFAULT tool, so a " \
        "key allowlist let the default job enumerate the filesystem"
    assert not gate_decision(_shape, "Grep", {"pattern": "KEY", "glob": "/home/**"})[0], \
        "and every key is checked, not just the first path-shaped one"
    assert gate_decision(_shape, "Grep", {"pattern": "TODO"})[0], \
        "while an argument that is not a path at all is left alone"
    assert pattern_problem("/bin/sh") and pattern_problem("/usr/bin/env"), \
        "the head is matched on its BASENAME: a denylist matched on the " \
        "literal string is one absolute path away from being no denylist"
    assert pattern_problem("uv run") and pattern_problem("npm run") and \
           pattern_problem("cargo run"), \
        "a subcommand does not make a runner safe - `uv run python -c ...` " \
        "is arbitrary execution, and that reads as an ordinary build command"
    assert pattern_problem("git status") is None and pattern_problem("npm ci") is None, \
        "while an ordinary subcommand still works"
    assert usable_root(_d + "/proj") is None and usable_root("/") and \
        usable_root(str(Path.home())) and usable_root(_d + "/nope") and \
        usable_root(None) and usable_root(""), \
        "usable_root judges the recorded directory on its own terms"
    got, _ = _req()
    assert got.cwd == str(Path(_d + "/proj").resolve()), \
        "the spec carries the RESOLVED directory, so what the gate confines " \
        "to and what the user was shown are the same path"

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
