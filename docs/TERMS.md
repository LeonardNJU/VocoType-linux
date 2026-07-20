# 术语库与原生热词

VoCoType 使用一份共享术语库同时驱动两层能力：

1. **Contextual Paraformer 原生热词**：在 ASR 解码阶段提高专有名词的识别概率。
2. **确定性标准化**：在 ASR 完成后把常见错写统一为指定的标准写法，并保护结果不被 ITN/数字规则误改。

IBus 与 Fcitx 5 共用：

```text
~/.config/vocotype/terms.yaml
```

编辑文件后无需重启，下一次转录会自动重新加载。也可用环境变量
`VOCOTYPE_TERMS_FILE` 指向其他文件。

## 配置格式

```yaml
terms:
  - canonical: Ghostty
    aliases:
      - 鬼斯提
      - 格斯提
    hotword: true
    protect: true

  - canonical: README.md
    aliases:
      - read me点md
      - README文件
    hotwords:
      - README
    protect: true

protect:
  - 三体问题
  - 一加手机
```

字段含义：

- `canonical`：最终标准写法。
- `aliases`：ASR 常见错写。替换大小写不敏感、最长优先，并且只执行一遍，不会级联。
- `hotword: true`：把 `canonical` 送入原生热词编码器。
- `hotwords`：显式指定送入模型的热词，可替代 `hotword: true`。
- `protect`：默认 `true`，保护标准词不被后续 ITN/数字归一化修改。
- 顶层 `protect`：只保护、不做 alias 替换的固定表达。

ASCII alias 使用单词边界。例如 `no → NoSQL` 不会误改 `nobody`，标准词本身重复处理也保持幂等。

## 原生热词限制

`funasr_onnx.ContextualParaformer` 使用空格分隔热词。VoCoType 会：

- 去重；
- 忽略含空格的单个原生热词；
- 忽略超过 10 个字符的热词；
- 最多发送 1000 个热词；
- 把 `asr.hotword` 中的临时热词与术语库合并。

多词英文表达应把适合模型识别的单个 token 写入 `hotwords`，再用 `aliases` 规范最终拼写。

## 旧用户词典兼容

Geequlim fork 使用的格式仍可直接读取：

```yaml
replace:
  Ghostty:
    - 鬼斯提
    - 格斯提

protect:
  - 一加手机
```

当 `terms.yaml` 不存在而 `~/.config/vocotype/user-dictionary.yaml` 存在时，VoCoType 会自动使用旧文件。
新安装会创建 `terms.yaml`，但不会覆盖任何已有术语文件。

## 处理顺序

```text
Contextual Paraformer + native hotwords
    ↓
标点恢复
    ↓
术语 alias → canonical
    ↓
VoCoType 数字产品规则
    ↓
保护 canonical/protect spans 与已确定格式
    ↓
强制 WeTextProcessing FST ITN（仅接受安全数字变化）
```

原生热词是概率性偏置，确定性 alias 替换负责保证最终标准写法；二者互补，不应互相替代。ITN 细节见 [`ITN.md`](ITN.md)。
