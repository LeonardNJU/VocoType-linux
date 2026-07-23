#include "vocotype/desktop/hotkey.hpp"

#include <algorithm>
#include <cctype>
#include <sstream>
#include <vector>

namespace vocotype::desktop {
namespace {

constexpr guint kSupportedModifiers = GDK_SHIFT_MASK | GDK_CONTROL_MASK |
                                      GDK_MOD1_MASK | GDK_SUPER_MASK |
                                      GDK_META_MASK | GDK_HYPER_MASK;

std::string trim(std::string value) {
  const auto first =
      std::find_if_not(value.begin(), value.end(), [](unsigned char character) {
        return std::isspace(character) != 0;
      });
  const auto last = std::find_if_not(value.rbegin(), value.rend(),
                                     [](unsigned char character) {
                                       return std::isspace(character) != 0;
                                     })
                        .base();
  if (first >= last)
    return {};
  return std::string(first, last);
}

std::string lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char character) {
                   return static_cast<char>(std::tolower(character));
                 });
  return value;
}

GdkModifierType canonical_modifiers(guint state) {
  guint result = state & kSupportedModifiers;
  if (result & (GDK_META_MASK | GDK_HYPER_MASK)) {
    result |= GDK_SUPER_MASK;
    result &= ~(GDK_META_MASK | GDK_HYPER_MASK);
  }
  return static_cast<GdkModifierType>(result);
}

GdkModifierType key_modifier(guint keyval) {
  switch (keyval) {
  case GDK_KEY_Shift_L:
  case GDK_KEY_Shift_R:
    return GDK_SHIFT_MASK;
  case GDK_KEY_Control_L:
  case GDK_KEY_Control_R:
    return GDK_CONTROL_MASK;
  case GDK_KEY_Alt_L:
  case GDK_KEY_Alt_R:
  case GDK_KEY_Meta_L:
  case GDK_KEY_Meta_R:
    return GDK_MOD1_MASK;
  case GDK_KEY_Super_L:
  case GDK_KEY_Super_R:
  case GDK_KEY_Hyper_L:
  case GDK_KEY_Hyper_R:
    return GDK_SUPER_MASK;
  default:
    return static_cast<GdkModifierType>(0);
  }
}

std::vector<std::string> split(const std::string &text) {
  std::vector<std::string> result;
  std::string item;
  std::istringstream input(text);
  while (std::getline(input, item, '+'))
    result.push_back(trim(item));
  return result;
}

} // namespace

bool Hotkey::valid() const {
  return keyval != 0 && keyval != GDK_KEY_VoidSymbol;
}

Hotkey parse_hotkey(const std::string &text, const Hotkey &fallback) {
  const auto parts = split(text);
  if (parts.empty())
    return fallback;

  guint modifiers = 0;
  guint keyval = 0;
  for (const auto &raw : parts) {
    if (raw.empty())
      return fallback;
    const std::string token = lower(raw);
    if (token == "shift") {
      modifiers |= GDK_SHIFT_MASK;
    } else if (token == "ctrl" || token == "control" || token == "primary") {
      modifiers |= GDK_CONTROL_MASK;
    } else if (token == "alt" || token == "mod1") {
      modifiers |= GDK_MOD1_MASK;
    } else if (token == "super" || token == "meta" || token == "mod4" ||
               token == "hyper") {
      modifiers |= GDK_SUPER_MASK;
    } else {
      if (keyval != 0)
        return fallback;
      keyval = gdk_keyval_from_name(raw.c_str());
      if (keyval == 0 && g_utf8_strlen(raw.c_str(), -1) == 1)
        keyval = gdk_unicode_to_keyval(g_utf8_get_char(raw.c_str()));
    }
  }

  Hotkey result{keyval, canonical_modifiers(modifiers)};
  if (!result.valid())
    return fallback;
  const auto own_modifier = key_modifier(result.keyval);
  result.modifiers = static_cast<GdkModifierType>(
      static_cast<guint>(result.modifiers) & ~static_cast<guint>(own_modifier));
  return result;
}

Hotkey hotkey_from_event(guint keyval, guint state) {
  Hotkey result{keyval, canonical_modifiers(state)};
  const auto own_modifier = key_modifier(keyval);
  result.modifiers = static_cast<GdkModifierType>(
      static_cast<guint>(result.modifiers) & ~static_cast<guint>(own_modifier));
  return result;
}

std::string hotkey_to_string(const Hotkey &hotkey) {
  if (!hotkey.valid())
    return {};
  std::string result;
  const guint modifiers = static_cast<guint>(hotkey.modifiers);
  if (modifiers & GDK_CONTROL_MASK)
    result += "Ctrl+";
  if (modifiers & GDK_MOD1_MASK)
    result += "Alt+";
  if (modifiers & GDK_SHIFT_MASK)
    result += "Shift+";
  if (modifiers & GDK_SUPER_MASK)
    result += "Super+";
  const gchar *name = gdk_keyval_name(hotkey.keyval);
  if (!name || !*name)
    return {};
  result += name;
  return result;
}

bool hotkey_matches(const Hotkey &hotkey, guint keyval, guint state) {
  if (!hotkey.valid() || hotkey.keyval != keyval)
    return false;
  return hotkeys_equal(hotkey, hotkey_from_event(keyval, state));
}

bool hotkeys_equal(const Hotkey &left, const Hotkey &right) {
  return left.keyval == right.keyval &&
         canonical_modifiers(left.modifiers) ==
             canonical_modifiers(right.modifiers);
}

bool hotkey_is_modifier_key(guint keyval) {
  return static_cast<guint>(key_modifier(keyval)) != 0 ||
         keyval == GDK_KEY_ISO_Level3_Shift ||
         keyval == GDK_KEY_ISO_Level5_Shift;
}

std::string hotkey_safety_error(const Hotkey &hotkey) {
  if (!hotkey.valid())
    return "没有识别到有效按键";

  const guint modifiers = static_cast<guint>(hotkey.modifiers);
  const guint strong_modifiers =
      modifiers & (GDK_CONTROL_MASK | GDK_MOD1_MASK | GDK_SUPER_MASK);
  const gunichar unicode = gdk_keyval_to_unicode(hotkey.keyval);
  if (unicode != 0 && g_unichar_isprint(unicode) && strong_modifiers == 0)
    return "裸字母、数字、标点以及仅加 Shift 的可打印按键会破坏正常输入";

  if (hotkey_is_modifier_key(hotkey.keyval)) {
    switch (hotkey.keyval) {
    case GDK_KEY_Alt_R:
    case GDK_KEY_Control_R:
    case GDK_KEY_Super_R:
    case GDK_KEY_Meta_R:
    case GDK_KEY_Hyper_R:
    case GDK_KEY_ISO_Level3_Shift:
    case GDK_KEY_ISO_Level5_Shift:
      break;
    default:
      return "单独使用左侧修饰键或 Shift 会干扰日常键盘操作；请使用右侧 "
             "Alt/Ctrl/Super 或组合键";
    }
  }

  if (strong_modifiers == 0) {
    switch (hotkey.keyval) {
    case GDK_KEY_space:
    case GDK_KEY_Tab:
    case GDK_KEY_ISO_Left_Tab:
    case GDK_KEY_Return:
    case GDK_KEY_KP_Enter:
    case GDK_KEY_BackSpace:
    case GDK_KEY_Delete:
    case GDK_KEY_Insert:
    case GDK_KEY_Left:
    case GDK_KEY_Right:
    case GDK_KEY_Up:
    case GDK_KEY_Down:
    case GDK_KEY_Home:
    case GDK_KEY_End:
    case GDK_KEY_Page_Up:
    case GDK_KEY_Page_Down:
    case GDK_KEY_Escape:
    case GDK_KEY_Caps_Lock:
    case GDK_KEY_Num_Lock:
    case GDK_KEY_Scroll_Lock:
      return "该按键承担基础输入、编辑或导航功能，不能被语音输入独占";
    default:
      break;
    }
  }

  const std::string portable = hotkey_to_string(hotkey);
  static const std::vector<std::string> reserved = {
      "Ctrl+a", "Ctrl+c",  "Ctrl+f",  "Ctrl+n",     "Ctrl+o",
      "Ctrl+p", "Ctrl+q",  "Ctrl+r",  "Ctrl+s",     "Ctrl+t",
      "Ctrl+v", "Ctrl+w",  "Ctrl+x",  "Ctrl+z",     "Ctrl+space",
      "Alt+F4", "Alt+Tab", "Super+l", "Super+space"};
  std::string normalized = lower(portable);
  for (const auto &item : reserved) {
    if (normalized == lower(item))
      return "该组合是常用系统或应用快捷键，VoCoType 不允许覆盖";
  }
  return {};
}

} // namespace vocotype::desktop
