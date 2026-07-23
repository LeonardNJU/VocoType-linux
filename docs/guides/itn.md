# ITN 与数字格式策略

VoCoType 在最终识别后由 C++ text normalizer 执行确定性逆文本标准化。处理顺序为：

```text
FunASR 文本
    ↓
标点恢复
    ↓
术语 alias → canonical
    ↓
固定短语保护
    ↓
中文数字、日期、时间、单位与金额规则
    ↓
重新应用术语保护
```

## 支持范围

- 中文整数、小数、百分比和序数；
- 日期、时间、电话号码和编号；
- 距离、重量、金额与常用量词；
- 950 条固定非数字短语保护；
- 紧凑日期、时间、距离和货币符号开关。

## 术语保护

用户名称、作品名、型号或特殊写法应加入 `~/.config/vocotype/terms.yaml` 并保持 `protect: true`。例如 `一百米计划` 不会因为启用了路程缩写而变成 `100m计划`。

## 实现与测试

所有转换位于 `src/core/src/text_normalizer.cpp`，不依赖 Pynini、WeTextProcessing 或 Python FST。C++ 测试覆盖历史 ITN 语料，并与迁移前的 426 个相关用例做过逐项等价校验。

可在 **VoCoType 设置 → ITN** 中实时预览。
