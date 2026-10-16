import sys
import threading
import time
import traceback
import webbrowser
import ctypes
from typing import Optional, Tuple, List

from PySide6.QtCore import QTimer, Qt, Slot, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox, QWidget, QDialog, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QSpinBox, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QDialogButtonBox, QStyle, QComboBox, QProgressBar
)

from app.config import Config, Settings, APP_NAME
from app.ntp_client import query_ntp, NtpResult, ping_servers
from app.icons import connected_icon, disconnected_icon, idle_icon, default_app_icon, github_icon, star_icon
from app import time_utils
from app.localization import configure_chinese
from app.timezone_follow import TimezoneFollower
import requests

# Thread-safe flags and shared state
class SyncState:
    def __init__(self):
        self.last_success_time = 0.0
        self.last_result: Optional[NtpResult] = None
        self.running = False
        self.stop_requested = False
        self.last_best_server: str = ""

class NtpSyncManager:
    """
    Manages background NTP probing, drift detection, and time updates.
    """
    def __init__(self, cfg: Config, state: SyncState, on_status_changed=None, on_best_server_changed=None):
        self.cfg = cfg
        self.state = state
        self.on_status_changed = on_status_changed
        self.on_best_server_changed = on_best_server_changed
        self.thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.thread and self.thread.is_alive():
                return
            self.state.stop_requested = False
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.state.stop_requested = True

    def _choose_server(self, timeout_ms: int) -> Optional[NtpResult]:
        s = self.cfg.settings
        servers = list(s.ntp_servers)
        if not servers:
            return None
        # Try preferred if manual mode
        if not s.auto_select_server and s.preferred_server:
            res = query_ntp(s.preferred_server, timeout_ms=timeout_ms, samples=3)
            if res.success:
                return res
        # Auto: choose best by lowest RTT (use multiple samples)
        best: Optional[NtpResult] = None
        for host in servers:
            res = query_ntp(host, timeout_ms=timeout_ms, samples=3)
            if res.success:
                if (best is None) or (res.rtt_ms < best.rtt_ms):
                    best = res
        # Announce if best changed
        if best and s.auto_select_server and best.server != self.state.last_best_server:
            self.state.last_best_server = best.server
            if self.on_best_server_changed:
                try:
                    self.on_best_server_changed(best.server, best.rtt_ms)
                except Exception:
                    pass
        return best

    def _verify_and_apply_time(self, selected: NtpResult) -> Tuple[bool, str]:
        threshold_ms = self.cfg.settings.drift_threshold_ms
        drift = selected.offset_ms
        if abs(drift) < threshold_ms:
            return False, f"当前时差为 {drift:.1f} 毫秒，未达到校时阈值（{threshold_ms} 毫秒），无需调整。"
        now_epoch = time.time()
        target_epoch_ms = int((now_epoch + (drift / 1000.0)) * 1000)
        ok, msg = time_utils.set_system_time_utc_via_helper(target_epoch_ms)
        return ok, f"本次时差为 {drift:.1f} 毫秒。{msg}"

    def _run_loop(self):
        self.state.running = True
        try:
            while not self.state.stop_requested:
                if not self.cfg.settings.auto_sync_enabled:
                    time.sleep(1.0)
                    continue

                try:
                    selected = self._choose_server(self.cfg.settings.ntp_timeout_ms)
                    if not selected:
                        self.state.last_result = NtpResult(server="（无）", rtt_ms=0, offset_ms=0, success=False, error="没有可用的校时服务器")
                        if self.on_status_changed:
                            self.on_status_changed()
                        time.sleep(self.cfg.settings.check_interval_sec)
                        continue

                    self.state.last_result = selected
                    if self.on_status_changed:
                        self.on_status_changed()

                    changed, message = self._verify_and_apply_time(selected)
                    if changed:
                        time.sleep(3.0)
                        recheck = query_ntp(selected.server, timeout_ms=self.cfg.settings.ntp_timeout_ms, samples=2)
                        if recheck.success and abs(recheck.offset_ms) < self.cfg.settings.drift_threshold_ms:
                            self.state.last_success_time = time.time()
                        else:
                            self.state.last_success_time = time.time()
                    else:
                        self.state.last_success_time = time.time()

                except Exception as e:
                    self.state.last_result = NtpResult(server="（异常）", rtt_ms=0, offset_ms=0, success=False, error=f"校时过程中发生异常：{e}")

                if self.on_status_changed:
                    self.on_status_changed()

                interval = max(15, int(self.cfg.settings.check_interval_sec))
                slept = 0
                while slept < interval and not self.state.stop_requested:
                    time.sleep(0.5)
                    slept += 0.5
        finally:
            self.state.running = False

class WelcomeDialog(QDialog):
    """
    Aesthetic dialog shown on manual startup to help user find and use the tray icon.
    Includes an option to never show again (for manual launches).
    """
    def __init__(self, cfg: Config, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("欢迎使用 TimeGuardian")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet("""
            QDialog {
                background-color: #0f172a;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 10px;
            }
            QLabel, QCheckBox {
                color: #e2e8f0;
            }
            QLabel#title {
                color: #93c5fd;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#body {
                font-size: 13px;
                line-height: 1.4em;
            }
            QCheckBox {
                font-size: 12px;
            }
            QPushButton {
                background-color: #2563eb;
                color: white;
                padding: 8px 18px;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        v = QVBoxLayout(self)
        title = QLabel("TimeGuardian 已在托盘中运行")
        title.setObjectName("title")
        v.addWidget(title)
        body = QLabel(
            "程序图标位于任务栏右下角的通知区域，可能收在折叠菜单中。\n"
            "右键点击图标，即可打开设置、立即校时或调整时区。\n\n"
            "图标状态：\n"
            "• 蓝色圆点：尚未校时或正在等待\n"
            "• 绿色对勾：最近校时正常\n"
            "• 红色叉号：最近未能完成校时\n\n"
            "可在设置中关闭此欢迎提示。登录自启动时不会弹出本窗口。"
        )
        body.setObjectName("body")
        body.setWordWrap(True)
        v.addWidget(body)
        self.chk_dont_show = QCheckBox("以后手动启动时不再显示")
        self.chk_dont_show.setChecked(False)
        v.addWidget(self.chk_dont_show)
        v.addSpacing(6)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        v.addWidget(buttons)
        buttons.accepted.connect(self._on_accept)

    def _on_accept(self):
        if self.chk_dont_show.isChecked():
            self.cfg.settings.show_welcome_on_launch = False
            self.cfg.save()
        self.accept()

class SettingsDialog(QDialog):
    """
    Settings dialog for configuring sync behavior and optional features.
    Includes server pinging with ultra-aesthetic progress, sorting by RTT, and preferred server selection.
    """
    ping_results_ready = Signal(list)   # list of dicts: {'server': str, 'rtt': float or None, 'method': 'NTP'|'ICMP'|'fail'}
    ping_progress = Signal(int, int)    # done, total

    def __init__(self, cfg: Config, parent: Optional[QWidget] = None, follow_status="尚未检查"):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 设置")
        self.setMinimumWidth(620)
        self.cfg = cfg

        layout = QVBoxLayout(self)

        # Auto sync
        self.chk_auto = QCheckBox("定期自动校时")
        self.chk_auto.setToolTip("定期检查系统时间，仅在时差达到阈值时校正；此选项不会自动切换时区。")
        self.chk_auto.setChecked(cfg.settings.auto_sync_enabled)
        layout.addWidget(self.chk_auto)

        # Interval
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("校时检查间隔："))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(30, 86400)
        self.spin_interval.setValue(cfg.settings.check_interval_sec)
        self.spin_interval.setSuffix(" 秒")
        row1.addWidget(self.spin_interval)
        layout.addLayout(row1)

        # Threshold
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("校时阈值："))
        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(10, 60000)
        self.spin_threshold.setValue(cfg.settings.drift_threshold_ms)
        self.spin_threshold.setSuffix(" 毫秒")
        self.spin_threshold.setToolTip("系统时间与服务器时间相差达到此值时，才会尝试校正。")
        row2.addWidget(self.spin_threshold)
        layout.addLayout(row2)

        # Server list and controls
        layout.addWidget(QLabel("NTP 校时服务器："))
        self.list_servers = QListWidget()
        for s in cfg.settings.ntp_servers:
            self.list_servers.addItem(QListWidgetItem(s))
        layout.addWidget(self.list_servers)

        # Aesthetic progress bar for pinging
        self.pbar = QProgressBar()
        self.pbar.setMinimum(0)
        self.pbar.setMaximum(100)
        self.pbar.setValue(0)
        self.pbar.setTextVisible(True)
        self.pbar.setVisible(False)
        self.pbar.setStyleSheet("""
            QProgressBar {
                background-color: #0f172a;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                            stop:0 #34d399, stop:1 #10b981);
                border-radius: 6px;
            }
        """)
        layout.addWidget(self.pbar)

        row3 = QHBoxLayout()
        self.txt_new_server = QLineEdit()
        self.txt_new_server.setPlaceholderText("服务器地址，例如 time.google.com")
        self.btn_add = QPushButton("添加")
        self.btn_remove = QPushButton("移除所选")
        self.btn_ping = QPushButton("测试延迟")
        row3.addWidget(self.txt_new_server)
        row3.addWidget(self.btn_add)
        row3.addWidget(self.btn_remove)
        row3.addWidget(self.btn_ping)
        layout.addLayout(row3)
        self.btn_add.clicked.connect(self._add_server)
        self.btn_remove.clicked.connect(self._remove_selected_servers)
        self.btn_ping.clicked.connect(self._ping_servers)

        # Auto vs Preferred
        self.chk_auto_select = QCheckBox("自动选择延迟最低的可用服务器")
        self.chk_auto_select.setChecked(cfg.settings.auto_select_server)
        layout.addWidget(self.chk_auto_select)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("首选服务器："))
        self.txt_preferred = QLineEdit()
        self.txt_preferred.setText(cfg.settings.preferred_server or "")
        self.txt_preferred.setToolTip("关闭自动选择后，优先使用此服务器。")
        row4.addWidget(self.txt_preferred)
        self.btn_set_from_selection = QPushButton("将所选设为首选")
        row4.addWidget(self.btn_set_from_selection)
        layout.addLayout(row4)
        self.btn_set_from_selection.clicked.connect(self._set_preferred_from_selection)

        # Startup
        self.chk_startup = QCheckBox("登录 Windows 后自动启动")
        self.chk_startup.setChecked(cfg.settings.launch_at_startup)
        layout.addWidget(self.chk_startup)

        # Optional features
        self.chk_tz_auto = QCheckBox("自动跟随代理出口时区")
        self.chk_tz_auto.setChecked(cfg.settings.auto_timezone_enabled)
        self.chk_tz_auto.setToolTip("至少两个查询来源一致，且连续两轮检测到同一出口后切换。查询失败或无共识时保留当前时区。")
        layout.addWidget(self.chk_tz_auto)
        timezone_row = QHBoxLayout()
        timezone_row.addWidget(QLabel("时区检查间隔："))
        self.spin_tz_interval = QSpinBox()
        self.spin_tz_interval.setRange(30, 3600)
        self.spin_tz_interval.setSuffix(" 秒")
        self.spin_tz_interval.setValue(cfg.settings.timezone_check_interval_sec)
        timezone_row.addWidget(self.spin_tz_interval)
        layout.addLayout(timezone_row)
        self.follow_status = QLabel(follow_status)
        self.follow_status.setWordWrap(True)
        self.follow_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.follow_status)

        self.chk_tz_ip = QCheckBox("允许手动根据公网 IP 设置时区")
        self.chk_tz_ip.setToolTip("可从托盘菜单立即查询并设置时区；仍须至少两个查询来源一致。自动跟随由上方独立开关控制。")
        self.chk_tz_ip.setChecked(cfg.settings.allow_timezone_from_ip)
        layout.addWidget(self.chk_tz_ip)

        self.chk_region_ip = QCheckBox("允许根据公网 IP 调整 Windows 地区和区域格式（可能需要重启）")
        self.chk_region_ip.setToolTip("会尝试更改 Windows 所在国家或地区，以及日期、数字等区域格式。")
        self.chk_region_ip.setChecked(cfg.settings.allow_region_from_ip)
        layout.addWidget(self.chk_region_ip)

        # Notifications
        self.chk_notifications = QCheckBox("显示托盘通知")
        self.chk_notifications.setChecked(cfg.settings.notifications_enabled)
        layout.addWidget(self.chk_notifications)

        # Welcome dialog visibility on manual launch
        self.chk_show_welcome = QCheckBox("手动启动时显示欢迎提示")
        self.chk_show_welcome.setChecked(cfg.settings.show_welcome_on_launch)
        layout.addWidget(self.chk_show_welcome)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # Signals
        self.ping_results_ready.connect(self._apply_ping_results)
        self.ping_progress.connect(self._on_ping_progress)

    def _add_server(self):
        s = self.txt_new_server.text().strip()
        if s:
            self.list_servers.addItem(QListWidgetItem(s))
            self.txt_new_server.clear()

    def _remove_selected_servers(self):
        for item in self.list_servers.selectedItems():
            self.list_servers.takeItem(self.list_servers.row(item))

    def _set_preferred_from_selection(self):
        items = self.list_servers.selectedItems()
        if not items:
            QMessageBox.information(self, APP_NAME, "请先在列表中选择一台服务器。")
            return
        self.txt_preferred.setText(items[0].data(Qt.UserRole) or items[0].text())

    def _on_ping_progress(self, done: int, total: int):
        if total <= 0:
            return
        if not self.pbar.isVisible():
            self.pbar.setVisible(True)
        self.pbar.setMaximum(total)
        self.pbar.setValue(done)
        self.pbar.setFormat(f"正在测试延迟… {done}/{total}")

    def _ping_servers(self):
        # Disable only the ping button during the operation
        self.btn_ping.setEnabled(False)
        self.pbar.setVisible(True)
        self.pbar.setValue(0)
        self.pbar.setFormat("正在测试延迟… 0/0")
        def work():
            try:
                servers = [(self.list_servers.item(i).data(Qt.UserRole) or self.list_servers.item(i).text()).strip()
                           for i in range(self.list_servers.count())
                           if self.list_servers.item(i).text().strip()]
                if not servers:
                    self.ping_results_ready.emit([])
                    return
                def progress(done, total):
                    # Emit from worker thread; Qt will queue it to UI thread
                    self.ping_progress.emit(done, total)
                results = ping_servers(
                    servers, timeout_ms=1000, max_workers=min(8, max(1, len(servers))), samples=5, progress_cb=progress
                )
                payload = []
                for r in results:
                    if r.success:
                        payload.append({"server": r.server, "rtt": r.rtt_ms, "method": "NTP"})
                    elif r.rtt_ms > 0:
                        payload.append({"server": r.server, "rtt": r.rtt_ms, "method": "ICMP"})
                    else:
                        payload.append({"server": r.server, "rtt": None, "method": "fail"})
                self.ping_results_ready.emit(payload)
            finally:
                QTimer.singleShot(0, lambda: self.btn_ping.setEnabled(True))
        threading.Thread(target=work, daemon=True).start()

    @Slot(list)
    def _apply_ping_results(self, payload: List[dict]):
        # Hide progress at the end
        self.pbar.setVisible(False)
        if not payload:
            QMessageBox.information(self, APP_NAME, "列表为空，请先添加校时服务器。")
            return
        # Sort by numeric RTT (None => inf)
        def rtt_val(entry):
            return entry["rtt"] if isinstance(entry["rtt"], (int, float)) else float("inf")
        sorted_payload = sorted(payload, key=rtt_val)
        # Update list widget texts and reorder based on sorted RTT
        self.list_servers.clear()
        reachable = 0
        for entry in sorted_payload:
            server = entry["server"]
            method = "未响应" if entry["method"] == "fail" else entry["method"]
            if entry["rtt"] is None:
                label = f"{server}（测试失败）"
            else:
                label = f"{server}（延迟：{entry['rtt']:.0f} 毫秒，{method}，5 次采样的中位数）"
                reachable += 1
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, server)
            self.list_servers.addItem(item)
        # Save ping summary and also update stored server order to the sorted order
        self.cfg.settings.last_ping_results = {
            e["server"]: (float(e["rtt"]) if isinstance(e["rtt"], (int, float)) else 0.0)
            for e in sorted_payload
        }
        self.cfg.settings.ntp_servers = [(self.list_servers.item(i).data(Qt.UserRole) or self.list_servers.item(i).text()).strip()
                                         for i in range(self.list_servers.count())]
        self.cfg.save()
        # Inform the user which server is currently the best by ping
        best = next((e for e in sorted_payload if isinstance(e["rtt"], (int, float))), None)
        if best:
            QMessageBox.information(self, APP_NAME, f"本次延迟最低的服务器：{best['server']}\n延迟：{best['rtt']:.0f} 毫秒（{best['method']}，5 次采样的中位数）。\n\n"
                                                    f"可将其设为首选，也可以继续使用自动选择。")
        else:
            QMessageBox.information(self, APP_NAME, "测试完成，所有服务器均未响应。请检查网络连接或更换服务器。")

    def accept(self):
        s = self.cfg.settings
        s.auto_sync_enabled = self.chk_auto.isChecked()
        s.check_interval_sec = int(self.spin_interval.value())
        s.drift_threshold_ms = int(self.spin_threshold.value())
        s.ntp_servers = [(self.list_servers.item(i).data(Qt.UserRole) or self.list_servers.item(i).text()).strip()
                         for i in range(self.list_servers.count())
                         if self.list_servers.item(i).text().strip()]
        if not s.ntp_servers:
            QMessageBox.warning(self, "无法保存设置", "请至少添加一台 NTP 校时服务器。")
            return
        s.auto_select_server = self.chk_auto_select.isChecked()
        s.preferred_server = self.txt_preferred.text().strip()
        s.launch_at_startup = self.chk_startup.isChecked()
        s.allow_timezone_from_ip = self.chk_tz_ip.isChecked()
        s.auto_timezone_enabled = self.chk_tz_auto.isChecked()
        s.timezone_check_interval_sec = self.spin_tz_interval.value()
        s.allow_region_from_ip = self.chk_region_ip.isChecked()
        s.show_welcome_on_launch = self.chk_show_welcome.isChecked()
        s.notifications_enabled = self.chk_notifications.isChecked()
        self.cfg.save()
        super().accept()

class TrayApp:
    """
    Main tray application that orchestrates UI and background syncing.
    """
    def __init__(self):
        self.app = QApplication(sys.argv)
        configure_chinese(self.app)
        # Brand the app so notifications don't say "Python"
        try:
            self.app.setApplicationName("TG")
            self.app.setApplicationDisplayName("TimeGuardian")
            self.app.setOrganizationName("MixTruS")
            # Set explicit AppUserModelID for Windows 7+
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MixTruS.TG")
        except Exception:
            pass

        self.app.setQuitOnLastWindowClosed(False)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, APP_NAME, "当前 Windows 会话不支持系统托盘，程序无法运行。")
            sys.exit(1)

        # Detect startup mode via '--startup' flag
        self.is_startup_launch = any(arg.lower() == "--startup" for arg in sys.argv[1:])

        self.cfg = Config()
        self.state = SyncState()
        self.timezone_follower = TimezoneFollower(self.cfg)
        self.timezone_follower.notice.connect(
            lambda text, error: self._notify(text, QSystemTrayIcon.Warning if error else QSystemTrayIcon.Information, 6000)
        )

        # Use default app icon (logo.png if present) for app window icon
        try:
            self.app.setWindowIcon(default_app_icon())
        except Exception:
            pass

        self.sync_manager = NtpSyncManager(
            self.cfg,
            self.state,
            on_status_changed=self._on_status_changed,
            on_best_server_changed=self._on_best_server_changed
        )

        # Tray and menu
        self.tray = QSystemTrayIcon()
        self.tray.setToolTip(f"{APP_NAME} · 时间校准")

        # Ensure a visible default icon (logo or idle) before showing tray
        try:
            icon = default_app_icon()
            if icon.isNull():
                icon = self.app.style().standardIcon(QStyle.SP_ComputerIcon)
            self.tray.setIcon(icon)
        except Exception:
            self.tray.setIcon(self.app.style().standardIcon(QStyle.SP_ComputerIcon))

        self.menu = QMenu()

        self.action_sync_now = QAction("立即校时")
        self.action_sync_now.triggered.connect(self.sync_now)
        self.menu.addAction(self.action_sync_now)

        self.action_auto = QAction("定期自动校时")
        self.action_auto.setCheckable(True)
        self.action_auto.setChecked(self.cfg.settings.auto_sync_enabled)
        self.action_auto.triggered.connect(self.toggle_auto_sync)
        self.menu.addAction(self.action_auto)

        self.menu.addSeparator()

        self.action_startup = QAction("登录后自动启动")
        self.action_startup.setCheckable(True)
        self.action_startup.setChecked(self.cfg.settings.launch_at_startup)
        self.action_startup.triggered.connect(self.toggle_startup)
        self.menu.addAction(self.action_startup)

        self.menu.addSeparator()

        self.action_set_tz = QAction("手动选择时区…")
        self.action_set_tz.triggered.connect(self.set_timezone_dialog)
        self.menu.addAction(self.action_set_tz)

        self.action_tz_from_ip = QAction("根据公网 IP 设置时区")
        self.action_tz_from_ip.triggered.connect(self.set_timezone_from_ip)
        self.menu.addAction(self.action_tz_from_ip)

        self.action_auto_timezone = QAction("自动跟随代理出口时区")
        self.action_auto_timezone.setCheckable(True)
        self.action_auto_timezone.setChecked(self.cfg.settings.auto_timezone_enabled)
        self.action_auto_timezone.triggered.connect(self.toggle_auto_timezone)
        self.menu.addAction(self.action_auto_timezone)
        self.action_timezone_status = QAction("查看时区跟随状态…")
        self.action_timezone_status.triggered.connect(self.show_timezone_status)
        self.menu.addAction(self.action_timezone_status)

        self.action_region_from_ip = QAction("根据公网 IP 调整 Windows 地区和区域格式")
        self.action_region_from_ip.triggered.connect(self.set_region_from_ip)
        self.menu.addAction(self.action_region_from_ip)

        self.menu.addSeparator()

        self.action_settings = QAction("设置…")
        self.action_settings.triggered.connect(self.open_settings)
        self.menu.addAction(self.action_settings)

        # New GitHub link (ultra aesthetic)
        self.menu.addSeparator()
        self.action_github = QAction(github_icon(), "访问作者的 GitHub 主页")
        self.action_github.triggered.connect(lambda: webbrowser.open("https://github.com/mixtrus"))
        self.menu.addAction(self.action_github)

        # Disabled invitation action (ultra aesthetic star)
        self.action_invite = QAction(star_icon(), "欢迎在 GitHub 点赞支持或反馈问题")
        self.action_invite.setEnabled(False)
        self.menu.addAction(self.action_invite)

        self.menu.addSeparator()

        self.action_exit = QAction("退出")
        self.action_exit.triggered.connect(self.exit_app)
        self.menu.addAction(self.action_exit)

        self.tray.setContextMenu(self.menu)

        # Initial icon/tooltip
        self._update_tray_icon()

        # Show tray, then optional welcome dialog (manual launch only, and only if enabled)
        self.tray.show()
        if (not self.is_startup_launch) and self.cfg.settings.show_welcome_on_launch:
            self._show_welcome_dialog()

        if self.cfg.settings.auto_sync_enabled:
            self.sync_manager.start()

        self._apply_startup_flag(self.cfg.settings.launch_at_startup)

        self.ui_timer = QTimer()
        self.ui_timer.timeout.connect(self._update_tray_icon)
        self.ui_timer.start(1000)
        self.timezone_follower.configure()

    def _notify(self, text: str, icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.NoIcon, timeout_ms: int = 4000):
        """
        Centralized notifications that respect the 'Enable notifications' setting.
        Use NoIcon to avoid the default red X; branding is handled via app identity and tray icon.
        """
        if not self.cfg.settings.notifications_enabled:
            return
        try:
            # Title set to "TG" per request
            self.tray.showMessage("TimeGuardian", text, icon, timeout_ms)
        except Exception:
            pass

    def _show_welcome_dialog(self):
        try:
            dlg = WelcomeDialog(self.cfg)
            dlg.exec()
        except Exception:
            self._notify("TimeGuardian 已启动。右键点击托盘图标即可打开设置。")

    def _on_status_changed(self):
        QTimer.singleShot(0, self._update_tray_icon)

    def _on_best_server_changed(self, server: str, rtt_ms: float):
        # Announce the new best server selection (auto mode only)
        self._notify(f"已自动选择校时服务器：{server}（延迟 {rtt_ms:.0f} 毫秒）")

    def _is_connected(self) -> bool:
        ttl = self.cfg.settings.connected_status_ttl_sec
        if self.state.last_success_time <= 0:
            return False
        return (time.time() - self.state.last_success_time) < ttl

    def _update_tray_icon(self):
        # Icon priority: Default app icon (logo or idle) when no result yet -> Connected -> Disconnected
        if self.state.last_result is None:
            self.tray.setIcon(default_app_icon())
            tip = f"{APP_NAME} · 等待校时"
        else:
            if self._is_connected():
                self.tray.setIcon(connected_icon())
            else:
                self.tray.setIcon(disconnected_icon())
            if self.state.last_result and self.state.last_result.success:
                tip = f"{APP_NAME}\n服务器：{self.state.last_result.server}\n延迟：{self.state.last_result.rtt_ms:.0f} 毫秒 · 时差：{self.state.last_result.offset_ms:.0f} 毫秒"
            elif self.state.last_result and not self.state.last_result.success:
                tip = f"{APP_NAME} · 最近一次校时失败\n{self.state.last_result.error or '原因未知'}"
            else:
                tip = f"{APP_NAME} · 等待校时"
        self.tray.setToolTip(tip + "\n时区跟随：" + self.timezone_follower.status.splitlines()[0])

    @Slot()
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.sync_now()

    def toggle_auto_sync(self, checked: bool):
        self.cfg.settings.auto_sync_enabled = checked
        self.cfg.save()
        if checked:
            self.sync_manager.start()
        else:
            self.sync_manager.stop()

    def _apply_startup_flag(self, enable: bool):
        exe = sys.executable
        ok, msg = time_utils.register_startup(APP_NAME, exe, enable)
        if not ok:
            self._notify(f"自启动设置未生效：{msg}", QSystemTrayIcon.Information, 5000)

    def toggle_startup(self, checked: bool):
        self.cfg.settings.launch_at_startup = checked
        self.cfg.save()
        ok, msg = time_utils.register_startup(APP_NAME, sys.executable, checked)
        self._notify(msg, QSystemTrayIcon.Information, 3000)

    def sync_now(self):
        def work():
            try:
                res = self.sync_manager._choose_server(self.cfg.settings.ntp_timeout_ms)
                if not res:
                    self.state.last_result = NtpResult(server="（无）", rtt_ms=0, offset_ms=0, success=False, error="所有校时服务器均未响应")
                    self._on_status_changed()
                    return
                self.state.last_result = res
                self._on_status_changed()
                changed, msg = self.sync_manager._verify_and_apply_time(res)
                if changed:
                    time.sleep(2.0)
                self.state.last_success_time = time.time()
                self._on_status_changed()
                self._notify(msg)
            except Exception as e:
                self.state.last_result = NtpResult(server="（异常）", rtt_ms=0, offset_ms=0, success=False, error=f"校时过程中发生异常：{e}")
                self._on_status_changed()
                self._notify(f"校时失败：{e}", QSystemTrayIcon.Critical, 5000)
        threading.Thread(target=work, daemon=True).start()

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, parent=None, follow_status=self.timezone_follower.status)
        self.timezone_follower.status_changed.connect(dlg.follow_status.setText)
        if dlg.exec() == QDialog.Accepted:
            self._apply_startup_flag(self.cfg.settings.launch_at_startup)
            self.action_auto_timezone.setChecked(self.cfg.settings.auto_timezone_enabled)
            self.timezone_follower.configure()
            if self.cfg.settings.auto_sync_enabled and not self.state.running:
                self.sync_manager.start()

    def set_timezone_dialog(self):
        choices = time_utils.list_timezone_choices_labels()
        if not choices:
            QMessageBox.warning(None, APP_NAME, "无法读取 Windows 时区列表。", QMessageBox.Ok)
            return
        d = QDialog()
        d.setWindowTitle("选择时区")
        d.setMinimumWidth(540)
        v = QVBoxLayout(d)
        lbl = QLabel("选择时区（按 UTC 偏移和城市／地区排列）：")
        v.addWidget(lbl)
        if self.cfg.settings.auto_timezone_enabled:
            hint = QLabel("手动选择并确认后，将暂停自动跟随。")
            v.addWidget(hint)
        cb = QComboBox()
        for label, key in choices:
            cb.addItem(label, userData=key)
        current = time_utils.get_current_timezone()
        idx = next((i for i in range(cb.count()) if cb.itemData(i) == current), -1)
        if idx >= 0:
            cb.setCurrentIndex(idx)
        v.addWidget(cb)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        v.addWidget(btns)
        btns.accepted.connect(d.accept)
        btns.rejected.connect(d.reject)
        if d.exec() == QDialog.Accepted:
            key = cb.currentData()
            self.cfg.settings.auto_timezone_enabled = False
            self.cfg.save()
            self.action_auto_timezone.setChecked(False)
            self.timezone_follower.configure()
            ok, msg = time_utils.set_windows_timezone(key)
            self._notify(msg, QSystemTrayIcon.Information if ok else QSystemTrayIcon.Critical, 4000)

    def set_timezone_from_ip(self):
        if not self.cfg.settings.allow_timezone_from_ip:
            ret = QMessageBox.question(None, APP_NAME, "尚未启用“根据公网 IP 设置时区”。是否启用并立即查询？", QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
            self.cfg.settings.allow_timezone_from_ip = True
            self.cfg.save()
        self.timezone_follower.request_check(manual=True)

    def toggle_auto_timezone(self, enabled):
        self.cfg.settings.auto_timezone_enabled = bool(enabled)
        self.cfg.save()
        self.timezone_follower.configure()

    def show_timezone_status(self):
        follower = self.timezone_follower
        details = "\n".join(follower.last_result.details) if follower.last_result else ""
        message = f"{follower.status}\n\n{details}\n\n运行记录：{follower.log_dir / 'timezone-follow.log'}"
        box = QMessageBox(QMessageBox.Information, "时区跟随状态", message, QMessageBox.Ok)
        box.setTextFormat(Qt.PlainText)
        box.exec()

    def set_region_from_ip(self):
        if not self.cfg.settings.allow_region_from_ip:
            ret = QMessageBox.question(None, APP_NAME, "尚未启用“根据公网 IP 调整 Windows 地区和区域格式”。\n此操作可能影响日期、数字等显示格式。是否启用并继续？", QMessageBox.Yes | QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
            self.cfg.settings.allow_region_from_ip = True
            self.cfg.save()
        def work():
            try:
                r = requests.get("https://ipapi.co/json/", timeout=5, headers={"User-Agent": "TimeGuardian/1.0"})
                cc = r.json().get("country") if r.status_code == 200 else None
            except Exception:
                cc = None
            if not cc:
                self._notify("无法根据公网 IP 确定国家或地区，请检查网络连接后重试。", QSystemTrayIcon.Critical, 4000)
                return
            ok, msg = time_utils.set_region_from_country_code(cc)
            self._notify(msg, QSystemTrayIcon.Information if ok else QSystemTrayIcon.Critical, 6000)
        threading.Thread(target=work, daemon=True).start()

    def exit_app(self):
        try:
            self.sync_manager.stop()
            self.timezone_follower.stop()
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()

    def run(self):
        sys.exit(self.app.exec())

def main():
    try:
        app = TrayApp()
        app.run()
    except Exception as e:
        QMessageBox.critical(None, APP_NAME, f"程序无法继续运行：{e}\n\n详细错误信息：\n{traceback.format_exc()}")
        sys.exit(1)

if __name__ == "__main__":
    main()
