#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>

namespace vocotype::desktop {
// Reject only near-digital-silence captures, not ordinary quiet speech.
// Thresholds are PCM16 counts (48/32768 peak, 8/32768 mean absolute).
inline bool has_usable_microphone_signal(const std::vector<std::int16_t>& samples) {
  if (samples.empty()) return false;
  std::uint64_t absolute_sum = 0;
  int peak = 0;
  for (const auto sample : samples) {
    const int magnitude = sample < 0 ? -static_cast<int>(sample) : static_cast<int>(sample);
    peak = std::max(peak, magnitude);
    absolute_sum += static_cast<unsigned int>(magnitude);
  }
  return peak >= 48 || absolute_sum >= static_cast<std::uint64_t>(samples.size()) * 8;
}
} // namespace vocotype::desktop
