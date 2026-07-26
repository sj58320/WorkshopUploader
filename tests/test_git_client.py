from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from git_client import GitCommandError, GitRunner, ensure_askpass
from windows_credentials import GitHubCredential


CREDENTIAL = GitHubCredential(
    "ghu_test_secret",
    None,
    None,
    None,
    "asset-user",
    12345,
)


class FakeProcessFactory:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, "stdout", self.stderr)


class FakeStreamingProcess:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self.stdout = io.StringIO(output)
        self.stdin = None
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


class FakePopenFactory:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return FakeStreamingProcess(self.output)
class GitRunnerTests(unittest.TestCase):
    def test_uses_fixed_executable_and_disables_interactive_credentials(self) -> None:
        process = FakeProcessFactory()
        runner = GitRunner(
            Path(r"C:\bundle\mingit\cmd\git.exe"),
            Path(r"C:\runtime\git-askpass.cmd"),
            process_factory=process,
            base_env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")},
        )

        runner.run(["status"], cwd=Path(r"C:\repo"))

        command, options = process.calls[0]
        self.assertEqual(command[0], r"C:\bundle\mingit\cmd\git.exe")
        self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(options["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertIn(options["env"]["GIT_CONFIG_GLOBAL"], ("NUL", "/dev/null"))
        self.assertEqual(options["env"]["GIT_CONFIG_KEY_1"], "http.sslVerify")
        self.assertEqual(options["env"]["GIT_CONFIG_VALUE_1"], "true")
        self.assertEqual(options["env"]["GIT_CONFIG_KEY_0"], "credential.helper")
        self.assertEqual(options["env"]["GIT_CONFIG_VALUE_0"], "")

    def test_token_is_only_in_child_environment(self) -> None:
        process = FakeProcessFactory()
        runner = GitRunner(
            Path(r"C:\bundle\mingit\cmd\git.exe"),
            Path(r"C:\runtime\git-askpass.cmd"),
            process_factory=process,
            base_env={},
        )

        result = runner.run(["fetch", "origin"], credential=CREDENTIAL)

        command, options = process.calls[0]
        self.assertNotIn(CREDENTIAL.access_token, repr(command))
        self.assertNotIn(CREDENTIAL.access_token, result.safe_command)
        self.assertEqual(
            options["env"]["WORKSHOP_UPLOADER_GIT_PASSWORD"],
            CREDENTIAL.access_token,
        )

    def test_multiline_commit_message_uses_stdin(self) -> None:
        process = FakeProcessFactory()
        runner = GitRunner(Path("git.exe"), Path("askpass.cmd"), process_factory=process)

        runner.run(["commit", "--file", "-"], stdin="line one\nline two")

        self.assertEqual(process.calls[0][1]["input"], "line one\nline two")

    def test_failed_command_redacts_token_from_exception(self) -> None:
        process = FakeProcessFactory(returncode=1, stderr="bad ghu_test_secret")
        runner = GitRunner(Path("git.exe"), Path("askpass.cmd"), process_factory=process)

        with self.assertRaises(GitCommandError) as raised:
            runner.run(["fetch"], credential=CREDENTIAL)

        self.assertNotIn(CREDENTIAL.access_token, str(raised.exception))

    def test_streaming_output_reports_progress_and_redacts_token(self) -> None:
        process = FakePopenFactory(
            "Receiving objects: 42% (42/100) ghu_test_secret\n"
        )
        runner = GitRunner(
            Path("git.exe"),
            Path("askpass.cmd"),
            popen_factory=process,
        )
        lines: list[str] = []

        result = runner.run_streaming(
            ["clone", "--progress", "origin"],
            credential=CREDENTIAL,
            progress=lines.append,
        )

        self.assertEqual(
            lines,
            ["Receiving objects: 42% (42/100) [REDACTED]"],
        )
        self.assertNotIn(CREDENTIAL.access_token, result.stdout)
        self.assertEqual(
            process.calls[0][1]["env"]["WORKSHOP_UPLOADER_GIT_PASSWORD"],
            CREDENTIAL.access_token,
        )
    def test_ensure_askpass_writes_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = ensure_askpass(Path(temporary_directory))
            contents = path.read_text(encoding="utf-8")

        self.assertIn("WORKSHOP_UPLOADER_GIT_PASSWORD", contents)
        self.assertNotIn(CREDENTIAL.access_token, contents)


if __name__ == "__main__":
    unittest.main()
