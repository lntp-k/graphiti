#!/usr/bin/env python3
"""Install one audit watch; root activation is explicit and self-tests use synthetic data."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Watch the stable parent so atomic .venv replacement remains observable.
WATCH = Path('/home/jl/coding/graphiti-src/mcp_server')
KEY = 'graphiti_venv'
RULE_FILE = Path('/etc/audit/rules.d/70-graphiti-venv.rules')
WORK = Path('/home/jl/coding/session-work/graphiti-execution-audit')


def run(args: list[str]) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def rule(directory: Path, key: str, action: str = '-a') -> list[str]:
    return [action, 'always,exit', '-F', f'dir={directory}', '-F', 'perm=wa', '-k', key]


def canonical(value: str) -> tuple[str, ...]:
    tokens = shlex.split(value)
    if '-k' in tokens:
        index = tokens.index('-k')
        tokens[index : index + 2] = ['-F', f'key={tokens[index + 1]}']
    pairs = [tuple(tokens[index : index + 2]) for index in range(0, len(tokens), 2)]
    return tuple(str(pair) for pair in sorted(pairs))


def check_rules(existing: str, desired: list[str], key: str) -> bool:
    matches = [
        line
        for line in existing.splitlines()
        if re.search(rf'(?:key=|-k\s+){re.escape(key)}(?:\s|$)', line)
    ]
    if any(canonical(line) != canonical(shlex.join(desired)) for line in matches):
        raise RuntimeError(f'Conflicting existing audit rule for key {key}; no rules changed')
    if len(matches) > 1:
        raise RuntimeError(f'Duplicate audit rules for key {key}; no rules changed')
    return bool(matches)


def check_privacy(config: Path = Path('/etc/audit/auditd.conf')) -> Path:
    values = {}
    for line in config.read_text().splitlines():
        clean = line.partition('#')[0].strip()
        if '=' in clean:
            key, value = clean.split('=', 1)
            values[key.strip()] = value.strip()
    if values.get('log_group', 'root') not in ('root', '0'):
        raise RuntimeError('Existing audit log_group is not root; review its privacy policy first')
    if values.get('write_logs', 'yes').lower() != 'yes':
        raise RuntimeError('Existing auditd disables disk logs')
    plugins = Path(values.get('plugin_dir', '/etc/audit/plugins.d'))
    for plugin in plugins.glob('*.conf'):
        if re.search(
            r'^\s*active\s*=\s*yes\s*(?:#.*)?$', plugin.read_text(), re.MULTILINE | re.IGNORECASE
        ):
            raise RuntimeError(
                'Existing active audit plugin may forward raw argv; review its policy first'
            )
    path = Path(values.get('log_file', '/var/log/audit/audit.log'))
    for item in (path.parent, path):
        info = item.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_mode & 0o027
        ):
            raise RuntimeError(
                f'Audit log path must already be root-owned with root group and no group-write/other access, without symlinks: {item}'
            )
    return path


def check_journal_privacy() -> None:
    # Journal readers may include adm/systemd-journal users, unlike root-only audit.log.
    for query in ('is-active', 'is-enabled'):
        result = subprocess.run(
            ['systemctl', query, 'systemd-journald-audit.socket'], text=True, capture_output=True
        )
        state = result.stdout.strip()
        if query == 'is-active' and state not in ('inactive', 'failed', 'unknown'):
            raise RuntimeError(
                'journald audit socket may expose raw argv; review its policy before activation'
            )
        if query == 'is-enabled' and state not in ('disabled', 'masked', 'not-found'):
            raise RuntimeError(
                'journald audit socket can activate later; review its policy before activation'
            )


def complete_event(raw: str, pid: int, ppid: int, fixture: Path, argv: list[str]) -> bool:
    """Require writer identity, successful syscall and evidence in the same audit event."""
    events: dict[str, list[str]] = {}
    for line in raw.splitlines():
        match = re.search(r'msg=audit\(([^)]+)\)', line)
        if match:
            events.setdefault(match.group(1), []).append(line)
    for lines in events.values():
        parsed = [
            dict(token.split('=', 1) for token in shlex.split(line) if '=' in token)
            for line in lines
        ]
        syscall = any(
            item.get('type') == 'SYSCALL'
            and item.get('pid') == str(pid)
            and item.get('ppid') == str(ppid)
            and item.get('success') == 'yes'
            and item.get('exe') == str(Path(argv[0]).resolve())
            for item in parsed
        )
        cwd = any(item.get('type') == 'CWD' and item.get('cwd') == str(fixture) for item in parsed)
        path = any(
            item.get('type') == 'PATH' and item.get('name') in ('probe', str(fixture / 'probe'))
            for item in parsed
        )
        titles = [item.get('proctitle', '') for item in parsed if item.get('type') == 'PROCTITLE']
        for title in titles:
            try:
                decoded = bytes.fromhex(title).rstrip(b'\0').split(b'\0')
            except ValueError:
                continue
            if syscall and cwd and path and decoded == [item.encode() for item in argv]:
                return True
    return False


def check_losses() -> None:
    if not re.search(r'^lost\s+0\s*$', run(['auditctl', '-s']), re.MULTILINE):
        raise RuntimeError('Kernel audit reports lost events; attribution verification incomplete')


def self_test() -> None:
    """Prove attribution without touching installed files or any graph."""
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    if WORK.is_symlink() or WORK.stat().st_uid != 0 or WORK.stat().st_mode & 0o077:
        raise RuntimeError(f'Fixture parent must be root-owned mode 0700: {WORK}')
    key = f'gvenv_test_{os.getpid()}'
    with tempfile.TemporaryDirectory(prefix='fixture-', dir=WORK) as directory:
        fixture = Path(directory)
        args = rule(fixture, key)
        run(['auditctl', *args])
        try:
            # The child writes only this newly created synthetic fixture.
            argv = [sys.executable, '-', 'probe']
            child = subprocess.Popen(argv, cwd=fixture, stdin=subprocess.PIPE, text=True)
            try:
                child.communicate('open(__import__("sys").argv[1],"w").write("x")', timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise
            if child.returncode != 0:
                raise RuntimeError('Synthetic writer failed')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                found = subprocess.run(
                    ['ausearch', '--input-logs', '-k', key, '--raw'], text=True, capture_output=True
                )
                raw = found.stdout
                if complete_event(raw, child.pid, os.getpid(), fixture, argv):
                    check_losses()
                    print(
                        f'PASS synthetic kernel attribution: writer PID {child.pid}; PID/PPID/exe/PATH/PROCTITLE present'
                    )
                    return
                time.sleep(0.2)
            raise RuntimeError(
                'No complete synthetic audit event within 10s; production watch not installed'
            )
        finally:
            run(['auditctl', *rule(fixture, key, '-d')])


def activate(install_tools: bool) -> None:
    if os.geteuid() != 0:
        raise RuntimeError('Root required: administrator must run the reviewed activation command')
    if not all(shutil.which(name) for name in ('auditctl', 'ausearch')):
        if not install_tools:
            raise RuntimeError(
                'auditd tools missing; rerun with --install-tools to install standard OS auditd'
            )
        subprocess.run(['apt-get', 'install', '--yes', 'auditd'], check=True)
    if WATCH.is_symlink() or not WATCH.is_dir() or WATCH.resolve() != WATCH:
        raise RuntimeError(f'Production watch must be an existing real directory: {WATCH}')
    desired = rule(WATCH, KEY)
    expected = shlex.join(desired) + '\n'
    if RULE_FILE.is_symlink():
        raise RuntimeError('Persistent rule file is a symlink; refusing')
    if RULE_FILE.exists() and (RULE_FILE.stat().st_uid != 0 or RULE_FILE.stat().st_mode & 0o022):
        raise RuntimeError('Persistent rule file is not exclusively root-writable')
    if RULE_FILE.exists() and RULE_FILE.read_text() != expected:
        raise RuntimeError(f'Conflicting persistent file: {RULE_FILE}; refusing to overwrite')
    check_journal_privacy()
    subprocess.run(['systemctl', 'enable', '--now', 'auditd.service'], check=True)
    check_privacy()
    status = run(['auditctl', '-s'])
    if re.search(r'^enabled\s+2\s*$', status, re.MULTILINE):
        raise RuntimeError(
            'Kernel audit is immutable; activation requires separately planned reboot'
        )
    if not re.search(r'^enabled\s+1\s*$', status, re.MULTILINE):
        raise RuntimeError('Kernel audit is not enabled')
    if not re.search(r'^lost\s+0\s*$', status, re.MULTILINE):
        raise RuntimeError(
            'Kernel audit reports lost events; review backlog before claiming complete attribution'
        )
    present = check_rules(run(['auditctl', '-l']), desired, KEY)
    self_test()
    if not present:
        run(['auditctl', *desired])
    if not check_rules(run(['auditctl', '-l']), desired, KEY):
        raise RuntimeError('Production rule not visible after activation')
    if not RULE_FILE.exists():
        descriptor = os.open(RULE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        with os.fdopen(descriptor, 'w') as output:
            output.write(expected)
    check_losses()
    print(
        'PASS production watch active and persistent; no production files were written by this helper'
    )
    print(
        f'Raw evidence is root-private. Administrator inspection: ausearch --input-logs -k {KEY} -i'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--activate',
        action='store_true',
        help='Explicit root activation; otherwise print plan only',
    )
    parser.add_argument(
        '--install-tools',
        action='store_true',
        help='Allow apt-get install auditd if tools are absent',
    )
    args = parser.parse_args()
    if not args.activate:
        print('Plan only; no changes: install auditd if requested; enable standard auditd service;')
        print('require root-private disk logs; verify isolated synthetic writer attribution;')
        print(f'add exactly: {shlex.join(rule(WATCH, KEY))}')
        print(f'persist in {RULE_FILE}; preserve all other rules/configuration')
        return 0
    try:
        activate(args.install_tools)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        # Do not expose captured command output: audit records can contain credentials.
        print(f'Activation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
