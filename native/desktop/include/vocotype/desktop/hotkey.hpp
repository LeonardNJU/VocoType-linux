#pragma once

#include <gdk/gdk.h>

#include <string>

namespace vocotype::desktop {

struct Hotkey {
  guint keyval = 0;
  GdkModifierType modifiers = static_cast<GdkModifierType>(0);

  [[nodiscard]] bool valid() const;
};

[[nodiscard]] Hotkey parse_hotkey(const std::string &text,
                                  const Hotkey &fallback = {});
[[nodiscard]] Hotkey hotkey_from_event(guint keyval, guint state);
[[nodiscard]] std::string hotkey_to_string(const Hotkey &hotkey);
[[nodiscard]] bool hotkey_matches(const Hotkey &hotkey, guint keyval,
                                  guint state);
[[nodiscard]] bool hotkeys_equal(const Hotkey &left, const Hotkey &right);
[[nodiscard]] bool hotkey_is_modifier_key(guint keyval);
[[nodiscard]] std::string hotkey_safety_error(const Hotkey &hotkey);

} // namespace vocotype::desktop
