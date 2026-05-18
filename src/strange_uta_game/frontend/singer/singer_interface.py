"""演唱者管理界面。

管理演唱者的添加、删除、重命名、颜色设置、顺序调整等。

主要交互：
- 多选（Ctrl/Shift）→ 批量删除 / 启用 / 禁用 / 上移 / 下移 / 置顶 / 置底
- Qt 原生拖放调整顺序
- 顶部常驻搜索框过滤（过滤期间禁用拖放与顺序按钮，避免操作隐藏项）
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QColorDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QAbstractItemView,
    QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    ListWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    CaptionLabel,
)

from typing import Optional, List, Set

from strange_uta_game.backend.domain import Project, Singer
from strange_uta_game.backend.application import SingerService
from strange_uta_game.backend.domain.entities import _compute_complement_color


class SingerEditDialog(QDialog):
    """演唱者编辑对话框"""

    def __init__(self, singer: Singer = None, parent=None):
        super().__init__(parent)

        self._singer = singer
        self._color = singer.color if singer else "#FF6B6B"

        if singer:
            self.setWindowTitle("编辑演唱者")
        else:
            self.setWindowTitle("添加演唱者")

        self.resize(300, 200)

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QFormLayout(self)

        # 名称输入（留空将自动生成"未命名N"）
        self.line_name = LineEdit()
        if self._singer:
            self.line_name.setText(self._singer.name)
        else:
            self.line_name.setPlaceholderText("输入演唱者名称（留空自动编号）...")
        layout.addRow("显示名称:", self.line_name)

        # 颜色选择
        color_layout = QHBoxLayout()

        self.lbl_color_preview = QLabel("    ")
        self.lbl_color_preview.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid gray;"
        )
        self.lbl_color_preview.setFixedSize(40, 30)
        color_layout.addWidget(self.lbl_color_preview)

        btn_color = PushButton("选择颜色...", self)
        btn_color.clicked.connect(self._on_select_color)
        color_layout.addWidget(btn_color)

        color_layout.addStretch()

        layout.addRow("颜色:", color_layout)

        # 默认演唱者
        self.chk_default = QPushButton("设为默认演唱者")
        self.chk_default.setCheckable(True)
        if self._singer and self._singer.is_default:
            self.chk_default.setChecked(True)
        layout.addRow("", self.chk_default)

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addRow("", button_box)

    def _on_select_color(self):
        """选择颜色"""
        color = QColorDialog.getColor(QColor(self._color), self, "选择演唱者颜色")
        if color.isValid():
            self._color = color.name()
            self.lbl_color_preview.setStyleSheet(
                f"background-color: {self._color}; border: 1px solid gray;"
            )

    def _on_accept(self):
        """确认"""
        # 允许空名称，将由后端自动生成 "未命名N"
        self.accept()

    def get_data(self) -> dict:
        """获取编辑后的数据"""
        return {
            "name": self.line_name.text().strip(),
            "color": self._color,
            "is_default": self.chk_default.isChecked(),
        }


class TransferTargetDialog(QDialog):
    """批量删除时选择转移目标的对话框"""

    def __init__(
        self,
        candidates: List[Singer],
        default_id: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("选择转移目标")
        self.resize(360, 160)

        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("被删除演唱者的歌词将转移到以下演唱者：")
        )

        self.combo = QComboBox(self)
        for s in candidates:
            label = s.name + ("（默认）" if s.is_default else "")
            self.combo.addItem(label, s.id)
        if default_id:
            idx = self.combo.findData(default_id)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_target_id(self) -> Optional[str]:
        return self.combo.currentData()


class SingerPresetLoadDialog(QDialog):
    """从软件预设加载演唱者的多选对话框（用 CheckState 驱动选中，避免 MultiSelection UI 刷新 BUG）"""

    def __init__(self, presets: list, existing_names: set, app_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从软件预设加载演唱者")
        self.resize(400, 450)

        self._presets = presets
        self._existing_names = existing_names
        self._app_settings = app_settings

        self._init_ui()
        self._populate_list()

        self.list_widget.itemChanged.connect(self._update_stats)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 搜索过滤框
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤:"))
        self.line_filter = LineEdit()
        self.line_filter.setPlaceholderText("输入名称搜索...")
        self.line_filter.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.line_filter)
        layout.addLayout(filter_layout)

        # 演唱者列表（NoSelection，选中状态完全由 CheckState 驱动）
        self.list_widget = ListWidget()
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        layout.addWidget(self.list_widget)

        # 全选/全不选按钮
        select_layout = QHBoxLayout()
        btn_select_all = PushButton("全选", self)
        btn_select_all.clicked.connect(self._on_select_all)
        select_layout.addWidget(btn_select_all)

        btn_deselect_all = PushButton("全不选", self)
        btn_deselect_all.clicked.connect(self._on_deselect_all)
        select_layout.addWidget(btn_deselect_all)

        select_layout.addStretch()

        # 统计标签
        self.lbl_stats = CaptionLabel("")
        select_layout.addWidget(self.lbl_stats)
        layout.addLayout(select_layout)

        # 按钮行
        button_layout = QHBoxLayout()
        
        # 删除选中演唱者按钮
        self.btn_delete_selected = PushButton("删除选中演唱者", self)
        self.btn_delete_selected.setIcon(FIF.DELETE)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        button_layout.addWidget(self.btn_delete_selected)
        
        button_layout.addStretch()
        
        # 加载选中按钮
        btn_load = PushButton("加载选中", self)
        btn_load.setIcon(FIF.DOWNLOAD)
        btn_load.clicked.connect(self._on_accept)
        button_layout.addWidget(btn_load)
        
        # 取消按钮
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(btn_cancel)
        
        layout.addLayout(button_layout)

    def _populate_list(self):
        """填充列表"""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for preset in self._presets:
            name = preset.get("name", "")
            if not name:
                continue

            is_existing = name in self._existing_names
            color = preset.get("color", "#FF6B6B")

            item = QListWidgetItem()
            item.setText(f"{name}  (已存在)" if is_existing else name)

            # 演唱者颜色作为背景
            bg_color = QColor(color)
            bg_color.setAlpha(80)
            item.setBackground(bg_color)

            if is_existing:
                item.setForeground(QColor("gray"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)

            item.setData(Qt.ItemDataRole.UserRole, preset)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_existing)

            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)
        self._update_stats()

    def _on_filter_changed(self, text: str):
        """过滤列表，只控制显示/隐藏，不影响勾选状态"""
        filter_text = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            preset = item.data(Qt.ItemDataRole.UserRole)
            name = preset.get("name", "").lower()
            item.setHidden(filter_text != "" and filter_text not in name)
        self._update_stats()

    def _on_select_all(self):
        """全选可见且未存在的项"""
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden() and not item.data(Qt.ItemDataRole.UserRole + 1):
                item.setCheckState(Qt.CheckState.Checked)
        self.list_widget.blockSignals(False)
        self._update_stats()

    def _on_deselect_all(self):
        """取消勾选所有可见项"""
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden() and not item.data(Qt.ItemDataRole.UserRole + 1):
                item.setCheckState(Qt.CheckState.Unchecked)
        self.list_widget.blockSignals(False)
        self._update_stats()

    def _update_stats(self):
        """更新统计信息（总勾选数/总数）"""
        checked = 0
        total = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            total += 1
            if item.checkState() == Qt.CheckState.Checked:
                checked += 1
        self.lbl_stats.setText(f"已选 {checked}/{total}")

    def _on_delete_selected(self):
        """删除选中的演唱者预设"""
        # 获取选中的预设
        selected_presets = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                preset = item.data(Qt.ItemDataRole.UserRole)
                if preset:
                    selected_presets.append(preset)
        
        if not selected_presets:
            InfoBar.warning(title="未选择", content="请至少选择一位演唱者",
                            parent=self, duration=2000)
            return
        
        # 二次确认
        names = "、".join(p.get("name", "") for p in selected_presets[:5])
        if len(selected_presets) > 5:
            names += f" 等 {len(selected_presets)} 位"
        
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要从预设中删除以下演唱者吗？\n\n{names}\n\n删除后将无法恢复。")
        btn_yes = msg.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        
        if msg.clickedButton() is not btn_yes:
            return
        
        # 从预设中删除选中的演唱者
        if not self._app_settings:
            InfoBar.error(title="错误", content="无法访问设置",
                          parent=self, duration=3000)
            return
        
        try:
            # 获取当前预设
            current_presets = self._app_settings.load_singer_presets()
            
            # 构建要删除的名称集合
            names_to_delete = {p.get("name", "") for p in selected_presets}
            
            # 过滤掉要删除的预设
            updated_presets = [p for p in current_presets if p.get("name", "") not in names_to_delete]
            
            # 保存更新后的预设
            self._app_settings.save_singer_presets(updated_presets)
            
            # 更新本地预设列表
            self._presets = updated_presets
            
            # 重新填充列表
            self._populate_list()
            
            InfoBar.success(title="删除成功", content=f"已删除 {len(selected_presets)} 位演唱者预设",
                            parent=self, duration=2000)
        except Exception as e:
            InfoBar.error(title="删除失败", content=f"保存预设时出错: {e}",
                          parent=self, duration=3000)

    def _on_accept(self):
        """确认选择"""
        has_checked = any(
            self.list_widget.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.list_widget.count())
        )
        if not has_checked:
            InfoBar.warning(title="未选择", content="请至少选择一位演唱者",
                            parent=self, duration=2000)
            return
        self.accept()

    def get_selected_presets(self) -> list:
        """获取勾选的预设列表"""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                preset = item.data(Qt.ItemDataRole.UserRole)
                if preset:
                    result.append(preset)
        return result


class SingerManagerInterface(QWidget):
    """演唱者管理界面"""

    singers_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._singer_service: Optional[SingerService] = None
        # 防止 reorder 引发的 model 信号回环触发自身
        self._suppress_reorder_signal = False
        # 当前搜索关键词（用于禁用顺序按钮）
        self._filter_text: str = ""
        # 持久选中状态：跨搜索/刷新保留，以 singer.id 为键
        self._selected_ids: Set[str] = set()

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 标题
        title = QLabel("演唱者管理")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = CaptionLabel(
            "管理演唱者：双击编辑；Ctrl/Shift 多选可批量操作；拖动可调整顺序。"
        )
        layout.addWidget(desc)

        # 搜索框（常驻）
        search_row = QHBoxLayout()
        self.line_search = LineEdit()
        self.line_search.setPlaceholderText("搜索演唱者名称...")
        self.line_search.setClearButtonEnabled(True)
        self.line_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.line_search)
        layout.addLayout(search_row)

        # 演唱者列表
        self.list_singers = ListWidget()
        self.list_singers.setMinimumHeight(260)
        self.list_singers.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list_singers.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.list_singers.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_singers.itemDoubleClicked.connect(self._on_edit_singer)
        self.list_singers.itemSelectionChanged.connect(self._on_selection_changed)
        # 拖放完成后，rowsMoved 由内部模型发出
        self.list_singers.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.list_singers, stretch=1)

        # ── 第一排按钮：常规操作 ──
        row1 = QHBoxLayout()

        self.btn_add = PrimaryPushButton("添加", self)
        self.btn_add.setIcon(FIF.ADD)
        self.btn_add.clicked.connect(self._on_add_singer)
        row1.addWidget(self.btn_add)

        self.btn_edit = PushButton("编辑", self)
        self.btn_edit.setIcon(FIF.EDIT)
        self.btn_edit.clicked.connect(self._on_edit_singer)
        self.btn_edit.setEnabled(False)
        row1.addWidget(self.btn_edit)

        self.btn_delete = PushButton("删除", self)
        self.btn_delete.setIcon(FIF.DELETE)
        self.btn_delete.clicked.connect(self._on_delete_singers)
        self.btn_delete.setEnabled(False)
        row1.addWidget(self.btn_delete)

        # 分隔
        row1.addSpacing(10)

        self.btn_enable = PushButton("启用", self)
        self.btn_enable.setIcon(FIF.ACCEPT)
        self.btn_enable.clicked.connect(lambda: self._on_set_enabled(True))
        self.btn_enable.setEnabled(False)
        row1.addWidget(self.btn_enable)

        self.btn_disable = PushButton("禁用", self)
        self.btn_disable.setIcon(FIF.CLOSE)
        self.btn_disable.clicked.connect(lambda: self._on_set_enabled(False))
        self.btn_disable.setEnabled(False)
        row1.addWidget(self.btn_disable)

        row1.addStretch()
        layout.addLayout(row1)

        # ── 第二排按钮：顺序调整 ──
        row2 = QHBoxLayout()

        self.btn_top = PushButton("置顶", self)
        self.btn_top.setIcon(FIF.UP)
        self.btn_top.clicked.connect(lambda: self._on_move("top"))
        self.btn_top.setEnabled(False)
        row2.addWidget(self.btn_top)

        self.btn_up = PushButton("上移", self)
        self.btn_up.setIcon(FIF.UP)
        self.btn_up.clicked.connect(lambda: self._on_move("up"))
        self.btn_up.setEnabled(False)
        row2.addWidget(self.btn_up)

        self.btn_down = PushButton("下移", self)
        self.btn_down.setIcon(FIF.DOWN)
        self.btn_down.clicked.connect(lambda: self._on_move("down"))
        self.btn_down.setEnabled(False)
        row2.addWidget(self.btn_down)

        self.btn_bottom = PushButton("置底", self)
        self.btn_bottom.setIcon(FIF.DOWN)
        self.btn_bottom.clicked.connect(lambda: self._on_move("bottom"))
        self.btn_bottom.setEnabled(False)
        row2.addWidget(self.btn_bottom)

        row2.addSpacing(20)

        # 预设按钮挪到这一排尾部
        self.btn_save_preset = PushButton("保存为软件预设", self)
        self.btn_save_preset.setIcon(FIF.SAVE)
        self.btn_save_preset.setToolTip(
            "将当前演唱者列表保存到软件设置，每次启动自动加载"
        )
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        row2.addWidget(self.btn_save_preset)

        self.btn_load_preset = PushButton("从软件预设加载", self)
        self.btn_load_preset.setIcon(FIF.DOWNLOAD)
        self.btn_load_preset.setToolTip("从软件设置中加载已保存的演唱者预设到当前项目")
        self.btn_load_preset.clicked.connect(self._on_load_preset)
        row2.addWidget(self.btn_load_preset)

        row2.addStretch()
        layout.addLayout(row2)

        # 统计信息
        self.lbl_stats = CaptionLabel("共 0 位演唱者")
        layout.addWidget(self.lbl_stats)

    # ==================== 数据接入 ====================

    def set_project(self, project: Project):
        """设置项目"""
        self._project = project
        self._singer_service = SingerService(project)
        self._selected_ids.clear()
        self._refresh_list()

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            project = self._store.project
            if project:
                self._project = project
                self._singer_service = SingerService(project)
            else:
                self._project = None
                self._singer_service = None
            self._selected_ids.clear()
            self._refresh_list()
        elif change_type == "singers":
            self._refresh_list()

    # ==================== 列表刷新 ====================

    def _refresh_list(self):
        """刷新演唱者列表（保持当前选中、滚动位置不变）"""
        # 抑制 rowsMoved 信号（清空/填充时不触发 reorder 回调）
        self._suppress_reorder_signal = True
        try:
            self.list_singers.clear()

            if not self._project:
                self.lbl_stats.setText("未加载项目")
                self._update_button_state()
                return

            filter_lower = self._filter_text.strip().lower()

            for singer in self._project.singers:
                # 过滤
                if filter_lower and filter_lower not in singer.name.lower():
                    continue

                item = QListWidgetItem()

                # 显示格式: [后台编号] 名称 [默认] (已禁用)
                display_text = singer.name
                if singer.is_default:
                    display_text += " [默认]"
                if not singer.enabled:
                    display_text += " (已禁用)"
                if singer.backend_number > 0:
                    display_text = f"[{singer.backend_number}] {display_text}"

                item.setText(display_text)

                # 存储演唱者 ID
                item.setData(Qt.ItemDataRole.UserRole, singer.id)

                # 颜色背景：选中时用补色，未选中时用演唱者本色
                is_selected = singer.id in self._selected_ids
                if is_selected:
                    bg_hex = _compute_complement_color(singer.color)
                else:
                    bg_hex = singer.color
                color = QColor(bg_hex)
                item.setBackground(color)
                luminance = (
                    0.299 * color.red()
                    + 0.587 * color.green()
                    + 0.114 * color.blue()
                ) / 255
                item.setForeground(
                    QColor("black") if luminance > 0.5 else QColor("white")
                )

                # 拖放控制：搜索过滤时禁止拖动（避免操作隐藏项造成混乱）
                flags = item.flags()
                if filter_lower:
                    flags = flags & ~Qt.ItemFlag.ItemIsDragEnabled
                else:
                    flags = flags | Qt.ItemFlag.ItemIsDragEnabled
                # 列表本身禁止子项作为放置目标（拖到项之间而非项上）
                flags = flags & ~Qt.ItemFlag.ItemIsDropEnabled
                item.setFlags(flags)

                # 还原选中（使用持久的 _selected_ids，跨搜索/刷新保留）
                if is_selected:
                    item.setSelected(True)

                self.list_singers.addItem(item)

            # 更新统计
            total = len(self._project.singers)
            enabled = sum(1 for s in self._project.singers if s.enabled)
            stats_text = f"共 {total} 位演唱者（{enabled} 位启用）"
            selected_count = len(self.list_singers.selectedItems())
            if selected_count > 0:
                stats_text += f" — 已选中 {selected_count} 位"
            if filter_lower:
                stats_text += f"  [搜索中：{self.list_singers.count()} 项可见]"
            self.lbl_stats.setText(stats_text)
        finally:
            self._suppress_reorder_signal = False

        self._update_button_state()

    # ==================== 选中与按钮状态 ====================

    def _get_selected_singer_ids(self) -> List[str]:
        """获取当前选中的演唱者 ID 列表。

        以后端 project.singers 的排列顺序为基准，返回 _selected_ids 中存在的 ID。
        这样即使在搜索状态下，不可见的选中项也会被包含，且顺序稳定。
        """
        if not self._project:
            return []
        return [s.id for s in self._project.singers if s.id in self._selected_ids]

    def _on_selection_changed(self):
        """选中变化：同步到持久选中集合，刷新按钮状态和统计。

        只对当前可见项做差量更新：
        - 可见且选中 → 加入 _selected_ids
        - 可见但未选中 → 从 _selected_ids 移除
        - 不可见（被搜索过滤掉）→ _selected_ids 保持原值不动
        """
        for i in range(self.list_singers.count()):
            item = self.list_singers.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            if not sid:
                continue
            if item.isSelected():
                self._selected_ids.add(sid)
            else:
                self._selected_ids.discard(sid)

        # 更新 item 背景色（选中态变化时需要重绘补色）
        self._suppress_reorder_signal = True
        try:
            for i in range(self.list_singers.count()):
                item = self.list_singers.item(i)
                sid = item.data(Qt.ItemDataRole.UserRole)
                if not sid:
                    continue
                singer = self._project.get_singer(sid) if self._project else None
                if not singer:
                    continue
                is_sel = sid in self._selected_ids
                bg_hex = _compute_complement_color(singer.color) if is_sel else singer.color
                color = QColor(bg_hex)
                item.setBackground(color)
                lum = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
                item.setForeground(QColor("black") if lum > 0.5 else QColor("white"))
        finally:
            self._suppress_reorder_signal = False

        self._update_button_state()
        # 仅更新统计文字尾部"已选中 K 位"，避免完全重建列表
        if self._project:
            total = len(self._project.singers)
            enabled = sum(1 for s in self._project.singers if s.enabled)
            stats_text = f"共 {total} 位演唱者（{enabled} 位启用）"
            selected_count = len(self._selected_ids)
            if selected_count > 0:
                stats_text += f" — 已选中 {selected_count} 位"
            if self._filter_text.strip():
                stats_text += (
                    f"  [搜索中：{self.list_singers.count()} 项可见]"
                )
            self.lbl_stats.setText(stats_text)

    def _update_button_state(self):
        """根据当前选中数量、过滤状态、项目状态更新按钮可用性"""
        has_project = self._project is not None
        selected_ids = self._get_selected_singer_ids()
        n_selected = len(selected_ids)
        is_filtering = bool(self._filter_text.strip())

        # 编辑：仅单选可用
        self.btn_edit.setEnabled(has_project and n_selected == 1)

        # 删除：至少 1 项，且不会清空所有演唱者
        total = len(self._project.singers) if self._project else 0
        self.btn_delete.setEnabled(
            has_project and n_selected >= 1 and (total - n_selected) >= 1
        )

        # 启用/禁用
        self.btn_enable.setEnabled(has_project and n_selected >= 1)
        self.btn_disable.setEnabled(has_project and n_selected >= 1)

        # 顺序：搜索过滤时禁用（看不到完整列表，操作会困惑）
        order_ok = has_project and n_selected >= 1 and not is_filtering
        self.btn_top.setEnabled(order_ok)
        self.btn_up.setEnabled(order_ok)
        self.btn_down.setEnabled(order_ok)
        self.btn_bottom.setEnabled(order_ok)

    # ==================== 搜索 ====================

    def _on_search_changed(self, text: str):
        self._filter_text = text or ""
        self._refresh_list()

    # ==================== 拖放重排 ====================

    def _on_rows_moved(self, *args, **kwargs):
        """Qt 内部拖放完成后触发：读取当前列表顺序并提交后端"""
        if self._suppress_reorder_signal:
            return
        if not self._project or not self._singer_service:
            return
        # 搜索过滤期间不允许拖放（项的 DragEnabled 已被禁，但稳妥起见再判一次）
        if self._filter_text.strip():
            return

        ordered_ids: List[str] = []
        for i in range(self.list_singers.count()):
            item = self.list_singers.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid:
                ordered_ids.append(sid)

        if len(ordered_ids) != len(self._project.singers):
            # 不一致：回滚刷新
            self._refresh_list()
            return

        ok = self._singer_service.reorder_singers(ordered_ids)
        if not ok:
            self._refresh_list()
            return

        self._notify_singers_changed()

    # ==================== 添加 / 编辑 ====================

    def _on_add_singer(self):
        if not self._project:
            self._warn("未加载项目", "请先打开或创建一个项目")
            return

        dialog = SingerEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        try:
            singer_name = data["name"] if data["name"] else None
            singer = self._singer_service.add_singer(
                name=singer_name, color=data["color"]
            )
            if data["is_default"]:
                self._singer_service.set_default_singer(singer.id)

            self._notify_singers_changed()
            self._info("添加成功", f"已添加演唱者: {singer.name}")
        except Exception as e:
            self._error("添加失败", str(e))

    def _on_edit_singer(self):
        selected_ids = self._get_selected_singer_ids()
        if len(selected_ids) != 1:
            return
        singer = self._project.get_singer(selected_ids[0])
        if not singer:
            return

        dialog = SingerEditDialog(singer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        try:
            if data["name"] and data["name"] != singer.name:
                self._singer_service.rename_singer(singer.id, data["name"])
            if data["color"] != singer.color:
                self._singer_service.change_singer_color(singer.id, data["color"])
            if data["is_default"] and not singer.is_default:
                self._singer_service.set_default_singer(singer.id)

            self._notify_singers_changed()
            self._info("修改成功", f"已更新演唱者: {singer.name}")
        except Exception as e:
            self._error("修改失败", str(e))

    # ==================== 批量删除 ====================

    def _on_delete_singers(self):
        if not self._project:
            return

        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return

        total = len(self._project.singers)
        if total - len(selected_ids) < 1:
            self._warn("无法删除", "必须至少保留一个演唱者")
            return

        selected_singers = [self._project.get_singer(sid) for sid in selected_ids]
        selected_singers = [s for s in selected_singers if s is not None]

        # 候选转移目标：不在被删除集合中的演唱者
        candidates = [
            s for s in self._project.singers if s.id not in set(selected_ids)
        ]
        if not candidates:
            self._warn("无法删除", "没有可用的转移目标")
            return

        # 弹窗选择转移目标
        default_singer = self._project.get_default_singer()
        default_target_id = (
            default_singer.id
            if default_singer and default_singer.id not in set(selected_ids)
            else candidates[0].id
        )
        dlg = TransferTargetDialog(candidates, default_target_id, self)

        # 简要确认信息
        names = "、".join(s.name for s in selected_singers[:5])
        if len(selected_singers) > 5:
            names += f" 等 {len(selected_singers)} 位"
        msg = QMessageBox(self)
        msg.setWindowTitle("确认批量删除")
        msg.setText(
            f"确定要删除 {len(selected_singers)} 位演唱者吗？\n\n{names}\n\n"
            "这些演唱者的歌词将转移到你下一步选择的演唱者。"
        )
        btn_yes = msg.addButton("继续", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        if msg.clickedButton() is not btn_yes:
            return

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        transfer_to = dlg.get_target_id()
        if not transfer_to:
            return

        ok = self._singer_service.batch_remove_singers(selected_ids, transfer_to)
        if not ok:
            self._error("删除失败", "请检查转移目标是否有效")
            return

        self._notify_singers_changed()
        self._info("删除成功", f"已删除 {len(selected_singers)} 位演唱者")

    # ==================== 批量启用/禁用 ====================

    def _on_set_enabled(self, enabled: bool):
        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return
        ok = self._singer_service.batch_set_enabled(selected_ids, enabled)
        if not ok:
            self._error(
                "操作失败", "部分演唱者状态未能更新" if not enabled else "部分演唱者未能启用"
            )
            return
        self._notify_singers_changed()
        self._info(
            "完成",
            f"已{'启用' if enabled else '禁用'} {len(selected_ids)} 位演唱者",
        )

    # ==================== 顺序调整（按钮） ====================

    def _on_move(self, direction: str):
        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return
        # 顺序操作期间用 backend 提供的"保持相对间隔"语义
        ok = self._singer_service.move_singers(selected_ids, direction)
        if not ok:
            return
        self._notify_singers_changed()

    # ==================== 演唱者预设 ====================

    def _on_save_preset(self):
        """将当前项目的演唱者保存到软件全局设置
        
        比较现有预设：同名则覆盖，不同名则新增。
        预设中有但本次项目未使用的保留。新更新的预设放在顶部。
        """
        if not self._project or not self._project.singers:
            self._warn("无法保存", "当前没有演唱者可保存")
            return

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        existing_presets = app_settings.load_singer_presets()

        # 构建当前项目演唱者名称集合
        current_names = {s.name for s in self._project.singers}

        # 保留本次项目未使用的预设（名称不在当前项目中的）
        kept_presets = [
            p for p in existing_presets
            if p.get("name", "") not in current_names
        ]

        # 当前项目演唱者转预设（放在顶部）
        new_presets = []
        for s in self._project.singers:
            new_presets.append(
                {
                    "name": s.name,
                    "color": s.color,
                    "is_default": s.is_default,
                    "backend_number": s.backend_number,
                }
            )

        # 合并：新预设在前，保留的旧预设在后
        merged = new_presets + kept_presets
        app_settings.save_singer_presets(merged)

        self._info(
            "保存成功", f"已保存 {len(new_presets)} 位演唱者预设到软件设置"
        )

    def _on_load_preset(self):
        """从软件全局设置加载演唱者预设到当前项目（弹窗多选）"""
        if not self._project or not self._singer_service:
            self._warn("未加载项目", "请先打开或创建一个项目")
            return

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        presets = app_settings.load_singer_presets()

        if not presets:
            self._warn("无预设", "软件中没有保存的演唱者预设，请先保存")
            return

        existing_names = {s.name for s in self._project.singers}

        # 弹出多选对话框
        dialog = SingerPresetLoadDialog(presets, existing_names, app_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_presets = dialog.get_selected_presets()
        if not selected_presets:
            return

        added = 0
        for preset in selected_presets:
            name = preset.get("name", "")
            if not name or name in existing_names:
                continue
            try:
                singer = self._singer_service.add_singer(
                    name=name, color=preset.get("color", "#FF6B6B")
                )
                if preset.get("is_default", False):
                    self._singer_service.set_default_singer(singer.id)
                added += 1
                existing_names.add(name)
            except Exception:
                pass

        self._notify_singers_changed()

        if added > 0:
            self._info("加载成功", f"已从预设加载 {added} 位新演唱者")

    # ==================== 工具方法 ====================

    def _notify_singers_changed(self):
        """统一通知：刷新本地列表 + 通知 ProjectStore"""
        self._refresh_list()
        if hasattr(self, "_store"):
            self._store.notify("singers")
        self.singers_changed.emit()

    def _info(self, title: str, content: str, duration: int = 2000):
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )

    def _warn(self, title: str, content: str, duration: int = 3000):
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )

    def _error(self, title: str, content: str, duration: int = 5000):
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )
