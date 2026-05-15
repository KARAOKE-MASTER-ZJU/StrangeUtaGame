"""设置界面 "应用更新" 分组。

包含：

* 启动时检查更新（开关）
* 源排序（三个 ComboBox 表示位置 1/2/3 各使用哪个源）
* "立即检查更新" 按钮（位于关于卡组上方）

还提供 :func:`refresh_about_version`：把硬编码的关于卡片版本号替换为
``__version__``，并附带一个 "检查更新" 按钮。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushButton,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    SwitchButton,
)

from ...__version__ import __version__
from .. import installer
from ..settings import UpdaterSettings, ensure_persisted
from ..sources import (
    DEFAULT_ORDER,
    SOURCE_IDS,
    SOURCE_LABELS,
    SourceId,
    normalize_order,
)
from ..worker import CheckResult, UpdateChecker
from .source_order_dialog import SourceOrderDialog
from .update_dialog import UpdateAvailableDialog, UpdateCheckErrorDialog

if TYPE_CHECKING:
    from ...frontend.settings.settings_interface import SettingsInterface


# ───────────────────────── 自定义卡片 ─────────────────────────


class _StartupCheckCard(SettingCard):
    """启动时自动检查更新（开关）。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.SYNC,
            "启动时检查更新",
            "应用启动后在后台轻量检查 GitHub Release，发现新版本时弹窗提示",
            parent,
        )
        self.switch = SwitchButton(self)
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


class _CheckIntervalCard(SettingCard):
    """启动检查的最小间隔（小时）。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.HISTORY,
            "启动检查间隔",
            "距上次检查不足该时长时，启动期不再发起请求（手动检查不受限）",
            parent,
        )
        self.spin = SpinBox(self)
        self.spin.setRange(0, 168)  # 0~7 天
        self.spin.setSingleStep(1)
        self.spin.setSuffix(" 小时")
        self.spin.setFixedWidth(160)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


class _SourceOrderCard(SettingCard):
    """显示当前源优先级 + "编辑顺序"按钮（点击弹出 :class:`SourceOrderDialog`）。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.PALETTE,
            "更新源优先级",
            "（尚未读取）",
            parent,
        )
        self.btn_edit = PushButton("编辑顺序", self)
        self.btn_edit.setFont(QFont("Microsoft YaHei", 10))
        self.btn_edit.setMinimumWidth(110)
        self.hBoxLayout.addWidget(self.btn_edit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def set_order(self, order: List[SourceId]) -> None:
        """更新副标题文本展示当前顺序。"""
        labels = [SOURCE_LABELS.get(sid, sid) for sid in order]
        # 用 " → " 展示，且把 ghproxy 等简称化以减少宽度
        short = []
        for sid in order:
            if sid == "github":
                short.append("GitHub")
            elif sid == "ghproxy":
                short.append("GHProxy")
            elif sid == "fastgit":
                short.append("FastGit")
            else:
                short.append(sid)
        self.setContent(" → ".join(short))


class _CheckNowCard(SettingCard):
    """立即检查更新按钮。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.UPDATE,
            "立即检查更新",
            "立即从所选源拉取最新发布信息（不受启动检查间隔限制）",
            parent,
        )
        self.btn = PushButton("检查更新", self)
        self.btn.setFont(QFont("Microsoft YaHei", 10))
        self.btn.setMinimumWidth(120)
        self.hBoxLayout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


# ───────────────────────── 主入口 ─────────────────────────


def attach_update_group(settings_interface: "SettingsInterface") -> None:
    """把"应用更新"分组追加到设置界面（位于"关于"之前）。"""
    parent = settings_interface
    group = SettingCardGroup("应用更新", parent.scrollWidget)

    startup_card = _StartupCheckCard(group)
    interval_card = _CheckIntervalCard(group)
    order_card = _SourceOrderCard(group)
    check_card = _CheckNowCard(group)

    group.addSettingCard(startup_card)
    group.addSettingCard(interval_card)
    group.addSettingCard(order_card)
    group.addSettingCard(check_card)
    parent.expandLayout.addWidget(group)

    parent.update_group = group  # type: ignore[attr-defined]
    parent.card_update_startup = startup_card  # type: ignore[attr-defined]
    parent.card_update_interval = interval_card  # type: ignore[attr-defined]
    parent.card_update_sources = order_card  # type: ignore[attr-defined]
    parent.card_update_check = check_card  # type: ignore[attr-defined]

    # ── 初值 ──
    # ``ensure_persisted`` 在首次启动时把默认 updater 节点写到 config.json
    s = ensure_persisted(parent.get_settings())
    startup_card.switch.setChecked(bool(s.check_on_startup))
    interval_card.spin.setValue(int(s.min_check_interval_hours))
    order_card.set_order(s.source_order)

    # ── 槽：启动开关 ──
    def _on_startup_changed(checked: bool):
        cur = UpdaterSettings.load(parent.get_settings())
        cur.check_on_startup = bool(checked)
        cur.save(parent.get_settings())

    startup_card.switch.checkedChanged.connect(_on_startup_changed)

    # ── 槽：启动检查间隔 ──
    def _on_interval_changed(value: int):
        cur = UpdaterSettings.load(parent.get_settings())
        cur.min_check_interval_hours = int(value)
        cur.save(parent.get_settings())

    interval_card.spin.valueChanged.connect(_on_interval_changed)

    # ── 槽：源排序（弹窗）──
    def _on_edit_order_clicked():
        cur = UpdaterSettings.load(parent.get_settings())
        dlg = SourceOrderDialog(current=list(cur.source_order), parent=parent)
        if dlg.exec():
            cur.source_order = list(dlg.order)
            cur.save(parent.get_settings())
            order_card.set_order(cur.source_order)

    order_card.btn_edit.clicked.connect(_on_edit_order_clicked)

    # ── 槽：立即检查更新 ──
    def _on_check_clicked():
        _trigger_manual_check(parent, check_card.btn)

    check_card.btn.clicked.connect(_on_check_clicked)


def refresh_about_version(settings_interface: "SettingsInterface") -> None:
    """把"关于"卡组里的硬编码版本号替换为当前 ``__version__``。

    可在 ``_init_about_group`` 后调用一次；安全幂等。
    """
    parent = settings_interface
    about_group = getattr(parent, "about_group", None)
    if about_group is None:
        return

    # SettingCardGroup 内部用 ExpandLayout 管理 cards，但其 ``count()`` 不报告 widget 数量。
    # 改为直接遍历 about_group 的子 widget（SettingCard 实例），按标题匹配。
    from qfluentwidgets import SettingCard

    for child in about_group.findChildren(SettingCard):
        try:
            title = child.titleLabel.text()  # type: ignore[attr-defined]
        except AttributeError:
            continue
        if title == "StrangeUtaGame - 歌词打轴软件":
            try:
                child.setContent(  # type: ignore[attr-defined]
                    f"版本 v{__version__}  |  由 RhythmicaLyrics 启发"
                )
            except AttributeError:
                pass
            break


# ───────────────────────── 手动检查 ─────────────────────────


def _trigger_manual_check(parent: "SettingsInterface", btn: PushButton) -> None:
    """用户在设置里手动点击「检查更新」时的入口。"""
    btn.setEnabled(False)
    btn.setText("检查中...")

    settings = UpdaterSettings.load(parent.get_settings())
    # 手动检查：跳过启动期防抖
    checker = UpdateChecker(settings, manual=True, parent=parent)

    def _on_done(result_obj: object):
        result: CheckResult = result_obj  # type: ignore[assignment]
        btn.setEnabled(True)
        btn.setText("检查更新")

        if not result.ok:
            dlg = UpdateCheckErrorDialog(
                result.error or "未知错误",
                attempts=[(a[0], a[1], a[2]) for a in result.attempts],
                parent=parent,
            )
            dlg.exec()
            return

        if not result.has_update or result.release is None:
            InfoBar.success(
                title="已是最新版本",
                content=f"当前版本 v{__version__} 已是最新",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=parent,
            )
            return

        _show_update_dialog(parent, result)

    checker.finished.connect(_on_done)
    checker.start()


def _show_update_dialog(parent: "SettingsInterface", result: CheckResult) -> None:
    """展示"有新版本"弹窗，并按用户选择联动 :mod:`installer`。"""
    release = result.release
    if release is None:
        return
    source_label = SOURCE_LABELS.get(result.primary_source, "")  # type: ignore[arg-type]
    dlg = UpdateAvailableDialog(
        release,
        local_version=__version__,
        primary_source_label=source_label,
        parent=parent,
    )
    accepted = dlg.exec()
    choice = dlg.user_choice

    if choice == "skip" or (accepted and choice == "skip"):
        cur = UpdaterSettings.load(parent.get_settings())
        cur.skipped_version = release.version
        cur.save(parent.get_settings())
        InfoBar.info(
            title="已跳过此版本",
            content=f"未来不再为 v{release.version} 提示。重新检测可重新启用。",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=parent,
        )
        return

    if not accepted or choice == "later":
        return

    # 用户点击立即更新 —— 启动 Updater.exe 并退出应用
    if not installer.is_updater_available():
        InfoBar.error(
            title="更新器未就绪",
            content="缺少 Updater.exe。请到 GitHub Release 手动下载最新版本。",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=parent,
        )
        return

    from .. import installer as _installer
    app_dir = _installer.find_app_dir()
    app_exe = _installer.find_app_exe_name()

    proxy = UpdaterSettings.load(parent.get_settings())
    _, _proxies = (None, None)
    # Updater 自己解析代理；我们传 proxy_url 字符串
    from ..proxy import resolve_proxy
    info, _ = resolve_proxy(proxy.proxy_mode, proxy.proxy_manual_url)
    proxy_url = info.url if info and info.is_valid else ""

    plan = _installer.LaunchPlan(
        app_dir=app_dir,
        app_exe_name=app_exe,
        target_version=release.version,
        target_tag=release.tag,
        asset_name=result.primary_asset_name,
        download_urls=list(result.download_candidates),
        proxy_url=proxy_url,
    )
    launch_result = _installer.launch_updater(plan)
    if not launch_result.launched:
        InfoBar.error(
            title="无法启动 Updater",
            content=launch_result.reason or "未知错误",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=parent,
        )
        return

    InfoBar.success(
        title="更新已启动",
        content="即将退出当前应用，由 Updater 完成替换并自动重启…",
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP,
        duration=3500,
        parent=parent,
    )

    # 延迟 1s 退出，让 InfoBar 来得及展示
    QTimer.singleShot(1000, _quit_app)


def _quit_app() -> None:
    """安全退出 Qt 应用。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.quit()
