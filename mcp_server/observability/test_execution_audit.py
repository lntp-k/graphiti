"""Non-root verification; these tests never invoke auditctl or access real graphs."""

import importlib.util
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
            config.write_text('log_file = /private/audit.log\nlog_group = root\n')
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
