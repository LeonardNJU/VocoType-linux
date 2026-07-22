# ITN 与数字格式策略

VoCoType 使用项目内的确定性中文数字规则，不再依赖 WeTextProcessing/Pynini。用户可以在图形设置中心整体关闭数字标准化，或独立选择日期、时间、距离和货币的输出风格。

## 默认行为

```text
二零二六年五月十一号  → 2026/05/11
下午三点二十分开会    → 15:20开会
延迟一点五秒           → 延迟1.5秒
九十九块九             → ¥99.9
系统还有二百五十六台   → 系统还有256台
跑了三百二十米         → 跑了320m
```

默认配置：

```json
{
  "normalization": {
    "enabled": true,
    "compact_dates": true,
    "compact_times": true,
    "compact_distances": true,
    "currency_symbols": true
  }
}
```

这组语义属于 VoCoType 配置，与当前使用 IBus 还是 Fcitx 5 无关。

## 开关语义

- `enabled=false`：不执行中文数字规则；用户词典 alias → canonical 仍然执行。
- `compact_dates=false`：保留 `2026年5月11号`，而不是 `2026/05/11`。
- `compact_times=false`：保留 `下午3点20分`，而不是 `15:20`。
- `compact_distances=false`：保留 `320米`，而不是 `320m`。
- `currency_symbols=false`：保留 `128元`，而不是 `¥128`。

## 处理顺序

```text
Contextual Paraformer + native hotwords
    ↓
标点恢复
    ↓
术语 alias → canonical
    ↓
（enabled=true）确定性中文数字规则
    ↓
重新应用术语保护
    ↓
按独立开关应用日期/时间/距离/金额书写风格
```

所有转换都由可审查的 Python 规则决定。日期、时间、单位、货币、序数、百分比、电话号码、编号、普通量词与固定短语都有独立上下文判断，不再经过通用 FST 二次改写。

## 术语保护

用户名称、作品名、型号或特殊写法应加入 `~/.config/vocotype/terms.yaml` 并保持 `protect: true`。例如 `一百米计划` 不会因为启用了路程缩写而变成 `100m计划`。

详见 [`TERMS.md`](terms.md) 和 [`SETTINGS_CENTER.md`](settings-center.md)。

## 运行依赖

数字标准化只使用 Python 标准库和 VoCoType 自身代码。安装包不再包含：

```text
WeTextProcessing
Pynini
```

这移除了约 160 MiB 的压缩 wheel，同时避免启动时加载大型 FST 运行时。
