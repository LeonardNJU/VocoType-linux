# macOS安装、升级与排障

VoCoType-linux V5提供原生 Apple Silicon版本。它由两个协作组件组成：

- `/Applications/VoCoType-linux.app`：用户可见的 AppKit设置中心；
- `~/Library/Input Methods/VoCoType-linux.app`：首次启动时自动安装的 InputMethodKit Palette Input Method。

输入法与当前键盘输入源共存，不要求把原来的英文、拼音或 Rime替换掉。

## 系统要求

- Apple Silicon（arm64）；
- macOS 13或更高版本；
- 允许麦克风权限；
- 首次下载模型需要网络，日常普通听写可离线运行。

当前没有 Intel / Universal DMG。

## 安装

1. 在 [GitHub Releases](https://github.com/LeonardNJU/VocoType-linux/releases) 下载 `VoCoType-linux-5.0.0b5-macOS-arm64.dmg`；
2. 双击 DMG；
3. 将 `VoCoType-linux.app`拖到右侧 `Applications`；
4. 从“应用程序”打开 VoCoType-linux；
5. App自动安装并注册内置输入法组件；
6. 根据系统提示允许麦克风权限；
7. 在任意文本框按住 `F9`说话，松开后提交文字。

首次启动会创建：

```text
~/Library/Application Support/io.github.LeonardNJU.VoCoTypeLinux.InputMethod/
  config.json
  macos.json
  terms.yaml
```

升级和修复不会覆盖已有配置、词典或 ModelScope模型缓存。

## Gatekeeper与ad-hoc签名

V5 Beta 5采用 ad-hoc签名，尚未经过 Apple notarization。ad-hoc只有代码完整性签名，没有一张可导入钥匙串的开发者证书。

首次打开若被阻止：

1. 先尝试打开一次 VoCoType-linux，并关闭拦截提示；
2. 打开 **系统设置 → 隐私与安全性**；
3. 在“安全性”区域找到 VoCoType-linux；
4. 点击 **仍要打开**；
5. 在确认框中再次点击 **仍要打开**。

系统会为这个具体 App建立本机例外。后续 Beta更新可能再次要求确认麦克风或安全例外。

## 快捷键与状态浮层

| 快捷键 | 功能 |
|---|---|
| `F9` | 普通离线听写 |
| `Shift+F9` | 识别后进行 AI润色 |
| `Ctrl+F9` | 语音编辑 |
| `Esc` | 取消当前操作 |
| 点击状态浮层 | 立即取消并关闭浮层；即使任务已经结束也会清理残留 |

“录音过短”等警告会在约 2秒后自动消失；成功提示约 2.5秒，错误提示约 5秒。实时预览与处理中状态不会自动消失。

一次操作会固定绑定 F9按下时的 InputMethodKit控制器和文本客户端。系统通知或其他应用中途激活不会抢走 key-up或最终提交目标；全局热键更新会延迟到按键周期结束后执行。

## 用户词典

打开 **用户词典**页面：

- **新增热词**：填写 canonical，可添加多条 aliases，并分别勾选“作为 ASR热词”和“禁止 ITN改写”；
- **新增保护词**：只填写一个需要保护的短语；
- **导入用户词典**：选择 YAML，验证成功后原子替换；
- **热更新词典**：在 Finder或外部编辑器批量修改当前文件后手动重新加载；
- **在 Finder中显示**：定位权威 `terms.yaml`。

图形化新增不会整体重新序列化 YAML，因此会尽量保留注释和手工排版。

## AI配置

在 **AI功能**页面配置 OpenAI-compatible endpoint、model和 API key。普通 `F9`不需要 AI；`Shift+F9`和 `Ctrl+F9`使用该配置。配置存于当前用户 Application Support目录，权限限制为当前用户可读写。

## 升级

1. 下载新 DMG；
2. 用新 `VoCoType-linux.app`覆盖 `/Applications`中的旧版本；
3. 启动 App；
4. App比较内置输入法版本，并自动替换 `~/Library/Input Methods/VoCoType-linux.app`；
5. 已有配置、词典和模型保持不变。

不要只手工替换 `~/Library/Input Methods`中的组件；让外层 App统一管理版本和注册状态。

## 卸载

运行 DMG中的 `卸载 VoCoType-linux.command`。默认删除外层 App、输入法组件并注销输入源，但保留用户配置与模型，便于重装。

需要完全清除时，再手动删除：

```text
~/Library/Application Support/io.github.LeonardNJU.VoCoTypeLinux.InputMethod
~/Library/Caches/io.github.LeonardNJU.VoCoTypeLinux.InputMethod
```

删除前建议备份 `config.json`和 `terms.yaml`。

## 常见问题

### 第一次F9松开后不提交，浮层一直存在

V5 Beta 5在 Beta 4生命周期修复的基础上，进一步在按下语音快捷键时准备最终 ASR与当前热词图。若仍遇到松键后长时间等待，先确认 App与输入法组件版本均至少为 `5.0.0b5`。

### 功能键控制亮度/媒体而不是F9

根据 Mac键盘设置，可能需要使用 `Fn+F9`产生真正的功能键事件。可在系统键盘设置中启用“将 F1、F2等键用作标准功能键”。

### 麦克风权限被拒绝

前往 **系统设置 → 隐私与安全性 → 麦克风**，允许 VoCoType-linux。权限身份在 ad-hoc Beta升级后可能需要重新确认。

### 输入法已安装但快捷键没有反应

打开 VoCoType-linux设置中心，确认输入源已启用；退出并重新打开目标应用，使 InputMethodKit重新建立文本客户端。仍无法解决时在 GitHub Issue中附上不含 API key的诊断日志。
