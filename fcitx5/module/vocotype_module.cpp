#include "vocotype_module.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <functional>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <thread>
#include <utility>

#include <fcntl.h>
#include <signal.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include <fcitx-config/iniparser.h>
#include <fcitx-utils/capabilityflags.h>
#include <fcitx-utils/event.h>
#include <fcitx-utils/eventdispatcher.h>
#include <fcitx-utils/log.h>
#if __has_include(<fcitx-utils/standardpaths.h>)
#include <fcitx-utils/standardpaths.h>
#define VOCOTYPE_HAS_STANDARD_PATHS 1
#else
#include <fcitx-utils/standardpath.h>
#define VOCOTYPE_HAS_STANDARD_PATHS 0
#endif
#include <fcitx-utils/utf8.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/candidatelist.h>
#include <fcitx/inputpanel.h>
#include <fcitx/text.h>
#include <fcitx/userinterface.h>
#include <nlohmann/json.hpp>

namespace {

constexpr auto FCITX_CONFIG_PATH = "conf/vocotype.conf";
constexpr uint64_t RECORDING_ANIMATION_INTERVAL_US = 200000;
constexpr uint64_t PTT_AUTOREPEAT_RELEASE_GRACE_US = 30000;
constexpr uint64_t POLISH_POLL_INTERVAL_US = 100000;
constexpr uint64_t DUPLICATE_COMMIT_SUPPRESS_US = 250000;

#if VOCOTYPE_HAS_STANDARD_PATHS
constexpr auto CONFIG_PATH_TYPE = fcitx::StandardPathsType::PkgConfig;
#else
constexpr auto CONFIG_PATH_TYPE = fcitx::StandardPath::Type::PkgConfig;
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

void editDebugLog(const std::string &message) {
    const char *path = std::getenv("VOCOTYPE_FCITX5_DEBUG_LOG");
    if (!path || *path == '\0') {
        return;
    }
    static std::mutex mutex;
    std::lock_guard<std::mutex> lock(mutex);
    std::ofstream stream(path, std::ios::app);
    if (stream) {
        stream << fcitx::now(CLOCK_MONOTONIC) << " " << message << "\n";
    }
}

std::string debugClip(std::string value, size_t limit = 160) {
    for (char &ch : value) {
        if (ch == '\n' || ch == '\r' || ch == '\t') {
            ch = ' ';
        }
    }
    if (value.size() > limit) {
        value.resize(limit);
        value += "...";
    }
    return value;
}

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

int acquireRecorderLock() {
    std::string lock_path;
    if (const char *runtime_dir = std::getenv("XDG_RUNTIME_DIR");
        runtime_dir && runtime_dir[0] == '/') {
        lock_path = std::string(runtime_dir) + "/vocotype-fcitx5-recorder.lock";
    } else {
        lock_path = "/tmp/vocotype-fcitx5-recorder-" +
                    std::to_string(static_cast<unsigned long>(getuid())) + ".lock";
    }

    int flags = O_RDWR | O_CREAT | O_CLOEXEC;
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const int fd = open(lock_path.c_str(), flags, 0600);
    if (fd < 0) {
        return -1;
    }
    struct stat metadata {};
    if (fstat(fd, &metadata) != 0 || !S_ISREG(metadata.st_mode) ||
        metadata.st_uid != getuid() || flock(fd, LOCK_EX | LOCK_NB) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

std::string finishRecorderProcess(
    pid_t pid, int stdin_fd, int lock_fd, std::thread output_thread,
    const std::shared_ptr<vocotype::RecorderOutputState> &output_state) {
    if (stdin_fd >= 0) {
        close(stdin_fd);
    }
    if (output_thread.joinable()) {
        output_thread.join();
    }
    if (pid > 0) {
        int status = 0;
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
        }
    }
    std::string audio_path;
    if (output_state) {
        std::lock_guard<std::mutex> lock(output_state->mutex);
        audio_path = output_state->audio_path;
    }
    if (lock_fd >= 0) {
        close(lock_fd);
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

std::string resolveBackendSocketPath() {
    if (const char *override_path = std::getenv("VOCOTYPE_FCITX5_SOCKET")) {
        if (*override_path != '\0') {
            return override_path;
        }
    }
    return "/tmp/vocotype-fcitx5.sock";
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
      backend_socket_path_(resolveBackendSocketPath()),
      ipc_client_(std::make_unique<IPCClient>(backend_socket_path_)) {
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
        fcitx::EventWatcherPhase::PostInputMethod,
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
    cancelPendingPttRelease();
    cancelPendingRecordingStart();
    cancelActiveVoiceEditTask();
    cancelActivePolishTask();
    clearPendingEditReplacement();
    edit_hint_timer_.reset();
    stopPanelAnimation();
    if (recorder_pid_ > 0 || recorder_stdin_fd_ >= 0 ||
        recorder_output_thread_.joinable()) {
        std::string audio_path = finishRecorderProcess(
            recorder_pid_, recorder_stdin_fd_, recorder_lock_fd_,
            std::move(recorder_output_thread_), recorder_output_state_);
        recorder_lock_fd_ = -1;
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
    min_recording_ms_ = std::max(0, config_.minRecordingMs.value());
    long_mode_modifier_ = modifier_state;
    polish_min_chars_ = std::max(0, config_.polishMinChars.value());
    polish_timeout_ms_ = std::max(1000, config_.polishTimeoutMs.value());
    enable_thinking_ = config_.enableThinking.value();
    block_when_composing_ = config_.blockWhenComposing.value();
    strip_trailing_period_on_commit_ =
        config_.stripTrailingPeriodOnCommit.value();
    animate_panel_ = toLower(config_.panelStyle.value()) == "animated";
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
    return static_cast<bool>(states & long_mode_modifier_);
}

bool VoCoTypeModule::editModeForStates(fcitx::KeyStates states) const {
    return static_cast<bool>(states & fcitx::KeyState::Ctrl);
}

std::string VoCoTypeModule::inputContextId(fcitx::InputContext *ic) {
    if (!ic) {
        return "unknown";
    }
    std::ostringstream stream;
    stream << std::hex << std::setfill('0');
    for (const auto byte : ic->uuid()) {
        stream << std::setw(2) << static_cast<unsigned int>(byte);
    }
    return stream.str();
}

bool VoCoTypeModule::captureVoiceEditSnapshot(
    fcitx::InputContext *ic,
    VoiceEditSnapshot &snapshot,
    std::string &error) const {
    snapshot = VoiceEditSnapshot();
    if (!ic ||
        !ic->capabilityFlags().test(fcitx::CapabilityFlag::SurroundingText)) {
        error = "当前输入框不支持获取输入内容";
        return false;
    }
    const auto &surrounding = ic->surroundingText();
    if (!surrounding.isValid()) {
        error = "当前输入框没有可用的上下文";
        return false;
    }
    snapshot.valid = true;
    snapshot.context_id = inputContextId(ic);
    snapshot.text = surrounding.text();
    snapshot.cursor = surrounding.cursor();
    snapshot.anchor = surrounding.anchor();
    snapshot.selected_text = surrounding.selectedText();
    return true;
}

bool VoCoTypeModule::voiceEditSnapshotStillMatches(
    fcitx::InputContext *ic,
    const VoiceEditSnapshot &snapshot) const {
    if (!ic || !snapshot.valid || !ic->hasFocus()) {
        return false;
    }
    const auto &surrounding = ic->surroundingText();
    return surrounding.isValid() && surrounding.text() == snapshot.text &&
           surrounding.cursor() == snapshot.cursor &&
           surrounding.anchor() == snapshot.anchor;
}

void VoCoTypeModule::showTemporaryMessage(fcitx::InputContext *ic,
                                           const std::string &message) {
    if (!ic || !ic->hasFocus()) {
        return;
    }
    edit_hint_timer_.reset();
    showPanelMessage(ic, message);
    auto ic_ref = ic->watch();
    edit_hint_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + 1200000ULL,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            edit_hint_timer_.reset();
            auto *ic_ptr = ic_ref.get();
            if (ic_ptr && ic_ptr->hasFocus()) {
                clearOwnedUI(ic_ptr);
            }
            return false;
        });
    edit_hint_timer_->setOneShot();
}

void VoCoTypeModule::runVoiceEditKeyActions(
    fcitx::InputContext *ic,
    const std::vector<EditKeyAction> &actions) {
    if (!ic || !ic->hasFocus()) {
        return;
    }
    for (const auto &action : actions) {
        fcitx::KeySym sym = FcitxKey_None;
        const std::string key = toLower(action.key);
        if (key == "left") {
            sym = FcitxKey_Left;
        } else if (key == "right") {
            sym = FcitxKey_Right;
        } else if (key == "up") {
            sym = FcitxKey_Up;
        } else if (key == "down") {
            sym = FcitxKey_Down;
        } else if (key == "home") {
            sym = FcitxKey_Home;
        } else if (key == "end") {
            sym = FcitxKey_End;
        } else if (key == "a") {
            sym = FcitxKey_a;
        } else if (key == "z") {
            sym = FcitxKey_z;
        }
        if (sym == FcitxKey_None) {
            FCITX_WARN() << "Unknown voice edit key action: " << action.key;
            continue;
        }

        fcitx::KeyStates states = fcitx::KeyState::NoState;
        for (const auto &modifier_value : action.modifiers) {
            const std::string modifier = toLower(modifier_value);
            if (modifier == "ctrl") {
                states |= fcitx::KeyState::Ctrl;
            } else if (modifier == "shift") {
                states |= fcitx::KeyState::Shift;
            } else if (modifier == "alt") {
                states |= fcitx::KeyState::Alt;
            } else if (modifier == "super") {
                states |= fcitx::KeyState::Super;
            }
        }
        const int repeat = std::clamp(action.repeat, 1, 20);
        for (int index = 0; index < repeat; ++index) {
            const fcitx::Key forwarded(sym, states);
            ic->forwardKey(forwarded, false, 0);
            ic->forwardKey(forwarded, true, 0);
        }
    }
}

void VoCoTypeModule::confirmVoiceEditApplied(
    const VoiceEditSnapshot &snapshot,
    const std::string &new_text,
    bool record_history) {
    const std::string socket_path = backend_socket_path_;
    std::thread([socket_path, snapshot, new_text, record_history]() {
        IPCClient client(socket_path);
        (void)client.confirmEditApplied(snapshot.context_id,
                                        snapshot.text,
                                        new_text,
                                        record_history);
    }).detach();
}

void VoCoTypeModule::clearPendingEditReplacement() {
    edit_replace_timer_.reset();
    edit_replace_pending_ = false;
    edit_replace_snapshot_ = VoiceEditSnapshot();
    edit_replace_new_text_.clear();
    edit_replace_hint_.clear();
    edit_replace_record_history_ = true;
    edit_replace_retries_left_ = 0;
}

void VoCoTypeModule::scheduleEditReplacementCheck() {
    edit_replace_timer_.reset();
    edit_replace_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + 40000ULL,
        0,
        [this](fcitx::EventSourceTime *, uint64_t) {
            edit_replace_timer_.reset();
            finalizeEditReplacement();
            return false;
        });
    edit_replace_timer_->setOneShot();
}

void VoCoTypeModule::replaceSurroundingText(
    fcitx::InputContext *ic,
    const VoiceEditSnapshot &snapshot,
    const std::string &new_text,
    bool record_history,
    const std::string &hint) {
    if (!voiceEditSnapshotStillMatches(ic, snapshot)) {
        showError(ic, "输入框内容已变化，请重试");
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (new_text == snapshot.text) {
        if (!hint.empty()) {
            showTemporaryMessage(ic, hint);
        } else {
            clearOwnedUI(ic);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    clearPendingEditReplacement();
    edit_replace_pending_ = true;
    edit_replace_snapshot_ = snapshot;
    edit_replace_new_text_ = new_text;
    edit_replace_hint_ = hint;
    edit_replace_record_history_ = record_history;
    edit_replace_retries_left_ = 4;
    const unsigned int original_length =
        static_cast<unsigned int>(fcitx::utf8::length(snapshot.text));
    ic->deleteSurroundingText(-static_cast<int>(snapshot.cursor),
                              original_length);
    scheduleEditReplacementCheck();
}

void VoCoTypeModule::finalizeEditReplacement() {
    if (!edit_replace_pending_) {
        return;
    }
    auto *ic = active_ic_.get();
    if (!ic || !ic->hasFocus()) {
        clearPendingEditReplacement();
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    const auto &surrounding = ic->surroundingText();
    const bool unchanged =
        surrounding.isValid() && !edit_replace_snapshot_.text.empty() &&
        surrounding.text() == edit_replace_snapshot_.text &&
        surrounding.cursor() == edit_replace_snapshot_.cursor;
    if (unchanged && edit_replace_retries_left_ > 0) {
        --edit_replace_retries_left_;
        scheduleEditReplacementCheck();
        return;
    }
    if (unchanged) {
        edit_replace_state_ = "unsupported";
        clearPendingEditReplacement();
        showError(ic, "当前输入框不支持替换文本");
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    edit_replace_state_ = "supported";
    const VoiceEditSnapshot snapshot = edit_replace_snapshot_;
    const std::string new_text = edit_replace_new_text_;
    const std::string hint = edit_replace_hint_;
    const bool record_history = edit_replace_record_history_;
    clearPendingEditReplacement();
    commitText(ic, new_text);
    confirmVoiceEditApplied(snapshot, new_text, record_history);
    if (!hint.empty()) {
        showTemporaryMessage(ic, hint);
    }
    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
}

void VoCoTypeModule::applyVoiceEditResult(
    fcitx::InputContext *ic,
    const VoiceEditSnapshot &snapshot,
    const VoiceEditResult &result) {
    if (!ic || !ic->hasFocus()) {
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (!result.success) {
        showVoiceEditFailure(
            ic,
            result.error.empty() ? "语音编辑失败" : result.error,
            result.instruction);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (!voiceEditSnapshotStillMatches(ic, snapshot)) {
        showError(ic, "输入框内容已变化，请重试");
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    if (result.mode == "key_actions") {
        clearOwnedUI(ic);
        runVoiceEditKeyActions(ic, result.key_actions);
        if (!result.hint.empty()) {
            showTemporaryMessage(ic, result.hint);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (result.mode == "no_replace") {
        if (!result.hint.empty()) {
            showTemporaryMessage(ic, result.hint);
        } else {
            clearOwnedUI(ic);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (result.mode == "commit_only") {
        if (!result.new_text.empty()) {
            commitText(ic, result.new_text);
            confirmVoiceEditApplied(
                snapshot,
                result.expected_text.empty() ? result.new_text
                                             : result.expected_text,
                result.record_history);
        }
        if (!result.hint.empty()) {
            showTemporaryMessage(ic, result.hint);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    replaceSurroundingText(ic,
                           snapshot,
                           result.new_text,
                           result.record_history,
                           result.hint);
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
    if (!active_voice_edit_task_id_.empty() && !event.isRelease()) {
        cancelActiveVoiceEditTask();
        clearOwnedUI(ic);
        if (key.sym() == FcitxKey_Escape) {
            event.filterAndAccept();
            return;
        }
    }
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
        // X11 autorepeat may arrive as a synthetic release immediately followed
        // by another press. Keep the current PTT session alive when that press
        // lands inside the release grace window. ReportKeyRepeat frontends also
        // mark repeated presses explicitly on rawKey().
        if (ptt_release_timer_) {
            cancelPendingPttRelease();
            event.filterAndAccept();
            return;
        }
        if (event.rawKey().states().test(fcitx::KeyState::Repeat)) {
            event.filterAndAccept();
            return;
        }
        if (!is_recording_ && !ptt_pressed_) {
            if (block_when_composing_ && hasActiveComposition(ic)) {
                FCITX_INFO() << "VoCoType hotkey ignored because current input method has active composition";
                event.filterAndAccept();
                return;
            }
            const bool edit_mode = editModeForStates(key.states());
            VoiceEditSnapshot edit_snapshot;
            if (edit_mode) {
                std::string error;
                if (!captureVoiceEditSnapshot(ic, edit_snapshot, error)) {
                    editDebugLog("native_snapshot_unavailable program=" + ic->program() +
                                 " frontend=" +
                                 std::string(ic->frontend() ? ic->frontend() : "") +
                                 " flags=" +
                                 std::to_string(ic->capabilityFlags().toInteger()) +
                                 " reason=" + error);
                    showError(ic,
                              error.empty()
                                  ? "当前输入框未通过输入法接口提供上下文"
                                  : error);
                    event.filterAndAccept();
                    return;
                }
            }
            if (edit_mode && edit_snapshot.valid) {
                editDebugLog("native_snapshot program=" + ic->program() +
                             " bytes=" + std::to_string(edit_snapshot.text.size()) +
                             " text=" + debugClip(edit_snapshot.text));
            }
            pending_ptt_states_ = key.states();
            active_ic_ = ic->watch();
            armPendingRecordingStart(
                ic,
                !edit_mode && polishModeForStates(key.states()),
                edit_mode,
                edit_snapshot);
        }
    } else if (is_recording_ || ptt_pressed_) {
        armPendingPttRelease(ic);
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

    cancelActiveVoiceEditTask();
    cancelActivePolishTask();
    clearPendingEditReplacement();
    edit_hint_timer_.reset();
    cancelPendingPttRelease();
    if (is_recording_) {
        FCITX_INFO() << "Input focus changed while recording; cancelling VoCoType session";
        stopRecording(false);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    } else {
        cancelPendingRecordingStart();
        clearOwnedUI(active);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    }
}

void VoCoTypeModule::armPendingRecordingStart(
    fcitx::InputContext *ic,
    bool long_mode,
    bool edit_mode,
    const VoiceEditSnapshot &edit_snapshot) {
    cancelPendingRecordingStart();
    ptt_pressed_ = true;
    pending_long_mode_ = long_mode;
    pending_edit_mode_ = edit_mode;
    pending_edit_snapshot_ = edit_snapshot;

    if (ptt_hold_threshold_ms_ <= 0) {
        startRecording(ic, long_mode, edit_mode, edit_snapshot);
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
            startRecording(ic_ptr,
                           pending_long_mode_,
                           pending_edit_mode_,
                           pending_edit_snapshot_);
            return false;
        });
    ptt_hold_timer_->setOneShot();
}

void VoCoTypeModule::cancelPendingRecordingStart() {
    ptt_pressed_ = false;
    ptt_suppressed_ = false;
    pending_long_mode_ = false;
    pending_edit_mode_ = false;
    pending_edit_snapshot_ = VoiceEditSnapshot();
    pending_ptt_states_ = fcitx::KeyState::NoState;
    ptt_hold_timer_.reset();
}

void VoCoTypeModule::armPendingPttRelease(fcitx::InputContext *ic) {
    cancelPendingPttRelease();
    auto ic_ref = ic->watch();
    ptt_release_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + PTT_AUTOREPEAT_RELEASE_GRACE_US,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            ptt_release_timer_.reset();
            auto *ic_ptr = ic_ref.get();
            if (is_recording_) {
                stopAndTranscribe();
            } else if (ptt_suppressed_) {
                cancelPendingRecordingStart();
            } else if (ptt_pressed_ && ic_ptr && ic_ptr->hasFocus()) {
                replayShortTapAsRegularKey(ic_ptr);
            } else {
                cancelPendingRecordingStart();
            }
            return false;
        });
    ptt_release_timer_->setOneShot();
}

void VoCoTypeModule::cancelPendingPttRelease() {
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

void VoCoTypeModule::startRecording(
    fcitx::InputContext *ic,
    bool long_mode,
    bool edit_mode,
    const VoiceEditSnapshot &edit_snapshot) {
    edit_hint_timer_.reset();
    cancelActiveVoiceEditTask();
    cancelActivePolishTask();
    if (is_recording_ || !ic || !ic->hasFocus()) {
        return;
    }
    if (recorder_launcher_path_.empty()) {
        showError(ic, "录音配置无效");
        return;
    }

    const int recorder_lock_fd = acquireRecorderLock();
    if (recorder_lock_fd < 0) {
        // A second module instance or a repeated key stream may observe the
        // same physical F9 press. Consume it until release, but never spawn a
        // second recorder or replay F9 into the client.
        ptt_suppressed_ = true;
        ptt_pressed_ = true;
        FCITX_WARN() << "Suppressed duplicate VoCoType recording start";
        return;
    }

    int stdin_pipe[2];
    int stdout_pipe[2];
    if (pipe(stdin_pipe) != 0) {
        close(recorder_lock_fd);
        showError(ic, "启动录音失败");
        return;
    }
    if (pipe(stdout_pipe) != 0) {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(recorder_lock_fd);
        showError(ic, "启动录音失败");
        return;
    }

    pid_t pid = fork();
    if (pid < 0) {
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        close(recorder_lock_fd);
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
        close(recorder_lock_fd);
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
        close(recorder_lock_fd);
        showError(ic, "启动录音失败");
        return;
    }

    active_voice_session_id_ = ++voice_session_counter_;
    recording_voice_session_id_ = active_voice_session_id_;
    editDebugLog("recording_start session=" +
                 std::to_string(recording_voice_session_id_) +
                 " edit=" + std::to_string(edit_mode) +
                 " context_bytes=" + std::to_string(edit_snapshot.text.size()));
    recorder_pid_ = pid;
    recorder_stdin_fd_ = stdin_pipe[1];
    recorder_lock_fd_ = recorder_lock_fd;
    recorder_output_state_ = std::make_shared<RecorderOutputState>();
    is_recording_ = true;
    recording_started_us_ = fcitx::now(CLOCK_MONOTONIC);
    ptt_pressed_ = true;
    ptt_suppressed_ = false;
    streaming_preview_visible_ = false;
    streaming_preview_text_.clear();
    recording_status_text_.clear();
    pending_long_mode_ = false;
    pending_edit_mode_ = false;
    pending_edit_snapshot_ = VoiceEditSnapshot();
    recording_long_mode_ = long_mode;
    recording_edit_mode_ = edit_mode;
    recording_edit_snapshot_ = edit_snapshot;
    active_ic_ = ic->watch();
    const uint64_t generation = ++recording_generation_;
    auto output_state = recorder_output_state_;
    auto ic_ref = active_ic_;
    recorder_output_thread_ = std::thread(
        [this, stdout_file, output_state, ic_ref, generation]() {
            char buffer[65536];
            while (fgets(buffer, sizeof(buffer), stdout_file) != nullptr) {
                std::string line(buffer);
                while (!line.empty() &&
                       (line.back() == '\n' || line.back() == '\r')) {
                    line.pop_back();
                }
                if (line.empty()) {
                    continue;
                }
                try {
                    const auto event = nlohmann::json::parse(line);
                    const std::string type = event.value("type", "");
                    if (type == "audio") {
                        std::lock_guard<std::mutex> lock(output_state->mutex);
                        output_state->audio_path = event.value("path", "");
                    } else if (type == "partial") {
                        const std::string text = event.value("text", "");
                        if (!text.empty()) {
                            scheduleWithContext(
                                ic_ref, [this, ic_ref, generation, text]() {
                                    auto *ic_ptr = ic_ref.get();
                                    if (!ic_ptr || !ic_ptr->hasFocus() ||
                                        !is_recording_ ||
                                        generation != recording_generation_) {
                                        return;
                                    }
                                    showStreamingPreview(ic_ptr, text);
                                });
                        }
                    }
                } catch (const std::exception &) {
                    // Backward compatibility with an older recorder that emits
                    // one plain path rather than JSON-lines.
                    if (!line.empty() && line.front() == '/') {
                        std::lock_guard<std::mutex> lock(output_state->mutex);
                        output_state->audio_path = line;
                    }
                }
            }
            fclose(stdout_file);
        });

    if (long_mode || edit_mode) {
        std::thread([this]() { (void)ipc_client_->prewarmSlm(); }).detach();
    }
    if (edit_mode) {
        const std::string replace_flag =
            edit_replace_state_ == "supported"
                ? "ok"
                : (edit_replace_state_ == "unsupported" ? "no" : "?");
        showVoiceEditStatusBar(
            ic,
            "🎤 编辑中(del=" + replace_flag +
                " sur=1 sel=" +
                std::to_string(edit_snapshot.selected_text.size()) +
                " active=1)",
            "松开 Ctrl+F9 后识别编辑指令");
    } else if (animate_panel_) {
        startPanelAnimation(
            ic,
            long_mode ? PanelAnimationKind::RecordingLong
                      : PanelAnimationKind::Recording);

    } else {
        recording_status_text_ =
            long_mode ? "🎤 录音中(长句)..." : "🎤 录音中...";
        renderRecordingPanel(ic, recording_status_text_);
    }
}

void VoCoTypeModule::stopAndTranscribe() {
    stopRecording(true);
}

void VoCoTypeModule::stopRecording(bool transcribe) {
    cancelPendingPttRelease();
    if (!is_recording_) {
        return;
    }

    const uint64_t stopped_at_us = fcitx::now(CLOCK_MONOTONIC);
    const uint64_t elapsed_us =
        recording_started_us_ > 0 && stopped_at_us >= recording_started_us_
            ? stopped_at_us - recording_started_us_
            : 0;
    const bool recording_too_short =
        transcribe && min_recording_ms_ > 0 &&
        elapsed_us < static_cast<uint64_t>(min_recording_ms_) * 1000ULL;
    if (recording_too_short) {
        transcribe = false;
    }
    recording_started_us_ = 0;

    // A settled physical PTT release ends the listening UI. The recorder
    // process and ASR continue off the Fcitx event thread after this point.
    stopPanelAnimation();
    streaming_preview_visible_ = false;
    streaming_preview_text_.clear();
    recording_status_text_.clear();
    ptt_hold_timer_.reset();
    ptt_release_timer_.reset();
    ptt_pressed_ = false;
    pending_long_mode_ = false;
    pending_edit_mode_ = false;
    pending_edit_snapshot_ = VoiceEditSnapshot();
    is_recording_ = false;
    const bool long_mode = recording_long_mode_;
    const bool edit_mode = recording_edit_mode_;
    const VoiceEditSnapshot edit_snapshot = recording_edit_snapshot_;
    const uint64_t session_id = recording_voice_session_id_;
    recording_long_mode_ = false;
    recording_edit_mode_ = false;
    recording_edit_snapshot_ = VoiceEditSnapshot();
    recording_voice_session_id_ = 0;

    auto ic_ref = active_ic_;
    auto *ic = ic_ref.get();
    if (ic && recording_too_short && ic->hasFocus()) {
        showTemporaryMessage(
            ic,
            "⚠️ 录音过短（至少 " + std::to_string(min_recording_ms_) +
                " ms）");
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    } else if (ic && transcribe && ic->hasFocus()) {
        // Release is a synchronous UI boundary: never leave the recording
        // animation visible while the recorder process is flushing.
        if (edit_mode) {
            showVoiceEditStatusBar(
                ic,
                "✍️ 正在识别编辑指令...",
                "指令：等待识别结果...");
        } else {
            showPanelMessage(ic, "⏳ 识别中");
        }

    } else if (ic) {
        clearOwnedUI(ic);
    }

    pid_t pid = recorder_pid_;
    int stdin_fd = recorder_stdin_fd_;
    int lock_fd = recorder_lock_fd_;
    auto output_thread = std::move(recorder_output_thread_);
    auto output_state = std::move(recorder_output_state_);
    recorder_pid_ = -1;
    recorder_stdin_fd_ = -1;
    recorder_lock_fd_ = -1;

    std::thread([this, pid, stdin_fd, lock_fd,
                 output_thread = std::move(output_thread),
                 output_state = std::move(output_state), transcribe, long_mode,
                 edit_mode, edit_snapshot, session_id, ic_ref]() mutable {
        std::string audio_path = finishRecorderProcess(
            pid, stdin_fd, lock_fd, std::move(output_thread), output_state);
        if (!transcribe) {
            if (!audio_path.empty()) {
                std::remove(audio_path.c_str());
            }
            if (long_mode || edit_mode) {
                (void)ipc_client_->releaseSlm();
            }
            return;
        }

        if (edit_mode) {
            scheduleWithContext(
                ic_ref,
                [this, ic_ref, audio_path, edit_snapshot, session_id]() mutable {
                    auto *ic_ptr = ic_ref.get();
                    if (!ic_ptr || !ic_ptr->hasFocus() || session_id == 0 ||
                        session_id != active_voice_session_id_) {
                        if (!audio_path.empty()) {
                            std::remove(audio_path.c_str());
                        }
                        return;
                    }
                    if (audio_path.empty()) {
                        showVoiceEditFailure(ic_ptr, "录音失败", "");
                        active_voice_session_id_ = 0;
                        active_ic_ =
                            fcitx::TrackableObjectReference<fcitx::InputContext>();
                        return;
                    }
                    if (!edit_snapshot.valid) {
                        std::remove(audio_path.c_str());
                        showVoiceEditFailure(
                            ic_ptr, "编辑上下文无效，请重试", "");
                        active_voice_session_id_ = 0;
                        active_ic_ =
                            fcitx::TrackableObjectReference<fcitx::InputContext>();
                        return;
                    }
                    launchVoiceEditTask(
                        ic_ref, audio_path, edit_snapshot, session_id);
                });
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
                            const std::string socket_path = backend_socket_path_;
                            std::thread([socket_path, task_id]() {
                                IPCClient client(socket_path);
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

void VoCoTypeModule::launchVoiceEditTask(
    fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref,
    const std::string &audio_path,
    const VoiceEditSnapshot &snapshot,
    uint64_t session_id) {
    std::thread([this, ic_ref, audio_path, snapshot, session_id]() {
        VoiceEditStartResult start_result;
        if (audio_path.empty()) {
            start_result.error = "录音失败";
        } else {
            start_result = ipc_client_->startVoiceEdit(
                audio_path,
                snapshot.context_id,
                snapshot.text,
                snapshot.cursor,
                snapshot.anchor,
                snapshot.selected_text,
                edit_replace_state_,
                true);
            // A successful async start transfers ownership of the audio file
            // to the backend task. Failed starts leave cleanup to the module.
            if (!start_result.success || start_result.task_id.empty()) {
                std::remove(audio_path.c_str());
            }
        }

        scheduleWithContext(
            ic_ref,
            [this, ic_ref, snapshot, session_id, start_result]() {
                auto *ic_ptr = ic_ref.get();
                const bool current =
                    session_id != 0 && session_id == active_voice_session_id_;
                if (!ic_ptr || !ic_ptr->hasFocus() || !current) {
                    if (start_result.success && !start_result.task_id.empty()) {
                        const std::string socket_path = backend_socket_path_;
                        const std::string task_id = start_result.task_id;
                        std::thread([socket_path, task_id]() {
                            IPCClient client(socket_path);
                            (void)client.cancelVoiceEditTask(task_id);
                        }).detach();
                    }
                    return;
                }
                if (!start_result.success || start_result.task_id.empty()) {
                    showVoiceEditFailure(
                        ic_ptr,
                        start_result.error.empty()
                            ? "启动语音编辑任务失败"
                            : start_result.error,
                        "");
                    active_voice_session_id_ = 0;
                    active_ic_ =
                        fcitx::TrackableObjectReference<fcitx::InputContext>();
                    return;
                }
                startVoiceEditPolling(
                    ic_ptr, start_result.task_id, snapshot, session_id);
            });
    }).detach();
}

void VoCoTypeModule::startVoiceEditPolling(
    fcitx::InputContext *ic,
    const std::string &task_id,
    const VoiceEditSnapshot &snapshot,
    uint64_t session_id) {
    stopPanelAnimation();
    active_voice_edit_task_id_ = task_id;
    active_voice_edit_session_id_ = session_id;
    active_voice_edit_instruction_.clear();
    editDebugLog("edit_task_start id=" + task_id +
                 " session=" + std::to_string(session_id));
    active_voice_edit_snapshot_ = snapshot;
    voice_edit_poll_in_flight_ = false;
    voice_edit_poll_timer_.reset();
    showVoiceEditProgress(ic, "asr", "");
    scheduleVoiceEditPoll(ic->watch());
}

void VoCoTypeModule::scheduleVoiceEditPoll(
    fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref) {
    if (active_voice_edit_task_id_.empty() || voice_edit_poll_timer_ ||
        voice_edit_poll_in_flight_) {
        return;
    }
    voice_edit_poll_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + POLISH_POLL_INTERVAL_US,
        0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            voice_edit_poll_timer_.reset();
            if (active_voice_edit_task_id_.empty() ||
                voice_edit_poll_in_flight_) {
                return false;
            }
            const std::string task_id = active_voice_edit_task_id_;
            const uint64_t session_id = active_voice_edit_session_id_;
            voice_edit_poll_in_flight_ = true;
            std::thread([this, ic_ref, task_id, session_id]() {
                const VoiceEditPollResult result =
                    ipc_client_->pollVoiceEditTask(task_id);
                scheduleWithContext(
                    ic_ref, [this, ic_ref, task_id, session_id, result]() {
                        voice_edit_poll_in_flight_ = false;
                        auto *ic_ptr = ic_ref.get();
                        if (!ic_ptr || !ic_ptr->hasFocus() ||
                            task_id != active_voice_edit_task_id_ ||
                            session_id == 0 ||
                            session_id != active_voice_edit_session_id_ ||
                            session_id != active_voice_session_id_) {
                            return;
                        }
                        handleVoiceEditPollResult(ic_ptr, result);
                    });
            }).detach();
            return false;
        });
    voice_edit_poll_timer_->setOneShot();
}

void VoCoTypeModule::handleVoiceEditPollResult(
    fcitx::InputContext *ic,
    const VoiceEditPollResult &result) {
    if (!result.instruction.empty() &&
        result.instruction != active_voice_edit_instruction_) {
        active_voice_edit_instruction_ = result.instruction;
        editDebugLog("edit_instruction id=" + active_voice_edit_task_id_ +
                     " text=" + debugClip(result.instruction));
    }
    if (!result.success) {
        const std::string instruction = active_voice_edit_instruction_;
        active_voice_edit_task_id_.clear();
        active_voice_edit_snapshot_ = VoiceEditSnapshot();
        voice_edit_poll_timer_.reset();
        showVoiceEditFailure(
            ic,
            result.error.empty() ? "读取语音编辑任务失败" : result.error,
            instruction);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    if (result.status == "final") {
        editDebugLog("edit_task_final id=" + active_voice_edit_task_id_ +
                     " instruction=" + debugClip(active_voice_edit_instruction_) +
                     " result_bytes=" + std::to_string(result.result.new_text.size()));
        VoiceEditResult edit_result = result.result;
        if (edit_result.instruction.empty()) {
            edit_result.instruction = active_voice_edit_instruction_;
        }
        const VoiceEditSnapshot snapshot = active_voice_edit_snapshot_;
        active_voice_edit_task_id_.clear();
        active_voice_edit_instruction_.clear();
        active_voice_edit_snapshot_ = VoiceEditSnapshot();
        voice_edit_poll_timer_.reset();
        applyVoiceEditResult(ic, snapshot, edit_result);
        return;
    }

    if (result.status == "error" || result.status == "cancelled") {
        editDebugLog("edit_task_error id=" + active_voice_edit_task_id_ +
                     " error=" + result.error +
                     " instruction=" + debugClip(active_voice_edit_instruction_));
        const std::string instruction = active_voice_edit_instruction_;
        active_voice_edit_task_id_.clear();
        active_voice_edit_instruction_.clear();
        active_voice_edit_snapshot_ = VoiceEditSnapshot();
        voice_edit_poll_timer_.reset();
        showVoiceEditFailure(
            ic,
            result.error.empty() ? "语音编辑失败" : result.error,
            instruction);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    showVoiceEditProgress(
        ic,
        result.phase.empty() ? "asr" : result.phase,
        active_voice_edit_instruction_);
    scheduleVoiceEditPoll(ic->watch());
}

void VoCoTypeModule::showVoiceEditStatusBar(
    fcitx::InputContext *ic,
    const std::string &title,
    const std::string &detail) {
    if (!ic || !ic->hasFocus()) {
        return;
    }
    stopPanelAnimation();
    pending_fallback_text_.clear();
    ui_owned_ = true;
    auto &panel = ic->inputPanel();
    panel.reset();

    // Fcitx's KDE UI does not open an input-panel window for aux text alone.
    // Panel preedit is UI-only (unlike clientPreedit), so it creates the same
    // floating status bar as the old IBus auxiliary status without inserting
    // anything into the application.
    fcitx::Text preedit;
    preedit.append(title);
    panel.setPreedit(preedit);
    if (!detail.empty()) {
        fcitx::Text auxiliary;
        auxiliary.append(detail);
        panel.setAuxDown(auxiliary);
    }
    ic->updatePreedit();
    ic->updateUserInterface(
        fcitx::UserInterfaceComponent::InputPanel, true);
}

void VoCoTypeModule::showVoiceEditProgress(
    fcitx::InputContext *ic,
    const std::string &phase,
    const std::string &instruction) {
    showVoiceEditStatusBar(
        ic,
        phase == "asr" ? "✍️ 正在识别编辑指令..."
                       : "✨ 正在执行编辑...",
        instruction.empty() ? "指令：等待识别结果..."
                            : "指令：" + instruction);
}

void VoCoTypeModule::showVoiceEditFailure(
    fcitx::InputContext *ic,
    const std::string &error,
    const std::string &instruction) {
    showVoiceEditStatusBar(
        ic,
        "❌ " + error,
        instruction.empty() ? "" : "识别指令：" + instruction);
}

void VoCoTypeModule::cancelActiveVoiceEditTask() {
    voice_edit_poll_timer_.reset();
    voice_edit_poll_in_flight_ = false;
    if (!active_voice_edit_task_id_.empty()) {
        const std::string socket_path = backend_socket_path_;
        const std::string task_id = active_voice_edit_task_id_;
        std::thread([socket_path, task_id]() {
            IPCClient client(socket_path);
            (void)client.cancelVoiceEditTask(task_id);
        }).detach();
    }
    active_voice_edit_task_id_.clear();
    active_voice_edit_instruction_.clear();
    active_voice_edit_snapshot_ = VoiceEditSnapshot();
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
    showPanelMessage(ic, "⏳ 识别中");
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
        const std::string socket_path = backend_socket_path_;
        std::thread([socket_path, task_id]() {
            IPCClient client(socket_path);
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

void VoCoTypeModule::renderRecordingPanel(
    fcitx::InputContext *ic, const std::string &status) {
    if (!ic || status.empty()) {
        return;
    }
    ui_owned_ = true;
    auto &panel = ic->inputPanel();
    panel.reset();

    // Use the same two-row contract as voice editing: recording state on the
    // first row, online partial on the second row. Keeping the preview out of
    // client preedit avoids composition/focus side effects.
    fcitx::Text status_text;
    status_text.append(status);
    panel.setPreedit(status_text);
    if (!streaming_preview_text_.empty()) {
        fcitx::Text preview;
        preview.append(streaming_preview_text_);
        panel.setAuxDown(preview);
    }
    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
}

void VoCoTypeModule::showStreamingPreview(
    fcitx::InputContext *ic, const std::string &text) {
    if (!ic || text.empty()) {
        return;
    }
    if (min_recording_ms_ > 0) {
        const uint64_t now_us = fcitx::now(CLOCK_MONOTONIC);
        if (recording_started_us_ == 0 || now_us < recording_started_us_ ||
            now_us - recording_started_us_ <
                static_cast<uint64_t>(min_recording_ms_) * 1000ULL) {
            return;
        }
    }
    streaming_preview_visible_ = true;
    streaming_preview_text_ = text;
    if (recording_status_text_.empty()) {
        recording_status_text_ = recording_long_mode_
                                     ? "🎤 录音中(长句)..."
                                     : "🎤 录音中...";
    }
    renderRecordingPanel(ic, recording_status_text_);
}

void VoCoTypeModule::showAnimationFrame(fcitx::InputContext *ic) {
    const auto *frames = &RECORDING_ANIMATION_FRAMES;
    if (panel_animation_kind_ == PanelAnimationKind::RecordingLong) {
        frames = &LONG_RECORDING_ANIMATION_FRAMES;
    } else if (panel_animation_kind_ == PanelAnimationKind::Polishing) {
        frames = &POLISHING_ANIMATION_FRAMES;
    }
    recording_status_text_ =
        (*frames)[recording_animation_frame_index_ % frames->size()];
    renderRecordingPanel(ic, recording_status_text_);
    recording_animation_frame_index_ =
        (recording_animation_frame_index_ + 1) % frames->size();
}

void VoCoTypeModule::startPanelAnimation(fcitx::InputContext *ic,
                                         PanelAnimationKind kind) {
    stopPanelAnimation();
    if (!ic || !ic->hasFocus()) {
        return;
    }
    streaming_preview_visible_ = false;
    streaming_preview_text_.clear();
    recording_status_text_.clear();
    panel_animation_kind_ = kind;
    showAnimationFrame(ic);
    schedulePanelAnimationFrame(ic->watch(), panel_animation_generation_);
}

void VoCoTypeModule::schedulePanelAnimationFrame(
    fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref,
    uint64_t generation) {
    if (generation != panel_animation_generation_ ||
        panel_animation_kind_ == PanelAnimationKind::None ||
        recording_animation_timer_) {
        return;
    }

    recording_animation_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) + RECORDING_ANIMATION_INTERVAL_US, 0,
        [this, ic_ref, generation](fcitx::EventSourceTime *, uint64_t) {
            recording_animation_timer_.reset();
            if (generation != panel_animation_generation_) {
                return false;
            }
            auto *ic_ptr = ic_ref.get();
            if (panel_animation_kind_ == PanelAnimationKind::None ||
                !ic_ptr || !ic_ptr->hasFocus()) {
                stopPanelAnimation();
                return false;
            }
            showAnimationFrame(ic_ptr);
            schedulePanelAnimationFrame(ic_ref, generation);
            return false;
        });
    recording_animation_timer_->setOneShot();
}

void VoCoTypeModule::stopPanelAnimation() {
    recording_animation_timer_.reset();
    recording_animation_frame_index_ = 0;
    panel_animation_kind_ = PanelAnimationKind::None;
    ++panel_animation_generation_;
}

void VoCoTypeModule::clearOwnedUI(fcitx::InputContext *ic) {
    stopPanelAnimation();
    streaming_preview_visible_ = false;
    streaming_preview_text_.clear();
    recording_status_text_.clear();
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
