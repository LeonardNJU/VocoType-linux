# ITN 与数字格式策略

VoCoType 使用 WeTextProcessing 中文 FST ITN 和产品级数字规则。运行依赖始终安装，但用户可以在图形设置中心整体关闭数字/ITN，或独立选择输出风格。

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

- `enabled=false`：不执行中文数字规则或 FST ITN；用户词典 alias → canonical 仍然执行。
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
（enabled=true）产品数字规则
    ↓
保护 canonical、固定短语和已确定格式
    ↓
WeTextProcessing 中文 FST ITN
    ↓
按独立开关应用日期/时间/路程/金额书写风格
```

通用 FST 仍受语义安全护栏约束。日期、时间、单位和货币风格不再由 FST 随机决定，而由最后一层确定性产品策略统一输出。

## 术语保护

用户名称、作品名、型号或特殊写法应加入 `~/.config/vocotype/terms.yaml` 并保持 `protect: true`。例如 `100米计划` 不会因为启用了路程缩写而变成 `100m计划`。

详见 [`TERMS.md`](terms.md) 和 [`SETTINGS_CENTER.md`](settings-center.md)。

## 依赖

基础安装仍固定依赖：

```text
WeTextProcessing==1.2.0
```

关闭 ITN 只是运行策略，不会卸载依赖；再次开启时无需重新安装。
