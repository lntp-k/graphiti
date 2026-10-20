#!/usr/bin/env python3
"""Install one audit watch; root activation is explicit and self-tests use synthetic data."""

from __future__ import annotations

import argparse
import json
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
    if '-S' in tokens:
        index = tokens.index('-S')
        if tokens[index + 1 : index + 2] == ['all']:
            del tokens[index : index + 2]
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


def privatize_logs(
    config: Path = Path('/etc/audit/auditd.conf'),
    backup_root: Path = WORK,
    expected_log: Path = Path('/var/log/audit/audit.log'),
) -> Path | None:
    """Explicit opt-in migration of only standard auditd log privacy; raw bytes stay in place."""
    if os.geteuid() != 0:
        raise RuntimeError('Root required for private audit log migration')
    config_info = config.lstat()
    if (
        not stat.S_ISREG(config_info.st_mode)
        or config_info.st_uid != 0
        or config_info.st_nlink != 1
    ):
        raise RuntimeError('Audit configuration must be a regular root-owned single-link file')
    original = config.read_bytes()
    text = original.decode()
    groups = list(
        re.finditer(
            r'^(?P<prefix>[ \t]*log_group[ \t]*=[ \t]*)(?P<value>[^\r\n#]*)(?P<comment>#[^\r\n]*)?$',
            text,
            re.MULTILINE,
        )
    )
    files = re.findall(r'^\s*log_file\s*=\s*([^#\r\n]+)', text, re.MULTILINE)
    if groups and groups[0].group('value').strip() not in ('adm', 'root', '0'):
        raise RuntimeError('Private migration supports only default adm or root log_group')
    if len(groups) > 1 or len(files) > 1:
        raise RuntimeError(
            'Duplicate audit log privacy directives; refusing ambiguous configuration'
        )
    log = Path(files[0].strip()) if files else Path('/var/log/audit/audit.log')
    if log != expected_log or not log.is_absolute():
        raise RuntimeError('Private migration supports only the reviewed standard audit log path')
    directory = log.parent
    directory_info = directory.lstat()
    if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != 0:
        raise RuntimeError('Audit log directory must be a real root-owned directory')
    entries = sorted(directory.iterdir())
    if log not in entries:
        raise RuntimeError('Current audit.log is missing; refusing partial migration')
    infos = []
    for entry in entries:
        info = entry.lstat()
        if (
            not re.fullmatch(r'audit\.log(?:\.\d+)?', entry.name)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
        ):
            raise RuntimeError(
                'Unexpected, linked, or non-root-owned audit log entry; refusing migration'
            )
        infos.append((entry, info))
    if groups:
        match = groups[0]
        replacement = match.group('prefix') + 'root'
        if match.group('comment'):
            replacement += ' ' + match.group('comment')
        updated = text[: match.start()] + replacement + text[match.end() :]
    else:
        updated = text + ('' if text.endswith('\n') else '\n') + 'log_group = root\n'
    if (
        updated.encode() == original
        and directory_info.st_gid == 0
        and stat.S_IMODE(directory_info.st_mode) == 0o700
        and all(info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o600 for _, info in infos)
    ):
        # A previous interrupted migration may have changed disk settings only.
        reload_private_config(config)
        return None
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_info = backup_root.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != 0
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise RuntimeError('Audit backup root must be a real root-owned 0700 directory')
    backup = Path(tempfile.mkdtemp(prefix='privacy-backup-', dir=backup_root))

    def save(name: str, contents: bytes) -> None:
        descriptor = os.open(
            backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, 'wb') as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())

    save('auditd.conf', original)
    metadata = [
        {
            'path': str(item),
            'uid': info.st_uid,
            'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode),
        }
        for item, info in [(config, config_info), (directory, directory_info), *infos]
    ]
    save('original-permissions.json', json.dumps(metadata, indent=2).encode())
    # Check again after the backup; do not silently replace a concurrent administrator edit.
    current_config = config.lstat()

    def identity(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mtime_ns,
            info.st_size,
            info.st_uid,
            info.st_gid,
            info.st_mode,
        )

    if identity(current_config) != identity(config_info) or config.read_bytes() != original:
        raise RuntimeError(
            'Audit configuration changed concurrently; backup kept, migration refused'
        )
    descriptor, temporary = tempfile.mkstemp(prefix='.auditd.conf.graphiti-', dir=config.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(updated.encode())
            os.fchown(output.fileno(), config_info.st_uid, config_info.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(config_info.st_mode))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, config)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.chown(directory, 0, 0, follow_symlinks=False)
    os.chmod(directory, 0o700)
    for entry, initial in infos:
        current = entry.lstat()
        if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino) or not stat.S_ISREG(
            current.st_mode
        ):
            raise RuntimeError('Audit logs rotated concurrently; rerun migration; backup kept')
        os.chown(entry, 0, 0, follow_symlinks=False)
        os.chmod(entry, 0o600)
    # HUP reloads auditd configuration without stopping it or flushing kernel rules.
    reload_private_config(config)
    print(f'Private audit log settings migrated; root-only backup: {backup}')
    return backup


def reload_private_config(config: Path) -> None:
    query = ['ausearch', '--input-logs', '-m', 'DAEMON_CONFIG', '--raw']
    before = subprocess.run(query, text=True, capture_output=True)
    baseline = set(re.findall(r'msg=audit\(([^)]+)\)', before.stdout))
    started = int(time.time() * 1000) / 1000
    subprocess.run(['auditctl', '--signal', 'reload'], check=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = subprocess.run(
            query,
            text=True,
            capture_output=True,
        )
        acknowledged = False
        for line in result.stdout.splitlines():
            match = re.search(r'msg=audit\(((\d+(?:\.\d+)?):\d+)\)', line)
            if (
                match
                and match.group(1) not in baseline
                and float(match.group(2)) >= started
                and 'type=DAEMON_CONFIG ' in line
                and 'op=reconfigure ' in line
                and re.search(r'\bres=success\b', line)
            ):
                acknowledged = True
                break
        if acknowledged:
            # Reconfigure may reopen/chown the log; validate only after its success event.
            check_privacy(config)
            return
        time.sleep(0.2)
    raise RuntimeError(
        'No successful auditd reconfigure event within 10s; verify private logs before collecting'
    )


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


def activate(install_tools: bool, private_logs: bool = False) -> None:
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
    if private_logs:
        privatize_logs()
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
    parser.add_argument(
        '--private-logs',
        action='store_true',
        help='Explicitly migrate standard auditd log_group/permissions to root-only, with backup',
    )
    args = parser.parse_args()
    if not args.activate:
        print('Plan only; no changes: install auditd if requested; enable standard auditd service;')
        if args.private_logs:
            print(
                'OPT-IN: back up auditd.conf; set only log_group=root and root-only log permissions; reload auditd'
            )
        print('require root-private disk logs; verify isolated synthetic writer attribution;')
        print(f'add exactly: {shlex.join(rule(WATCH, KEY))}')
        print(f'persist in {RULE_FILE}; preserve all other rules/configuration')
        return 0
    try:
        activate(args.install_tools, args.private_logs)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        # Do not expose captured command output: audit records can contain credentials.
        print(f'Activation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
