/*
 * VoCoType Fcitx5 Addon
 *
 * 语音 + Rime 拼音输入法
 * - F9: 按住录音，松开识别（极速模式）
 * - Shift+F9: 长句模式，松开后识别并可选 SLM 润色
 * - 其他键: Rime 拼音输入
 */

#ifndef VOCOTYPE_ADDON_H
#define VOCOTYPE_ADDON_H

#include <fcitx/addoninstance.h>
#include <fcitx-config/option.h>
#include <fcitx/instance.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputmethodentry.h>
#include <fcitx/inputcontextproperty.h>
#include <fcitx-utils/eventloopinterface.h>
#include <fcitx-utils/key.h>
#include <memory>
#include <string>
#include <sys/types.h>
#include <vector>

#include "ipc_client.h"

namespace vocotype {

FCITX_CONFIGURATION(VoCoTypeInputMethodConfig,
    fcitx::Option<fcitx::Key, fcitx::KeyConstrain> pttKey{
        this,
        "PTTKey",
        "按住说话主键",
        fcitx::Key(FcitxKey_F9),
        fcitx::KeyConstrain({fcitx::KeyConstrainFlag::AllowModifierLess,
                             fcitx::KeyConstrainFlag::AllowModifierOnly})};
    fcitx::Option<int, fcitx::IntConstrain> pttHoldThresholdMs{
        this,
        "PTTHoldThresholdMs",
        "开始录音所需长按阈值（毫秒）",
        0,
        fcitx::IntConstrain(0, 2000)};
    fcitx::Option<fcitx::Key, fcitx::KeyConstrain> longModeModifier{
        this,
        "LongModeModifier",
        "长句模式修饰键",
        fcitx::Key(FcitxKey_Shift_L),
        fcitx::KeyConstrain({fcitx::KeyConstrainFlag::AllowModifierLess,
                     fcitx::KeyConstrainFlag::AllowModifierOnly})};
    fcitx::Option<bool> stripTrailingPeriodOnCommit{
        this,
        "StripTrailingPeriodOnCommit",
        "提交时移除尾部句号",
        false};
);

/**
 * VoCoType Addon（同时也是输入法引擎）
 */
class VoCoTypeAddon : public fcitx::InputMethodEngine {
public:
    VoCoTypeAddon(fcitx::Instance* instance);
    ~VoCoTypeAddon();

    void reloadConfig() override;
    void save() override;

    const fcitx::Configuration* getConfigForInputMethod(
        const fcitx::InputMethodEntry& entry) const override;

    void setConfigForInputMethod(const fcitx::InputMethodEntry& entry,
                                 const fcitx::RawConfig& config) override;

    // 返回输入法列表（必须实现，否则 Fcitx5 找不到输入法）
    std::vector<fcitx::InputMethodEntry> listInputMethods() override;

    void keyEvent(const fcitx::InputMethodEntry& entry,
                  fcitx::KeyEvent& keyEvent) override;

    void reset(const fcitx::InputMethodEntry& entry,
               fcitx::InputContextEvent& event) override;

    void activate(const fcitx::InputMethodEntry& entry,
                  fcitx::InputContextEvent& event) override;

    void deactivate(const fcitx::InputMethodEntry& entry,
                    fcitx::InputContextEvent& event) override;

private:
    enum class PanelAnimationKind {
        None,
        Recording,
        RecordingLong,
        Polishing,
    };

    void applyHotkeyConfig();
    void armPendingRecordingStart(fcitx::InputContext* ic, bool long_mode);
    void cancelPendingRecordingStart();
    void armPendingRecordingStop(fcitx::InputContext* ic);
    void cancelPendingRecordingStop();
    bool forwardKeyToRime(fcitx::InputContext* ic, fcitx::KeySym keyval,
                          fcitx::KeyStates states);
    bool handlePendingFallbackKey(fcitx::InputContext* ic, fcitx::KeySym keyval,
                                  fcitx::KeyStates states, bool is_release);
    void replayShortTapAsRegularKey(fcitx::InputContext* ic);
    void showPanelMessage(fcitx::InputContext* ic, const std::string& message);
    void startPanelAnimation(fcitx::InputContext* ic, PanelAnimationKind kind);
    void startRecordingAnimation(fcitx::InputContext* ic);
    void startLongRecordingAnimation(fcitx::InputContext* ic);
    void startPolishingAnimation(fcitx::InputContext* ic);
    void stopRecordingAnimation();
    void showAnimationFrame(fcitx::InputContext* ic);

    /**
     * F9 按下：开始录音
     */
    void startRecording(fcitx::InputContext* ic, bool long_mode);

    /**
     * F9 松开：停止录音并转录
     */
    void stopAndTranscribe(fcitx::InputContext* ic);

    /**
     * 停止录音，可选择是否转录
     */
    void stopRecording(fcitx::InputContext* ic, bool transcribe);

    /**
     * 更新 UI（预编辑、候选词）
     */
    void updateUI(fcitx::InputContext* ic, const RimeUIState& state);

    /**
     * 清除 UI
     */
    void clearUI(fcitx::InputContext* ic);
    bool pasteTextForClient(fcitx::InputContext* ic, const std::string& text);

    /**
     * 提交文本
     */
    void commitText(fcitx::InputContext* ic, const std::string& text,
                    bool strip_trailing_period = false);

    /**
     * 显示错误信息
     */
    void showError(fcitx::InputContext* ic, const std::string& error,
                   const std::string& original_text = {});

    /**
     * 检查是否是输入法切换热键
     */
    bool isIMSwitchHotkey(const fcitx::Key& key) const;

    fcitx::Instance* instance_;
    std::unique_ptr<IPCClient> ipc_client_;
    VoCoTypeInputMethodConfig config_;

    // 录音状态
    bool is_recording_ = false;
    bool recording_long_mode_ = false;
    pid_t recorder_pid_ = -1;
    int recorder_stdin_fd_ = -1;
    FILE* recorder_stdout_ = nullptr;

    // 录音启动器路径（安装时配置）
    std::string recorder_launcher_path_;
    fcitx::KeySym ptt_key_sym_ = FcitxKey_F9;
    fcitx::KeyStates long_mode_modifier_ = fcitx::KeyState::Shift;
    std::string ptt_key_name_ = "F9";
    int ptt_hold_threshold_ms_ = 0;
    std::string long_mode_modifier_name_ = "Shift";
    bool strip_trailing_period_on_commit_ = false;
    bool ptt_pressed_ = false;
    bool pending_long_mode_ = false;
    fcitx::KeyStates pending_ptt_states_ = fcitx::KeyState::NoState;
    std::unique_ptr<fcitx::EventSourceTime> ptt_hold_timer_;
    std::unique_ptr<fcitx::EventSourceTime> ptt_release_timer_;
    std::unique_ptr<fcitx::EventSourceTime> recording_animation_timer_;
    size_t recording_animation_frame_index_ = 0;
    PanelAnimationKind panel_animation_kind_ = PanelAnimationKind::None;
    std::string pending_fallback_text_;
    fcitx::InputContext* last_committed_ic_ = nullptr;
    std::string last_committed_program_;
    std::string last_committed_frontend_;
    std::string last_committed_text_;
    uint64_t last_commit_time_us_ = 0;
};

} // namespace vocotype

#endif // VOCOTYPE_ADDON_H
