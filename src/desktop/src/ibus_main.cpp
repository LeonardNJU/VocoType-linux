#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/hotkey.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/recorder_process.hpp"
#include "vocotype/desktop/task_status.hpp"
#include "vocotype/desktop/rime_session.hpp"

#include <ibus.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>

using vocotype::desktop::Hotkey;
using vocotype::desktop::Json;

#ifndef VOCOTYPE_VERSION
#define VOCOTYPE_VERSION "development"
#endif

namespace {

struct Snapshot {
  bool valid = false;
  std::string text;
  std::string selected;
  guint cursor = 0;
  guint anchor = 0;
};

enum class VoiceMode {
  none,
  transcribe,
  polish,
  edit,
};

const Hotkey kDefaultTranscribeHotkey = vocotype::desktop::parse_hotkey("F9");
const Hotkey kDefaultPolishHotkey = vocotype::desktop::parse_hotkey("Shift+F9");
const Hotkey kDefaultEditHotkey = vocotype::desktop::parse_hotkey("Ctrl+F9");

struct EngineState {
  std::mutex recorder_mutex;
  std::unique_ptr<vocotype::desktop::RecorderProcess> recorder;
  std::unique_ptr<vocotype::desktop::RimeSession> rime;
  std::atomic_bool recording{false};
  std::atomic_bool busy{false};
  std::atomic_bool focused{false};
  std::atomic_bool enabled{false};
  std::atomic_uint64_t generation{0};
  std::chrono::steady_clock::time_point recording_started;
  bool long_mode = false;
  bool edit_mode = false;
  Hotkey transcribe_hotkey = kDefaultTranscribeHotkey;
  Hotkey polish_hotkey = kDefaultPolishHotkey;
  Hotkey edit_hotkey = kDefaultEditHotkey;
  Hotkey active_hotkey = kDefaultTranscribeHotkey;
  Snapshot snapshot;
  std::string socket = vocotype::desktop::backend_socket_path();
  std::string recorder_path;
  int min_recording_ms = 1000;
  int polish_min_chars = 8;
  int polish_timeout_ms = 20000;
  bool enable_thinking = false;
};

typedef struct _VocotypeEngine {
  IBusEngine parent_instance;
  EngineState *state;
} VocotypeEngine;

typedef struct _VocotypeEngineClass {
  IBusEngineClass parent_class;
} VocotypeEngineClass;

#define VOCOTYPE_TYPE_ENGINE (vocotype_engine_get_type())
GType vocotype_engine_get_type();
G_DEFINE_TYPE(VocotypeEngine, vocotype_engine, IBUS_TYPE_ENGINE)

struct IdleClosure {
  std::function<void()> function;
};

gboolean run_idle(gpointer data) {
  std::unique_ptr<IdleClosure> closure(static_cast<IdleClosure *>(data));
  closure->function();
  return G_SOURCE_REMOVE;
}

void post_idle(std::function<void()> function) {
  g_idle_add_full(G_PRIORITY_DEFAULT, run_idle,
                  new IdleClosure{std::move(function)}, nullptr);
}

void post_engine(VocotypeEngine *engine,
                 std::function<void(VocotypeEngine *)> function) {
  g_object_ref(engine);
  post_idle([engine, function = std::move(function)] {
    function(engine);
    g_object_unref(engine);
  });
}

void show_aux(VocotypeEngine *engine, const std::string &text) {
  ibus_engine_update_auxiliary_text(IBUS_ENGINE(engine),
                                    ibus_text_new_from_string(text.c_str()),
                                    !text.empty());
}

void clear_voice_ui(VocotypeEngine *engine) {
  ibus_engine_hide_auxiliary_text(IBUS_ENGINE(engine));
}

void clear_rime_ui(VocotypeEngine *engine) {
  ibus_engine_update_preedit_text(IBUS_ENGINE(engine),
                                  ibus_text_new_from_string(""), 0, false);
  ibus_engine_hide_lookup_table(IBUS_ENGINE(engine));
}

std::string utf8_slice(const std::string &text, guint first, guint last) {
  if (first > last)
    std::swap(first, last);
  const glong length = g_utf8_strlen(text.c_str(), -1);
  first =
      std::min<guint>(first, static_cast<guint>(std::max<glong>(0, length)));
  last = std::min<guint>(last, static_cast<guint>(std::max<glong>(0, length)));
  const char *begin = g_utf8_offset_to_pointer(text.c_str(), first);
  const char *end = g_utf8_offset_to_pointer(text.c_str(), last);
  return std::string(begin, static_cast<std::size_t>(end - begin));
}

Snapshot capture_snapshot(VocotypeEngine *engine) {
  Snapshot snapshot;
  IBusText *text = nullptr;
  guint cursor = 0;
  guint anchor = 0;
  ibus_engine_get_surrounding_text(IBUS_ENGINE(engine), &text, &cursor,
                                   &anchor);
  if (!text)
    return snapshot;
  const char *raw = ibus_text_get_text(text);
  if (!raw)
    return snapshot;
  snapshot.valid = true;
  snapshot.text = raw;
  snapshot.cursor = cursor;
  snapshot.anchor = anchor;
  snapshot.selected = utf8_slice(snapshot.text, cursor, anchor);
  return snapshot;
}

void load_engine_config(EngineState &state) {
  try {
    (void)vocotype::desktop::migrate_config_layout();
    const Json config = vocotype::desktop::read_shared_config(true);
    const Json ibus_config = vocotype::desktop::read_ibus_config(true);
    if (config.contains("audio") && config["audio"].is_object())
      state.min_recording_ms =
          std::max(0, config["audio"].value("min_recording_ms", 1000));
    if (config.contains("slm") && config["slm"].is_object()) {
      const auto &slm = config["slm"];
      state.polish_min_chars = std::max(0, slm.value("min_chars", 8));
      state.polish_timeout_ms = std::max(1000, slm.value("timeout_ms", 20000));
      state.enable_thinking = slm.value("enable_thinking", false);
    }
    const Json hotkeys =
        ibus_config.contains("hotkeys") && ibus_config["hotkeys"].is_object()
            ? ibus_config["hotkeys"]
            : (config.contains("hotkeys") && config["hotkeys"].is_object()
                   ? config["hotkeys"]
                   : Json::object());
    if (!hotkeys.empty()) {
      state.transcribe_hotkey = vocotype::desktop::parse_hotkey(
          hotkeys.value("transcribe", "F9"), kDefaultTranscribeHotkey);
      state.polish_hotkey = vocotype::desktop::parse_hotkey(
          hotkeys.value("polish", "Shift+F9"), kDefaultPolishHotkey);
      state.edit_hotkey = vocotype::desktop::parse_hotkey(
          hotkeys.value("edit", "Ctrl+F9"), kDefaultEditHotkey);
      if (!vocotype::desktop::hotkey_safety_error(state.transcribe_hotkey)
               .empty() ||
          !vocotype::desktop::hotkey_safety_error(state.polish_hotkey)
               .empty() ||
          !vocotype::desktop::hotkey_safety_error(state.edit_hotkey).empty() ||
          vocotype::desktop::hotkeys_equal(state.transcribe_hotkey,
                                           state.polish_hotkey) ||
          vocotype::desktop::hotkeys_equal(state.transcribe_hotkey,
                                           state.edit_hotkey) ||
          vocotype::desktop::hotkeys_equal(state.polish_hotkey,
                                           state.edit_hotkey)) {
        state.transcribe_hotkey = kDefaultTranscribeHotkey;
        state.polish_hotkey = kDefaultPolishHotkey;
        state.edit_hotkey = kDefaultEditHotkey;
      }
    }
  } catch (const std::exception &) {
  }
}

std::string resolve_recorder() {
  return vocotype::desktop::find_executable(
      "vocotype-audio-recorder",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-native/bin/vocotype-audio-recorder",
       "/usr/libexec/vocotype-audio-recorder",
       "/usr/lib/vocotype/vocotype-audio-recorder",
       "/usr/lib64/vocotype/vocotype-audio-recorder"});
}

void update_rime_ui(VocotypeEngine *engine) {
  auto &state = *engine->state;
  if (!state.rime || !state.rime->available()) {
    clear_rime_ui(engine);
    return;
  }
  const auto context = state.rime->context();
  if (context.preedit.empty()) {
    ibus_engine_update_preedit_text(IBUS_ENGINE(engine),
                                    ibus_text_new_from_string(""), 0, false);
  } else {
    IBusText *text = ibus_text_new_from_string(context.preedit.c_str());
    ibus_text_append_attribute(text, IBUS_ATTR_TYPE_UNDERLINE,
                               IBUS_ATTR_UNDERLINE_SINGLE, 0, -1);
    ibus_engine_update_preedit_text(
        IBUS_ENGINE(engine), text,
        static_cast<guint>(std::max(0, context.cursor)), true);
  }
  if (context.candidates.empty()) {
    ibus_engine_hide_lookup_table(IBUS_ENGINE(engine));
    return;
  }
  IBusLookupTable *table = ibus_lookup_table_new(
      static_cast<guint>(std::clamp(context.page_size, 1, 16)),
      static_cast<guint>(std::max(0, context.highlighted)), true, false);
  for (const auto &candidate : context.candidates) {
    std::string display = candidate.text;
    if (!candidate.comment.empty())
      display += " " + candidate.comment;
    ibus_lookup_table_append_candidate(
        table, ibus_text_new_from_string(display.c_str()));
  }
  ibus_engine_update_lookup_table(IBUS_ENGINE(engine), table, true);
}

bool is_switch_hotkey(guint keyval, guint state) {
  if (keyval == IBUS_KEY_space && (state & (IBUS_SUPER_MASK | IBUS_MOD4_MASK)))
    return true;
  if ((keyval == IBUS_KEY_Shift_L || keyval == IBUS_KEY_Shift_R) &&
      (state & IBUS_MOD1_MASK))
    return true;
  return false;
}

guint hotkey_mask(guint state) {
  guint result = 0;
  if (state & IBUS_SHIFT_MASK)
    result |= GDK_SHIFT_MASK;
  if (state & IBUS_CONTROL_MASK)
    result |= GDK_CONTROL_MASK;
  if (state & IBUS_MOD1_MASK)
    result |= GDK_MOD1_MASK;
  if (state & (IBUS_SUPER_MASK | IBUS_MOD4_MASK))
    result |= GDK_SUPER_MASK;
  if (state & IBUS_META_MASK)
    result |= GDK_META_MASK;
  if (state & IBUS_HYPER_MASK)
    result |= GDK_HYPER_MASK;
  return result;
}

int rime_mask(guint state) {
  int result = 0;
  if (state & IBUS_SHIFT_MASK)
    result |= 1 << 0;
  if (state & IBUS_LOCK_MASK)
    result |= 1 << 1;
  if (state & IBUS_CONTROL_MASK)
    result |= 1 << 2;
  if (state & IBUS_MOD1_MASK)
    result |= 1 << 3;
  return result;
}

void start_recording(VocotypeEngine *engine, VoiceMode mode,
                     const Hotkey &active_hotkey) {
  auto &state = *engine->state;
  if (state.recording.exchange(true) || state.busy.load())
    return;
  state.recorder_path = resolve_recorder();
  if (state.recorder_path.empty()) {
    state.recording.store(false);
    show_aux(engine, "❌ 找不到原生录音器");
    return;
  }
  if (!vocotype::desktop::ensure_native_core(
          state.socket, vocotype::desktop::runtime_config_path())) {
    state.recording.store(false);
    show_aux(engine, "❌ 原生语音核心启动失败");
    return;
  }
  state.long_mode = mode == VoiceMode::polish;
  state.edit_mode = mode == VoiceMode::edit;
  state.active_hotkey = active_hotkey;
  state.snapshot = state.edit_mode ? capture_snapshot(engine) : Snapshot{};
  if (state.edit_mode && !state.snapshot.valid) {
    state.recording.store(false);
    show_aux(engine, "❌ 当前输入框不支持语音编辑");
    return;
  }
  const std::uint64_t generation = ++state.generation;
  state.recording_started = std::chrono::steady_clock::now();
  try {
    std::lock_guard lock(state.recorder_mutex);
    state.recorder = std::make_unique<vocotype::desktop::RecorderProcess>();
    state.recorder->start(
        state.recorder_path, [engine, generation](const std::string &type,
                                                  const std::string &value) {
          if (type == "partial" && !value.empty()) {
            post_engine(engine, [generation, value](VocotypeEngine *target) {
              if (target->state->generation.load() == generation &&
                  target->state->recording.load())
                show_aux(target, "🎤 " + value);
            });
          } else if (type == "error" && !value.empty()) {
            post_engine(engine, [generation, value](VocotypeEngine *target) {
              if (target->state->generation.load() == generation)
                show_aux(target, "❌ " + value);
            });
          }
        });
  } catch (const std::exception &error) {
    state.recording.store(false);
    show_aux(engine, std::string("❌ 启动录音失败：") + error.what());
    return;
  }
  show_aux(engine, state.edit_mode ? "🎤 正在听编辑指令…"
                                   : (state.long_mode ? "✨ 正在听，将自动润色…"
                                                      : "🎤 正在听…"));
}

Json poll_transcription(EngineState &state, const std::string &task_id,
                        VocotypeEngine *engine, std::uint64_t generation) {
  int after_seq = 0;
  const auto deadline =
      std::chrono::steady_clock::now() +
      std::chrono::milliseconds(state.polish_timeout_ms + 120000);
  while (std::chrono::steady_clock::now() < deadline &&
         state.generation.load() == generation) {
    Json response =
        vocotype::desktop::unix_json_request(state.socket,
                                             {{"type", "polish_poll"},
                                              {"task_id", task_id},
                                              {"after_seq", after_seq}},
                                             4000);
    after_seq = response.value("last_seq", after_seq);
    std::string preview = response.value("preview", "");
    if (!preview.empty()) {
      post_engine(engine, [generation, preview](VocotypeEngine *target) {
        if (target->state->generation.load() == generation)
          show_aux(target, "✨ " + preview);
      });
    }
    const std::string status = response.value("status", "");
    if (vocotype::desktop::task_status_is_terminal(status))
      return response;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return {{"success", false}, {"status", "failed"}, {"error", "润色任务超时"}};
}

Json poll_edit(EngineState &state, const std::string &task_id,
               VocotypeEngine *engine, std::uint64_t generation) {
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(150);
  while (std::chrono::steady_clock::now() < deadline &&
         state.generation.load() == generation) {
    Json response = vocotype::desktop::unix_json_request(
        state.socket, {{"type", "edit_poll"}, {"task_id", task_id}}, 4000);
    const std::string instruction = response.value("instruction", "");
    if (!instruction.empty()) {
      post_engine(engine, [generation, instruction](VocotypeEngine *target) {
        if (target->state->generation.load() == generation)
          show_aux(target, "✍️ " + instruction);
      });
    }
    const std::string status = response.value("status", "");
    if (vocotype::desktop::task_status_is_terminal(status))
      return response;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return {
      {"success", false}, {"status", "failed"}, {"error", "语音编辑任务超时"}};
}

std::pair<guint, guint> edit_key(const std::string &name) {
  static const std::unordered_map<std::string, guint> keys = {
      {"left", IBUS_KEY_Left},
      {"right", IBUS_KEY_Right},
      {"up", IBUS_KEY_Up},
      {"down", IBUS_KEY_Down},
      {"home", IBUS_KEY_Home},
      {"end", IBUS_KEY_End},
      {"pageup", IBUS_KEY_Page_Up},
      {"pagedown", IBUS_KEY_Page_Down},
      {"backspace", IBUS_KEY_BackSpace},
      {"delete", IBUS_KEY_Delete},
      {"enter", IBUS_KEY_Return},
      {"tab", IBUS_KEY_Tab},
      {"escape", IBUS_KEY_Escape},
      {"space", IBUS_KEY_space},
      {"a", IBUS_KEY_a},
      {"c", IBUS_KEY_c},
      {"v", IBUS_KEY_v},
      {"x", IBUS_KEY_x},
      {"z", IBUS_KEY_z}};
  const auto found = keys.find(name);
  return found == keys.end() ? std::pair<guint, guint>{0, 0}
                             : std::pair<guint, guint>{found->second, 0};
}

void apply_key_actions(VocotypeEngine *engine, const Json &actions) {
  if (!actions.is_array())
    return;
  for (const auto &item : actions) {
    if (!item.is_object())
      continue;
    auto [keyval, keycode] = edit_key(item.value("key", ""));
    if (!keyval)
      continue;
    guint modifiers = 0;
    if (item.contains("modifiers") && item["modifiers"].is_array()) {
      for (const auto &value : item["modifiers"]) {
        if (!value.is_string())
          continue;
        const auto modifier = value.get<std::string>();
        if (modifier == "ctrl")
          modifiers |= IBUS_CONTROL_MASK;
        else if (modifier == "shift")
          modifiers |= IBUS_SHIFT_MASK;
        else if (modifier == "alt")
          modifiers |= IBUS_MOD1_MASK;
        else if (modifier == "super")
          modifiers |= IBUS_SUPER_MASK;
      }
    }
    const int repeat = std::clamp(item.value("repeat", 1), 1, 100);
    for (int index = 0; index < repeat; ++index) {
      ibus_engine_forward_key_event(IBUS_ENGINE(engine), keyval, keycode,
                                    modifiers);
      ibus_engine_forward_key_event(IBUS_ENGINE(engine), keyval, keycode,
                                    modifiers | IBUS_RELEASE_MASK);
    }
  }
}

void apply_edit_result(VocotypeEngine *engine, const Snapshot &snapshot,
                       const Json &poll) {
  Json result = poll.contains("result") && poll["result"].is_object()
                    ? poll["result"]
                    : poll;
  if (!result.value("success", false)) {
    show_aux(engine, "❌ " + result.value("error",
                                          poll.value("error", "语音编辑失败")));
    return;
  }
  const std::string mode = result.value("mode", "no_op");
  const std::string hint = result.value("hint", "");
  if (mode == "key_actions") {
    apply_key_actions(engine, result.value("key_actions", Json::array()));
  } else if (mode == "replace") {
    const Snapshot current = capture_snapshot(engine);
    if (!current.valid || current.text != snapshot.text ||
        current.cursor != snapshot.cursor ||
        current.anchor != snapshot.anchor) {
      show_aux(engine, "❌ 输入框内容已变化，请重试");
      return;
    }
    const std::string replacement = result.value("new_text", "");
    const guint length =
        static_cast<guint>(g_utf8_strlen(snapshot.text.c_str(), -1));
    ibus_engine_delete_surrounding_text(
        IBUS_ENGINE(engine), -static_cast<gint>(snapshot.cursor), length);
    if (!replacement.empty())
      ibus_engine_commit_text(IBUS_ENGINE(engine),
                              ibus_text_new_from_string(replacement.c_str()));
    try {
      (void)vocotype::desktop::unix_json_request(
          engine->state->socket,
          {{"type", "edit_applied"},
           {"context_id", "ibus"},
           {"original_text", snapshot.text},
           {"new_text", replacement},
           {"record_history", result.value("record_history", true)}},
          2000);
    } catch (const std::exception &) {
    }
  } else if (mode == "commit_only") {
    const std::string text = result.value("new_text", "");
    if (!text.empty())
      ibus_engine_commit_text(IBUS_ENGINE(engine),
                              ibus_text_new_from_string(text.c_str()));
  }
  if (!hint.empty())
    show_aux(engine, "✓ " + hint);
  else
    clear_voice_ui(engine);
}

void stop_recording(VocotypeEngine *engine) {
  auto &state = *engine->state;
  if (!state.recording.exchange(false))
    return;
  if (state.busy.exchange(true))
    return;
  const auto elapsed =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - state.recording_started)
          .count();
  const bool too_short = elapsed < state.min_recording_ms;
  const bool long_mode = state.long_mode;
  const bool edit_mode = state.edit_mode;
  const Snapshot snapshot = state.snapshot;
  const std::uint64_t generation = state.generation.load();
  show_aux(engine, too_short ? "⚠️ 录音过短" : "⏳ 正在识别…");
  g_object_ref(engine);
  std::thread([engine, generation, too_short, long_mode, edit_mode, snapshot] {
    auto &worker_state = *engine->state;
    std::string audio_path;
    std::string error;
    try {
      std::lock_guard lock(worker_state.recorder_mutex);
      if (worker_state.recorder) {
        if (too_short) {
          worker_state.recorder->cancel_async();
          worker_state.recorder.reset();
        } else {
          audio_path = worker_state.recorder->stop();
          worker_state.recorder.reset();
        }
      }
      if (!too_short && audio_path.empty()) {
        error = "录音失败";
      } else if (!too_short && edit_mode) {
        Json started = vocotype::desktop::unix_json_request(
            worker_state.socket,
            {{"type", "edit_start"},
             {"audio_path", audio_path},
             {"context_id", "ibus"},
             {"replace_state", "unknown"},
             {"supports_surrounding", snapshot.valid},
             {"snapshot",
              {{"text", snapshot.text},
               {"cursor_pos", snapshot.cursor},
               {"anchor_pos", snapshot.anchor},
               {"selected_text", snapshot.selected}}}},
            5000);
        if (!started.value("success", false))
          error = started.value("error", "语音编辑启动失败");
        else {
          const Json result = poll_edit(
              worker_state, started.value("task_id", ""), engine, generation);
          post_engine(engine,
                      [generation, snapshot, result](VocotypeEngine *target) {
                        if (target->state->generation.load() == generation)
                          apply_edit_result(target, snapshot, result);
                      });
        }
      } else if (!too_short && long_mode) {
        Json started = vocotype::desktop::unix_json_request(
            worker_state.socket,
            {{"type", "transcribe_start"},
             {"audio_path", audio_path},
             {"long_mode", true},
             {"polish_min_chars", worker_state.polish_min_chars},
             {"polish_timeout_ms", worker_state.polish_timeout_ms},
             {"enable_thinking", worker_state.enable_thinking}},
            5000);
        if (!started.value("success", false))
          error = started.value("error", "转录启动失败");
        else {
          const Json result = poll_transcription(
              worker_state, started.value("task_id", ""), engine, generation);
          const std::string text =
              result.value("final_text", result.value("original_text", ""));
          if (result.value("success", false) && !text.empty()) {
            post_engine(engine, [generation, text](VocotypeEngine *target) {
              if (target->state->generation.load() == generation) {
                clear_voice_ui(target);
                ibus_engine_commit_text(
                    IBUS_ENGINE(target),
                    ibus_text_new_from_string(text.c_str()));
              }
            });
          } else
            error = result.value("error", "润色失败");
        }
      } else if (!too_short) {
        Json result =
            vocotype::desktop::unix_json_request(worker_state.socket,
                                                 {{"type", "transcribe"},
                                                  {"audio_path", audio_path},
                                                  {"long_mode", false}},
                                                 120000);
        std::filesystem::remove(audio_path);
        const std::string text = result.value("text", "");
        if (result.value("success", false) && !text.empty()) {
          post_engine(engine, [generation, text](VocotypeEngine *target) {
            if (target->state->generation.load() == generation) {
              clear_voice_ui(target);
              ibus_engine_commit_text(IBUS_ENGINE(target),
                                      ibus_text_new_from_string(text.c_str()));
            }
          });
        } else
          error = result.value("error", "识别失败");
      }
    } catch (const std::exception &exception) {
      error = exception.what();
      if (!audio_path.empty())
        std::filesystem::remove(audio_path);
    }
    post_idle([engine, generation, too_short, error] {
      if (engine->state->generation.load() == generation) {
        engine->state->busy.store(false);
        if (too_short)
          show_aux(engine, "⚠️ 录音过短");
        else if (!error.empty())
          show_aux(engine, "❌ " + error);
      }
      g_object_unref(engine);
    });
  }).detach();
}

void cancel_recording(VocotypeEngine *engine) {
  auto &state = *engine->state;
  ++state.generation;
  state.recording.store(false);
  std::lock_guard lock(state.recorder_mutex);
  if (state.recorder) {
    state.recorder->cancel();
    state.recorder.reset();
  }
  state.busy.store(false);
  clear_voice_ui(engine);
}

gboolean process_key_event(IBusEngine *base, guint keyval, guint keycode,
                           guint state_mask) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  auto &state = *engine->state;
  const bool release = (state_mask & IBUS_RELEASE_MASK) != 0;
  const guint modifiers = hotkey_mask(state_mask & ~IBUS_RELEASE_MASK);

  if (state.recording.load()) {
    if (vocotype::desktop::hotkey_matches(state.active_hotkey, keyval,
                                          modifiers)) {
      if (release)
        stop_recording(engine);
      return true;
    }
    if (!release && keyval == IBUS_KEY_Escape) {
      cancel_recording(engine);
      return true;
    }
    return false;
  }
  if (state.busy.load()) {
    if (!release && keyval == IBUS_KEY_Escape) {
      cancel_recording(engine);
      return true;
    }
    return false;
  }

  if (!release) {
    VoiceMode mode = VoiceMode::none;
    Hotkey matched;
    if (vocotype::desktop::hotkey_matches(state.edit_hotkey, keyval,
                                          modifiers)) {
      mode = VoiceMode::edit;
      matched = state.edit_hotkey;
    } else if (vocotype::desktop::hotkey_matches(state.polish_hotkey, keyval,
                                                 modifiers)) {
      mode = VoiceMode::polish;
      matched = state.polish_hotkey;
    } else if (vocotype::desktop::hotkey_matches(state.transcribe_hotkey,
                                                 keyval, modifiers)) {
      mode = VoiceMode::transcribe;
      matched = state.transcribe_hotkey;
    }
    if (mode != VoiceMode::none) {
      start_recording(engine, mode, matched);
      return true;
    }
  }

  if (is_switch_hotkey(keyval, state_mask) || release)
    return false;
  if (!state.rime)
    state.rime = std::make_unique<vocotype::desktop::RimeSession>();
  if (!state.rime->available())
    return false;
  const bool handled =
      state.rime->process_key(static_cast<int>(keyval), rime_mask(state_mask));
  const std::string commit = state.rime->take_commit();
  if (!commit.empty()) {
    clear_rime_ui(engine);
    ibus_engine_commit_text(base, ibus_text_new_from_string(commit.c_str()));
  }
  update_rime_ui(engine);
  (void)keycode;
  return handled;
}

void engine_enable(IBusEngine *base) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  engine->state->enabled.store(true);
  ibus_engine_get_surrounding_text(base, nullptr, nullptr, nullptr);
}

void engine_disable(IBusEngine *base) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  engine->state->enabled.store(false);
  cancel_recording(engine);
  if (engine->state->rime)
    engine->state->rime->clear();
  clear_rime_ui(engine);
}

void engine_focus_in(IBusEngine *base) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  load_engine_config(*engine->state);
  engine->state->focused.store(true);
}

void engine_focus_out(IBusEngine *base) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  engine->state->focused.store(false);
  cancel_recording(engine);
  if (engine->state->rime)
    engine->state->rime->clear();
  clear_rime_ui(engine);
}

void engine_reset(IBusEngine *base) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(base);
  if (engine->state->rime)
    engine->state->rime->clear();
  clear_rime_ui(engine);
  clear_voice_ui(engine);
}

void engine_finalize(GObject *object) {
  auto *engine = reinterpret_cast<VocotypeEngine *>(object);
  if (engine->state) {
    cancel_recording(engine);
    delete engine->state;
    engine->state = nullptr;
  }
  G_OBJECT_CLASS(vocotype_engine_parent_class)->finalize(object);
}

void vocotype_engine_init(VocotypeEngine *engine) {
  engine->state = new EngineState();
  load_engine_config(*engine->state);
}

void vocotype_engine_class_init(VocotypeEngineClass *klass) {
  auto *engine_class = IBUS_ENGINE_CLASS(klass);
  engine_class->process_key_event = process_key_event;
  engine_class->enable = engine_enable;
  engine_class->disable = engine_disable;
  engine_class->focus_in = engine_focus_in;
  engine_class->focus_out = engine_focus_out;
  engine_class->reset = engine_reset;
  G_OBJECT_CLASS(klass)->finalize = engine_finalize;
}

void bus_disconnected(IBusBus *, gpointer) { ibus_quit(); }

void print_xml(const char *executable) {
  std::printf(
      "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
      "<component><name>org.vocotype.IBus.VoCoType</name>"
      "<description>VoCoType Voice Input Method</description>"
      "<exec>%s --ibus</exec><version>%s</version><author>VoCoType</author>"
      "<license>GPL</license><homepage>https://github.com/LeonardNJU/"
      "VocoType-linux</homepage>"
      "<textdomain>vocotype</textdomain><engines><engine><name>vocotype</name>"
      "<language>zh</language><license>GPL</license><author>VoCoType</author>"
      "<layout>default</layout><longname>VoCoType Voice Input</longname>"
      "<description>Configurable Push-to-Talk Voice "
      "Input</description><rank>50</rank>"
      "<symbol>🎤</symbol></engine></engines></component>\n",
      executable, VOCOTYPE_VERSION);
}

} // namespace

int main(int argc, char **argv) {
  bool by_ibus = false;
  bool deploy_rime = false;
  for (int index = 1; index < argc; ++index) {
    const std::string argument = argv[index];
    if (argument == "--ibus" || argument == "-i")
      by_ibus = true;
    else if (argument == "--deploy-rime")
      deploy_rime = true;
    else if (argument == "--xml" || argument == "-x") {
      print_xml(argv[0]);
      return 0;
    } else if (argument == "--help") {
      std::puts("Usage: vocotype-ibus-engine [--ibus|--xml|--deploy-rime]");
      return 0;
    }
  }
  if (deploy_rime) {
    std::string error;
    if (!vocotype::desktop::deploy_rime_workspace(
            vocotype::desktop::config_dir() / "rime", error)) {
      g_printerr("VoCoType: Rime deployment failed: %s\n", error.c_str());
      return 1;
    }
    std::puts("RIME_DEPLOY_OK");
    return 0;
  }
  ibus_init();
  IBusBus *bus = ibus_bus_new();
  if (!ibus_bus_is_connected(bus)) {
    g_printerr("VoCoType: cannot connect to IBus daemon\n");
    g_object_unref(bus);
    return 1;
  }
  g_signal_connect(bus, "disconnected", G_CALLBACK(bus_disconnected), nullptr);
  IBusFactory *factory = ibus_factory_new(ibus_bus_get_connection(bus));
  ibus_factory_add_engine(factory, "vocotype", VOCOTYPE_TYPE_ENGINE);
  if (by_ibus) {
    if (!ibus_bus_request_name(bus, "org.vocotype.IBus.VoCoType", 0)) {
      g_printerr("VoCoType: cannot acquire IBus service name\n");
      g_object_unref(factory);
      g_object_unref(bus);
      return 1;
    }
  }
  ibus_main();
  g_object_unref(factory);
  g_object_unref(bus);
  return 0;
}
