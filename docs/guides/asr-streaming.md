# 实时识别预览（FunASR 2-pass）

VoCoType 可选启用 FunASR 官方在线 Paraformer，在按住录音键说话时实时更新
IBus/Fcitx 5 的 preedit。该功能默认关闭。

## 准确率边界

实时文本只用于预览，不会直接提交，也不会替代或裁剪原始录音。松开录音键后，
VoCoType 仍把完整 PCM 交给原来的 Contextual Paraformer，并继续使用原有热词、
标点、ITN 和标准化流程。最终提交结果因此仍由离线模型唯一决定。

```text
麦克风 PCM ─┬─> 在线 Paraformer ─> 可反复覆盖的 preedit
            └─> 完整录音缓冲 ─> Contextual Paraformer ─> 最终提交
```

在线通道初始化失败、处理过慢或 IPC 中断时，本次录音会自动退化为原来的
“录完后识别”模式，最终离线识别不受影响。

## 安装可选 runtime

基础 DEB、RPM 与 Arch 包只安装稳定的最终识别链路，不捆绑体积较大的 native streaming runtime，也不会在包管理器构建阶段联网下载第三方源码。开发者可在源码树运行：

```bash
./native/streaming_worker/build.sh
```

随后重新运行 VoCoType 的 IBus 或 Fcitx 5 安装 / 修复流程；安装器会检测预构建 bundle 并复制到用户目录。未来也可以安装单独发布的 native streaming bundle。

## 开启方式

安装上述可选 runtime 后，在设置中心的“逆文本标准化（ITN）”页面开启“实时识别预览（2-pass）”，保存后：

- Fcitx 5 后端会自动重启，但不会立即加载在线模型；
- IBus 会在下一次开始录音前重新载入配置；
- 第一次实际录音时才启动本地 native worker，并按需下载/加载官方在线 ONNX 模型；
- 后续连续录音在空闲窗口内复用该 worker，超时后进程退出并完整释放模型内存。

对应运行配置为：

```json
{
  "asr_streaming": {
    "enabled": true,
    "model": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx",
    "chunk_size": [5, 10, 5],
    "intra_op_num_threads": 1,
    "idle_timeout_s": 30,
    "session_idle_timeout_s": 15
  }
}
```

该开关属于统一的 VoCoType 配置，与当前采用 IBus 还是 Fcitx 5 integration 无关。

## 资源策略

- 固定使用 CPU，不请求独立 GPU；
- 默认每约 600 ms 执行一次在线推理；
- 在线 ONNX Runtime 默认只使用一个 intra-op 线程；
- 现有 VoCoType backend/IBus engine 只管理生命周期；在线模型实际运行在本地 native 子进程中；
- 松键后不等待在线尾部 flush，立即进入最终离线识别；
- 最后一个预览 session 结束并空闲 30 秒后，worker 自动退出并由系统完整回收内存；
- 在当前开发机的真实 3 秒录音烟测中，portable native worker 加载约 0.58 秒，RSS 约 307 MB；600 ms chunk 平均约 41 ms，RTF≈0.069；
- 同一模型的 Python ONNX 原型曾占约 0.8–1.0 GiB RSS，因此最终实现改用官方 FunASR C++ runtime；
- 关闭功能时立即终止在线 worker；未开启或空闲退出后不增加常驻推理内存。

在线预览不运行完整标点和 ITN，因此录音期间的文字可能较粗糙；松键后的离线
结果会覆盖它并成为唯一提交文本。
