#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "vocotype/core/config.hpp"
#include "vocotype/core/offline_asr.hpp"
#include "vocotype/core/voice_edit.hpp"

namespace vocotype::core {

class VoiceEditTaskManager {
public:
  VoiceEditTaskManager(OfflineAsrProcess &asr, const VoiceEditPlanner &planner);
  VoiceEditTaskManager(const VoiceEditTaskManager &) = delete;
  VoiceEditTaskManager &operator=(const VoiceEditTaskManager &) = delete;
  ~VoiceEditTaskManager();

  [[nodiscard]] Json run_sync(const Json &request);
  [[nodiscard]] Json start(const Json &request);
  [[nodiscard]] Json poll(const Json &request) const;
  [[nodiscard]] Json cancel(const Json &request);

private:
  struct Task;
  struct WorkerSlot {
    std::thread thread;
    std::shared_ptr<std::atomic<bool>> finished;
  };

  [[nodiscard]] Json perform(const Json &request,
                             const std::shared_ptr<Task> &task = nullptr);
  [[nodiscard]] std::shared_ptr<Task>
  find_task(const std::string &task_id) const;
  [[nodiscard]] std::string next_task_id();
  void cleanup_finished_workers();
  void cleanup_expired_tasks();
  void run_task(const std::shared_ptr<Task> &task, Json request);

  OfflineAsrProcess &asr_;
  const VoiceEditPlanner &planner_;
  mutable std::mutex tasks_mutex_;
  std::unordered_map<std::string, std::shared_ptr<Task>> tasks_;
  std::mutex workers_mutex_;
  std::vector<WorkerSlot> workers_;
  std::atomic<unsigned long long> next_id_{0};
};

} // namespace vocotype::core
