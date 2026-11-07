"""
Master test runner. Executes all test scripts as subprocesses,
streams their output live, and tees everything into tests.log.

Exit code: 0 if all tests passed, 1 if any failed.
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

TESTS = [
    "test_ents.py",
    "test_engine.py",
    "test_hardening.py",
    "test_persistence.py",
    "test_input.py",
    "test_index.py",
    "test_spatial.py",
    "test_network.py",
]

LOG_PATH = Path("tests.log")


def main():
    results = []
    start = datetime.utcnow().isoformat(timespec="seconds")

    with LOG_PATH.open("w", encoding="utf-8") as log:
        log.write(f"# Test run: {start}\n")
        log.write(f"# Python: {sys.version.split()[0]}\n")
        log.write("#" * 72 + "\n\n")

        for test in TESTS:
            header = f"\n{'=' * 72}\n=== {test}\n{'=' * 72}\n"
            print(header, end="")
            log.write(header)

            proc = subprocess.Popen(
                [sys.executable, test],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                print(line, end="")
                log.write(line)
            proc.wait()

            status = "PASS" if proc.returncode == 0 else "FAIL"
            footer = f"--- {test}: {status} (exit {proc.returncode})\n"
            print(footer, end="")
            log.write(footer)
            results.append((test, proc.returncode))

        end = datetime.utcnow().isoformat(timespec="seconds")
        log.write("\n" + "#" * 72 + "\n")
        log.write(f"# Finished: {end}\n")

        total = len(results)
        passed = sum(1 for _, rc in results if rc == 0)
        failed = total - passed
        summary = f"# {passed}/{total} passed, {failed} failed\n"
        log.write(summary)

    print(f"\n{'=' * 72}")
    print(summary.rstrip())
    print(f"Log written to: {LOG_PATH.resolve()}")
    return 0 if all(rc == 0 for _, rc in results) else 1


if __name__ == "__main__":
    sys.exit(main())
