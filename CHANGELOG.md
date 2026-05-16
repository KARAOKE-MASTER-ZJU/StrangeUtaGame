# Changelog

## [0.3.12] - 2026-05-17

### 新增功能
- 点击空白处会就近寻找字符
- 增加主程序更新更新器
- 更新器支持重试断点续传（获得此版本后）
- 右键菜单增强：1：在前方插入空格。2：在前方插入空行。

### 特性改变
- LUR音频缓存更改为5份
- 音频引擎向Windows申请的延时更换为100ms，避免低性能机器下卡顿
- 优化设置页面的UI
- 清理更换了一些github代理源

## [0.3.11] - 2026-05-16

### 特性改变
- 更新了哈希值的计算逻辑，继续尝试修复增量更新

### 修复项目
- 导出时默认演唱者标签到处异常
- 用户词典更新时系统环境语言异常导致报错
- 打轴完成度中空行不纳入统计
- 自动补偿Windows音频流创建导致的偏移


## [0.3.10] - 2026-05-16

### 新增功能
- 允许自动补轴点时单独选择ん/ン

### 特性改变
- 现在行编辑的时间戳和界面中的时间戳一样了
- 现在更新字典不会直接覆盖用户词典了
- 加载sug时会获取sug中的全局偏移

### 修复项目
- 修复了部分内置用户词典异常
- 修复了项目应用/创建新项目时字符未应用全局偏移


## [0.3.9] - 2026-05-16

### 特性改变
- 对ctrl+z/ctrl+y进行了重构，现在应该更符合预期了

### 修复项目
- 修复了老Updater对于全量更新不兼容


## [0.3.8] - 2026-05-16

### 新增功能
- *（待补充）*

### 特性改变
- *（待补充）*

### 修复项目
- 预设全局偏移为-390ms


## [0.3.7] - 2026-05-16

### 新增功能
- 无更新内容，纯测试自动更新用

### 特性改变
- *（待补充）*

### 修复项目
- 修复版本号显示


## [0.3.6] - 2026-05-16

### Added
- **什么都没改纯粹测试自动更新**

### Changed
- *（待补充）*

### Fixed
- *（待补充）*


> 本文件维护 StrangeUtaGame 的发布历史。
>
> **每发一版**：在文件顶部新增一段（推荐用 `scripts/release.py prepare X.Y.Z`
> 自动注入模板）；发布脚本会从这里抽取对应版本的段落作为 GitHub Release body，
> 该 body 会原样显示在用户的"有新版本"弹窗里。
>
> **写作格式**：遵循 [Keep a Changelog](https://keepachangelog.com/) 风格 +
> [SemVer](https://semver.org/lang/zh-CN/)。每段标题严格使用 `## [X.Y.Z] - YYYY-MM-DD`。



## [0.3.5] - 2026-05-16

### Added
- （在这里写未发布的新增内容…）

### Added
- **增量更新（分包式）**：发布产物在原有全量 `StrangeUtaGame-vX.Y.Z.zip` 之外
  额外生成 `-app.zip`（~5MB，含主 EXE + Updater.exe + 应用代码）、`-runtime.zip`
  （~178MB，含 PyQt6/numpy/pyav/sudachidict 等依赖）以及一份
  `manifest-vX.Y.Z.json` 描述各 part 的 sha256 与管辖目录。Updater 升级时优先
  拉取 manifest，比对本地 `_internal/.installed_manifest.json` 中的 part sha256，
  只下载哈希变化的 part 并精确替换。小版本升级（应用代码改动）通常只需要下载
  约 5MB，节省 ~95% 带宽。
  - **兼容策略**：找不到远端 manifest / 找不到本地 manifest / 任何 part 下载或
    应用失败 → 自动回退到现有的全量 zip 流程，行为与旧版完全一致。
  - **首次升级即可走增量**：构建阶段直接把出厂版本的
    `_internal/.installed_manifest.json` 打进全量 zip 一并发布，因此用户无论
    通过哪种方式拿到本版本（GitHub Web 直接下载解压、Updater 全量装、Updater
    增量装）都自带本地清单。下次升级 Updater 读到清单即可走增量路径，无需"必须
    先经历一次全量"。
  - **构建顺序**：part-zip 不包含 `.installed_manifest.json`（避免 sha256 循环
    依赖）；先打 part-zip 算 sha256，再用 sha256 写本地清单到 dist，最后打全量
    zip。全量 zip 因此天然带清单。
- **发布资产自动生成 `.zip.sha256` 校验文件**：`scripts/release.py build` 与
  GitHub Actions workflow 都会同时输出 `StrangeUtaGame-vX.Y.Z.zip.sha256`
  （sha256sum / coreutils 兼容格式），并随 Release 一并上传。Updater 拿到主
  zip 后会自动按 `<zip-url>.sha256` 拉取同源的校验文件做完整性校验；找不到
  就降级为"跳过校验"，与旧版本向后兼容。

### Changed
- 更新弹窗的 changelog 改为用 `QTextEdit.setMarkdown` 渲染 Markdown，
  支持 ###/列表/链接/`代码`/代码块等 GFM 主要语法。

### Fixed
- **`Updater.exe` 不会被重新打包 → 新功能（manifest/sha256/增量）永远不生效**：
  之前 `scripts/release.py` 的 `_ensure_updater_exe()` 只检查 `Updater.exe` 是否
  存在，不检查 `updater_app/main.py` 等源码是否更新。结果：第一次 release.py
  打出 Updater.exe 后，后续即便改了 Updater 代码、再跑 release.py，Updater.exe
  也**绝不**会被重打 —— 发布出去的 zip 里永远是历史上第一次打的旧 Updater。
  改为：比较 `updater_app/**/*.py` 的最大 mtime 与 `Updater.exe` 的 mtime，前者
  更新就强制重打。同时新增 `--rebuild-updater` CLI 选项做显式强制重打。
- **主程序在更新时不会真正退出 → Updater 备份 `_internal` 失败 (WinError 5)**：
  之前用 `QApplication.quit()` 退出，遇到脏项目数据、modal 弹窗、未结束的
  QThread 时不会真退出，导致 Updater 拿不到 `_internal` 写权限。改为新增
  `MainWindow.request_force_quit()` —— bypass "未保存"对话框，把脏数据兜底
  写到 `.cache` 临时文件，250ms 内 `os._exit(0)` 硬退出。
- **Updater 在 PID 消失后立刻动手 → 文件句柄未释放报 Access Denied**：
  Windows 内核释放 DLL/`_internal` 句柄是异步的，主进程 "退出" 后还会
  hold 文件锁一两秒。Updater 现在在 `wait_for_pid_exit` 后**再宽限 2 秒**，
  且对 `os.rename` / `shutil.copytree` 等关键操作做最多 6 次、每次 1.5s 的
  `PermissionError` 重试。
- **Updater.exe 启动后看不到任何输出**：之前用 `DETACHED_PROCESS` flag
  让 Updater 完全无控制台，所有 `print` / 报错都被吞掉。改为
  `CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP`，启动一个独立 cmd 窗口
  让用户看到下载进度与错误。
- **Updater.exe 报 `No module named 'colorsys'`**：PyInstaller 在
  `--exclude-module=qfluentwidgets/PyQt6` 的副作用下把一些标准库小模块也
  一并 exclude。给 `build_updater.py` 显式 `--hidden-import=colorsys` 以及
  `encodings.*`、`hashlib`、`zipfile`、`ssl` 等易漏标准库做兜底。
- **主程序 `StrangeUtaGame.exe` 也报 `No module named 'colorsys'`**：
  `backend/domain/entities.py` 直接 `import colorsys` 做颜色 HSV 变换，
  PyInstaller 这次静态分析没把它列入产物。给 `build.py` 显式声明
  `--hidden-import=colorsys` 兜底。
- **GitHub Actions 创建 Release 时 403 失败**：默认 `GITHUB_TOKEN` 只读，
  `softprops/action-gh-release` 无法创建 Release。
  解决：`.github/workflows/release.yml` 顶部声明 `permissions: contents: write`。
- **GitHub Actions Windows runner 的 UTF-8 编码问题**：runner 默认 stdout
  编码为 cp1252，会让发布脚本的中文 `print` 抛 `UnicodeEncodeError`。
  解决方案：`.github/workflows/release.yml` 顶部加 `PYTHONIOENCODING=utf-8` /
  `PYTHONUTF8=1`，并给 `build.py` / `build_updater.py` / `updater_app/main.py`
  / `scripts/release.py` 加 `_force_utf8_stdio()` 兜底。

## [0.3.4] - 2026-05-16

### Added
- * None *

### Changed
- * 更改sug数据结构，以后新保存的sug会记录全局偏移信息 *

### Fixed
- * None(用作自动更新测试用)*

## [0.3.3] - 2026-05-16

### Added
- **自动更新**：启动时检查 GitHub Release，发现新版本弹窗展示 changelog；用户
  确认后由独立 `Updater.exe` 替换 `StrangeUtaGame.exe` + `_internal/`，绝不
  触碰 `config.json` / `dictionary.json` / `singers.json` 等用户数据。
- **网络与代理设置**：支持「关闭 / 系统代理 / 自动检测 / 手动指定」四种模式；
  自动检测会扫描常用本地代理端口（含 Clash Verge 7897、V2RayN 10809 等 18 个端口）；
  提供「测试连通性」按钮直连 `api.github.com/zen` 验证。
- **更新源优先级**：默认 `GitHub Release → GHProxy → FastGit`，用户可在弹窗里
  拖动 / 上下移动调整；前一源失败时自动接力到下一源。
- **启动检查间隔**：默认 8 小时内不重复发起启动期检查（手动检查不受限）。
- **关于卡片**：新增「检查更新」按钮，版本号改为动态读取 `__version__`。

### Changed
- 版本号统一收敛到 `src/strange_uta_game/__version__.py`；设置-关于的版本文案
  不再硬编码。
- 内置 `config.json` 增加 `updater` 默认节点；首次启动若用户配置缺失会自动写入。

### Fixed
- *（首次发布该模块；暂无修复条目）*

### 0.3.2-0.3.3未发布内容
- 修复Karaoke渲染逐字走字不够精确的问题
- 修改默认导出偏移为0
- 修改普通轴点对齐策略为对齐字符左侧
- 修复播放时backspace蓝色光标没有跟随的问题
- 功能强化，现在应用演唱者可以直接双击应用
- 功能新增，现在可以给插入空格绑定快捷键了，默认为M
- 功能新增，autocheck，check规则可以取消长音符号的自动添加节奏点了。
- 修复导唱符时间校验，避免小于零的时间戳
- 功能新增，现在可以快速复制emoji参数到其他行
- 修复当前保存为预设会覆盖旧预设的问题。
- 修复插入导唱符对特殊emoji的解析异常，已过滤0xfe0e（变体选择符 VS15）
- 隐藏了行编辑界面，全文本编辑界面，在线查询界面
- 更新了可爱软件图标(GPT生成)
- 确保RubyTag中的停顿符删除
- 修复新建字符未应用全局偏移的问题
- 支持利用停顿符号，辅助英文词语整体掉落
- 新增开关拦截用户词典中的英文

## [0.3.2] - 之前的版本

- 详见 GitHub Release 历史
