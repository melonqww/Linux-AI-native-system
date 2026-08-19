import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class GnomeExtensionFilesTest(unittest.TestCase):
    def test_metadata_is_valid(self):
        metadata = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["uuid"], "ai-native-linux@melonqww")
        self.assertIn("46", metadata["shell-version"])

    def test_runtime_files_exist(self):
        for filename in (
            "extension.js",
            "runtime-client.js",
            "panel-presenter.js",
            "stylesheet.css",
            "install.sh",
            "smoke-test.sh",
            "README.md",
            "TROUBLESHOOTING.md",
        ):
            self.assertTrue((ROOT / filename).is_file(), filename)

    def test_install_reloads_live_extension_before_copy(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('gnome-extensions disable "${EXTENSION_UUID}"', script)
        self.assertIn('gnome-extensions enable "${EXTENSION_UUID}"', script)
        self.assertIn('runtime-client.js" "${TARGET_DIR}/runtime-client.js', script)
        self.assertIn('panel-presenter.js" "${TARGET_DIR}/panel-presenter.js', script)

    def test_native_panel_contract_is_present(self):
        source = (ROOT / "extension.js").read_text(encoding="utf-8")
        for marker in (
            "Main.layoutManager.addChrome",
            "monitors-changed",
            "this._scroll.set_child(this._messages)",
            "monitor.height * 0.52",
            "this._toggleLabel.set_text(this._collapsed ? '‹' : '›')",
            "this._collapsedTranslation = PANEL_WIDTH + PANEL_HORIZONTAL_MARGIN",
            "const toggleTarget = this._collapsed ? -4 : 0",
            "preferences-system-symbolic",
            "folder-symbolic",
            "const resizeEntry = () =>",
            "entry.clutter_text.editable = true",
            "entry.clutter_text.single_line_mode = false",
            "_animateHighlight",
            "Qwen 3.5 2B",
            "Рабочая область",
            "Сообщение для вашего ИИ",
            "import {RuntimeClient, RuntimeRequestError} from './runtime-client.js'",
            "this._stylesheet = this.dir.get_child('stylesheet.css')",
            "this._theme.load_stylesheet(this._stylesheet)",
            "this._theme.unload_stylesheet(this._stylesheet)",
            "stylesheet load failed",
            "new ChatView(this._runtime)",
            "const setModelMenuOpen = open =>",
            "modelChevron.set_text(open ? '⌃' : '⌄')",
            "const SidebarView = GObject.registerClass",
            "class SidebarView extends St.BoxLayout",
            "this._sidebar = new SidebarView(this._runtime)",
            "Состояние системы",
            "metricBlock('Загрузка ЦП', null",
            "metricBlock('Оперативная память', null",
            "Мини-диспетчер задач",
            "Быстрые системные действия",
            "Последние действия",
            "entry.grab_key_focus()",
            "entry.connect('button-press-event'",
            "const entryScroll = new St.ScrollView",
            "const entryScrollContent = new St.BoxLayout",
            "entryScrollContent.add_child(entry)",
            "entryScroll.set_height(Math.min(78, desiredHeight))",
            "const entryAdjustment = entryScroll.get_vadjustment()",
            "entryAdjustment.value = bottom",
            "ai-composer-spacer",
            "metricBlock('Батарея', null",
            "ai-process-header",
            "ai-process-heading-cpu",
            "ai-process-column-memory",
            "style_class: 'ai-sidebar-scroll'",
            "this._historyButton = this._buildHistoryButton()",
            "this._workspace = new ChatView(this._runtime)",
            "_attachFallback(error)",
            "panel construction failed",
            "this._runtime.compileIntent(text)",
            "this._runtime.executePlan(compilation.plan.plan_id)",
            "this._runtime.respondToApproval",
            "this._runtime.indexStatus()",
            "this._runtime.tasks()",
            "this._runtime.taskDetail(taskId)",
            "executionPresentation(result)",
            "systemPresentation(health, capabilities, index)",
            "taskDetailPresentation(task)",
        ):
            self.assertIn(marker, source)

    def test_runtime_client_uses_closed_authenticated_ipc_contract(self):
        source = (ROOT / "runtime-client.js").read_text(encoding="utf-8")
        for marker in (
            "GLib.get_user_runtime_dir()",
            "new Gio.UnixSocketAddress",
            "GLib.uuid_string_random()",
            "version: IPC_VERSION",
            "request_id: requestId",
            "MAX_MESSAGE_BYTES = 64 * 1024",
            "envelope.request_id !== requestId",
            "'/v1/intent/compile'",
            "'/v1/plan/execute'",
            "'/v1/approval/respond'",
            "'/v1/index-status'",
            "'/v1/tasks'",
            "'/v1/tasks/detail'",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("http://", source)
        extension = (ROOT / "extension.js").read_text(encoding="utf-8")
        self.assertNotIn("Подтверждать за меня", extension)
        self.assertNotIn("Подтверждать за меня", source)
        self.assertNotIn("security-high-symbolic", source)

    def test_panel_presenter_scenarios(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, str(ROOT / "tests" / "panel_presenter.test.js")],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_linux_smoke_script_checks_real_shell_state(self):
        script = ROOT / "smoke-test.sh"
        source = script.read_text(encoding="utf-8")
        for marker in (
            "gnome-shell --version",
            'gnome-extensions enable "${EXTENSION_UUID}"',
            "gnome-extensions list --enabled",
            "journalctl --user",
            "RESULT: READY FOR VISUAL CHECK",
        ):
            self.assertIn(marker, source)
        bash = shutil.which("bash") if sys.platform.startswith("linux") else None
        if bash is not None:
            result = subprocess.run(
                [bash, "-n", str(script)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_panel_surface_does_not_paint_behind_toggle(self):
        stylesheet = (ROOT / "stylesheet.css").read_text(encoding="utf-8")
        self.assertIn(".ai-native-shell", stylesheet)
        self.assertIn("background-color: transparent;", stylesheet)
        self.assertIn(".ai-panel-content", stylesheet)
        self.assertIn("box-shadow: 0 18px 52px", stylesheet)
        self.assertIn("margin-top: 5px", stylesheet)
        self.assertIn("border-radius: 22px;", stylesheet)
        self.assertIn("font-weight: 700;", stylesheet)
        self.assertIn("border-radius: 21px 21px 0 0;", stylesheet)
        self.assertIn("margin: 0 7px 6px 4px;", stylesheet)
        self.assertIn("max-height: 78px;", stylesheet)
        self.assertIn("margin-top: 2px;", stylesheet)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux Unix IPC")
    def test_gjs_runtime_client_reaches_authenticated_unix_server(self):
        gjs = shutil.which("gjs")
        if gjs is None:
            self.skipTest("gjs is not installed")
        from ai_native_linux.unix_socket import create_unix_server

        root = Path(tempfile.mkdtemp(prefix="ai-native-gjs-"))
        os.chmod(root, 0o700)
        server = create_unix_server(object(), root / "runtime.sock")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            probe = ROOT / "tests" / "runtime_client_probe.js"
            result = subprocess.run(
                [gjs, "-m", str(probe), str(server.socket_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.strip()), {"status": "ok"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            shutil.rmtree(root, ignore_errors=True)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux GJS")
    def test_panel_presenter_loads_in_gjs(self):
        gjs = shutil.which("gjs")
        if gjs is None:
            self.skipTest("gjs is not installed")
        probe = ROOT / "tests" / "presenter_probe.js"
        result = subprocess.run(
            [gjs, "-m", str(probe)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.strip()), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
