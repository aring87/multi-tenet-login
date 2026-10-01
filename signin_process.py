"""Interruptible Azure authentication commands; never used for deployments."""
import subprocess
import tempfile
import time

from auth_recovery import SignInCancelled
from lighthouse_onboarding import Stop

SIGNIN_TIMEOUT = 300


def check_cancelled(cancel):
    if cancel.is_set():
        raise SignInCancelled()


def run_signin(command, env, cancel, timeout=SIGNIN_TIMEOUT):
    check_cancelled(cancel)
    deadline = time.monotonic() + min(timeout, SIGNIN_TIMEOUT)
    # Files avoid pipe-buffer deadlocks and inherited browser handles keeping a
    # communicate() call open after the CLI exits. Nothing is logged on cancel.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            env=env, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while True:
                check_cancelled(cancel)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Stop("Microsoft sign-in timed out after five minutes. Close the old sign-in tab and try again.")
                try:
                    process.wait(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            # Cancel wins even when success arrives during the same wait interval.
            check_cancelled(cancel)
            stdout.seek(0); stderr.seek(0)
            return subprocess.CompletedProcess(command, process.returncode,
                stdout.read().decode("utf-8", errors="replace"), stderr.read().decode("utf-8", errors="replace"))
        finally:
            if process.poll() is None:
                # Only our direct CLI child is stopped. Never kill browsers,
                # broker hosts or other Azure sessions by executable name.
                process.kill()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired as error:
                    raise Stop("The sign-in process did not stop. Close this app's old sign-in window before trying again.") from error
