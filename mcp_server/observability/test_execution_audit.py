"""Non-root verification; these tests never invoke auditctl or access real graphs."""

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'execution_audit', Path(__file__).with_name('install_execution_audit.py')
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AuditSetupTests(unittest.TestCase):
    def test_watch_parent_survives_venv_replacement(self):
        self.assertEqual(audit.WATCH.name, 'mcp_server')
        args = audit.rule(audit.WATCH, audit.KEY)
        self.assertEqual(
            args,
            [
                '-a',
                'always,exit',
                '-F',
                f'dir={audit.WATCH}',
                '-F',
                'perm=wa',
                '-k',
                'graphiti_venv',
            ],
        )
        self.assertNotIn('-D', args)

    def test_live_rule_idempotency_and_other_rules_preserved(self):
        args = audit.rule(audit.WATCH, audit.KEY)
        rendered = ' '.join(args).replace('-k graphiti_venv', '-F key=graphiti_venv')
        self.assertTrue(
            audit.check_rules('-w /etc/passwd -p wa -k other\n' + rendered, args, audit.KEY)
        )
        self.assertFalse(audit.check_rules('-w /etc/passwd -p wa -k other', args, audit.KEY))
        self.assertTrue(
            audit.check_rules(
                rendered.replace('-a always,exit', '-a always,exit -S all'), args, audit.KEY
            )
        )
        with self.assertRaisesRegex(RuntimeError, 'Conflicting'):
            audit.check_rules(
                rendered.replace('-a always,exit', '-a always,exit -S openat'), args, audit.KEY
            )

    def test_conflicting_key_and_duplicates_rejected(self):
        args = audit.rule(audit.WATCH, audit.KEY)
        with self.assertRaisesRegex(RuntimeError, 'Conflicting'):
            audit.check_rules('-w /etc -p wa -k graphiti_venv', args, audit.KEY)
        rendered = ' '.join(args)
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            audit.check_rules(rendered + '\n' + rendered, args, audit.KEY)

    def test_event_requires_correlated_successful_writer_and_exact_argv(self):
        fixture = Path('/synthetic/fixture')
        argv = ['/usr/bin/python3', '-c', 'write_fixture()', 'probe']
        title = b'\0'.join(item.encode() for item in argv).hex()
        lines = [
            f'type=SYSCALL msg=audit(1.2:3): success=yes pid=123 ppid=100 exe="{Path(argv[0]).resolve()}"',
            'type=CWD msg=audit(1.2:3): cwd="/synthetic/fixture"',
            'type=PATH msg=audit(1.2:3): name="probe"',
            f'type=PROCTITLE msg=audit(1.2:3): proctitle={title}',
        ]
        raw = '\n'.join(lines)
        self.assertTrue(audit.complete_event(raw, 123, 100, fixture, argv))
        for field, changed in [
            ('success=yes', 'success=no'),
            ('pid=123 ', 'pid=124 '),
            ('ppid=100 ', 'ppid=101 '),
            ('name="probe"', 'name="other"'),
            ('cwd="/synthetic/fixture"', 'cwd="/other"'),
            (f'proctitle={title}', 'proctitle=00'),
        ]:
            with self.subTest(field=field):
                self.assertFalse(
                    audit.complete_event(raw.replace(field, changed), 123, 100, fixture, argv)
                )
        disconnected = '\n'.join(lines[:-1] + [lines[-1].replace('1.2:3', '1.2:4')])
        self.assertFalse(audit.complete_event(disconnected, 123, 100, fixture, argv))
        self.assertFalse(
            audit.complete_event(raw.replace(title, 'nothex'), 123, 100, fixture, argv)
        )

    def test_journal_privacy_refuses_current_or_future_forwarding(self):
        with mock.patch.object(
            audit.subprocess,
            'run',
            side_effect=[mock.Mock(stdout='inactive\n'), mock.Mock(stdout='disabled\n')],
        ):
            audit.check_journal_privacy()
        for states in (['active\n'], ['inactive\n', 'enabled\n']):
            with (
                mock.patch.object(
                    audit.subprocess,
                    'run',
                    side_effect=[mock.Mock(stdout=value) for value in states],
                ),
                self.assertRaisesRegex(RuntimeError, 'journald audit socket'),
            ):
                audit.check_journal_privacy()

    def test_audit_loss_rejected(self):
        with mock.patch.object(audit, 'run', return_value='enabled 1\nlost 0\n'):
            audit.check_losses()
        with (
            mock.patch.object(audit, 'run', return_value='enabled 1\nlost 1\n'),
            self.assertRaisesRegex(RuntimeError, 'lost events'),
        ):
            audit.check_losses()

    def test_private_log_migration_opt_in_backup_idempotency_and_content_preservation(self):
        parent = Path('/home/jl/coding/graphiti-src/session-work/20261021-audit-tests')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            root = Path(folder)
            logs = root / 'audit'
            logs.mkdir(mode=0o750)
            current, rotated = logs / 'audit.log', logs / 'audit.log.1'
            current.write_bytes(b'raw sensitive audit bytes\n')
            rotated.write_bytes(b'rotated sensitive audit bytes\n')
            current.chmod(0o640)
            rotated.chmod(0o640)
            config = root / 'auditd.conf'
            original = f'log_file = {current}\nlog_group = adm # site comment\nmax_log_file = 8\n'
            config.write_text(original)
            config.chmod(0o600)
            backup_root = root / 'backups'
            owners = {}
            original_lstat = Path.lstat

            def virtual_lstat(path):
                info = list(original_lstat(path))
                info[4], info[5] = owners.get(str(path), (0, 4))
                return os.stat_result(info)

            def virtual_chown(path, uid, gid, **_):
                owners[str(path)] = (uid, gid)

            with (
                mock.patch.object(audit.os, 'geteuid', return_value=0),
                mock.patch.object(Path, 'lstat', virtual_lstat),
                mock.patch.object(audit.os, 'chown', side_effect=virtual_chown),
                mock.patch.object(audit.os, 'fchown'),
                mock.patch.object(audit, 'reload_private_config') as reload_config,
            ):
                backup = audit.privatize_logs(config, backup_root, current)
                self.assertEqual((backup / 'auditd.conf').read_text(), original)
                self.assertEqual(stat.S_IMODE((backup / 'auditd.conf').stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
                metadata = json.loads((backup / 'original-permissions.json').read_text())
                self.assertEqual(len(metadata), 4)
                self.assertEqual(
                    config.read_text(), original.replace('log_group = adm', 'log_group = root')
                )
                self.assertEqual(current.read_bytes(), b'raw sensitive audit bytes\n')
                self.assertEqual(rotated.read_bytes(), b'rotated sensitive audit bytes\n')
                self.assertEqual(stat.S_IMODE(logs.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(current.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(rotated.stat().st_mode), 0o600)
                reload_config.assert_called_once_with(config)
                self.assertIsNone(audit.privatize_logs(config, backup_root, current))
                # A previous interrupted migration may have left only disk settings private.
                self.assertEqual(reload_config.call_count, 2)
                self.assertEqual(len(list(backup_root.iterdir())), 1)
                self.assertEqual((backup / 'auditd.conf').read_text(), original)
                # Custom policy is refused before creating another backup or changing logs.
                config.write_text(original.replace('adm', 'custom_group'))
                with self.assertRaisesRegex(RuntimeError, 'default adm or root'):
                    audit.privatize_logs(config, backup_root, current)
                self.assertEqual(len(list(backup_root.iterdir())), 1)
                config.write_text(original)
                unexpected = logs / 'unrelated.txt'
                unexpected.write_text('preserve')
                with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
                    audit.privatize_logs(config, backup_root, current)
                unexpected.unlink()
                linked = logs / 'audit.log.2'
                linked.symlink_to(config)
                with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
                    audit.privatize_logs(config, backup_root, current)
                self.assertEqual(config.read_text(), original)
                self.assertEqual(len(list(backup_root.iterdir())), 1)

    def test_private_migration_requires_root(self):
        with (
            mock.patch.object(audit.os, 'geteuid', return_value=1000),
            self.assertRaisesRegex(RuntimeError, 'Root required'),
        ):
            audit.privatize_logs()

    def test_cli_private_logs_is_explicit(self):
        for arguments, expected in [
            (['--activate'], False),
            (['--activate', '--private-logs'], True),
        ]:
            with (
                mock.patch.object(audit.sys, 'argv', ['install_execution_audit.py', *arguments]),
                mock.patch.object(audit, 'activate') as activate,
            ):
                self.assertEqual(audit.main(), 0)
                activate.assert_called_once_with(False, expected)

    def test_reload_requires_new_event_id_and_accepts_same_millisecond_ack(self):
        config = Path('/synthetic/auditd.conf')
        stale = 'type=DAEMON_CONFIG msg=audit(100.000:1): op=reconfigure res=success'
        failed = 'type=DAEMON_CONFIG msg=audit(100.000:2): op=reconfigure res=failed'
        fresh = 'type=DAEMON_CONFIG msg=audit(100.000:3): op=reconfigure res=success'
        with (
            mock.patch.object(audit.time, 'time', return_value=100.0009),
            mock.patch.object(audit.time, 'monotonic', side_effect=[0, 1, 2, 3]),
            mock.patch.object(audit.time, 'sleep'),
            mock.patch.object(
                audit.subprocess,
                'run',
                side_effect=[
                    mock.Mock(stdout=stale),
                    mock.Mock(),
                    mock.Mock(stdout=stale),
                    mock.Mock(stdout=stale + '\n' + failed),
                    mock.Mock(stdout=stale + '\n' + fresh),
                ],
            ) as command,
            mock.patch.object(audit, 'check_privacy') as privacy,
        ):
            audit.reload_private_config(config)
            self.assertEqual(command.call_args_list[1].args[0], ['auditctl', '--signal', 'reload'])
            self.assertEqual(command.call_count, 5)
            privacy.assert_called_once_with(config)

    def test_reload_stale_ack_times_out_without_privacy_success(self):
        stale = 'type=DAEMON_CONFIG msg=audit(100.000:1): op=reconfigure res=success'
        with (
            mock.patch.object(audit.time, 'time', return_value=100.0009),
            mock.patch.object(audit.time, 'monotonic', side_effect=[0, 1, 11]),
            mock.patch.object(audit.time, 'sleep'),
            mock.patch.object(
                audit.subprocess,
                'run',
                side_effect=[mock.Mock(stdout=stale), mock.Mock(), mock.Mock(stdout=stale)],
            ),
            mock.patch.object(audit, 'check_privacy') as privacy,
            self.assertRaisesRegex(RuntimeError, 'No successful'),
        ):
            audit.reload_private_config(Path('/synthetic/auditd.conf'))
        privacy.assert_not_called()

    def test_nonroot_cannot_activate(self):
        with (
            mock.patch.object(audit.os, 'geteuid', return_value=1000),
            mock.patch.object(audit, 'run') as command,
        ):
            with self.assertRaisesRegex(RuntimeError, 'Root required'):
                audit.activate(True)
            command.assert_not_called()

    def test_plan_is_readonly(self):
        with (
            mock.patch.object(audit.sys, 'argv', ['install_execution_audit.py']),
            mock.patch.object(audit, 'activate') as activate,
        ):
            self.assertEqual(audit.main(), 0)
            activate.assert_not_called()

    def test_private_log_config_checks(self):
        # TemporaryDirectory stays under coding rather than the system temporary directory.
        parent = Path('/home/jl/coding/graphiti-src/session-work/20261021-audit-tests')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            config = Path(folder) / 'auditd.conf'
            config.write_text('log_group = adm\n')
            with self.assertRaisesRegex(RuntimeError, 'log_group'):
                audit.check_privacy(config)
            config.write_text('write_logs = no\n')
            with self.assertRaisesRegex(RuntimeError, 'disables disk logs'):
                audit.check_privacy(config)
            config.write_text(
                f'log_file = /private/audit.log\nlog_group = root\nplugin_dir = {folder}/empty-plugins\n'
            )
            owner = mock.Mock(st_mode=0o100640, st_uid=0, st_gid=0)
            with mock.patch.object(Path, 'lstat', return_value=owner):
                self.assertEqual(audit.check_privacy(config), Path('/private/audit.log'))
            with (
                mock.patch.object(
                    Path, 'lstat', return_value=mock.Mock(st_mode=0o100644, st_uid=0, st_gid=0)
                ),
                self.assertRaisesRegex(RuntimeError, 'root-owned'),
            ):
                audit.check_privacy(config)
            with (
                mock.patch.object(
                    Path, 'lstat', return_value=mock.Mock(st_mode=0o120777, st_uid=0, st_gid=0)
                ),
                self.assertRaisesRegex(RuntimeError, 'root-owned'),
            ):
                audit.check_privacy(config)

    def test_failed_commands_do_not_print_raw_audit_output(self):
        failure = subprocess.CalledProcessError(1, ['auditctl', '-l'], output='SECRET_PROCTITLE')
        with (
            mock.patch.object(audit.sys, 'argv', ['install_execution_audit.py', '--activate']),
            mock.patch.object(audit, 'activate', side_effect=failure),
            mock.patch('builtins.print') as output,
        ):
            self.assertEqual(audit.main(), 1)
            self.assertNotIn('SECRET_PROCTITLE', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()
