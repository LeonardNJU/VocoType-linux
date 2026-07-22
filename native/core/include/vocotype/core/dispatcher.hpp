#pragma once

#include "vocotype/core/config.hpp"
#include "vocotype/core/slm_client.hpp"

namespace vocotype::core {

class CoreDispatcher {
public:
  explicit CoreDispatcher(AppConfig config);
  [[nodiscard]] Json dispatch(const Json &request) const;

private:
  AppConfig config_;
  SlmClient slm_;
};

} // namespace vocotype::core
