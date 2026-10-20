> Document version v3 — 2026-10-21

# Linux installation-write audit

`install_execution_audit.py` prepares a narrow kernel audit rule that attributes
writes and metadata changes beneath this checkout's `mcp_server` directory.
Watching its stable parent rather than `.venv` also captures atomic virtualenv
replacement. Source/config/test writes beneath that directory are consequently
included. This is not a system-wide command or read audit.

The helper preserves other audit rules and configuration. It never flushes rules,
changes a graph, edits `spark-infra`, restarts Graphiti, or writes test data into
the installed virtualenv. It refuses conflicting rules, immutable audit mode,
existing lost events, and broader-readable existing raw log configuration. Standard
root-group read permissions are accepted only for root-owned, root-group log paths,
with no group-write or other access. Active audit forwarding plugins and active or
enabled journald audit sockets also cause refusal; their policies are not rewritten.

## Activation by an administrator

First inspect the plan without privileges:

```sh
python3 /home/jl/coding/graphiti-src/mcp_server/observability/install_execution_audit.py
```

Then explicitly authorize standard OS `auditd` installation (if absent) and
activation, after reviewing this script:

```sh
sudo python3 /home/jl/coding/graphiti-src/mcp_server/observability/install_execution_audit.py --activate --install-tools
```

This installs the standard package if needed, enables the standard auditd service,
requires root-private disk logs, exercises an isolated synthetic writer under
`/home/jl/coding/session-work/graphiti-execution-audit`, checks its kernel event for
the child's PID, PPID, executable, CWD/PATH, and exact decoded PROCTITLE in the
same successful syscall event, checks lost-event counters, removes only that temporary
rule, then installs and verifies the live production rule and its persistence file
`/etc/audit/rules.d/70-graphiti-venv.rules`. No audit kernel attribution has been
verified until this command prints both PASS messages. Existing global rules such
as `never,task` may prevent the test; the helper fails rather than removing them.
A package install/service enable can remain in effect after a later check fails;
the helper reports failure and does not claim rollback.

Auditctl 3.1.2 can list this same DIR/PERM/KEY syscall rule as
`-w /home/jl/coding/graphiti-src/mcp_server -p wa -k graphiti_venv`.
The comparison accepts this exact alias, preserving path, permissions and key;
additional filters or duplicate rules still fail. An already active matching
watch is not added again; activation verifies it and completes persistence.
Failure after a rule-add can leave that live rule in place before persistence.
Inspect current rules before drawing conclusions from a failure message.
This formatting follows the official [auditctl listing implementation](https://raw.githubusercontent.com/linux-audit/audit-userspace/v3.1.2/src/auditctl-listing.c).

Persistence uses the distribution's normal audit rules startup loader. Confirm the
rule after the next planned host reboot; this helper does not reboot or reload all
rules. If `mcp_server` itself is moved/replaced, rerun activation and verify the
watch before relying on it. Kernel audit watches do not cross mounted subtrees;
a separately mounted `.venv` needs its own separately reviewed watch.

## Explicit root-only log migration on a fresh default installation

Ubuntu's fresh `auditd` package may configure `log_group = adm` and root:adm
0750 log-directory permissions. Default activation still refuses this wider
read access. For this installation, explicitly opt into the reviewed migration:

```sh
sudo python3 /home/jl/coding/graphiti-src/mcp_server/observability/install_execution_audit.py --activate --private-logs
```

The option supports only the standard `/var/log/audit/audit.log`, a single
`log_group` set to `adm` or `root`, and existing root-owned regular single-link
`audit.log` / `audit.log.N` files. Custom paths/groups, duplicate directives,
symlinks, hard links and unrelated directory entries cause refusal. It first saves
`auditd.conf` byte-for-byte and original owner/group/mode metadata in a new
root-owned 0700 `privacy-backup-*` directory beneath
`/home/jl/coding/session-work/graphiti-execution-audit`; backup files are 0600 and
previous backups are preserved.

Only `log_group` is changed to `root`; other config settings remain intact. The
log directory becomes root:root 0700, and current/rotated logs become root:root
0600. Raw log bytes stay in place. The helper requests an auditd configuration
reload with `auditctl --signal reload`, waits for its fresh successful
`DAEMON_CONFIG` event, then rechecks log privacy before synthetic attribution.
It does not stop auditd or flush audit rules. Already-private reactivation needs
no new migration backup. Configuration/log rotation conflicts or reload failure
leave the backup and report failure; privacy changes are not silently rolled back.

## Evidence and privacy

Raw kernel events include full process command lines and paths, which can contain
credentials. They remain in auditd's standard root-private OS log
(`/var/log/audit/audit.log` unless already configured otherwise), outside ordinary
user reports. This is the system-generated OS audit log; authored fixtures and
reports remain under `/home/jl/coding/`. Do not paste raw PROCTITLE/EXECVE records
into chats, repositories, or unprivileged report files. Existing site retention,
rotation and forwarding configuration is preserved; review any site forwarding
policy before activation if raw command-line confidentiality is required.

After the next reproduction, an administrator can inspect locally:

```sh
sudo ausearch --input-logs -k graphiti_venv -i
sudo auditctl -s
```

Correlate audit timestamp/serial, writer PID/PPID, executable and path with ASGI
records. Report only redacted, necessary metadata. Audit lost/backlog counters,
disk-full behavior and rotation bound what can be concluded from missing events;
this setup does not promise indefinite retention or proof that no write occurred.
It records the actual writer, not necessarily an earlier orchestrator that asked
that writer to run. Linux PROCTITLE can truncate the command line to 128 bytes; this
file-write watch does not collect complete EXECVE argument history. The synthetic
test deliberately uses short argv with script input over stdin, so its exact argv
check remains meaningful within that bound. Root can disable or alter auditing.

For deliberate removal, remove only the exact production rule shown by the plan
(using `auditctl -d always,exit ...` with the same fields) and the matching managed
rules file after review. Do not run `auditctl -D`, stop shared auditd, or change
other audit policy to remove this collector.
