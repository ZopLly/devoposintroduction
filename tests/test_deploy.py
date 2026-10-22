import functools
import fcntl
import http.server
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        (directory / "index.html").write_text("DevOps Deployment Dashboard")
        (directory / "build-sha.txt").write_text(SHA + "\n")
        # Docker is an external boundary; HTTP checks use a real server and curl.
        docker = directory / "docker"
        docker.write_text('#!/bin/sh\nexit "${DOCKER_EXIT:-0}"\n')
        docker.chmod(0o755)

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.env = dict(os.environ,
                        PATH=str(directory) + os.pathsep + os.environ["PATH"],
                        DASHBOARD_URL=f"http://127.0.0.1:{self.server.server_port}",
                        HEALTH_ATTEMPTS="2", HEALTH_DELAY="0")

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def deploy(self, sha=SHA):
        return subprocess.run(["bash", str(ROOT / "scripts/deploy-dashboard"), sha],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_accepts_requested_revision_and_project_page(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(SHA, result.stdout)

    def test_rejects_different_revision_even_with_http_200(self):
        (Path(self.tmp.name) / "build-sha.txt").write_text("b" * 40)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Website did not serve", result.stderr)

    def test_rejects_wrong_page_even_with_matching_revision(self):
        (Path(self.tmp.name) / "index.html").write_text("Welcome to nginx!")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Website did not serve", result.stderr)

    def test_rejects_invalid_revision(self):
        result = self.deploy("latest")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)

    def test_docker_failure_is_not_reported_as_success(self):
        self.env["DOCKER_EXIT"] = "1"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Deployment failed", result.stderr)

    def test_retries_until_requested_revision_is_ready(self):
        revision_file = Path(self.tmp.name) / "build-sha.txt"
        revision_file.unlink()
        self.env.update(HEALTH_ATTEMPTS="10", HEALTH_DELAY="0.1")
        timer = threading.Timer(0.25, lambda: revision_file.write_text(SHA))
        timer.start()
        self.addCleanup(timer.join)
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_concurrent_deploy_in_same_checkout(self):
        with (ROOT / ".deploy.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.deploy()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Another deployment", result.stderr)


if __name__ == "__main__":
    unittest.main()
