# VoCoType Rime 配置说明

> **Fcitx 5 用户注意**：VoCoType 已改为全局 Fcitx 5 Module，不再内嵌 Rime。
> 请直接配置和使用 `fcitx5-rime`；VoCoType 的 F9/Shift+F9 会自动增强当前输入法。
> 下文中 VoCoType 自行调用 librime/pyrime 的说明仅适用于 IBus 版本。

## 配置共享机制

如果您安装了 **VoCoType 完整版**（语音 + Rime 拼音），VoCoType 会直接使用 ibus-rime 的配置目录：

```
~/.config/ibus/rime/
```

这意味着：

✅ **如果您已经在使用 ibus-rime**：
- VoCoType 会自动继承您的所有配置、词库、用户词典
- 无需重复配置，开箱即用
- 两个输入法共享同一套配置和词库

✅ **如果您是新用户**：
- VoCoType 会使用 Rime 默认配置
- 您可以按照 Rime 的标准方式配置拼音输入
- 配置完成后，ibus-rime 和 VoCoType 都能使用

> 方案选择：安装脚本会把选择的方案记录在 `~/.config/vocotype/rime/user.yaml`，
> 启动时优先使用该方案。

## 配置目录结构

```
~/.config/ibus/rime/
├── default.yaml              # 默认配置
├── default.custom.yaml       # 用户自定义配置
├── luna_pinyin.yaml          # 明月拼音方案
├── luna_pinyin.custom.yaml   # 用户自定义方案
├── user.yaml                 # 用户信息
├── installation.yaml         # 安装信息
└── *.userdb/                 # 用户词库
```

## 推荐配置方案

### 🎨 rime-ice（雾凇拼音）

如果您希望获得更好的拼音输入体验，我们推荐使用 **rime-ice（雾凇拼音）** 配置方案：

- **项目地址**：https://github.com/iDvel/rime-ice
- **特点**：
  - 开箱即用的现代词库
  - 支持全拼、双拼
  - 智能纠错和模糊音
  - Emoji 支持
  - 持续更新的网络流行词

### 安装 rime-ice

1. **备份现有配置**（如果有）：
   ```bash
   cp -r ~/.config/ibus/rime ~/.config/ibus/rime.backup
   ```

2. **安装 rime-ice**：
   ```bash
   # 克隆配置仓库
   git clone https://github.com/iDvel/rime-ice.git /tmp/rime-ice

   # 复制配置文件到 Rime 目录
   cp -r /tmp/rime-ice/* ~/.config/ibus/rime/
   ```

3. **重新部署 Rime**：
   ```bash
   # 如果您在使用 ibus-rime
   ibus-daemon -drx

   # 或者直接重启 IBus
   ibus restart
   ```

4. **切换到 VoCoType**，尝试拼音输入，新配置会立即生效

## 不使用 rime-ice？

如果您不想使用 rime-ice，Rime 默认配置也完全够用：

- **明月拼音**：经典的全拼方案
- **朙月拼音（简化字）**：适合简体中文用户
- **自然码双拼**、**小鹤双拼** 等

您可以在 `~/.config/ibus/rime/default.custom.yaml` 中选择方案。

## 自定义配置

VoCoType 完全遵循 Rime 的配置规范，所有 Rime 的自定义配置都适用。

### 常用配置示例

**1. 修改候选词数量**（`default.custom.yaml`）：

```yaml
patch:
  "menu/page_size": 9  # 每页显示 9 个候选词
```

**2. 添加自定义词库**（`luna_pinyin.custom.yaml`）：

```yaml
patch:
  "translator/dictionary": luna_pinyin.extended
```

**3. 启用 Emoji**（需要 rime-ice 或手动配置）：

```yaml
patch:
  "switches/@next":
    name: emoji_suggestion
    reset: 1
    states: [ "🈚️️", "🈶️" ]
```

### 重新部署

每次修改配置后，需要重新部署 Rime：

```bash
# 删除编译缓存
rm -rf ~/.config/ibus/rime/build/

# 重启 IBus
ibus restart
```

或者在 VoCoType 输入法激活时，按 `Ctrl + ~` 或 `F4` 打开 Rime 菜单，选择"重新部署"。

## 配置资源

- **Rime 官方文档**：https://rime.im/docs/
- **rime-ice 配置指南**：https://github.com/iDvel/rime-ice
- **Rime 配置教程**：https://github.com/rime/home/wiki/UserGuide
- **方案选单**：https://github.com/rime/plum

## 疑难解答

### Q: VoCoType 和 ibus-rime 的配置会互相影响吗？

A: 是的，它们共享同一个配置目录。任何一方的配置修改都会影响另一方。

### Q: 我只想在 VoCoType 中使用特定配置怎么办？

A: Rime 的配置是全局的，无法针对不同前端使用不同配置。
如果需要完全独立的配置，建议只安装 VoCoType 纯语音版。

### Q: 配置修改后不生效？

A: 确保：
1. 配置文件语法正确（YAML 格式）
2. 已删除 `~/.config/ibus/rime/build/` 目录
3. 已重启 IBus：`ibus restart`
4. 重新切换到 VoCoType 输入法

### Q: VoCoType 完整版的拼音输入和 ibus-rime 有什么区别？

A: 技术上没有区别，都是调用 librime 引擎。区别在于：

- **VoCoType 完整版**：语音 + 拼音一体，F9 语音，其他键拼音
- **ibus-rime**：纯拼音输入法

您可以根据使用场景选择：
- 需要频繁语音输入 → VoCoType 完整版
- 纯拼音场景 → ibus-rime 或 VoCoType 完整版均可

## 总结

VoCoType 完整版的 Rime 集成设计理念：

✅ **配置共享**：不重复造轮子，直接复用 ibus-rime 的配置生态
✅ **零学习成本**：如果您熟悉 Rime，在 VoCoType 中完全一样
✅ **可选增强**：推荐 rime-ice，但不强制，用户自主选择
✅ **语音优先**：F9 语音输入是核心，拼音是便利补充

享受语音与拼音无缝切换的输入体验！🎤⌨️
