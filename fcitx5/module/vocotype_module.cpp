#include "vocotype_module.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <functional>
#include <thread>

#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>

#include <fcitx-config/iniparser.h>
#include <fcitx-utils/capabilityflags.h>
#include <fcitx-utils/event.h>
#include <fcitx-utils/eventdispatcher.h>
#include <fcitx-utils/log.h>
#include <fcitx-utils/standardpath.h>
#include <fcitx-utils/utf8.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/candidatelist.h>
#include <fcitx/inputpanel.h>
#include <fcitx/text.h>
#include <fcitx/userinterface.h>

namespace {

constexpr auto FCITX_CONFIG_PATH = "conf/vocotype.conf";
constexpr uint64_t RECORDING_ANIMATION_INTERVAL_US = 200000;
constexpr uint64_t PTT_RELEASE_DEBOUNCE_US = 50000;
constexpr uint64_t POLISH_POLL_INTERVAL_US = 100000;
constexpr uint64_t DUPLICATE_COMMIT_SUPPRESS_US = 250000;

#ifdef VOCOTYPE_FCITX_LEGACY_STANDARD_PATH
constexpr auto CONFIG_PATH_TYPE = fcitx::StandardPath::Type::PkgConfig;
#else
constexpr auto CONFIG_PATH_TYPE = fcitx::StandardPathsType::PkgConfig;
#endif

constexpr std::array<const char *, 8> RECORDING_ANIMATION_FRAMES = {
    "🟢 正在听 ●     ", "🟢 正在听  ●    ", "🟢 正在听   ●   ",
    "🟢 正在听    ●  ", "⚫ 正在听     ● ", "⚫ 正在听    ●  ",
    "⚫ 正在听   ●   ", "⚫ 正在听  ●    ",
};

constexpr std::array<const char *, 8> LONG_RECORDING_ANIMATION_FRAMES = {
    "✨ 正在听·将润色 ●     ", "✨ 正在听·将润色  ●    ",
    "✨ 正在听·将润色   ●   ", "✨ 正在听·将润色    ●  ",
    "✨ 正在听·将润色     ● ", "✨ 正在听·将润色    ●  ",
    "✨ 正在听·将润色   ●   ", "✨ 正在听·将润色  ●    ",
};

constexpr std::array<const char *, 8> POLISHING_ANIMATION_FRAMES = {
    "✨ 正在润色 ●     ", "✨ 正在润色  ●    ", "✨ 正在润色   ●   ",
    "✨ 正在润色    ●  ", "✨ 正在润色     ● ", "✨ 正在润色    ●  ",
    "✨ 正在润色   ●   ", "✨ 正在润色  ●    ",
};

std::string toLower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string stripTrailingCommitPeriod(std::string text) {
    if (text.ends_with("。")) {
        text.resize(text.size() - std::char_traits<char>::length("。"));
    } else if (text.ends_with(".")) {
        text.pop_back();
    }
    return text;
}

std::string stopRecorderProcess(pid_t pid, int stdin_fd, FILE *stdout_file) {
    if (stdin_fd >= 0) {
        close(stdin_fd);
    }

    std::string audio_path;
    if (stdout_file) {
        char buffer[1024];
        if (fgets(buffer, sizeof(buffer), stdout_file) != nullptr) {
            audio_path = buffer;
            while (!audio_path.empty() &&
                   (audio_path.back() == '\n' || audio_path.back() == '\r')) {
                audio_path.pop_back();
            }
        }
        fclose(stdout_file);
    }

    if (pid > 0) {
        int status = 0;
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
        }
    }
    return audio_path;
}

bool copyTextToWaylandClipboard(const std::string &text) {
    FILE *pipe = popen("wl-copy", "w");
    if (!pipe) {
        return false;
    }
    const size_t written = fwrite(text.data(), 1, text.size(), pipe);
    const int status = pclose(pipe);
    return written == text.size() && status == 0;
}

bool pasteTextToX11Client(const std::string &text) {
    constexpr auto command =
        "python3 -c 'import subprocess, sys, tkinter as tk; "
        "data = sys.stdin.read(); root = tk.Tk(); root.withdraw(); "
        "sentinel = \"__VOCOTYPE_CLIPBOARD_EMPTY__\"; "
        "previous = root.tk.eval(\"if {[catch {clipboard get} result]} {set result {__VOCOTYPE_CLIPBOARD_EMPTY__}}; set result\"); "
        "root.clipboard_clear(); root.clipboard_append(data); root.update(); "
        "root.after(50, lambda: subprocess.Popen([\"xdotool\", \"key\", \"--clearmodifiers\", \"ctrl+v\"])); "
        "root.after(800, lambda: (root.clipboard_clear(), None if previous == sentinel else root.clipboard_append(previous), root.update())); "
        "root.after(30000, root.destroy); root.mainloop()'";

    FILE *pipe = popen(command, "w");
    if (!pipe) {
        return false;
    }
    const size_t written = fwrite(text.data(), 1, text.size(), pipe);
    const int status = pclose(pipe);
    return written == text.size() && status == 0;
}

} // namespace

namespace vocotype {

VoCoTypeModule::VoCoTypeModule(fcitx::Instance *instance)
    : instance_(instance),
      ipc_client_(std::make_unique<IPCClient>("/tmp/vocotype-fcitx5.sock")) {
    event_dispatcher_.attach(&instance_->eventLoop());
    if (const char *home = std::getenv("HOME")) {
        const std::string user_launcher =
            std::string(home) + "/.local/bin/vocotype-fcitx5-recorder";
        if (access(user_launcher.c_str(), X_OK) == 0) {
            recorder_launcher_path_ = user_launcher;
        }
    } else {
        FCITX_ERROR() << "HOME environment variable not set";
    }
    if (recorder_launcher_path_.empty() &&
        access("/usr/bin/vocotype-fcitx5-recorder", X_OK) == 0) {
        recorder_launcher_path_ = "/usr/bin/vocotype-fcitx5-recorder";
    }

    reloadConfig();

    key_handler_ = instance_->watchEvent(
        fcitx::EventType::InputContextKeyEvent,
        fcitx::EventWatcherPhase::PreInputMethod,
        [this](fcitx::Event &event) {
            handleKeyEvent(static_cast<fcitx::KeyEvent &>(event));
        });
    focus_out_handler_ = instance_->watchEvent(
        fcitx::EventType::InputContextFocusOut,
        fcitx::EventWatcherPhase::ReservedFirst,
        [this](fcitx::Event &event) {
            handleFocusOut(static_cast<fcitx::InputContextEvent &>(event));
        });

    FCITX_INFO() << "VoCoType global module initialized; hotkey="
                 << ptt_key_name_;
    if (!ipc_client_->ping()) {
        FCITX_WARN() << "VoCoType backend is not responding";
    }
}

VoCoTypeModule::~VoCoTypeModule() {
    cancelPendingRecordingStart();
    cancelPendingRecordingStop();
    cancelActivePolishTask();
    stopPanelAnimation();
    if (recorder_pid_ > 0 || recorder_stdout_ || recorder_stdin_fd_ >= 0) {
        std::string audio_path = stopRecorderProcess(
            recorder_pid_, recorder_stdin_fd_, recorder_stdout_);
        if (!audio_path.empty()) {
            std::remove(audio_path.c_str());
        }
    }
    event_dispatcher_.detach();
}

void VoCoTypeModule::reloadConfig() {
    fcitx::readAsIni(config_, CONFIG_PATH_TYPE,
                     FCITX_CONFIG_PATH);
    applyConfig();
}

void VoCoTypeModule::save() {
    if (!fcitx::safeSaveAsIni(config_, CONFIG_PATH_TYPE,
                              FCITX_CONFIG_PATH)) {
        FCITX_WARN() << "Failed to save VoCoType module config";
    }
}

const fcitx::Configuration *VoCoTypeModule::getConfig() const {
    return &config_;
}

void VoCoTypeModule::setConfig(const fcitx::RawConfig &config) {
    auto updated = config_;
    updated.load(config, true);
    config_ = updated;
    applyConfig();
    save();
}

void VoCoTypeModule::applyConfig() {
    auto ptt_key = config_.pttKey.value().normalize();
    if (!ptt_key.isValid()) {
        ptt_key = fcitx::Key(FcitxKey_F9);
    }

    auto modifier = config_.longModeModifier.value().normalize();
    auto modifier_state = fcitx::Key::keySymToStates(modifier.sym());
    if (!modifier.isValid() || modifier_state == fcitx::KeyState::NoState) {
        modifier_state = fcitx::KeyState::Shift;
    }

    ptt_key_sym_ = ptt_key.sym();
    ptt_key_name_ = ptt_key.toString();
    ptt_hold_threshold_ms_ = config_.pttHoldThresholdMs.value();
    long_mode_modifier_ = modifier_state;
    polish_by_default_ = config_.polishByDefault.value();
    polish_min_chars_ = std::max(0, config_.polishMinChars.value());
    polish_timeout_ms_ = std::max(1000, config_.polishTimeoutMs.value());
    enable_thinking_ = config_.enableThinking.value();
    block_when_composing_ = config_.blockWhenComposing.value();
    strip_trailing_period_on_commit_ =
        config_.stripTrailingPeriodOnCommit.value();
}

bool VoCoTypeModule::hasActiveComposition(fcitx::InputContext *ic) const {
    if (!ic) {
        return false;
    }
    const auto &panel = ic->inputPanel();
    return !panel.preedit().empty() || !panel.clientPreedit().empty() ||
           static_cast<bool>(panel.candidateList());
}

bool VoCoTypeModule::polishModeForStates(fcitx::KeyStates states) const {
    const bool modifier_active = static_cast<bool>(states & long_mode_modifier_);
    return polish_by_default_ ? !modifier_active : modifier_active;
}

void VoCoTypeModule::handleKeyEvent(fcitx::KeyEvent &event) {
    auto *ic = event.inputContext();
    if (!ic) {
        return;
    }

    if (handlePendingFallbackKey(event)) {
        return;
    }

    const auto key = event.key();
    if (!active_polish_task_id_.empty() && !event.isRelease()) {
        cancelActivePolishTask();
        clearOwnedUI(ic);
        if (key.sym() == FcitxKey_Escape) {
            event.filterAndAccept();
            return;
        }
    }
    if (key.sym() != ptt_key_sym_) {
        return;
    }

    if (!event.isRelease()) {
        if (is_recording_ && ptt_release_timer_) {
            cancelPendingRecordingStop();
            event.filterAndAccept();
            return;
        }
        if (!is_recording_ && !ptt_pressed_) {
            if (block_when_composing_ && hasActiveComposition(ic)) {
                FCITX_INFO() << "VoCoType hotkey ignored because current input method has active composition";
                event.filterAndAccept();
                return;
            }
            pending_ptt_states_ = key.states();
            active_ic_ = ic->watch();
            armPendingRecordingStart(ic, polishModeForStates(key.states()));
        }
    } else if (is_recording_) {
        armPendingRecordingStop();
    } else if (ptt_pressed_) {
        replayShortTapAsRegularKey(ic);
    } else {
        cancelPendingRecordingStart();
    }

    event.filterAndAccept();
}

void VoCoTypeModule::handleFocusOut(fcitx::InputContextEvent &event) {
    auto *active = active_ic_.get();
    if (!active || event.inputContext() != active) {
        return;
    }

    cancelActivePolishTask();
    if (is_recording_) {
        FCITX_INFO() << "Input focus changed while recording; cancelling VoCoType session";
        stopRecording(false);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    } else {
        cancelPendingRecordingStart();
        cancelPendingRecordingStop();
        clearOwnedUI(active);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    }
}

void VoCoTypeModule::armPendingRecordingStart(fcitx::InputContext *ic,
                                               bool long_mode) {
    cancelPendingRecordingStart();
    ptt_pressed_ = true;
    pending_long_mode_ = long_mode;

    if (ptt_hold_threshold_ms_ <= 0) {
        startRecording(ic, long_mode);
        return;
    }

    auto ic_ref = ic->watch();
    ptt_hold_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) +
            static_cast<uint64_t>(ptt_hold_threshold_ms_) * 1000ULL,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            ptt_hold_timer_.reset();
            auto *ic_ptr = ic_ref.get();
            if (!ptt_pressed_ || is_recording_ || !ic_ptr || !ic_ptr->hasFocus()) {
                cancelPendingRecordingStart();
                return false;
            }
            startRecording(ic_ptr, pending_long_mode_);
            return false;
        });
    ptt_hold_timer_->setOneShot();
}

void VoCoTypeModule::cancelPendingRecordingStart() {
    ptt_pressed_ = false;
    pending_long_mode_ = false;
    pending_ptt_states_ = fcitx::KeyState::NoState;
    ptt_hold_timer_.reset();
}

void VoCoTypeModule::armPendingRecordingStop() {
    cancelPendingRecordingStop();
    auto ic_ref = active_ic_;
    ptt_release_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + PTT_RELEASE_DEBOUNCE_US,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            ptt_release_timer_.reset();
            auto *ic_ptr = ic_ref.get();
            if (!is_recording_ || !ic_ptr || !ic_ptr->hasFocus()) {
                stopRecording(false);
                return false;
            }
            stopAndTranscribe();
            return false;
        });
    ptt_release_timer_->setOneShot();
}

void VoCoTypeModule::cancelPendingRecordingStop() {
    ptt_release_timer_.reset();
}

void VoCoTypeModule::replayShortTapAsRegularKey(fcitx::InputContext *ic) {
    const auto states = pending_ptt_states_;
    cancelPendingRecordingStart();
    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    const int time = 0;
    ic->forwardKey(fcitx::Key(ptt_key_sym_, states), false, time);
    ic->forwardKey(fcitx::Key(ptt_key_sym_, states), true, time);
}

void VoCoTypeModule::startRecording(fcitx::InputContext *ic, bool long_mode) {
    cancelActivePolishTask();
    if (is_recording_ || !ic || !ic->hasFocus()) {
        return;
    }
    if (recorder_launcher_path_.empty()) {
        showError(ic, "录音配置无效");
        return;
    }

    int stdin_pipe[2];
    int stdout_pipe[2];
    if (pipe(stdin_pipe) != 0) {
        showError(ic, "启动录音失败");
        return;
    }
    if (pipe(stdout_pipe) != 0) {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        showError(ic, "启动录音失败");
        return;
    }

    pid_t pid = fork();
    if (pid < 0) {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        showError(ic, "启动录音失败");
        return;
    }

    if (pid == 0) {
        dup2(stdin_pipe[0], STDIN_FILENO);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        execl(recorder_launcher_path_.c_str(), recorder_launcher_path_.c_str(),
              static_cast<char *>(nullptr));
        _exit(127);
    }

    close(stdin_pipe[0]);
    close(stdout_pipe[1]);
    FILE *stdout_file = fdopen(stdout_pipe[0], "r");
    if (!stdout_file) {
        close(stdout_pipe[0]);
        close(stdin_pipe[1]);
        kill(pid, SIGTERM);
        waitpid(pid, nullptr, 0);
        showError(ic, "启动录音失败");
        return;
    }

    recorder_pid_ = pid;
    recorder_stdin_fd_ = stdin_pipe[1];
    recorder_stdout_ = stdout_file;
    is_recording_ = true;
    ptt_pressed_ = true;
    pending_long_mode_ = false;
    recording_long_mode_ = long_mode;
    active_ic_ = ic->watch();

    if (long_mode) {
        std::thread([this]() { (void)ipc_client_->prewarmSlm(); }).detach();
        startPanelAnimation(ic, PanelAnimationKind::RecordingLong);
    } else {
        startPanelAnimation(ic, PanelAnimationKind::Recording);
    }
}

void VoCoTypeModule::stopAndTranscribe() {
    stopRecording(true);
}

void VoCoTypeModule::stopRecording(bool transcribe) {
    if (!is_recording_) {
        return;
    }

    cancelPendingRecordingStop();
    ptt_hold_timer_.reset();
    ptt_pressed_ = false;
    pending_long_mode_ = false;
    is_recording_ = false;
    const bool long_mode = recording_long_mode_;
    recording_long_mode_ = false;

    auto ic_ref = active_ic_;
    auto *ic = ic_ref.get();
    if (ic && transcribe && ic->hasFocus()) {
        if (long_mode) {
            startPanelAnimation(ic, PanelAnimationKind::Polishing);
        } else {
            showPanelMessage(ic, "⏳ 识别中...");
        }
    } else if (ic) {
        clearOwnedUI(ic);
    }

    pid_t pid = recorder_pid_;
    int stdin_fd = recorder_stdin_fd_;
    FILE *stdout_file = recorder_stdout_;
    recorder_pid_ = -1;
    recorder_stdin_fd_ = -1;
    recorder_stdout_ = nullptr;

    std::thread([this, pid, stdin_fd, stdout_file, transcribe, long_mode,
                 ic_ref]() mutable {
        std::string audio_path = stopRecorderProcess(pid, stdin_fd, stdout_file);
        if (!transcribe) {
            if (!audio_path.empty()) {
                std::remove(audio_path.c_str());
            }
            if (long_mode) {
                (void)ipc_client_->releaseSlm();
            }
            return;
        }

        if (long_mode) {
            TranscribeStartResult start_result;
            if (audio_path.empty()) {
                start_result.error = "录音失败";
            } else {
                start_result = ipc_client_->startTranscription(
                    audio_path,
                    true,
                    polish_min_chars_,
                    polish_timeout_ms_,
                    enable_thinking_);
                if (!start_result.success || start_result.task_id.empty()) {
                    std::remove(audio_path.c_str());
                }
            }

            scheduleWithContext(
                ic_ref, [this, ic_ref, start_result]() {
                    auto *ic_ptr = ic_ref.get();
                    if (!ic_ptr || !ic_ptr->hasFocus()) {
                        if (start_result.success && !start_result.task_id.empty()) {
                            const std::string task_id = start_result.task_id;
                            std::thread([task_id]() {
                                IPCClient client("/tmp/vocotype-fcitx5.sock");
                                (void)client.cancelPolishTask(task_id);
                            }).detach();
                        }
                        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                        return;
                    }
                    if (start_result.success && !start_result.task_id.empty()) {
                        startPolishPolling(ic_ptr, start_result.task_id);
                    } else {
                        showError(
                            ic_ptr,
                            start_result.error.empty() ? "转录失败" : start_result.error);
                    }
                });
            return;
        }

        TranscribeResult result;
        if (audio_path.empty()) {
            result.error = "录音失败";
        } else {
            result = ipc_client_->transcribeAudio(audio_path, false);
            std::remove(audio_path.c_str());
        }

        scheduleWithContext(
            ic_ref, [this, ic_ref, result]() {
                auto *ic_ptr = ic_ref.get();
                if (!ic_ptr || !ic_ptr->hasFocus()) {
                    FCITX_INFO() << "Discarded transcription because original input context lost focus";
                    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                    return;
                }
                bool keep_context_for_fallback = false;
                if (result.success && !result.text.empty()) {
                    commitText(ic_ptr, result.text,
                               strip_trailing_period_on_commit_);
                } else if (!result.success) {
                    showError(ic_ptr,
                              result.error.empty() ? "转录失败" : result.error,
                              result.original_text);
                    keep_context_for_fallback = !result.original_text.empty();
                } else {
                    clearOwnedUI(ic_ptr);
                }
                if (!keep_context_for_fallback) {
                    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                }
            });
    }).detach();
}

void VoCoTypeModule::startPolishPolling(fcitx::InputContext *ic,
                                            const std::string &task_id) {
    stopPanelAnimation();
    active_polish_task_id_ = task_id;
    active_polish_preview_.clear();
    active_polish_original_.clear();
    active_polish_after_seq_ = 0;
    polish_poll_in_flight_ = false;
    polish_poll_timer_.reset();
    showPanelMessage(ic, "⏳ 识别中...");
    schedulePolishPoll(ic->watch());
}

void VoCoTypeModule::schedulePolishPoll(
    fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref) {
    if (active_polish_task_id_.empty() || polish_poll_timer_ ||
        polish_poll_in_flight_) {
        return;
    }

    polish_poll_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + POLISH_POLL_INTERVAL_US,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            polish_poll_timer_.reset();
            if (active_polish_task_id_.empty() || polish_poll_in_flight_) {
                return false;
            }

            const std::string task_id = active_polish_task_id_;
            const int after_seq = active_polish_after_seq_;
            polish_poll_in_flight_ = true;
            std::thread([this, ic_ref, task_id, after_seq]() {
                const PolishPollResult result =
                    ipc_client_->pollPolishTask(task_id, after_seq);
                scheduleWithContext(
                    ic_ref, [this, ic_ref, task_id, result]() {
                        polish_poll_in_flight_ = false;
                        auto *ic_ptr = ic_ref.get();
                        if (!ic_ptr || !ic_ptr->hasFocus() ||
                            task_id != active_polish_task_id_) {
                            return;
                        }
                        handlePolishPollResult(ic_ptr, result);
                    });
            }).detach();
            return false;
        });
    polish_poll_timer_->setOneShot();
}

void VoCoTypeModule::handlePolishPollResult(
    fcitx::InputContext *ic,
    const PolishPollResult &result) {
    if (!result.success) {
        const std::string fallback = active_polish_original_;
        active_polish_task_id_.clear();
        polish_poll_timer_.reset();
        showError(ic,
                  result.error.empty() ? "润色任务失败" : result.error,
                  fallback);
        return;
    }

    active_polish_after_seq_ = result.last_seq;
    if (!result.original_text.empty()) {
        active_polish_original_ = result.original_text;
    }
    if (!result.preview.empty()) {
        active_polish_preview_ = result.preview;
    }

    std::string status_text =
        result.phase == "asr" ? "⏳ 识别中..." : "✨ 正在润色...";
    for (const auto &event : result.events) {
        if (event.kind == "status" && !event.text.empty()) {
            status_text = event.text;
        } else if (event.kind == "delta" && !event.preview.empty()) {
            active_polish_preview_ = event.preview;
        }
    }

    if (result.status == "final") {
        const std::string final_text = result.final_text.empty()
                                           ? active_polish_preview_
                                           : result.final_text;
        active_polish_task_id_.clear();
        polish_poll_timer_.reset();
        active_polish_preview_.clear();
        active_polish_original_.clear();
        active_polish_after_seq_ = 0;
        if (!final_text.empty()) {
            commitText(ic, final_text, strip_trailing_period_on_commit_);
        } else {
            clearOwnedUI(ic);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    if (result.status == "error" || result.status == "cancelled") {
        const std::string error = result.error.empty() ? "润色失败" : result.error;
        const std::string fallback = result.original_text.empty()
                                         ? active_polish_original_
                                         : result.original_text;
        active_polish_task_id_.clear();
        polish_poll_timer_.reset();
        active_polish_preview_.clear();
        active_polish_original_.clear();
        active_polish_after_seq_ = 0;
        showError(ic, error, fallback);
        return;
    }

    showPolishProgress(ic, status_text, active_polish_preview_,
                       active_polish_original_);
    schedulePolishPoll(ic->watch());
}

void VoCoTypeModule::showPolishProgress(
    fcitx::InputContext *ic,
    const std::string &status,
    const std::string &preview,
    const std::string &original_text) {
    stopPanelAnimation();
    pending_fallback_text_.clear();
    ui_owned_ = true;

    auto &panel = ic->inputPanel();
    panel.reset();
    fcitx::Text status_text;
    status_text.append(status.empty() ? "✨ 正在润色..." : status);
    panel.setAuxUp(status_text);

    auto candidates = std::make_unique<fcitx::CommonCandidateList>();
    candidates->setPageSize(2);
    fcitx::Text preview_text;
    preview_text.append(preview.empty() ? "等待模型输出..." : preview);
    candidates->append<fcitx::DisplayOnlyCandidateWord>(preview_text);
    if (!original_text.empty()) {
        fcitx::Text original;
        original.append(original_text);
        candidates->append<fcitx::DisplayOnlyCandidateWord>(original);
    }
    candidates->setGlobalCursorIndex(0);
    panel.setCandidateList(std::move(candidates));
    ic->updatePreedit();
    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
}

void VoCoTypeModule::cancelActivePolishTask() {
    polish_poll_timer_.reset();
    polish_poll_in_flight_ = false;
    if (!active_polish_task_id_.empty()) {
        const std::string task_id = active_polish_task_id_;
        // Never block the Fcitx key-event thread on backend cancellation and
        // never capture the module object in the detached request.
        std::thread([task_id]() {
            IPCClient client("/tmp/vocotype-fcitx5.sock");
            (void)client.cancelPolishTask(task_id);
        }).detach();
    }
    active_polish_task_id_.clear();
    active_polish_preview_.clear();
    active_polish_original_.clear();
    active_polish_after_seq_ = 0;
    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
}

void VoCoTypeModule::showPanelMessage(fcitx::InputContext *ic,
                                      const std::string &message) {
    if (!ic) {
        return;
    }
    ui_owned_ = true;
    auto &panel = ic->inputPanel();
    panel.reset();
    fcitx::Text text;
    text.append(message);
    panel.setAuxUp(text);
    ic->updatePreedit();
    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
}

void VoCoTypeModule::showAnimationFrame(fcitx::InputContext *ic) {
    const auto *frames = &RECORDING_ANIMATION_FRAMES;
    if (panel_animation_kind_ == PanelAnimationKind::RecordingLong) {
        frames = &LONG_RECORDING_ANIMATION_FRAMES;
    } else if (panel_animation_kind_ == PanelAnimationKind::Polishing) {
        frames = &POLISHING_ANIMATION_FRAMES;
    }
    showPanelMessage(
        ic, (*frames)[recording_animation_frame_index_ % frames->size()]);
    recording_animation_frame_index_ =
        (recording_animation_frame_index_ + 1) % frames->size();
}

void VoCoTypeModule::startPanelAnimation(fcitx::InputContext *ic,
                                         PanelAnimationKind kind) {
    stopPanelAnimation();
    panel_animation_kind_ = kind;
    showAnimationFrame(ic);

    auto ic_ref = ic->watch();
    auto schedule_next = std::make_shared<std::function<void()>>();
    *schedule_next = [this, ic_ref, schedule_next]() {
        recording_animation_timer_ = instance_->eventLoop().addTimeEvent(
            CLOCK_MONOTONIC,
            fcitx::now(CLOCK_MONOTONIC) + RECORDING_ANIMATION_INTERVAL_US, 0,
            [this, ic_ref, schedule_next](fcitx::EventSourceTime *, uint64_t) {
                recording_animation_timer_.reset();
                auto *ic_ptr = ic_ref.get();
                if (panel_animation_kind_ == PanelAnimationKind::None ||
                    !ic_ptr || !ic_ptr->hasFocus()) {
                    stopPanelAnimation();
                    return false;
                }
                showAnimationFrame(ic_ptr);
                (*schedule_next)();
                return false;
            });
        recording_animation_timer_->setOneShot();
    };
    (*schedule_next)();
}

void VoCoTypeModule::stopPanelAnimation() {
    recording_animation_timer_.reset();
    recording_animation_frame_index_ = 0;
    panel_animation_kind_ = PanelAnimationKind::None;
}

void VoCoTypeModule::clearOwnedUI(fcitx::InputContext *ic) {
    stopPanelAnimation();
    pending_fallback_text_.clear();
    if (!ic || !ui_owned_) {
        return;
    }
    ic->inputPanel().reset();
    ic->updatePreedit();
    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
    ui_owned_ = false;
}

void VoCoTypeModule::showError(fcitx::InputContext *ic,
                               const std::string &error,
                               const std::string &original_text) {
    stopPanelAnimation();
    if (!ic) {
        return;
    }

    if (original_text.empty()) {
        showPanelMessage(ic, "❌ " + error);
        return;
    }

    pending_fallback_text_ = original_text;
    ui_owned_ = true;
    auto &panel = ic->inputPanel();
    panel.reset();
    fcitx::Text title;
    title.append("❌ " + error);
    panel.setAuxUp(title);
    auto candidates = std::make_unique<fcitx::CommonCandidateList>();
    candidates->setPageSize(1);
    candidates->setSelectionKey({fcitx::Key(FcitxKey_1)});
    fcitx::Text original;
    original.append(original_text);
    candidates->append<fcitx::DisplayOnlyCandidateWord>(original);
    candidates->setGlobalCursorIndex(0);
    panel.setCandidateList(std::move(candidates));
    ic->updatePreedit();
    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
}

bool VoCoTypeModule::handlePendingFallbackKey(fcitx::KeyEvent &event) {
    if (pending_fallback_text_.empty()) {
        return false;
    }
    auto *ic = event.inputContext();
    if (!ic || active_ic_.get() != ic) {
        return false;
    }

    const auto sym = event.key().sym();
    const bool relevant = sym == FcitxKey_1 || sym == FcitxKey_space ||
                          sym == FcitxKey_Return || sym == FcitxKey_KP_Enter ||
                          sym == FcitxKey_Escape;
    if (!relevant) {
        pending_fallback_text_.clear();
        clearOwnedUI(ic);
        return false;
    }

    if (!event.isRelease()) {
        if (sym == FcitxKey_Escape) {
            pending_fallback_text_.clear();
            clearOwnedUI(ic);
        } else {
            std::string text = pending_fallback_text_;
            pending_fallback_text_.clear();
            commitText(ic, text);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    }
    event.filterAndAccept();
    return true;
}

bool VoCoTypeModule::pasteTextForClient(fcitx::InputContext *ic,
                                        const std::string &text) {
    const std::string program = toLower(ic->program());
    if (program.find("wechat") == std::string::npos) {
        return false;
    }

    const std::string session_type = toLower(
        std::getenv("XDG_SESSION_TYPE") ? std::getenv("XDG_SESSION_TYPE") : "");
    clearOwnedUI(ic);

    if (session_type == "x11") {
        auto ic_ref = ic->watch();
        std::thread([this, ic_ref, text]() {
            if (pasteTextToX11Client(text)) {
                return;
            }
            scheduleWithContext(
                ic_ref, [this, ic_ref, text]() {
                    auto *ic_ptr = ic_ref.get();
                    if (ic_ptr && ic_ptr->hasFocus()) {
                        ic_ptr->commitString(text);
                    }
                });
        }).detach();
        return true;
    }

    if (!copyTextToWaylandClipboard(text)) {
        return false;
    }
    ic->forwardKey(fcitx::Key(FcitxKey_v, fcitx::KeyState::Ctrl), false, 0);
    ic->forwardKey(fcitx::Key(FcitxKey_v, fcitx::KeyState::Ctrl), true, 0);
    return true;
}

void VoCoTypeModule::commitText(fcitx::InputContext *ic,
                                const std::string &text,
                                bool strip_trailing_period) {
    if (!ic || !ic->hasFocus()) {
        return;
    }
    const std::string commit_text = strip_trailing_period
                                        ? stripTrailingCommitPeriod(text)
                                        : text;
    const uint64_t now = fcitx::now(CLOCK_MONOTONIC);
    const std::string program = ic->program();
    const std::string frontend = ic->frontend() ? ic->frontend() : "";
    if (last_committed_ic_ == ic && last_committed_text_ == commit_text &&
        last_committed_program_ == program &&
        last_committed_frontend_ == frontend && now >= last_commit_time_us_ &&
        now - last_commit_time_us_ < DUPLICATE_COMMIT_SUPPRESS_US) {
        return;
    }

    if (!pasteTextForClient(ic, commit_text)) {
        clearOwnedUI(ic);
        ic->commitString(commit_text);
    }

    last_committed_ic_ = ic;
    last_committed_program_ = program;
    last_committed_frontend_ = frontend;
    last_committed_text_ = commit_text;
    last_commit_time_us_ = now;
}

} // namespace vocotype

class VoCoTypeModuleFactory final : public fcitx::AddonFactory {
public:
    fcitx::AddonInstance *create(fcitx::AddonManager *manager) override {
        return new vocotype::VoCoTypeModule(manager->instance());
    }
};

FCITX_ADDON_FACTORY(VoCoTypeModuleFactory);
