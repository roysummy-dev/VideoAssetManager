import sys
import os
import json
import re
import subprocess

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QLineEdit,
    QPushButton,
    QLabel,
    QCheckBox,
    QScrollArea,
    QGroupBox,
    QMessageBox,
    QFileDialog,
    QStatusBar,
    QRubberBand,
)
from PySide6.QtCore import Qt, QTimer, QItemSelectionModel, QRect, QPoint, QSize
from PySide6.QtGui import QColor, QBrush, QCursor


class RubberBandTableWidget(QTableWidget):
    """支持橡皮筋框选的表格控件。

    在非复选框区域按住左键拖拽可框选行，释放后自动勾选被覆盖的行。
    按住 Ctrl 拖拽为追加模式，不会取消已有勾选。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rubber_band = None
        self._origin = None
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            if index.isValid() and index.column() == 0:
                self._origin = None
            else:
                self._origin = pos
                self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._origin is not None:
            pos = event.position().toPoint()
            if not self._dragging:
                delta = pos - self._origin
                if delta.manhattanLength() > 5:
                    self._dragging = True
                    if self._rubber_band is None:
                        self._rubber_band = QRubberBand(
                            QRubberBand.Rectangle, self.viewport()
                        )
                    self._rubber_band.setGeometry(QRect(self._origin, QSize(0, 0)))
                    self._rubber_band.show()

            if self._dragging and self._rubber_band:
                rect = QRect(self._origin, pos).normalized()
                self._rubber_band.setGeometry(rect)
                self._preview_selection(rect, event.modifiers())
                return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._rubber_band:
                rect = self._rubber_band.geometry()
                self._rubber_band.hide()
                self._apply_rubber_band_selection(rect, event.modifiers())
            self._origin = None
            return

        self._origin = None
        super().mouseReleaseEvent(event)

    def _row_visual_rect(self, row):
        first = self.visualRect(self.model().index(row, 0))
        last = self.visualRect(self.model().index(row, self.columnCount() - 1))
        return first.united(last)

    def _preview_selection(self, rect, modifiers):
        """拖拽过程中实时高亮被框选覆盖的行。"""
        ctrl_held = bool(modifiers & Qt.ControlModifier)
        sel_model = self.selectionModel()
        if not sel_model:
            return
        sel_model.clearSelection()
        for row in range(self.rowCount()):
            row_rect = self._row_visual_rect(row)
            if row_rect.intersects(rect):
                self.selectRow(row)
            elif ctrl_held:
                check_item = self.item(row, 0)
                if check_item and check_item.checkState() == Qt.Checked:
                    self.selectRow(row)

    def _apply_rubber_band_selection(self, rect, modifiers):
        """框选结束后，根据覆盖范围更新复选框状态并同步行高亮。"""
        ctrl_held = bool(modifiers & Qt.ControlModifier)

        self.blockSignals(True)
        for row in range(self.rowCount()):
            row_rect = self._row_visual_rect(row)
            check_item = self.item(row, 0)
            if check_item is None:
                continue
            if row_rect.intersects(rect):
                check_item.setCheckState(Qt.Checked)
            elif not ctrl_held:
                check_item.setCheckState(Qt.Unchecked)
        self.blockSignals(False)

        sel_model = self.selectionModel()
        if sel_model:
            sel_model.clearSelection()
            for row in range(self.rowCount()):
                check_item = self.item(row, 0)
                if check_item and check_item.checkState() == Qt.Checked:
                    self.selectRow(row)


class VideoAssetAssistant(QWidget):
    def __init__(self):
        super().__init__()

        # 使用 exe 同级目录作为数据目录
        self.base_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
        self.data_path = os.path.join(self.base_dir, "data.json")
        # Everything 文件列表导出路径（.efu）
        self.export_path = os.path.join(self.base_dir, "search_results.efu")

        # 尝试自动探测 Everything 可执行文件路径
        candidate1 = os.path.join(self.base_dir, "Everything.exe")
        candidate2 = r"C:\Program Files\Everything\Everything.exe"
        if os.path.exists(candidate1):
            self.everything_exe_path = candidate1
        elif os.path.exists(candidate2):
            self.everything_exe_path = candidate2
        else:
            # 默认指向常见安装路径，真正使用时再做存在性检查
            self.everything_exe_path = candidate2

        self.records = []  # 内存中的素材列表（dict）
        self.tag_checkboxes = {}  # tag -> QCheckBox
        self.filtered_record_indices = []  # 当前表格中每一行对应的 self.records 索引
        self.last_browse_dir = self.base_dir  # 记忆上一次浏览路径所在目录

        self.init_ui()
        self.load_data()
        self.refresh_tag_filters()
        self.refresh_table()

    # ---------------------- 数据读写 ----------------------
    def load_data(self):
        """启动时加载 JSON 数据，如果不存在则创建空数组文件。"""
        if not os.path.exists(self.data_path):
            # 创建空数组文件
            try:
                with open(self.data_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
            except OSError as e:
                QMessageBox.critical(self, "错误", f"无法创建 data.json：\n{e}")
                self.records = []
                return

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    self.records = []
                else:
                    data = json.loads(content)
                    if isinstance(data, list):
                        self.records = self._normalize_records(data)
                    else:
                        # 若格式不正确，重置为空数组
                        self.records = []
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "警告", f"读取 data.json 失败，已重置为空数组：\n{e}")
            self.records = []
            try:
                with open(self.data_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
            except OSError:
                pass

    def _normalize_records(self, data):
        """保证每条记录包含 path/tags/description 三个字段。"""
        normalized = []
        for item in data:
            if not isinstance(item, dict):
                continue
            path = self.clean_path(str(item.get("path", "")))
            tags = item.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tags = [str(t).strip() for t in tags if str(t).strip()]
            desc = str(item.get("description", ""))
            normalized.append(
                {
                    "path": path,
                    "tags": tags,
                    "description": desc,
                }
            )
        return normalized

    def clean_path(self, path: str) -> str:
        """清洗路径字符串，去除首尾空格和包裹引号。"""
        if not isinstance(path, str):
            path = str(path)
        cleaned = path.strip()
        # 去除可能存在的包裹引号
        if len(cleaned) >= 2 and (
            (cleaned[0] == '"' and cleaned[-1] == '"')
            or (cleaned[0] == "'" and cleaned[-1] == "'")
        ):
            cleaned = cleaned[1:-1].strip()
        return cleaned

    def save_data(self):
        """每次添加后立即覆盖写入 data.json。"""
        try:
            with open(self.data_path, "w", encoding="utf-8") as f:
                json.dump(self.records, f, ensure_ascii=False, indent=2)
        except OSError as e:
            QMessageBox.critical(self, "错误", f"保存 data.json 失败：\n{e}")

    # ---------------------- UI 初始化 ----------------------
    def init_ui(self):
        self.setWindowTitle("视频素材助手")
        self.resize(900, 600)

        main_layout = QVBoxLayout(self)

        # 顶部：标签筛选区（滚动区域内复选框）
        filter_group = QGroupBox("标签筛选（多选为交集）")
        filter_layout = QVBoxLayout(filter_group)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.filter_container = QWidget()
        self.filter_container_layout = QVBoxLayout(self.filter_container)
        self.filter_container_layout.addStretch(1)
        self.scroll_area.setWidget(self.filter_container)

        filter_layout.addWidget(self.scroll_area)
        main_layout.addWidget(filter_group, stretch=1)

        # 中间：表格
        self.table = RubberBandTableWidget()
        # 增加一列复选框列用于选择
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["选择", "路径", "标签", "描述"])
        self.table.setSortingEnabled(True)
        self.table.itemDoubleClicked.connect(self.on_table_item_double_clicked)
        self.table.itemChanged.connect(self.on_table_item_changed)
        self.table.horizontalHeader().setStretchLastSection(True)

        main_layout.addWidget(self.table, stretch=3)

        # 导出 / 删除 按钮区域
        export_layout = QHBoxLayout()
        export_layout.addStretch(1)
        self.delete_button = QPushButton("删除选中素材")
        self.delete_button.clicked.connect(self.delete_selected_records)
        self.export_button = QPushButton("导出到 Everything")
        self.export_button.clicked.connect(self.export_to_everything)
        export_layout.addWidget(self.delete_button)
        export_layout.addWidget(self.export_button)
        main_layout.addLayout(export_layout)

        # 底部：新增素材表单
        form_group = QGroupBox("新增素材")
        form_layout = QVBoxLayout(form_group)

        # 路径
        path_layout = QHBoxLayout()
        path_label = QLabel("路径：")
        self.path_edit = QLineEdit()
        self.browse_button = QPushButton("浏览...")
        self.browse_button.clicked.connect(self.browse_files)
        path_layout.addWidget(path_label)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.browse_button)
        form_layout.addLayout(path_layout)

        # 标签
        tags_layout = QHBoxLayout()
        tags_label = QLabel("标签（空格或逗号分隔）：")
        self.tags_edit = QLineEdit()
        tags_layout.addWidget(tags_label)
        tags_layout.addWidget(self.tags_edit)
        form_layout.addLayout(tags_layout)

        # 描述
        desc_layout = QHBoxLayout()
        desc_label = QLabel("描述：")
        self.desc_edit = QLineEdit()
        desc_layout.addWidget(desc_label)
        desc_layout.addWidget(self.desc_edit)
        form_layout.addLayout(desc_layout)

        # 提交按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.add_button = QPushButton("提交保存")
        self.add_button.clicked.connect(self.add_record)
        button_layout.addWidget(self.add_button)
        form_layout.addLayout(button_layout)

        main_layout.addWidget(form_group, stretch=0)

        # 状态栏，用于显示复制路径等持久化反馈信息
        self.status_bar = QStatusBar()
        main_layout.addWidget(self.status_bar)

    # ---------------------- 标签筛选区 ----------------------
    def refresh_tag_filters(self):
        """根据当前所有记录中的标签刷新复选框列表。"""
        # 记录当前已勾选标签，刷新后尽量保留
        previously_checked = {
            tag for tag, cb in self.tag_checkboxes.items() if cb.isChecked()
        }

        # 清空旧控件
        for i in reversed(range(self.filter_container_layout.count() - 1)):
            item = self.filter_container_layout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        self.tag_checkboxes.clear()

        # 统计所有标签
        all_tags = set()
        for rec in self.records:
            for t in rec.get("tags", []):
                if t:
                    all_tags.add(t)

        # 排序展示
        for tag in sorted(all_tags):
            cb = QCheckBox(tag)
            if tag in previously_checked:
                cb.setChecked(True)
            cb.stateChanged.connect(self.on_tag_filter_changed)
            self.filter_container_layout.insertWidget(
                self.filter_container_layout.count() - 1, cb
            )
            self.tag_checkboxes[tag] = cb

    def on_tag_filter_changed(self, state):
        _ = state
        self.refresh_table()

    def get_selected_tags(self):
        return [tag for tag, cb in self.tag_checkboxes.items() if cb.isChecked()]

    # ---------------------- 表格展示 ----------------------
    def get_filtered_records(self):
        """根据选中的标签返回筛选后的记录列表，并记录对应索引。"""
        selected_tags = self.get_selected_tags()
        self.filtered_record_indices = []
        filtered = []

        for idx, rec in enumerate(self.records):
            if not selected_tags:
                filtered.append(rec)
                self.filtered_record_indices.append(idx)
                continue
            tags = rec.get("tags", [])
            if all(t in tags for t in selected_tags):
                filtered.append(rec)
                self.filtered_record_indices.append(idx)

        return filtered

    def refresh_table(self):
        """根据当前筛选结果刷新表格内容。"""
        # 先根据标签计算筛选结果，并记录映射索引
        filtered = self.get_filtered_records()
        selected_tags = self.get_selected_tags()

        # 暂时关闭排序，避免刷新时跳动
        sorting_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)

        # 清空当前选择，避免旧的 selected 状态干扰
        self.table.clearSelection()

        # 在批量更新单元格时临时阻塞 itemChanged 信号，防止 on_table_item_changed 误触发
        self.table.blockSignals(True)

        self.table.setRowCount(len(filtered))
        for row, rec in enumerate(filtered):
            path = rec.get("path", "")
            tags = rec.get("tags", [])
            desc = rec.get("description", "")

            # 选择列（复选框）
            check_item = QTableWidgetItem()
            check_item.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            )
            # 如果当前有标签筛选，则默认勾选所有可见项；如果没有筛选，则默认不勾选
            if selected_tags:
                check_item.setCheckState(Qt.Checked)
            else:
                check_item.setCheckState(Qt.Unchecked)

            # 路径列
            path_item = QTableWidgetItem(path)
            path_item.setFlags(path_item.flags() ^ Qt.ItemIsEditable)

            # 如果文件不存在，用红色文字标记路径
            if path and not os.path.exists(path):
                path_item.setForeground(QBrush(QColor("red")))

            # 标签列，显示为 #标签1 #标签2
            tag_str = " ".join(f"#{t}" for t in tags)
            tags_item = QTableWidgetItem(tag_str)
            tags_item.setFlags(tags_item.flags() ^ Qt.ItemIsEditable)

            # 描述列
            desc_item = QTableWidgetItem(desc)
            desc_item.setFlags(desc_item.flags() ^ Qt.ItemIsEditable)

            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, path_item)
            self.table.setItem(row, 2, tags_item)
            self.table.setItem(row, 3, desc_item)

        # 恢复信号
        self.table.blockSignals(False)

        # 如果当前存在标签筛选，则将所有可见行标记为选中行（与复选框一致）
        if selected_tags:
            for row in range(self.table.rowCount()):
                self.table.selectRow(row)

        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(sorting_enabled)

    # ---------------------- 新增素材 ----------------------
    def add_record(self):
        path_text = self.path_edit.text().strip()
        tags_text = self.tags_edit.text().strip()
        desc_text = self.desc_edit.text().strip()

        if not path_text:
            QMessageBox.warning(self, "提示", "路径不能为空。")
            return

        # 允许用户输入相对路径，统一转为绝对路径
        path_cleaned = self.clean_path(path_text)
        path_abs = os.path.abspath(path_cleaned)

        # 标签用空格或逗号分隔
        raw_tags = re.split(r"[,\s]+", tags_text) if tags_text else []
        tags = [t.strip() for t in raw_tags if t.strip()]

        new_record = {
            "path": path_abs,
            "tags": tags,
            "description": desc_text,
        }

        self.records.append(new_record)
        self.save_data()
        self.refresh_tag_filters()
        self.refresh_table()

        # 清空输入框
        self.path_edit.clear()
        self.tags_edit.clear()
        self.desc_edit.clear()

        QMessageBox.information(self, "成功", "素材已添加并保存。")

    # ---------------------- 导出功能 ----------------------
    def export_to_everything(self):
        """生成标准 EFU 文件并调用 Everything 打开。"""
        # 优先使用复选框勾选的行；如果没有勾选，则退回到当前筛选结果的全部行
        checked_paths = []
        for row in range(self.table.rowCount()):
            check_item = self.table.item(row, 0)
            if check_item and check_item.checkState() == Qt.Checked:
                path_item = self.table.item(row, 1)
                if path_item:
                    p = self.clean_path(path_item.text())
                    if p:
                        checked_paths.append(p)

        if checked_paths:
            paths = checked_paths
        else:
            # 没有勾选时，使用当前筛选结果的全部记录
            filtered = self.get_filtered_records()
            paths = [self.clean_path(rec.get("path", "")) for rec in filtered if rec.get("path", "")]

        if not paths:
            QMessageBox.information(self, "提示", "当前筛选结果为空，无法导出。")
            return

        # 状态栏反馈
        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage("正在生成 EFU 文件...")

        # 检查 Everything 路径是否存在，如不存在则让用户手动选择
        exe_path = self.everything_exe_path
        if not os.path.exists(exe_path):
            QMessageBox.information(
                self,
                "选择 Everything 程序",
                "未在默认位置找到 Everything.exe，请手动选择 Everything 可执行文件。",
            )
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "选择 Everything.exe",
                self.base_dir,
                "Everything 可执行文件 (Everything.exe);;所有文件 (*.*)",
            )
            if not file_path:
                if hasattr(self, "status_bar") and self.status_bar is not None:
                    self.status_bar.clearMessage()
                QMessageBox.warning(self, "取消", "未选择 Everything.exe，导出已取消。")
                return
            self.everything_exe_path = file_path
            exe_path = file_path

        # 生成标准 EFU 文件（手动写入）
        efu_output = self.export_path
        try:
            # 使用 utf-8 编码写入 EFU 文件
            with open(efu_output, "w", encoding="utf-8") as f:
                # 写入 EFU 头
                f.write("Filename,Size,Date Modified,Date Created,Attributes\n")
                # 每行仅填充 Filename，其余字段留空但保留逗号
                for p in paths:
                    if not p:
                        continue
                    line = f"\"{p}\"\n"
                    f.write(line)
        except OSError as e:
            if hasattr(self, "status_bar") and self.status_bar is not None:
                self.status_bar.clearMessage()
            QMessageBox.critical(self, "错误", f"写入 EFU 文件失败：\n{e}")
            return

        # 成功后调用 Everything 打开生成的 EFU 文件
        try:
            subprocess.Popen([exe_path, efu_output])
        except Exception as e:
            if hasattr(self, "status_bar") and self.status_bar is not None:
                self.status_bar.clearMessage()
            QMessageBox.warning(self, "警告", f"EFU 已生成，但打开 Everything 失败：\n{e}")
            return

        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage("✅ EFU 列表已生成并打开")
            QTimer.singleShot(4000, self.status_bar.clearMessage)

        QMessageBox.information(
            self,
            "导出完成",
            f"Everything 已生成并打开 EFU 列表文件：\n{efu_output}",
        )

    # ---------------------- 交互：双击复制路径 ----------------------
    def on_table_item_double_clicked(self, item):
        """双击任意单元格，复制该行路径到剪贴板。"""
        row = item.row()
        # 路径列现在在第 1 列
        path_item = self.table.item(row, 1)
        if not path_item:
            return
        path_text = path_item.text()
        if not path_text:
            return

        # 清洗路径后再复制，避免多余引号等字符
        cleaned = self.clean_path(path_text)
        if not cleaned:
            return

        clipboard = QApplication.clipboard()
        clipboard.setText(cleaned)

        # 在状态栏中给出 3-5 秒的持久化反馈
        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage("✅ 路径已复制")
            QTimer.singleShot(4000, self.status_bar.clearMessage)

    # 当复选框勾选/取消勾选时，同步表格的行选择状态
    def on_table_item_changed(self, item):
        if not item:
            return
        # 只处理“选择”这一列的复选框
        if item.column() != 0:
            return
        if not (item.flags() & Qt.ItemIsUserCheckable):
            return

        row = item.row()
        if item.checkState() == Qt.Checked:
            # 勾选时，高亮整行
            self.table.selectRow(row)
        else:
            # 取消勾选时，取消该行的选择状态
            sel_model = self.table.selectionModel()
            if sel_model:
                index = self.table.model().index(row, 0)
                sel_model.select(
                    index,
                    QItemSelectionModel.Deselect | QItemSelectionModel.Rows,
                )

    # ---------------------- 关闭事件 ----------------------
    def closeEvent(self, event):
        # 可以在此扩展需要的清理操作
        event.accept()

    # ---------------------- 删除选中素材 ----------------------
    def delete_selected_records(self):
        """根据复选框状态删除表格中选中的素材项。"""
        rows = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                rows.append(row)

        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要删除的素材。")
            return

        # 确认提示
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(rows)} 条素材记录吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # 使用 filtered_record_indices 将表格行映射回 self.records 索引
        indices_to_delete = []
        for r in rows:
            if 0 <= r < len(self.filtered_record_indices):
                indices_to_delete.append(self.filtered_record_indices[r])

        # 去重并从大到小删除，避免索引位移
        indices_to_delete = sorted(set(indices_to_delete), reverse=True)
        for idx in indices_to_delete:
            if 0 <= idx < len(self.records):
                self.records.pop(idx)

        self.save_data()
        self.refresh_tag_filters()
        self.refresh_table()

        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage(f"已删除 {len(indices_to_delete)} 条素材记录。")
            QTimer.singleShot(4000, self.status_bar.clearMessage)

    # ---------------------- 文件浏览 ----------------------
    def browse_files(self):
        """通过文件对话框选择视频文件：
        - 单选：仅填充路径输入框，不写入列表
        - 多选：按当前标签和描述批量导入
        """
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            self.last_browse_dir,
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.flv *.wmv);;所有文件 (*.*)",
        )
        if not file_paths:
            return
        # 始终将第一个文件路径显示在输入框中，方便用户查看 / 手动添加
        first_path = os.path.abspath(self.clean_path(file_paths[0]))
        # 记住本次浏览的目录，下一次作为起始目录
        self.last_browse_dir = os.path.dirname(first_path) or self.last_browse_dir
        self.path_edit.setText(first_path)

        # 如果只选择了一个文件，不做任何写入操作，由用户点击“提交保存”决定是否添加
        if len(file_paths) == 1:
            return

        # 多选时才执行批量导入：当前表单中的标签与描述，将应用于所有文件
        tags_text = self.tags_edit.text().strip()
        desc_text = self.desc_edit.text().strip()

        raw_tags = re.split(r"[,\s]+", tags_text) if tags_text else []
        tags = [t.strip() for t in raw_tags if t.strip()]

        imported_count = 0
        for p in file_paths:
            path_abs = os.path.abspath(self.clean_path(p))
            if not path_abs:
                continue
            new_record = {
                "path": path_abs,
                "tags": tags,
                "description": desc_text,
            }
            self.records.append(new_record)
            imported_count += 1

        if imported_count > 0:
            self.save_data()
            self.refresh_tag_filters()
            self.refresh_table()

        # 在状态栏提示批量导入结果
        if hasattr(self, "status_bar") and self.status_bar is not None:
            self.status_bar.showMessage(f"已批量导入 {imported_count} 个文件到素材列表。")
            QTimer.singleShot(5000, self.status_bar.clearMessage)


def main():
    app = QApplication(sys.argv)
    win = VideoAssetAssistant()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()