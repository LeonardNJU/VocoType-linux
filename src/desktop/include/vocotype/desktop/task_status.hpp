#pragma once

#include <string_view>

namespace vocotype::desktop {

[[nodiscard]] constexpr bool task_status_is_terminal(
    std::string_view status) noexcept {
  return status == "final" || status == "error" || status == "cancelled" ||
         status == "completed" || status == "failed";
}

} // namespace vocotype::desktop
