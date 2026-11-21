from test_roots import TOOLKIT_ROOT
"""gen3tl web server: the editor server detached, on a free port."""
import io
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import ProxyHandler, build_opener

from gen3tl.cli import main as cli
from gen3tl.cli import web
from gen3tl.catalog.draftcatalog import source_digest
from gen3tl.catalog.po import PoEntry, write
from gen3tl.profile.profile import Profile
from gen3tl import __version__
from gen3tl.catalog import extract
from gen3tl.catalog.po import KEY_ONLY, SOURCE_FIELD
from gen3tl.project import Manifest, Registry, registry_scope
from gen3tl.web.editordata import write_languages
from project_fixture import write_policy

urlopen = build_opener(ProxyHandler({})).open   # the local server, never a proxy


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def finished_pid() -> int:
    """The pid of a process that has exited."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    return process.pid


class ServerCommandTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        registry = Path(temp.name).resolve()
        profile = registry / "fixture.toml"
        profile.write_text('[rom]\nsha1 = "' + "0" * 40 + '"\n[support]\nstage = "catalog"\n')
        write_policy(registry, Profile.load(profile), "fr")
        (registry / "projects.toml").write_text('[defaults]\nprofile="fixture"\nlanguage="fr"\n'
            '[[project]]\nprofile="fixture"\nlanguage="fr"\nroot="."\npolicy="policy.toml"\n')
        items = [PoEntry("s1", "Hello", "", translator=["source: fixture"], flags=["fuzzy"])]
        write(registry / "translation/po/source.po", items, {"Project-Id-Version": "fixture", "Language": "fr"})
        (registry / "translation/catalog.json").write_text(json.dumps({"profile": "fixture", "language": "fr",
            "rom_sha1": "0" * 40, "source_sha256": source_digest(items)}))
        self.port = free_port()
        self.argv = ["--project-root", str(registry), "--profile", str(profile), "--lang", "fr"]
        self.pid_file = registry / f"build/editor-server-{self.port}.pid"
        self.log = registry / f"build/editor-server-{self.port}.log"
        source = str(TOOLKIT_ROOT / "src")
        environment = patch.dict(os.environ, {"PYTHONPATH": source})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.end_server)
        self.addCleanup(self.end_site_server)

    def end_server(self):
        """A server a failed test left running."""
        try:
            os.kill(int(self.pid_file.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def end_site_server(self):
        """A site server a failed test left running."""
        try:
            os.kill(int(self.site_pid_file.read_text()), signal.SIGTERM)
        except (AttributeError, OSError, ValueError):
            pass

    def server(self, *words):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)   # the server outlives its handle
            code = cli.main(self.argv + ["web", "server", *words, "--port", str(self.port)])
        return code, [row.split(None, 1) for row in output.getvalue().splitlines()]

    def test_start_status_log_restart_and_stop(self):
        self.pid_file.parent.mkdir()
        self.pid_file.write_text(f"{finished_pid()}\n")   # stale: started anyway
        code, rows = self.server("start")
        self.assertEqual(code, 0, rows)
        self.assertEqual([label for label, _ in rows], ["status", "pid", "site", "editor", "log"])
        values = dict(rows)
        site = f"http://127.0.0.1:{self.port}/"
        self.assertEqual((values["status"], values["site"], values["editor"], values["log"]),
                         ("started", site, f"{site}editor", str(self.log)))
        pid = int(values["pid"])
        self.assertEqual(int(self.pid_file.read_text()), pid)
        self.assertEqual(os.getsid(pid), pid)   # its own session, apart from the terminal
        with urlopen(f"{site}editor", timeout=5) as page:
            self.assertEqual(page.status, 200)

        self.assertEqual(self.server("status"), (0, [["status", "running"], ["pid", str(pid)],
            ["port", str(self.port)], ["site", site], ["editor", f"{site}editor"]]))
        # a second start on the port is refused
        self.assertEqual(self.server("start"), (1, [["status", "running"], ["pid", str(pid)]]))
        self.assertEqual(int(self.pid_file.read_text()), pid)

        code, rows = self.server("log")
        self.assertEqual(code, 0)
        self.assertIn(["site", site], rows)
        self.assertIn("ready", [row[0] for row in rows])
        code, rows = self.server("log", "2")
        self.assertEqual(code, 0)
        self.assertEqual(rows, [text.split(None, 1) for text in self.log.read_text().splitlines()[-2:]])

        code, rows = self.server("restart")
        self.assertEqual(code, 0, rows)
        self.assertEqual([value for label, value in rows if label == "status"], ["stopped", "started"])
        restarted = int(dict(rows)["pid"])
        self.assertNotEqual(restarted, pid)
        self.assertEqual(int(self.pid_file.read_text()), restarted)

        self.assertEqual(self.server("stop"), (0, [["status", "stopped"]]))
        self.assertFalse(self.pid_file.exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(restarted, 0)
        with self.assertRaises(OSError):
            urlopen(site, timeout=1)
        self.assertEqual(self.server("stop"), (0, [["status", "not running"]]))
        self.assertEqual(self.server("status"), (0, [["status", "not running"], ["port", str(self.port)]]))

    def test_site_start_status_log_and_stop(self):
        registry = Path(self.argv[1])
        data = registry / "build/editor"
        data.mkdir(parents=True)
        (data / "languages.json").write_text("{}", encoding="utf-8")
        self.site_pid_file = registry / f"build/site-server-{self.port}.pid"
        self.site_log = registry / f"build/site-server-{self.port}.log"

        code, rows = self.server("start", "--page", "site")
        self.assertEqual(code, 0, rows)
        values = dict(rows)
        site = f"http://127.0.0.1:{self.port}/"
        self.assertEqual((values["status"], values["site"], values["log"]),
                         ("started", site, str(self.site_log)))
        with urlopen(site, timeout=5) as page:
            self.assertEqual(page.status, 200)
        self.assertEqual(self.server("status", "--page", "site"),
                         (0, [["status", "running"], ["pid", values["pid"]], ["port", str(self.port)],
                              ["site", site], ["editor", f"{site}editor"]]))
        code, rows = self.server("log", "--page", "site")
        self.assertEqual(code, 0)
        self.assertIn(["status", "ready"], rows)
        self.assertEqual(self.server("stop", "--page", "site"), (0, [["status", "stopped"]]))
        self.assertFalse(self.site_pid_file.exists())

    def test_start_wait_option_and_previous_log_rotation(self):
        code, rows = self.server("start", "--wait", "5")
        self.assertEqual(code, 0, rows)
        self.assertEqual(self.server("stop"), (0, [["status", "stopped"]]))
        first_log = self.log.read_text(encoding="utf-8")
        previous = self.log.with_name(self.log.name + ".1")
        previous.write_text("older log\n", encoding="utf-8")

        code, rows = self.server("start")
        self.assertEqual(code, 0, rows)
        self.assertEqual(previous.read_text(encoding="utf-8"), first_log)
        self.assertEqual(self.server("stop"), (0, [["status", "stopped"]]))

    def test_a_stale_pid_file_counts_as_not_running_and_is_removed(self):
        self.pid_file.parent.mkdir()
        for action in ("status", "stop"):
            with self.subTest(action=action):
                self.pid_file.write_text(f"{finished_pid()}\n")
                expected = [["status", "not running"]]
                if action == "status":
                    expected.append(["port", str(self.port)])
                self.assertEqual(self.server(action), (0, expected))
                self.assertFalse(self.pid_file.exists())

    def test_an_unrelated_process_pid_file_counts_as_stale(self):
        self.pid_file.parent.mkdir()
        self.addCleanup(self.pid_file.unlink, missing_ok=True)
        for action in ("status", "stop"):
            with self.subTest(action=action):
                self.pid_file.write_text(f"{os.getpid()}\n")
                expected = [["status", "not running"]]
                if action == "status":
                    expected.append(["port", str(self.port)])
                self.assertEqual(self.server(action), (0, expected))
                self.assertFalse(self.pid_file.exists())
                os.kill(os.getpid(), 0)

    def test_a_port_another_process_holds_is_refused(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", self.port))
            other.listen()
            self.assertEqual(self.server("start"), (1, [["status", "in use"], ["port", str(self.port)]]))
        self.assertFalse(self.pid_file.exists())

    def test_log_without_a_log_file(self):
        self.assertEqual(self.server("log"), (1, [["log", "missing"]]))


class ServerFreshnessTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.profile = self.root / "fixture.toml"
        self.profile.write_text('[rom]\nname="Fixture"\nsha1="' + "0" * 40
                                + '"\n[support]\nstage="catalog"\n')
        prof = Profile.load(self.profile)
        self.codes = ("ru", "de", "es", "fr")
        manifest = Manifest(self.root / "project.toml", prof.name, prof.sha1, __version__, self.codes, self.codes[0])
        manifest.path.write_text(manifest.text())
        rows = [f'[defaults]\nprofile="fixture"\nlanguage="{self.codes[0]}"\n']
        for code in self.codes:
            write_policy(self.root, prof, code)
            policy = f"policy-{code}.toml"
            (self.root / "policy.toml").rename(self.root / policy)
            rows.append(f'[[project]]\nprofile="fixture"\nlanguage="{code}"\nroot="."\npolicy="{policy}"\n')
        (self.root / "projects.toml").write_text("\n".join(rows))
        self.registry = Registry.load(self.root)
        items = [PoEntry("moves:1", "Test move", "")]
        extract.write(self.root / "build/catalog.pot", items, prof)
        for project in self.registry.projects:
            write(project.catalog / "source.po", items,
                  {"Project-Id-Version": prof.name, "Language": project.language, SOURCE_FIELD: KEY_ONLY})
            (project.translation / "catalog.json").write_text(json.dumps({"profile": prof.name,
                "language": project.language, "rom_sha1": prof.sha1, "source_sha256": source_digest(items)}))
            write(project.translation / "pokedex-labels.po", [], {"Language": project.language})
        dataset = self.registry.projects[0].dataset
        dataset.mkdir(parents=True)
        (dataset / "pokedex.json").write_text(json.dumps({"moves": [{"id": 1, "name": "moves:1"}]}))
        self.data = self.root / "build/editor"
        source = str(TOOLKIT_ROOT / "src")
        self.enterContext(patch.dict(os.environ, {"PYTHONPATH": source}))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.argv = ["--project-root", str(self.root), "--profile", str(self.profile)]

    def start(self, code, page="editor", before_bind=None):
        def server(*args, **kwargs):
            if before_bind:
                before_bind()
            return Mock(server_address=("127.0.0.1", 0))
        name = "gen3tl.web.editorserve.preview_server" if page == "site" else "gen3tl.web.editorsave.local_server"
        with patch(name, side_effect=server) as listener:
            result = cli.main(self.argv + ["--lang", code, "web", "server", "start", "--foreground", "--page", page])
        return result, listener.call_count

    def test_second_language_labels_refresh_before_either_listener_opens(self):
        first, second = self.registry.projects[:2]
        self.assertEqual(self.start(first.language), (0, 1))
        self.assertEqual(self.start(second.language), (0, 1))
        exported = self.data / second.key / "pokedex-texts.json"
        selected = self.data / first.key / "pokedex-texts.json"
        selected_time = selected.stat().st_mtime_ns
        key = "page:mechanics:flinch:hit"
        self.assertNotIn(key, json.loads(exported.read_text()).get("page", {}))
        for page in ("editor", "site"):
            with self.subTest(page=page):
                label = f"Flinches after a hit ({page})."
                catalog = second.translation / "pokedex-labels.po"
                write(catalog, [PoEntry(key, label, "")], {"Language": second.language})
                old = catalog.stat().st_mtime_ns - 6 * 86400 * 10**9
                os.utime(exported, ns=(old, old))
                self.assertLess(exported.stat().st_mtime_ns, catalog.stat().st_mtime_ns)

                def verified():
                    self.assertEqual(json.loads(exported.read_text())["page"][key], [label, ""])
                    self.assertEqual(selected.stat().st_mtime_ns, selected_time)

                self.assertEqual(self.start(first.language, page, verified), (0, 1))
        self.assertFalse((self.data / self.registry.projects[2].key).exists())
        with patch("subprocess.run", wraps=subprocess.run) as run:
            self.assertEqual(self.start(first.language), (0, 1))
        self.assertFalse(any("--language-only" in call.args[0] for call in run.call_args_list))

    def test_failed_peer_refresh_refuses_to_open_a_listener(self):
        first, second = self.registry.projects[:2]
        self.assertEqual(self.start(second.language), (0, 1))
        self.assertTrue((self.data / first.key / "pokedex-texts.json").is_file())
        catalog = second.translation / "pokedex-labels.po"
        write(catalog, [PoEntry("page:mechanics:flinch:hit", "Flinches after a hit.", "")], {})
        before = (self.data / second.key / "pokedex-texts.json").read_bytes()
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="fixture failure")):
            self.assertEqual(self.start(first.language), (1, 0))
        self.assertEqual((self.data / second.key / "pokedex-texts.json").read_bytes(), before)
        self.assertEqual(list((self.root / "build").glob(".editor-refresh-*")), [])

    def test_source_selection_uses_configuration_with_one_or_four_exports(self):
        first = self.registry.projects[0]
        for count in (1, 4):
            with self.subTest(count=count):
                for project in self.registry.projects[:count]:
                    self.assertEqual(self.start(project.language), (0, 1))
                doc = json.loads((self.data / "languages.json").read_text())
                self.assertEqual(len([row for row in doc["languages"] if row["profile"] == first.profile]), count)
                profile = next(row for row in doc["profiles"] if row["id"] == first.profile)
                self.assertEqual(profile["source_texts"],
                                 {"code": first.language, "texts": f"{first.key}/pokedex-texts.json"})
        with registry_scope(self.root), first.activate(first.effective(Profile.load(self.profile))):
            doc = write_languages(self.data / first.profile)
        profile = next(row for row in doc["profiles"] if row["id"] == first.profile)
        self.assertEqual(profile["source_texts"]["texts"], f"{first.language}/pokedex-texts.json")


class ServerProcessTest(unittest.TestCase):
    def test_process_matches_the_selected_page_and_port(self):
        for page in ("site", "editor"):
            command = f"python -m gen3tl web server start --page {page} --port 8123"
            result = SimpleNamespace(stdout=command, returncode=0)
            with self.subTest(page=page), patch("subprocess.run", return_value=result):
                self.assertTrue(web._server_process(12345, 8123, page))
                self.assertFalse(web._server_process(12345, 8124, page))
                other_page = "site" if page == "editor" else "editor"
                self.assertFalse(web._server_process(12345, 8123, other_page))

    def test_process_requires_a_running_server_command(self):
        for command, code in (("python -m gen3tl web server status --page site --port 8123", 0),
                              ("python -m other web server start --page site --port 8123", 0),
                              ("python -m gen3tl web server start --page site --port 8123", 1)):
            result = SimpleNamespace(stdout=command, returncode=code)
            with self.subTest(command=command, code=code), patch("subprocess.run", return_value=result):
                self.assertFalse(web._server_process(12345, 8123, "site"))


class ServerStartOutcomeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.args = SimpleNamespace(action="start", foreground=False, page="editor", port=12345,
            project=None, build_root=str(root / "build"), profile="fixture", lang="fr",
            data=str(root / "data"), po_root=str(root / "po"), wait=0)
        self.pid_file = root / "build/editor-server-12345.pid"
        self.log = self.pid_file.with_suffix(".log")

    def start(self, action, polls, log_text):
        self.args.action = action
        process = Mock(pid=12345)
        process.poll.side_effect = polls

        def launch(*args, stdout, **kwargs):
            self.command = args[0]
            self.assertTrue(all(isinstance(part, str) for part in self.command))
            stdout.write(log_text.encode("utf-8"))
            stdout.flush()
            return process

        output = io.StringIO()
        with patch("socket.create_connection", side_effect=OSError), \
                patch("subprocess.Popen", side_effect=launch), \
                patch("urllib.request.build_opener", return_value=Mock(open=Mock(side_effect=OSError))), \
                patch("time.monotonic", side_effect=[10, 10.4, 10.4]), \
                patch("time.sleep") as sleep, redirect_stdout(output):
            code = web.server(self.args)
        sleep.assert_not_called()
        rows = [row.split(None, 1) for row in output.getvalue().splitlines()]
        if action == "restart":
            self.assertEqual(rows.pop(0), ["status", "not running"])
        return code, rows

    def test_wait_expired_while_running_reports_progress_and_preserves_pid(self):
        for action in ("start", "restart"):
            with self.subTest(action=action):
                self.assertEqual(self.start(action, [None, None], "project fixture\ndata loading\n\n \n"),
                    (1, [["status", "starting"], ["waited", "0.4 s"], ["pid", "12345"],
                         ["log", str(self.log)], ["last", "data loading"]]))
                self.assertEqual(self.pid_file.read_text(), "12345\n")
                self.args.action = "status"
                output = io.StringIO()
                with patch.object(web, "_alive", return_value=True), \
                        patch.object(web, "_server_process", return_value=True), redirect_stdout(output):
                    self.assertEqual(web.server(self.args), 0)
                values = dict(row.split(None, 1) for row in output.getvalue().splitlines())
                self.assertEqual((values["status"], values["pid"]), ("running", "12345"))
                self.pid_file.unlink()

    def test_exited_process_reports_last_line_and_removes_pid(self):
        for action in ("start", "restart"):
            for polls in ([1, 1], [None, 1]):
                with self.subTest(action=action, polls=polls):
                    self.assertEqual(self.start(action, polls, "project fixture\nerror failed\n\n \n"),
                        (1, [["status", "exited"], ["log", str(self.log)], ["last", "error failed"]]))
                    self.assertFalse(self.pid_file.exists())
                    self.args.action = "status"
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(web.server(self.args), 0)
                    self.assertEqual([row.split(None, 1) for row in output.getvalue().splitlines()],
                                     [["status", "not running"], ["port", "12345"]])

    def test_start_without_log_output_reports_empty(self):
        for polls in ([None, None], [1, 1]):
            with self.subTest(polls=polls):
                code, rows = self.start("start", polls, "\n \n")
                self.assertEqual(code, 1)
                self.assertEqual(rows[-1], ["last", "empty"])
                self.pid_file.unlink(missing_ok=True)

    def test_site_without_a_project_omits_profile_and_language_selection(self):
        self.args.page, self.args.profile, self.args.lang = "site", None, ""
        self.pid_file = Path(self.args.build_root) / "site-server-12345.pid"
        self.log = self.pid_file.with_suffix(".log")
        self.assertEqual(self.start("start", [1, 1], "")[0], 1)
        self.assertNotIn("--profile", self.command)
        self.assertNotIn("--lang", self.command)


if __name__ == "__main__":
    unittest.main()
