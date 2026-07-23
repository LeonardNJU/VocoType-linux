/*
 * VoCoType Fcitx5 Addon - IPC Client
 *
 * IPC 客户端，负责与 native Core 通过 Unix Socket 通信
 */

#ifndef VOCOTYPE_IPC_CLIENT_H
#define VOCOTYPE_IPC_CLIENT_H

#include <string>
#include <vector>
#include <memory>

namespace vocotype {

/**
 * Rime UI 状态
 *
 * 表示 Rime 的预编辑、候选词等 UI 状态
 */
struct RimeUIState {
    bool handled = false;               // 按键是否被 Rime 处理
    std::string commit_text;            // 提交的文本（如果有）
    std::string preedit_text;           // 预编辑文本
    int cursor_pos = 0;                 // 光标位置

    // 候选词列表: (text, comment)
    std::vector<std::pair<std::string, std::string>> candidates;
    int highlighted_index = 0;          // 高亮的候选词索引
    int page_size = 5;                  // 每页候选词数
};

/**
 * 语音识别结果
 */
struct TranscribeResult {
    bool success = false;
    std::string text;
    std::string error;
    std::string original_text;
};

struct TranscribeStartResult {
    bool success = false;
    std::string task_id;
    std::string status;
    std::string error;
};

struct EditKeyAction {
    std::string key;
    std::vector<std::string> modifiers;
    int repeat = 1;
};

struct VoiceEditResult {
    bool success = false;
    bool handled = false;
    bool record_history = true;
    std::string mode;
    std::string new_text;
    std::string expected_text;
    std::string hint;
    std::string error;
    std::string instruction;
    std::string reason;
    std::vector<EditKeyAction> key_actions;
};

struct VoiceEditStartResult {
    bool success = false;
    std::string task_id;
    std::string status;
    std::string error;
};

struct VoiceEditPollResult {
    bool success = false;
    std::string task_id;
    std::string status;
    std::string phase;
    std::string instruction;
    std::string error;
    std::string reason;
    VoiceEditResult result;
};

struct PolishEvent {
    int seq = 0;
    std::string kind;
    std::string text;
    std::string preview;
    std::string reason;
};

struct PolishPollResult {
    bool success = false;
    std::string task_id;
    std::string status;
    std::string phase;
    std::string error;
    std::string reason;
    std::string preview;
    std::string final_text;
    std::string original_text;
    int last_seq = 0;
    std::vector<PolishEvent> events;
};

/**
 * IPC 客户端
 *
 * 通过 Unix Socket 与 native Core 通信
 */
class IPCClient {
public:
    /**
     * 构造函数
     *
     * @param socket_path Unix Socket 路径
     */
    explicit IPCClient(const std::string& socket_path);

    /**
     * 析构函数
     */
    ~IPCClient();

    /**
     * 语音识别
     *
     * @param audio_path 音频文件路径
     * @return 识别结果
     */
    TranscribeResult transcribeAudio(const std::string& audio_path, bool long_mode = false);

    VoiceEditResult editAudio(const std::string& audio_path,
                              const std::string& context_id,
                              const std::string& surrounding_text,
                              unsigned int cursor_pos,
                              unsigned int anchor_pos,
                              const std::string& selected_text,
                              const std::string& replace_state = "unknown",
                              bool supports_surrounding = true);

    VoiceEditStartResult startVoiceEdit(
        const std::string& audio_path,
        const std::string& context_id,
        const std::string& surrounding_text,
        unsigned int cursor_pos,
        unsigned int anchor_pos,
        const std::string& selected_text,
        const std::string& replace_state = "unknown",
        bool supports_surrounding = true);

    VoiceEditPollResult pollVoiceEditTask(const std::string& task_id);

    bool cancelVoiceEditTask(const std::string& task_id);

    bool confirmEditApplied(const std::string& context_id,
                            const std::string& original_text,
                            const std::string& new_text,
                            bool record_history);


    TranscribeStartResult startTranscription(const std::string& audio_path,
                                             bool polish_enabled = false,
                                             int polish_min_chars = 0,
                                             int polish_timeout_ms = 0,
                                             bool enable_thinking = false);

    PolishPollResult pollPolishTask(const std::string& task_id, int after_seq);

    bool cancelPolishTask(const std::string& task_id);

    /**
     * 处理 Rime 按键
     *
     * @param keyval X11 keysym 值
     * @param mask Rime modifier mask
     * @return Rime UI 状态
     */
    RimeUIState processKey(int keyval, int mask);

    /**
     * 重置 Rime 状态
     */
    void reset();

    /**
     * 健康检查
     *
     * @return 是否连接成功
     */
    bool ping();

private:
    /**
     * 发送请求并接收响应
     *
     * @param request JSON 请求字符串
     * @return JSON 响应字符串
     */
    std::string sendRequest(const std::string& request,
                            int receive_timeout_ms = 2000);

    std::string socket_path_;
};

} // namespace vocotype

#endif // VOCOTYPE_IPC_CLIENT_H
