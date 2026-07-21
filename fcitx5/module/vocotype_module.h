#ifndef VOCOTYPE_FCITX5_MODULE_H
#define VOCOTYPE_FCITX5_MODULE_H

#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <sys/types.h>

#include <fcitx-config/option.h>
#include <fcitx-utils/event.h>
#include <fcitx-utils/eventdispatcher.h>
#include <fcitx-utils/key.h>
#include <fcitx/addoninstance.h>
#include <fcitx/event.h>
#include <fcitx/inputcontext.h>
#include <fcitx/instance.h>

#include "ipc_client.h"

namespace vocotype {

struct VoiceEditSnapshot {
    bool valid = false;
    std::string context_id;
    std::string text;
    std::string selected_text;
    unsigned int cursor = 0;
    unsigned int anchor = 0;
};

struct RecorderOutputState {
    std::mutex mutex;
    std::string audio_path;
};

FCITX_CONFIGURATION(
    VoCoTypeModuleConfig,
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
        "AI 润色模式修饰键",
        fcitx::Key(FcitxKey_Shift_L),
        fcitx::KeyConstrain({fcitx::KeyConstrainFlag::AllowModifierLess,
                             fcitx::KeyConstrainFlag::AllowModifierOnly})};
    fcitx::Option<int, fcitx::IntConstrain> polishMinChars{
        this,
        "PolishMinChars",
        "AI 润色最少字数",
        8,
        fcitx::IntConstrain(0, 2000)};
    fcitx::Option<int, fcitx::IntConstrain> polishTimeoutMs{
        this,
        "PolishTimeoutMs",
        "AI 流式输出空闲超时（毫秒）",
        20000,
        fcitx::IntConstrain(1000, 120000)};
    fcitx::Option<bool> enableThinking{
        this,
        "EnableThinking",
        "允许模型 thinking / reasoning",
        false};
    fcitx::Option<bool> blockWhenComposing{
        this,
        "BlockWhenComposing",
        "存在未提交预编辑时禁止开始录音",
        true};
    fcitx::Option<bool> stripTrailingPeriodOnCommit{
        this,
        "StripTrailingPeriodOnCommit",
        "提交时移除尾部句号",
        false};
    fcitx::Option<std::string> panelStyle{
        this,
        "PanelStyle",
        "状态提示样式（minimal 或 animated）",
        "minimal"};
);

class VoCoTypeModule final : public fcitx::AddonInstance {
public:
    explicit VoCoTypeModule(fcitx::Instance *instance);
    ~VoCoTypeModule() override;

    void reloadConfig() override;
    void save() override;
    const fcitx::Configuration *getConfig() const override;
    void setConfig(const fcitx::RawConfig &config) override;

private:
    enum class PanelAnimationKind {
        None,
        Recording,
        RecordingLong,
        Polishing,
    };



    void applyConfig();
    void handleKeyEvent(fcitx::KeyEvent &event);
    void handleFocusOut(fcitx::InputContextEvent &event);
    bool hasActiveComposition(fcitx::InputContext *ic) const;

    bool polishModeForStates(fcitx::KeyStates states) const;
    bool editModeForStates(fcitx::KeyStates states) const;
    bool captureVoiceEditSnapshot(fcitx::InputContext *ic,
                                  VoiceEditSnapshot &snapshot,
                                  std::string &error) const;
    void launchVoiceEditTask(
        fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref,
        const std::string &audio_path,
        const VoiceEditSnapshot &snapshot,
        uint64_t session_id);
    void showVoiceEditStatusBar(fcitx::InputContext *ic,
                                const std::string &title,
                                const std::string &detail = {});
    static std::string inputContextId(fcitx::InputContext *ic);
    bool voiceEditSnapshotStillMatches(
        fcitx::InputContext *ic,
        const VoiceEditSnapshot &snapshot) const;
    void applyVoiceEditResult(fcitx::InputContext *ic,
                              const VoiceEditSnapshot &snapshot,
                              const VoiceEditResult &result);
    void runVoiceEditKeyActions(fcitx::InputContext *ic,
                                const std::vector<EditKeyAction> &actions);
    void replaceSurroundingText(fcitx::InputContext *ic,
                                const VoiceEditSnapshot &snapshot,
                                const std::string &new_text,
                                bool record_history,
                                const std::string &hint);
    void scheduleEditReplacementCheck();
    void finalizeEditReplacement();
    void clearPendingEditReplacement();
    void confirmVoiceEditApplied(const VoiceEditSnapshot &snapshot,
                                 const std::string &new_text,
                                 bool record_history);
    void showTemporaryMessage(fcitx::InputContext *ic,
                              const std::string &message);
    void startVoiceEditPolling(fcitx::InputContext *ic,
                               const std::string &task_id,
                               const VoiceEditSnapshot &snapshot,
                               uint64_t session_id);
    void scheduleVoiceEditPoll(
        fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref);
    void handleVoiceEditPollResult(fcitx::InputContext *ic,
                                   const VoiceEditPollResult &result);
    void showVoiceEditProgress(fcitx::InputContext *ic,
                               const std::string &phase,
                               const std::string &instruction);
    void showVoiceEditFailure(fcitx::InputContext *ic,
                              const std::string &error,
                              const std::string &instruction);
    void cancelActiveVoiceEditTask();

    void startPolishPolling(fcitx::InputContext *ic, const std::string &task_id);
    void schedulePolishPoll(
        fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref);
    void handlePolishPollResult(fcitx::InputContext *ic,
                                const PolishPollResult &result);
    void showPolishProgress(fcitx::InputContext *ic,
                            const std::string &status,
                            const std::string &preview,
                            const std::string &original_text);
    void cancelActivePolishTask();

    void armPendingRecordingStart(fcitx::InputContext *ic,
                                  bool long_mode,
                                  bool edit_mode,
                                  const VoiceEditSnapshot &edit_snapshot);
    void cancelPendingRecordingStart();
    void armPendingPttRelease(fcitx::InputContext *ic);
    void cancelPendingPttRelease();
    void replayShortTapAsRegularKey(fcitx::InputContext *ic);

    void startRecording(fcitx::InputContext *ic,
                        bool long_mode,
                        bool edit_mode,
                        const VoiceEditSnapshot &edit_snapshot);
    void stopRecording(bool transcribe);
    void stopAndTranscribe();

    void showPanelMessage(fcitx::InputContext *ic, const std::string &message);
    void showStreamingPreview(fcitx::InputContext *ic, const std::string &text);
    void showAnimationFrame(fcitx::InputContext *ic);
    void startPanelAnimation(fcitx::InputContext *ic, PanelAnimationKind kind);
    void schedulePanelAnimationFrame(
        fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref,
        uint64_t generation);
    void stopPanelAnimation();
    void clearOwnedUI(fcitx::InputContext *ic);
    void showError(fcitx::InputContext *ic, const std::string &error,
                   const std::string &original_text = {});
    bool handlePendingFallbackKey(fcitx::KeyEvent &event);

    bool pasteTextForClient(fcitx::InputContext *ic, const std::string &text);
    void commitText(fcitx::InputContext *ic, const std::string &text,
                    bool strip_trailing_period = false);

    template <typename T>
    void scheduleWithContext(fcitx::TrackableObjectReference<T> context,
                             std::function<void()> functor) {
        if (!context.isValid()) {
            return;
        }
        event_dispatcher_.schedule(
            [context = std::move(context), functor = std::move(functor)]() mutable {
                if (context.isValid()) {
                    functor();
                }
            });
    }

    fcitx::Instance *instance_;
    fcitx::EventDispatcher event_dispatcher_;
    std::string backend_socket_path_;
    std::unique_ptr<IPCClient> ipc_client_;
    VoCoTypeModuleConfig config_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> key_handler_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> focus_out_handler_;

    std::string recorder_launcher_path_;
    fcitx::KeySym ptt_key_sym_ = FcitxKey_F9;
    fcitx::KeyStates long_mode_modifier_ = fcitx::KeyState::Shift;
    std::string ptt_key_name_ = "F9";
    int ptt_hold_threshold_ms_ = 0;
    int polish_min_chars_ = 8;
    int polish_timeout_ms_ = 20000;
    bool enable_thinking_ = false;
    bool block_when_composing_ = true;
    bool strip_trailing_period_on_commit_ = false;
    bool animate_panel_ = false;

    bool ptt_pressed_ = false;
    bool ptt_suppressed_ = false;
    bool is_recording_ = false;
    bool recording_long_mode_ = false;
    bool recording_edit_mode_ = false;
    bool pending_long_mode_ = false;
    bool pending_edit_mode_ = false;
    bool ui_owned_ = false;
    VoiceEditSnapshot pending_edit_snapshot_;
    VoiceEditSnapshot recording_edit_snapshot_;
    bool streaming_preview_visible_ = false;
    fcitx::KeyStates pending_ptt_states_ = fcitx::KeyState::NoState;
    fcitx::TrackableObjectReference<fcitx::InputContext> active_ic_;

    pid_t recorder_pid_ = -1;
    int recorder_stdin_fd_ = -1;
    int recorder_lock_fd_ = -1;
    std::thread recorder_output_thread_;
    std::shared_ptr<RecorderOutputState> recorder_output_state_;
    uint64_t recording_generation_ = 0;

    std::unique_ptr<fcitx::EventSourceTime> ptt_hold_timer_;
    std::unique_ptr<fcitx::EventSourceTime> ptt_release_timer_;
    std::unique_ptr<fcitx::EventSourceTime> recording_animation_timer_;
    std::unique_ptr<fcitx::EventSourceTime> polish_poll_timer_;
    std::unique_ptr<fcitx::EventSourceTime> edit_replace_timer_;
    std::unique_ptr<fcitx::EventSourceTime> edit_hint_timer_;
    std::unique_ptr<fcitx::EventSourceTime> voice_edit_poll_timer_;
    size_t recording_animation_frame_index_ = 0;
    PanelAnimationKind panel_animation_kind_ = PanelAnimationKind::None;
    uint64_t panel_animation_generation_ = 0;

    uint64_t voice_session_counter_ = 0;
    uint64_t active_voice_session_id_ = 0;
    uint64_t recording_voice_session_id_ = 0;
    uint64_t active_voice_edit_session_id_ = 0;
    bool voice_edit_poll_in_flight_ = false;
    std::string active_voice_edit_task_id_;
    std::string active_voice_edit_instruction_;
    VoiceEditSnapshot active_voice_edit_snapshot_;

    bool polish_poll_in_flight_ = false;
    std::string active_polish_task_id_;
    std::string active_polish_preview_;
    std::string active_polish_original_;
    int active_polish_after_seq_ = 0;

    std::string pending_fallback_text_;
    bool edit_replace_pending_ = false;
    VoiceEditSnapshot edit_replace_snapshot_;
    std::string edit_replace_new_text_;
    std::string edit_replace_hint_;
    bool edit_replace_record_history_ = true;
    int edit_replace_retries_left_ = 0;
    std::string edit_replace_state_ = "unknown";
    fcitx::InputContext *last_committed_ic_ = nullptr;
    std::string last_committed_program_;
    std::string last_committed_frontend_;
    std::string last_committed_text_;
    uint64_t last_commit_time_us_ = 0;
};

} // namespace vocotype

#endif
