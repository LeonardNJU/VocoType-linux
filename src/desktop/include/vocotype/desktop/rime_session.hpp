#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace vocotype::desktop {

struct RimeCandidateView {
  std::string text;
  std::string comment;
};

struct RimeContextView {
  std::string preedit;
  int cursor = 0;
  int page_size = 5;
  int highlighted = 0;
  std::vector<RimeCandidateView> candidates;
};

class RimeSession {
public:
  RimeSession();
  ~RimeSession();
  RimeSession(const RimeSession &) = delete;
  RimeSession &operator=(const RimeSession &) = delete;

  bool available() const;
  bool process_key(int keyval, int mask);
  std::string take_commit();
  RimeContextView context() const;
  void clear();
  std::string schema() const;

private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

bool deploy_rime_workspace(const std::filesystem::path &user_data_dir,
                           std::string &error);

} // namespace vocotype::desktop
