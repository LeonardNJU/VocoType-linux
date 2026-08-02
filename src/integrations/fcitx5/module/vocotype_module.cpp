#include "vocotype_module.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <functional>
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

#ifndef VOCOTYPE_FCITX5_RECORDER_PATH
#define VOCOTYPE_FCITX5_RECORDER_PATH ""
#endif
#ifndef VOCOTYPE_FCITX5_BACKEND_PATH
#define VOCOTYPE_FCITX5_BACKEND_PATH ""
#endif

namespace {

constexpr auto FCITX_CONFIG_PATH = "conf/vocotype.conf";
constexpr uint64_t RECORDING_ANIMATION_INTERVAL_US = 200000;
constexpr uint64_t PTT_AUTOREPEAT_RELEASE_GRACE_US = 30000;
constexpr uint64_t POLISH_POLL_INTERVAL_US = 100000;
constexpr uint64_t FINAL_ASR_WATCHDOG_US = 120000000;
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
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
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

bool launchDetached(const std::string &executable) {
  if (executable.empty() || access(executable.c_str(), X_OK) != 0) {
    return false;
  }
  const pid_t child = fork();
  if (child < 0) {
    return false;
  }
  if (child == 0) {
    (void)setsid();
    const int null_fd = open("/dev/null", O_RDWR | O_CLOEXEC);
    if (null_fd >= 0) {
      dup2(null_fd, STDIN_FILENO);
      dup2(null_fd, STDOUT_FILENO);
      dup2(null_fd, STDERR_FILENO);
      if (null_fd > STDERR_FILENO) {
        close(null_fd);
      }
    }
    execl(executable.c_str(), executable.c_str(), static_cast<char *>(nullptr));
    _exit(127);
  }
  std::thread([child]() {
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
  }).detach();
  return true;
}

std::string configuredBackendLauncher() {
  if (const char *override_path = std::getenv("VOCOTYPE_FCITX5_BACKEND")) {
    if (*override_path != '\0' && access(override_path, X_OK) == 0) {
      return override_path;
    }
  }
  const std::string compiled = VOCOTYPE_FCITX5_BACKEND_PATH;
  if (!compiled.empty() && access(compiled.c_str(), X_OK) == 0) {
    return compiled;
  }
  return {};
}

bool startBackendUserService() {
    std::string manager;
    for (const char *candidate : {"/usr/bin/systemctl", "/bin/systemctl"}) {
        if (access(candidate, X_OK) == 0) {
            manager = candidate;
            break;
        }
    }
  if (!manager.empty()) {
    const pid_t child = fork();
    if (child >= 0) {
    if (child == 0) {
        const std::string runtime =
            "/run/user/" + std::to_string(static_cast<unsigned long>(getuid()));
        if (!std::getenv("XDG_RUNTIME_DIR") &&
            access(runtime.c_str(), F_OK) == 0) {
            setenv("XDG_RUNTIME_DIR", runtime.c_str(), 1);
        }
        const std::string bus = runtime + "/bus";
        if (!std::getenv("DBUS_SESSION_BUS_ADDRESS") &&
            access(bus.c_str(), F_OK) == 0) {
            const std::string address = "unix:path=" + bus;
            setenv("DBUS_SESSION_BUS_ADDRESS", address.c_str(), 1);
        }
        execl(manager.c_str(), manager.c_str(), "--user", "start",
              "vocotype-fcitx5-backend.service", static_cast<char *>(nullptr));
        _exit(127);
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
      if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        return true;
      }
    }
  }
  return launchDetached(configuredBackendLauncher());
}

bool waitForBackend(const std::string &socket_path, int timeout_ms) {
    vocotype::IPCClient client(socket_path);
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (std::chrono::steady_clock::now() < deadline) {
        if (client.ping()) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    return false;
}

void markAsrPrepareAttempt(
    const std::shared_ptr<vocotype::AsrPrewarmState> &state) {
    std::lock_guard<std::mutex> lock(state->mutex);
    if (!state->first_attempt_done) {
        state->first_attempt_done = true;
        state->changed.notify_all();
    }
}

bool waitForAsrPrepare(
    const std::shared_ptr<vocotype::AsrPrewarmState> &state,
    std::chrono::milliseconds timeout) {
    if (!state) {
        return true;
    }
    std::unique_lock<std::mutex> lock(state->mutex);
    return state->changed.wait_for(
        lock, timeout, [&state] { return state->first_attempt_done; });
}

std::string resolveBackendSocketPath() {
    if (const char *override_path = std::getenv("VOCOTYPE_FCITX5_SOCKET")) {
        if (*override_path != '\0') {
            return override_path;
        }
    }
    return "/tmp/vocotype-fcitx5.sock";
}

} // namespace

namespace vocotype {

VoCoTypeModule::VoCoTypeModule(fcitx::Instance *instance)
    : instance_(instance), backend_socket_path_(resolveBackendSocketPath()),
      ipc_client_(std::make_unique<IPCClient>(backend_socket_path_)) {
    event_dispatcher_.attach(&instance_->eventLoop());
  if (const char *override_path = std::getenv("VOCOTYPE_FCITX5_RECORDER")) {
    if (*override_path != '\0' && access(override_path, X_OK) == 0) {
      recorder_launcher_path_ = override_path;
    }
  }
  const std::string compiled_recorder = VOCOTYPE_FCITX5_RECORDER_PATH;
  if (recorder_launcher_path_.empty() && !compiled_recorder.empty() &&
      access(compiled_recorder.c_str(), X_OK) == 0) {
    recorder_launcher_path_ = compiled_recorder;
  }
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
      fcitx::EventWatcherPhase::PreInputMethod, [this](fcitx::Event &event) {
            handleKeyEvent(static_cast<fcitx::KeyEvent &>(event));
        });
    focus_out_handler_ = instance_->watchEvent(
        fcitx::EventType::InputContextFocusOut,
      fcitx::EventWatcherPhase::PostInputMethod, [this](fcitx::Event &event) {
            handleFocusOut(static_cast<fcitx::InputContextEvent &>(event));
        });

  FCITX_INFO() << "VoCoType global module initialized; hotkeys="
               << hotkey_summary_;
    if (!ipc_client_->ping()) {
    FCITX_WARN()
        << "VoCoType backend is not responding; requesting user service start";
        const std::string socket_path = backend_socket_path_;
        std::thread([socket_path]() {
            (void)startBackendUserService();
            (void)waitForBackend(socket_path, 45000);
        }).detach();
    }
}

VoCoTypeModule::~VoCoTypeModule() {
    (void)stopAsrPrewarm();
    cancelPendingPttRelease();
    cancelPendingRecordingStart();
    cancelActiveVoiceEditTask();
    cancelActivePolishTask();
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
  fcitx::readAsIni(config_, CONFIG_PATH_TYPE, FCITX_CONFIG_PATH);
    applyConfig();
}

void VoCoTypeModule::save() {
  if (!fcitx::safeSaveAsIni(config_, CONFIG_PATH_TYPE, FCITX_CONFIG_PATH)) {
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
  auto transcribe = config_.pttKey.value().normalize();
  auto polish = config_.polishKey.value().normalize();
  auto edit = config_.editKey.value().normalize();
  const fcitx::Key default_transcribe(FcitxKey_F9);
  const fcitx::Key default_polish(FcitxKey_F9, fcitx::KeyState::Shift);
  const fcitx::Key default_edit(FcitxKey_F9, fcitx::KeyState::Ctrl);
  if (!transcribe.isValid()) {
    transcribe = default_transcribe;
  }
  if (!polish.isValid()) {
    polish = default_polish;
  }
  if (!edit.isValid()) {
    edit = default_edit;
  }
  if (hotkeyIsUnsafe(transcribe) || hotkeyIsUnsafe(polish) ||
      hotkeyIsUnsafe(edit) || transcribe == polish || transcribe == edit ||
      polish == edit) {
    FCITX_WARN()
        << "VoCoType shortcut is unsafe or duplicated; restoring defaults";
    transcribe = default_transcribe;
    polish = default_polish;
    edit = default_edit;
  }

  transcribe_key_ = transcribe;
  polish_key_ = polish;
  edit_key_ = edit;
  hotkey_summary_ = transcribe_key_.toString() + " / " +
                    polish_key_.toString() + " / " + edit_key_.toString();
    ptt_hold_threshold_ms_ = config_.pttHoldThresholdMs.value();
    min_recording_ms_ = std::max(0, config_.minRecordingMs.value());
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

bool VoCoTypeModule::hotkeyMatches(const fcitx::Key &key,
                                   const fcitx::Key &configured) {
  if (!configured.isValid()) {
    return false;
  }
  return key.normalize().check(configured.normalize());
}

bool VoCoTypeModule::hotkeyReleaseMatches(const fcitx::Key &key,
                                          const fcitx::Key &configured) {
  if (!configured.isValid() || key.sym() != configured.sym()) {
    return false;
  }
  if (fcitx::Key::keySymToStates(configured.sym()) !=
      fcitx::KeyState::NoState) {
    return key.isReleaseOfModifier(configured) ||
           hotkeyMatches(key, configured);
  }
  return true;
}

bool VoCoTypeModule::hotkeyIsUnsafe(const fcitx::Key &configured) {
  const auto key = configured.normalize();
  if (!key.isValid()) {
    return true;
  }
  const auto states = key.states();
  const bool strong_modifier = states.testAny(fcitx::KeyStates{
      fcitx::KeyState::Ctrl, fcitx::KeyState::Alt, fcitx::KeyState::Super,
      fcitx::KeyState::Hyper, fcitx::KeyState::Meta});
  const auto sym = key.sym();
  if (!strong_modifier && sym >= FcitxKey_space && sym <= FcitxKey_asciitilde) {
    return true;
  }
  if (!strong_modifier) {
    switch (sym) {
    case FcitxKey_space:
    case FcitxKey_Tab:
    case FcitxKey_ISO_Left_Tab:
    case FcitxKey_Return:
    case FcitxKey_KP_Enter:
    case FcitxKey_BackSpace:
    case FcitxKey_Delete:
    case FcitxKey_Insert:
    case FcitxKey_Left:
    case FcitxKey_Right:
    case FcitxKey_Up:
    case FcitxKey_Down:
    case FcitxKey_Home:
    case FcitxKey_End:
    case FcitxKey_Page_Up:
    case FcitxKey_Page_Down:
    case FcitxKey_Escape:
    case FcitxKey_Caps_Lock:
    case FcitxKey_Num_Lock:
    case FcitxKey_Scroll_Lock:
    case FcitxKey_Shift_L:
    case FcitxKey_Shift_R:
    case FcitxKey_Control_L:
    case FcitxKey_Alt_L:
    case FcitxKey_Super_L:
    case FcitxKey_Meta_L:
    case FcitxKey_Hyper_L:
      return true;
    default:
      break;
    }
  }
  const std::string normalized = toLower(key.toString());
  static const std::unordered_set<std::string> reserved = {
      "control+a", "control+c", "control+f", "control+n",  "control+o",
      "control+p", "control+q", "control+r", "control+s",  "control+t",
      "control+v", "control+w", "control+x", "control+z",  "control+space",
      "alt+f4",    "alt+tab",   "super+l",   "super+space"};
  return reserved.contains(normalized);
}

VoCoTypeModule::VoiceHotkeyMode
VoCoTypeModule::hotkeyModeForKey(const fcitx::Key &key) const {
  if (hotkeyMatches(key, edit_key_)) {
    return VoiceHotkeyMode::Edit;
  }
  if (hotkeyMatches(key, polish_key_)) {
    return VoiceHotkeyMode::Polish;
  }
  if (hotkeyMatches(key, transcribe_key_)) {
    return VoiceHotkeyMode::Transcribe;
  }
  return VoiceHotkeyMode::None;
}

const fcitx::Key &VoCoTypeModule::hotkeyForMode(VoiceHotkeyMode mode) const {
  switch (mode) {
  case VoiceHotkeyMode::Polish:
    return polish_key_;
  case VoiceHotkeyMode::Edit:
    return edit_key_;
  case VoiceHotkeyMode::Transcribe:
  case VoiceHotkeyMode::None:
    return transcribe_key_;
  }
  return transcribe_key_;
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

bool VoCoTypeModule::captureVoiceEditSnapshot(fcitx::InputContext *ic,
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
    fcitx::InputContext *ic, const VoiceEditSnapshot &snapshot) const {
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
      CLOCK_MONOTONIC, fcitx::now(CLOCK_MONOTONIC) + 1200000ULL, 0,
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
    fcitx::InputContext *ic, const std::vector<EditKeyAction> &actions) {
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
        } else if (key == "pageup") {
            sym = FcitxKey_Page_Up;
        } else if (key == "pagedown") {
            sym = FcitxKey_Page_Down;
        } else if (key == "backspace") {
            sym = FcitxKey_BackSpace;
        } else if (key == "delete") {
            sym = FcitxKey_Delete;
        } else if (key == "enter") {
            sym = FcitxKey_Return;
        } else if (key == "tab") {
            sym = FcitxKey_Tab;
        } else if (key == "escape") {
            sym = FcitxKey_Escape;
        } else if (key == "space") {
            sym = FcitxKey_space;
        } else if (key == "a") {
            sym = FcitxKey_a;
        } else if (key == "c") {
            sym = FcitxKey_c;
        } else if (key == "v") {
            sym = FcitxKey_v;
        } else if (key == "x") {
            sym = FcitxKey_x;
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
        const int repeat = std::clamp(action.repeat, 1, 100);
        for (int index = 0; index < repeat; ++index) {
            const fcitx::Key forwarded(sym, states);
            ic->forwardKey(forwarded, false, 0);
            ic->forwardKey(forwarded, true, 0);
        }
    }
}

void VoCoTypeModule::confirmVoiceEditApplied(const VoiceEditSnapshot &snapshot,
    const std::string &new_text,
    bool record_history) {
    const std::string socket_path = backend_socket_path_;
    std::thread([socket_path, snapshot, new_text, record_history]() {
        IPCClient client(socket_path);
    (void)client.confirmEditApplied(snapshot.context_id, snapshot.text,
                                    new_text, record_history);
    }).detach();
}

void VoCoTypeModule::replaceSurroundingText(fcitx::InputContext *ic,
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

    // deleteSurroundingText is an ordered input-method request, while the
    // surrounding-text snapshot is an asynchronously refreshed cache. GTK
    // clients such as gedit may refresh that cache only after this callback.
    // Waiting for the cache can therefore delete the source and suppress the
    // replacement. Validate before editing, then issue delete and commit in
    // the same input-method transaction.
    const unsigned int original_length =
        static_cast<unsigned int>(fcitx::utf8::length(snapshot.text));
    clearOwnedUI(ic);
    ic->deleteSurroundingText(-static_cast<int>(snapshot.cursor),
                              original_length);
    commitText(ic, new_text);
    confirmVoiceEditApplied(snapshot, new_text, record_history);
    if (!hint.empty()) {
        showTemporaryMessage(ic, hint);
    }
    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
}

void VoCoTypeModule::applyVoiceEditResult(fcitx::InputContext *ic,
    const VoiceEditSnapshot &snapshot,
    const VoiceEditResult &result) {
    if (!ic || !ic->hasFocus()) {
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }
    if (!result.success) {
    showVoiceEditFailure(ic,
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
    if (result.mode == "no_op" || result.mode == "no_replace") {
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
          result.expected_text.empty() ? result.new_text : result.expected_text,
                result.record_history);
        }
        if (!result.hint.empty()) {
            showTemporaryMessage(ic, result.hint);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

  replaceSurroundingText(ic, snapshot, result.new_text, result.record_history,
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
    if ((transcription_start_pending_ || !active_polish_task_id_.empty()) &&
        !event.isRelease()) {
        cancelActivePolishTask();
        clearOwnedUI(ic);
        if (key.sym() == FcitxKey_Escape) {
            event.filterAndAccept();
            return;
        }
    }

  if (event.isRelease()) {
    if ((is_recording_ || ptt_pressed_) &&
        hotkeyReleaseMatches(key, active_hotkey_)) {
      armPendingPttRelease(ic);
      event.filterAndAccept();
    }
        return;
    }

  const auto mode = hotkeyModeForKey(key);
  if (mode == VoiceHotkeyMode::None) {
    return;
  }
  const auto &configured_hotkey = hotkeyForMode(mode);

        // X11 autorepeat may arrive as a synthetic release immediately followed
        // by another press. Keep the current PTT session alive when that press
        // lands inside the release grace window. ReportKeyRepeat frontends also
        // mark repeated presses explicitly on rawKey().
  if (ptt_release_timer_ && hotkeyMatches(key, active_hotkey_)) {
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
      FCITX_INFO() << "VoCoType hotkey ignored because current input method "
                      "has active composition";
                event.filterAndAccept();
                return;
            }
    const bool edit_mode = mode == VoiceHotkeyMode::Edit;
            VoiceEditSnapshot edit_snapshot;
            if (edit_mode) {
                std::string error;
                if (!captureVoiceEditSnapshot(ic, edit_snapshot, error)) {
        editDebugLog(
            "native_snapshot_unavailable program=" + ic->program() +
            " frontend=" + std::string(ic->frontend() ? ic->frontend() : "") +
            " flags=" + std::to_string(ic->capabilityFlags().toInteger()) +
                                 " reason=" + error);
        showError(ic, error.empty() ? "当前输入框未通过输入法接口提供上下文"
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
            active_ic_ = ic->watch();
    armPendingRecordingStart(ic, mode == VoiceHotkeyMode::Polish, edit_mode,
                             edit_snapshot, key, configured_hotkey);
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
    edit_hint_timer_.reset();
    cancelPendingPttRelease();
    if (is_recording_) {
    FCITX_INFO()
        << "Input focus changed while recording; cancelling VoCoType session";
        stopRecording(false);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    } else {
        cancelPendingRecordingStart();
        clearOwnedUI(active);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    }
}

void VoCoTypeModule::startAsrPrewarm() {
    (void)stopAsrPrewarm();
    auto state = std::make_shared<AsrPrewarmState>();
    asr_prewarm_ = state;
    const std::string socket_path = backend_socket_path_;
    const auto backend_start_pending = backend_start_pending_;
    std::thread([state, socket_path, backend_start_pending]() {
        IPCClient client(socket_path);
        bool backend_ready = client.ping();
        if (!backend_ready) {
            bool expected = false;
            const bool owns_start_gate =
                backend_start_pending->compare_exchange_strong(
                    expected, true, std::memory_order_acq_rel);
            if (owns_start_gate) {
                (void)startBackendUserService();
            }
            backend_ready = waitForBackend(socket_path, 45000);
            if (owns_start_gate) {
                backend_start_pending->store(false, std::memory_order_release);
            }
        }
        if (!backend_ready) {
            markAsrPrepareAttempt(state);
            return;
        }
        while (state->active.load(std::memory_order_acquire)) {
            (void)client.prepareAsr(45000);
            markAsrPrepareAttempt(state);
            for (int tick = 0; tick < 80 &&
                               state->active.load(std::memory_order_acquire);
                 ++tick) {
                std::this_thread::sleep_for(std::chrono::milliseconds(250));
            }
        }
    }).detach();
}

std::shared_ptr<AsrPrewarmState> VoCoTypeModule::stopAsrPrewarm() {
    auto state = std::move(asr_prewarm_);
    if (state) {
        state->active.store(false, std::memory_order_release);
    }
    return state;
}

void VoCoTypeModule::armPendingRecordingStart(
    fcitx::InputContext *ic, bool long_mode, bool edit_mode,
    const VoiceEditSnapshot &edit_snapshot, const fcitx::Key &pressed_key,
    const fcitx::Key &configured_hotkey) {
    cancelPendingRecordingStart();
    pending_ptt_key_ = pressed_key.normalize();
    active_hotkey_ = configured_hotkey;
    ptt_pressed_ = true;
    pending_long_mode_ = long_mode;
    pending_edit_mode_ = edit_mode;
    pending_edit_snapshot_ = edit_snapshot;
    startAsrPrewarm();

    if (ptt_hold_threshold_ms_ <= 0) {
        startRecording(ic, long_mode, edit_mode, edit_snapshot);
        return;
    }

    auto ic_ref = ic->watch();
    ptt_hold_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
        fcitx::now(CLOCK_MONOTONIC) +
            static_cast<uint64_t>(ptt_hold_threshold_ms_) * 1000ULL,
      0, [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            ptt_hold_timer_.reset();
            auto *ic_ptr = ic_ref.get();
            if (!ptt_pressed_ || is_recording_ || !ic_ptr || !ic_ptr->hasFocus()) {
                cancelPendingRecordingStart();
                return false;
            }
        startRecording(ic_ptr, pending_long_mode_, pending_edit_mode_,
                           pending_edit_snapshot_);
            return false;
        });
    ptt_hold_timer_->setOneShot();
}

void VoCoTypeModule::cancelPendingRecordingStart() {
    (void)stopAsrPrewarm();
    ptt_pressed_ = false;
    ptt_suppressed_ = false;
    pending_long_mode_ = false;
    pending_edit_mode_ = false;
    pending_edit_snapshot_ = VoiceEditSnapshot();
  pending_ptt_key_ = fcitx::Key();
  active_hotkey_ = fcitx::Key();
    ptt_hold_timer_.reset();
}

void VoCoTypeModule::armPendingPttRelease(fcitx::InputContext *ic) {
    cancelPendingPttRelease();
    auto ic_ref = ic->watch();
    ptt_release_timer_ = instance_->eventLoop().addTimeEvent(
        CLOCK_MONOTONIC,
      fcitx::now(CLOCK_MONOTONIC) + PTT_AUTOREPEAT_RELEASE_GRACE_US, 0,
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

void VoCoTypeModule::cancelPendingPttRelease() { ptt_release_timer_.reset(); }

void VoCoTypeModule::replayShortTapAsRegularKey(fcitx::InputContext *ic) {
  const auto key = pending_ptt_key_;
    cancelPendingRecordingStart();
    active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
  if (!key.isValid()) {
    return;
  }
    const int time = 0;
  ic->forwardKey(key, false, time);
  ic->forwardKey(key, true, time);
}

void VoCoTypeModule::startRecording(fcitx::InputContext *ic, bool long_mode,
    bool edit_mode,
    const VoiceEditSnapshot &edit_snapshot) {
    edit_hint_timer_.reset();
    cancelActiveVoiceEditTask();
    cancelActivePolishTask();
    if (is_recording_ || !ic || !ic->hasFocus()) {
        return;
    }
    if (recorder_launcher_path_.empty()) {
        (void)stopAsrPrewarm();
        showError(ic, "录音配置无效");
        return;
    }
    if (!ipc_client_->ping()) {
        // Audio capture must begin immediately. The recording-time prewarm
        // thread starts the backend, while the recorder's preview loop retries
        // the socket independently.
        showPanelMessage(ic, "🎤 正在录音并准备语音后台...");
    }

    const int recorder_lock_fd = acquireRecorderLock();
    if (recorder_lock_fd < 0) {
        (void)stopAsrPrewarm();
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
        (void)stopAsrPrewarm();
        close(recorder_lock_fd);
        showError(ic, "启动录音失败");
        return;
    }
    if (pipe(stdout_pipe) != 0) {
        (void)stopAsrPrewarm();
        close(stdin_pipe[0]);
        close(stdin_pipe[1]);
        close(recorder_lock_fd);
        showError(ic, "启动录音失败");
        return;
    }

    pid_t pid = fork();
    if (pid < 0) {
        (void)stopAsrPrewarm();
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
        (void)stopAsrPrewarm();
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
  editDebugLog(
      "recording_start session=" + std::to_string(recording_voice_session_id_) +
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
  recorder_output_thread_ = std::thread([this, stdout_file, output_state,
                                         ic_ref, generation]() {
            char buffer[65536];
            while (fgets(buffer, sizeof(buffer), stdout_file) != nullptr) {
                std::string line(buffer);
      while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) {
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
            scheduleWithContext(ic_ref, [this, ic_ref, generation, text]() {
                                    auto *ic_ptr = ic_ref.get();
              if (!ic_ptr || !ic_ptr->hasFocus() || !is_recording_ ||
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
    }
    if (edit_mode) {
    showVoiceEditStatusBar(ic, "🎤 语音编辑中...",
                               "松开 Ctrl+F9 后识别编辑指令");
    } else if (animate_panel_) {
    startPanelAnimation(ic, long_mode ? PanelAnimationKind::RecordingLong
                      : PanelAnimationKind::Recording);

    } else {
    recording_status_text_ = long_mode ? "🎤 录音中(长句)..." : "🎤 录音中...";
        renderRecordingPanel(ic, recording_status_text_);
    }
}

void VoCoTypeModule::stopAndTranscribe() { stopRecording(true); }

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
    const auto asr_prewarm = stopAsrPrewarm();
    if (transcribe && !edit_mode) {
        transcription_start_pending_ = true;
    } else if (!transcribe) {
        active_voice_session_id_ = 0;
    }
    recording_long_mode_ = false;
    recording_edit_mode_ = false;
    recording_edit_snapshot_ = VoiceEditSnapshot();
    recording_voice_session_id_ = 0;

    auto ic_ref = active_ic_;
    auto *ic = ic_ref.get();
    if (ic && recording_too_short && ic->hasFocus()) {
    showTemporaryMessage(ic, "⚠️ 录音过短（至少 " +
                                 std::to_string(min_recording_ms_) + " ms）");
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
    } else if (ic && transcribe && ic->hasFocus()) {
        // Release is a synchronous UI boundary: never leave the recording
        // animation visible while the recorder process is flushing.
        if (edit_mode) {
      showVoiceEditStatusBar(ic, "✍️ 正在识别编辑指令...",
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
                 edit_mode, edit_snapshot, session_id, ic_ref,
                 asr_prewarm]() mutable {
        std::string audio_path = finishRecorderProcess(
            pid, stdin_fd, lock_fd, std::move(output_thread), output_state);
        if (!transcribe) {
            if (!audio_path.empty()) {
                std::remove(audio_path.c_str());
            }
            if (long_mode || edit_mode) {
            }
            return;
        }

        (void)waitForAsrPrepare(asr_prewarm,
                                std::chrono::milliseconds(45000));

        if (edit_mode) {
      scheduleWithContext(ic_ref, [this, ic_ref, audio_path, edit_snapshot,
                                   session_id]() mutable {
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
          active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                        return;
                    }
                    if (!edit_snapshot.valid) {
                        std::remove(audio_path.c_str());
          showVoiceEditFailure(ic_ptr, "编辑上下文无效，请重试", "");
                        active_voice_session_id_ = 0;
          active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                        return;
                    }
        launchVoiceEditTask(ic_ref, audio_path, edit_snapshot, session_id);
                });
            return;
        }

        TranscribeStartResult start_result;
        if (audio_path.empty()) {
            start_result.error = "录音失败";
        } else {
            start_result = ipc_client_->startTranscription(
                audio_path, long_mode, polish_min_chars_, polish_timeout_ms_,
                enable_thinking_);
            // A successful async start transfers ownership of the WAV to Core.
            // Failed starts leave cleanup to the frontend.
            if (!start_result.success || start_result.task_id.empty()) {
                std::remove(audio_path.c_str());
            }
        }

        event_dispatcher_.schedule(
            [this, ic_ref, start_result, long_mode, session_id]() {
                auto *ic_ptr = ic_ref.get();
                const bool current =
                    transcription_start_pending_ && session_id != 0 &&
                    session_id == active_voice_session_id_;
                if (!ic_ptr || !ic_ptr->hasFocus() || !current) {
                    if (start_result.success && !start_result.task_id.empty()) {
                        const std::string socket_path = backend_socket_path_;
                        const std::string task_id = start_result.task_id;
                        std::thread([socket_path, task_id]() {
                            IPCClient client(socket_path);
                            (void)client.cancelPolishTask(task_id);
                        }).detach();
                    }
                    if (current) {
                        transcription_start_pending_ = false;
                        active_voice_session_id_ = 0;
                        active_ic_ =
                            fcitx::TrackableObjectReference<fcitx::InputContext>();
                    }
                    return;
                }

                transcription_start_pending_ = false;
                if (start_result.success && !start_result.task_id.empty()) {
                    startPolishPolling(ic_ptr, start_result.task_id, long_mode,
                                       session_id);
                } else {
                    active_voice_session_id_ = 0;
                    showError(ic_ptr,
                              start_result.error.empty() ? "转录启动失败"
                                                         : start_result.error);
                    active_ic_ =
                        fcitx::TrackableObjectReference<fcitx::InputContext>();
                }
            });
    }).detach();
}

void VoCoTypeModule::launchVoiceEditTask(
    fcitx::TrackableObjectReference<fcitx::InputContext> ic_ref,
    const std::string &audio_path, const VoiceEditSnapshot &snapshot,
    uint64_t session_id) {
    std::thread([this, ic_ref, audio_path, snapshot, session_id]() {
        VoiceEditStartResult start_result;
        if (audio_path.empty()) {
            start_result.error = "录音失败";
        } else {
            start_result = ipc_client_->startVoiceEdit(
          audio_path, snapshot.context_id, snapshot.text, snapshot.cursor,
          snapshot.anchor, snapshot.selected_text, "supported", true);
            // A successful async start transfers ownership of the audio file
            // to the backend task. Failed starts leave cleanup to the module.
            if (!start_result.success || start_result.task_id.empty()) {
                std::remove(audio_path.c_str());
            }
        }

    scheduleWithContext(ic_ref, [this, ic_ref, snapshot, session_id,
                                 start_result]() {
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
        showVoiceEditFailure(ic_ptr,
                             start_result.error.empty() ? "启动语音编辑任务失败"
                            : start_result.error,
                        "");
                    active_voice_session_id_ = 0;
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
                    return;
                }
      startVoiceEditPolling(ic_ptr, start_result.task_id, snapshot, session_id);
            });
    }).detach();
}

void VoCoTypeModule::startVoiceEditPolling(fcitx::InputContext *ic,
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
      CLOCK_MONOTONIC, fcitx::now(CLOCK_MONOTONIC) + POLISH_POLL_INTERVAL_US, 0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            voice_edit_poll_timer_.reset();
        if (active_voice_edit_task_id_.empty() || voice_edit_poll_in_flight_) {
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
                    task_id != active_voice_edit_task_id_ || session_id == 0 ||
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
    fcitx::InputContext *ic, const VoiceEditPollResult &result) {
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
        ic, result.error.empty() ? "读取语音编辑任务失败" : result.error,
            instruction);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    if (result.status == "final") {
    editDebugLog(
        "edit_task_final id=" + active_voice_edit_task_id_ +
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
        ic, result.error.empty() ? "语音编辑失败" : result.error, instruction);
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

  showVoiceEditProgress(ic, result.phase.empty() ? "asr" : result.phase,
        active_voice_edit_instruction_);
    scheduleVoiceEditPoll(ic->watch());
}

void VoCoTypeModule::showVoiceEditStatusBar(fcitx::InputContext *ic,
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
  ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel, true);
}

void VoCoTypeModule::showVoiceEditProgress(fcitx::InputContext *ic,
    const std::string &phase,
    const std::string &instruction) {
    showVoiceEditStatusBar(
      ic, phase == "asr" ? "✍️ 正在识别编辑指令..." : "✨ 正在执行编辑...",
      instruction.empty() ? "指令：等待识别结果..." : "指令：" + instruction);
}

void VoCoTypeModule::showVoiceEditFailure(fcitx::InputContext *ic,
    const std::string &error,
    const std::string &instruction) {
  showVoiceEditStatusBar(ic, "❌ " + error,
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
                                           const std::string &task_id,
                                           bool polish_enabled,
                                           uint64_t session_id) {
    stopPanelAnimation();
    active_polish_task_id_ = task_id;
    active_polish_enabled_ = polish_enabled;
    active_polish_session_id_ = session_id;
    active_polish_preview_.clear();
    active_polish_original_.clear();
    active_polish_after_seq_ = 0;
    active_polish_started_us_ = fcitx::now(CLOCK_MONOTONIC);
    polish_poll_in_flight_ = false;
    polish_poll_timer_.reset();
    showPanelMessage(
        ic, polish_enabled ? "⏳ 识别中"
                           : "⏳ 识别中（按 Esc 或继续输入可取消）");
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
        fcitx::now(CLOCK_MONOTONIC) + POLISH_POLL_INTERVAL_US, 0,
        [this, ic_ref](fcitx::EventSourceTime *, uint64_t) {
            polish_poll_timer_.reset();
            if (active_polish_task_id_.empty() || polish_poll_in_flight_) {
                return false;
            }

            auto *ic_ptr = ic_ref.get();
            const uint64_t now_us = fcitx::now(CLOCK_MONOTONIC);
            const uint64_t watchdog_us =
                active_polish_enabled_
                    ? static_cast<uint64_t>(polish_timeout_ms_ + 120000) *
                          1000ULL
                    : FINAL_ASR_WATCHDOG_US;
            const bool watchdog_expired =
                active_polish_started_us_ > 0 &&
                now_us >= active_polish_started_us_ &&
                now_us - active_polish_started_us_ >= watchdog_us;
            if (watchdog_expired) {
                const bool was_polish = active_polish_enabled_;
                cancelActivePolishTask();
                if (ic_ptr && ic_ptr->hasFocus()) {
                    showTemporaryMessage(
                        ic_ptr, was_polish ? "❌ 润色任务超时，已取消"
                                           : "❌ 识别超时，已取消");
                }
                return false;
            }

            const std::string task_id = active_polish_task_id_;
            const int after_seq = active_polish_after_seq_;
            const uint64_t session_id = active_polish_session_id_;
            polish_poll_in_flight_ = true;
            std::thread([this, ic_ref, task_id, after_seq, session_id]() {
                const PolishPollResult result =
                    ipc_client_->pollPolishTask(task_id, after_seq);
                event_dispatcher_.schedule(
                    [this, ic_ref, task_id, session_id, result]() {
                        auto *target = ic_ref.get();
                        const bool current =
                            task_id == active_polish_task_id_ &&
                            session_id != 0 &&
                            session_id == active_polish_session_id_ &&
                            session_id == active_voice_session_id_;
                        // A cancelled task may finish its in-flight poll after a
                        // newer voice session has started. Never let that stale
                        // callback clear the new session's in-flight guard.
                        if (!current) {
                            return;
                        }
                        polish_poll_in_flight_ = false;
                        if (!target || !target->hasFocus()) {
                            cancelActivePolishTask();
                            return;
                        }
                        handlePolishPollResult(target, result);
                    });
            }).detach();
            return false;
        });
    polish_poll_timer_->setOneShot();
}

void VoCoTypeModule::handlePolishPollResult(
    fcitx::InputContext *ic, const PolishPollResult &result) {
    const bool polish_enabled = active_polish_enabled_;
    if (!result.success) {
        const std::string fallback =
            polish_enabled ? active_polish_original_ : std::string();
        active_polish_task_id_.clear();
        active_polish_enabled_ = false;
        active_polish_session_id_ = 0;
        active_polish_preview_.clear();
        active_polish_original_.clear();
        active_polish_after_seq_ = 0;
        active_polish_started_us_ = 0;
        polish_poll_timer_.reset();
        active_voice_session_id_ = 0;
        showError(ic,
                  result.error.empty()
                      ? (polish_enabled ? "润色任务失败" : "识别任务失败")
                      : result.error,
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

    for (const auto &event : result.events) {
        if (event.kind == "delta" && !event.preview.empty()) {
            active_polish_preview_ = event.preview;
        }
    }

    if (result.status == "final") {
        const std::string final_text =
            result.final_text.empty() ? active_polish_preview_
                                      : result.final_text;
        active_polish_task_id_.clear();
        active_polish_enabled_ = false;
        active_polish_session_id_ = 0;
        polish_poll_timer_.reset();
        active_polish_preview_.clear();
        active_polish_original_.clear();
        active_polish_after_seq_ = 0;
        active_polish_started_us_ = 0;
        active_voice_session_id_ = 0;
        if (!final_text.empty()) {
            commitText(ic, final_text, strip_trailing_period_on_commit_);
        } else {
            clearOwnedUI(ic);
        }
        active_ic_ = fcitx::TrackableObjectReference<fcitx::InputContext>();
        return;
    }

    if (result.status == "error" || result.status == "cancelled") {
        const std::string fallback =
            polish_enabled
                ? (result.original_text.empty() ? active_polish_original_
                                                : result.original_text)
                : std::string();
        const std::string error =
            result.error.empty()
                ? (result.status == "cancelled"
                       ? (polish_enabled ? "润色已取消" : "识别已取消")
                       : (polish_enabled ? "润色失败" : "识别失败"))
                : result.error;
        active_polish_task_id_.clear();
        active_polish_enabled_ = false;
        active_polish_session_id_ = 0;
        polish_poll_timer_.reset();
        active_polish_preview_.clear();
        active_polish_original_.clear();
        active_polish_after_seq_ = 0;
        active_polish_started_us_ = 0;
        active_voice_session_id_ = 0;
        showError(ic, error, fallback);
        return;
    }

    if (polish_enabled) {
        showPolishProgress(ic, active_polish_preview_, active_polish_original_);
    }
    schedulePolishPoll(ic->watch());
}

void VoCoTypeModule::showPolishProgress(fcitx::InputContext *ic,
    const std::string &preview,
    const std::string &original_text) {
    stopPanelAnimation();
    pending_fallback_text_.clear();
    ui_owned_ = true;

    auto &panel = ic->inputPanel();
    panel.reset();
    const uint64_t now_us = fcitx::now(CLOCK_MONOTONIC);
    const uint64_t elapsed_us =
        active_polish_started_us_ == 0 || now_us < active_polish_started_us_
            ? 0
            : now_us - active_polish_started_us_;
    const int elapsed_seconds = static_cast<int>(elapsed_us / 1000000ULL);
    const int timeout_seconds = std::max(1, polish_timeout_ms_ / 1000);

    fcitx::Text status_text;
    status_text.append("正在润色... （等待模型输出 " +
                       std::to_string(elapsed_seconds) + "s/" +
                       std::to_string(timeout_seconds) + "s）");
    panel.setAuxUp(status_text);

    auto candidates = std::make_unique<fcitx::CommonCandidateList>();
    candidates->setPageSize(2);
    fcitx::Text original;
    original.append(original_text.empty() ? "粗识别文本：等待识别结果..."
                                          : "粗识别文本：" + original_text);
    candidates->append<fcitx::DisplayOnlyCandidateWord>(original);
    if (!preview.empty()) {
        fcitx::Text preview_text;
        preview_text.append(preview);
        candidates->append<fcitx::DisplayOnlyCandidateWord>(preview_text);
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
    transcription_start_pending_ = false;
    active_polish_task_id_.clear();
    active_polish_enabled_ = false;
    active_polish_session_id_ = 0;
    active_polish_preview_.clear();
    active_polish_original_.clear();
    active_polish_after_seq_ = 0;
    active_polish_started_us_ = 0;
    active_voice_session_id_ = 0;
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

void VoCoTypeModule::renderRecordingPanel(fcitx::InputContext *ic,
                                          const std::string &status) {
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

void VoCoTypeModule::showStreamingPreview(fcitx::InputContext *ic,
                                          const std::string &text) {
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
    recording_status_text_ =
        recording_long_mode_ ? "🎤 录音中(长句)..." : "🎤 录音中...";
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
        if (panel_animation_kind_ == PanelAnimationKind::None || !ic_ptr ||
            !ic_ptr->hasFocus()) {
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

void VoCoTypeModule::commitText(fcitx::InputContext *ic,
                                const std::string &text,
                                bool strip_trailing_period) {
    if (!ic || !ic->hasFocus()) {
        return;
    }
  const std::string commit_text =
      strip_trailing_period ? stripTrailingCommitPeriod(text) : text;
    const uint64_t now = fcitx::now(CLOCK_MONOTONIC);
    const std::string program = ic->program();
    const std::string frontend = ic->frontend() ? ic->frontend() : "";
    if (last_committed_ic_ == ic && last_committed_text_ == commit_text &&
        last_committed_program_ == program &&
        last_committed_frontend_ == frontend && now >= last_commit_time_us_ &&
        now - last_commit_time_us_ < DUPLICATE_COMMIT_SUPPRESS_US) {
        return;
    }

    clearOwnedUI(ic);
    ic->commitString(commit_text);

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
