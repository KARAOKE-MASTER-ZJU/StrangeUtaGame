# Changelog

> 本文件维护 StrangeUtaGame 的发布历史。
>
> **每发一版**：在文件顶部新增一段（推荐用 `scripts/release.py prepare X.Y.Z`
> 自动注入模板）；发布脚本会从这里抽取对应版本的段落作为 GitHub Release body，
> 该 body 会原样显示在用户的"有新版本"弹窗里。
>
> **写作格式**：遵循 [Keep a Changelog](https://keepachangelog.com/) 风格 +
> [SemVer](https://semver.org/lang/zh-CN/)。每段标题严格使用 `## [X.Y.Z] - YYYY-MM-DD`。

## [Unreleased]

### Added
- （在这里写未发布的新增内容…）

### Changed
- （在这里写未发布的改动内容…）

### Fixed
- （在这里写未发布的修复内容…）

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
