#include "vocotype/core/transcription_tasks.hpp"

#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <optional>
#include <utility>

namespace vocotype::core {
namespace {

Json error_response(const std::string &error) {
  return {{"success", false}, {"error", error}};
}

constexpr auto kTaskTtl = std::chrono::minutes(5);

} // namespace

struct TranscriptionTaskManager::Task {
  explicit Task(std::string value) : task_id(std::move(value)) {}

  void add_event_locked(const std::string &kind, const std::string &text,
                        const std::string &event_reason = "") {
    ++seq;
    Json event{{"seq", seq}, {"kind", kind}, {"text", text}};
    if (!event_reason.empty()) {
      event["reason"] = event_reason;
    }
    if (kind == "delta") {
      event["preview"] = text;
      preview = text;
    }
    events.push_back(std::move(event));
    if (events.size() > 200U) {
      events.erase(events.begin(),
                   events.begin() + static_cast<long>(events.size() - 200U));
    }
  }

  void set_phase(const std::string &value, const std::string &status_text) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    phase = value;
    add_event_locked("status", status_text);
  }

  bool accept_stream_event(const SlmStreamEvent &stream_event) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return false;
    }
    if (stream_event.kind == "heartbeat" || stream_event.kind == "final" ||
        stream_event.kind == "error") {
      return true;
    }
    if (stream_event.kind == "status") {
      add_event_locked("status", stream_event.text);
      return true;
    }
    if (stream_event.kind == "delta") {
      ++seq;
      const std::string full_preview = stream_event.preview.empty()
                                           ? preview + stream_event.text
                                           : stream_event.preview;
      events.push_back({{"seq", seq},
                        {"kind", "delta"},
                        {"text", stream_event.text},
                        {"preview", full_preview}});
      preview = full_preview;
      if (events.size() > 200U) {
        events.erase(events.begin(),
                     events.begin() + static_cast<long>(events.size() - 200U));
      }
    }
    return true;
  }

  void set_original(const std::string &value) {
    std::lock_guard lock(mutex);
    if (!cancelled) {
      original_text = value;
    }
  }

  void mark_final(const std::string &value, const std::string &final_reason) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    status = "final";
    phase = "done";
    preview = value;
    final_text = value;
    reason = final_reason;
    add_event_locked("final", value, final_reason);
    done_at = std::chrono::steady_clock::now();
  }

  void mark_error(const std::string &message, const std::string &error_reason) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    status = "error";
    phase = "done";
    error = message;
    reason = error_reason;
    add_event_locked("error", message, error_reason);
    done_at = std::chrono::steady_clock::now();
  }

  void cancel() {
    std::lock_guard lock(mutex);
    if (status != "running") {
      return;
    }
    cancelled = true;
    status = "cancelled";
    phase = "done";
    reason = "cancelled";
    add_event_locked("cancelled", "已取消", "cancelled");
    done_at = std::chrono::steady_clock::now();
  }

  [[nodiscard]] bool is_cancelled() const {
    std::lock_guard lock(mutex);
    return cancelled;
  }

  [[nodiscard]] bool expired(std::chrono::steady_clock::time_point now) const {
    std::lock_guard lock(mutex);
    return status != "running" &&
           done_at != std::chrono::steady_clock::time_point{} &&
           now - done_at > kTaskTtl;
  }

  [[nodiscard]] Json snapshot(int after_seq) const {
    std::lock_guard lock(mutex);
    Json selected = Json::array();
    for (const auto &event : events) {
      if (event.value("seq", 0) > after_seq) {
        selected.push_back(event);
      }
    }
    return {{"success", true},
            {"task_id", task_id},
            {"status", status},
            {"phase", phase},
            {"events", selected},
            {"last_seq", seq},
            {"preview", preview},
            {"final_text", final_text},
            {"original_text", original_text},
            {"error", error},
            {"reason", reason}};
  }

  std::string task_id;
  mutable std::mutex mutex;
  std::string status = "running";
  std::string phase = "asr";
  std::vector<Json> events;
  int seq = 0;
  std::string preview;
  std::string final_text;
  std::string original_text;
  std::string error;
  std::string reason;
  bool cancelled = false;
  std::chrono::steady_clock::time_point done_at{};
};

TranscriptionTaskManager::TranscriptionTaskManager(OfflineAsrProcess &asr,
                                                   const SlmClient &slm)
    : asr_(asr), slm_(slm) {}

TranscriptionTaskManager::~TranscriptionTaskManager() {
  std::lock_guard lock(workers_mutex_);
  workers_.clear();
}

void TranscriptionTaskManager::cleanup_finished_workers() {
  std::lock_guard lock(workers_mutex_);
  for (auto iterator = workers_.begin(); iterator != workers_.end();) {
    if (!iterator->finished->load(std::memory_order_acquire)) {
      ++iterator;
      continue;
    }
    if (iterator->thread.joinable()) {
      iterator->thread.join();
    }
    iterator = workers_.erase(iterator);
  }
}

void TranscriptionTaskManager::cleanup_expired_tasks() {
  const auto now = std::chrono::steady_clock::now();
  std::lock_guard lock(tasks_mutex_);
  for (auto iterator = tasks_.begin(); iterator != tasks_.end();) {
    if (iterator->second->expired(now)) {
      iterator = tasks_.erase(iterator);
    } else {
      ++iterator;
    }
  }
}

std::string TranscriptionTaskManager::next_task_id() {
  return "cpp-" + std::to_string(::getpid()) + "-" + std::to_string(++next_id_);
}

std::shared_ptr<TranscriptionTaskManager::Task>
TranscriptionTaskManager::find_task(const std::string &task_id) const {
  std::lock_guard lock(tasks_mutex_);
  const auto found = tasks_.find(task_id);
  return found == tasks_.end() ? nullptr : found->second;
}

Json TranscriptionTaskManager::start(const Json &request) {
  cleanup_finished_workers();
  cleanup_expired_tasks();
  const std::string audio_path = request.value("audio_path", "");
  if (audio_path.empty()) {
    return error_response("missing_audio_path");
  }
  const auto expanded = expand_user_path(audio_path);
  if (!std::filesystem::is_regular_file(expanded)) {
    return error_response("audio_file_not_found");
  }

  Json owned_request = request;
  owned_request["audio_path"] = std::filesystem::canonical(expanded).string();
  auto task = std::make_shared<Task>(next_task_id());
  task->set_phase("asr", "⏳ 正在识别...");
  {
    std::lock_guard lock(tasks_mutex_);
    tasks_[task->task_id] = task;
  }
  {
    auto finished = std::make_shared<std::atomic<bool>>(false);
    WorkerSlot slot;
    slot.finished = finished;
    slot.thread = std::jthread(
        [this, task, owned_request, finished](std::stop_token) mutable {
          struct FinishGuard {
            std::shared_ptr<std::atomic<bool>> flag;
            ~FinishGuard() { flag->store(true, std::memory_order_release); }
          } guard{finished};
          run_task(task, std::move(owned_request));
        });
    std::lock_guard lock(workers_mutex_);
    workers_.push_back(std::move(slot));
  }
  return {{"success", true}, {"task_id", task->task_id}, {"status", "running"}};
}

Json TranscriptionTaskManager::poll(const Json &request) const {
  const std::string task_id = request.value("task_id", "");
  if (task_id.empty()) {
    return error_response("missing_task_id");
  }
  const auto task = find_task(task_id);
  if (!task) {
    return error_response("task_not_found");
  }
  return task->snapshot(std::max(0, request.value("after_seq", 0)));
}

Json TranscriptionTaskManager::cancel(const Json &request) {
  const std::string task_id = request.value("task_id", "");
  if (task_id.empty()) {
    return error_response("missing_task_id");
  }
  const auto task = find_task(task_id);
  if (task) {
    task->cancel();
  }
  return {{"success", true}};
}

void TranscriptionTaskManager::run_task(const std::shared_ptr<Task> &task,
                                        Json request) {
  const std::filesystem::path audio_path = request.value("audio_path", "");
  struct AudioCleanup {
    std::filesystem::path path;
    ~AudioCleanup() {
      std::error_code error;
      std::filesystem::remove(path, error);
    }
  } cleanup{audio_path};

  Json result = asr_.transcribe(request);
  if (task->is_cancelled()) {
    return;
  }
  if (!result.value("success", false)) {
    task->mark_error(result.value("error", "转录失败"),
                     result.value("reason", "asr_error"));
    return;
  }

  const std::string original = result.value("text", "");
  task->set_original(original);
  if (!request.value("long_mode", false)) {
    task->mark_final(original, "ok");
    return;
  }
  if (!slm_.enabled()) {
    task->mark_final(original, "disabled");
    return;
  }

  const int min_chars = request.value("polish_min_chars", -1);
  if (!slm_.should_polish(original, min_chars)) {
    task->mark_final(original, "too_short");
    return;
  }

  task->set_phase("polishing", "✨ 正在润色...");
  const std::optional<bool> enable_thinking =
      request.contains("enable_thinking")
          ? std::optional<bool>(request.value("enable_thinking", false))
          : std::nullopt;
  const PolishResult polished =
      slm_.remote_stream()
          ? slm_.stream_polish(original, enable_thinking,
                               [task](const SlmStreamEvent &event) {
                                 return task->accept_stream_event(event);
                               })
          : slm_.polish(original, enable_thinking);
  if (task->is_cancelled()) {
    return;
  }
  if (!polished.success) {
    task->mark_error(polished.error.empty() ? "SLM 调用失败" : polished.error,
                     polished.reason.empty() ? "slm_error" : polished.reason);
    return;
  }
  task->mark_final(polished.text, polished.reason);
}

} // namespace vocotype::core
