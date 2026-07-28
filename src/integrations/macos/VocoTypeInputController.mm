#import "VocoTypeInputController.h"

#import <AppKit/AppKit.h>
#import <Carbon/Carbon.h>
#import <CoreGraphics/CoreGraphics.h>

#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/recorder_process.hpp"
#include "vocotype/desktop/streaming_preview.hpp"
#include "vocotype/desktop/task_status.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <cmath>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

using vocotype::desktop::Json;

namespace {

enum class VoiceMode { none, transcribe, polish, edit };

constexpr UInt32 kTranscribeHotKeyId = 1;
constexpr UInt32 kPolishHotKeyId = 2;
constexpr UInt32 kEditHotKeyId = 3;
constexpr OSType kVocoTypeHotKeySignature =
    (static_cast<OSType>('V') << 24U) |
    (static_cast<OSType>('o') << 16U) |
    (static_cast<OSType>('C') << 8U) |
    static_cast<OSType>('o');

__weak VocoTypeInputController *g_active_controller = nil;
// Retain the controller that accepted the current voice operation. InputMethodKit
// may transiently activate a system client (for example UserNotificationCenter)
// between F9 down and F9 up; routing the release through g_active_controller
// would orphan the original recorder and commit into the wrong text client.
VocoTypeInputController *g_voice_operation_controller = nil;
bool g_hotkey_event_down = false;
bool g_hotkey_reload_pending = false;

struct Hotkey {
  unsigned short key_code = 0;
  NSEventModifierFlags modifiers = 0;
  bool valid = false;
};

struct Snapshot {
  bool valid = false;
  std::string text;
  std::string selected;
  std::size_t cursor = 0;
  std::size_t anchor = 0;
  NSRange selection = NSMakeRange(NSNotFound, 0);
  NSRange document = NSMakeRange(NSNotFound, 0);
};

struct ControllerState {
  std::mutex recorder_mutex;
  std::unique_ptr<vocotype::desktop::RecorderProcess> recorder;
  std::atomic_bool recording{false};
  std::atomic_bool busy{false};
  std::atomic_uint64_t generation{0};
  // Nanoseconds on steady_clock. Zero means the recorder child has not yet
  // confirmed that the CoreAudio stream is actually recording.
  std::atomic_int64_t microphone_started_ns{0};
  std::chrono::steady_clock::time_point recording_started;
  VoiceMode mode = VoiceMode::none;
  Hotkey active_hotkey;
  Hotkey transcribe_hotkey;
  Hotkey polish_hotkey;
  Hotkey edit_hotkey;
  Snapshot snapshot;
  std::string socket = vocotype::desktop::backend_socket_path();
  std::string recorder_path;
  int min_recording_ms = 500;
  int polish_min_chars = 8;
  int polish_timeout_ms = 20000;
  bool enable_thinking = false;
  std::string last_partial;
};

std::int64_t steady_now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

bool begin_recording_state(ControllerState &state) {
  if (state.busy.load(std::memory_order_acquire) ||
      state.recording.load(std::memory_order_acquire))
    return false;
  bool expected = false;
  return state.recording.compare_exchange_strong(
      expected, true, std::memory_order_acq_rel, std::memory_order_acquire);
}

bool recording_is_too_short(const ControllerState &state,
                            std::int64_t now_ns) {
  const std::int64_t started_ns =
      state.microphone_started_ns.load(std::memory_order_acquire);
  if (started_ns <= 0 || now_ns <= started_ns)
    return true;
  const auto elapsed_ms = (now_ns - started_ns) / 1000000;
  return elapsed_ms < state.min_recording_ms;
}

void release_voice_operation(VocoTypeInputController *controller) {
  if (g_voice_operation_controller == controller)
    g_voice_operation_controller = nil;
  if (!g_voice_operation_controller && g_hotkey_reload_pending)
    VocoTypeReloadGlobalHotKeys();
}

NSEventModifierFlags canonical_modifiers(NSEventModifierFlags flags) {
  constexpr NSEventModifierFlags supported =
      NSEventModifierFlagShift | NSEventModifierFlagControl |
      NSEventModifierFlagOption | NSEventModifierFlagCommand;
  return flags & supported;
}

std::string lower_ascii(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char value) {
                   return static_cast<char>(std::tolower(value));
                 });
  return value;
}

std::vector<std::string> split_hotkey(const std::string &text) {
  std::vector<std::string> result;
  std::size_t start = 0;
  while (start <= text.size()) {
    const std::size_t end = text.find('+', start);
    std::string item = text.substr(start, end - start);
    item.erase(item.begin(),
               std::find_if(item.begin(), item.end(), [](unsigned char c) {
                 return std::isspace(c) == 0;
               }));
    item.erase(std::find_if(item.rbegin(), item.rend(), [](unsigned char c) {
                 return std::isspace(c) == 0;
               }).base(),
               item.end());
    result.push_back(std::move(item));
    if (end == std::string::npos)
      break;
    start = end + 1;
  }
  return result;
}

std::optional<unsigned short> function_key_code(const std::string &name) {
  static const std::unordered_map<std::string, unsigned short> keys = {
      {"f1", kVK_F1},   {"f2", kVK_F2},   {"f3", kVK_F3},
      {"f4", kVK_F4},   {"f5", kVK_F5},   {"f6", kVK_F6},
      {"f7", kVK_F7},   {"f8", kVK_F8},   {"f9", kVK_F9},
      {"f10", kVK_F10}, {"f11", kVK_F11}, {"f12", kVK_F12},
      {"f13", kVK_F13}, {"f14", kVK_F14}, {"f15", kVK_F15},
      {"f16", kVK_F16}, {"f17", kVK_F17}, {"f18", kVK_F18},
      {"f19", kVK_F19}, {"f20", kVK_F20},
  };
  const auto found = keys.find(lower_ascii(name));
  if (found == keys.end())
    return std::nullopt;
  return found->second;
}

Hotkey parse_hotkey(const std::string &text, const Hotkey &fallback) {
  Hotkey result;
  bool has_key = false;
  for (const auto &raw : split_hotkey(text)) {
    const std::string token = lower_ascii(raw);
    if (token == "shift")
      result.modifiers |= NSEventModifierFlagShift;
    else if (token == "ctrl" || token == "control")
      result.modifiers |= NSEventModifierFlagControl;
    else if (token == "alt" || token == "option")
      result.modifiers |= NSEventModifierFlagOption;
    else if (token == "cmd" || token == "command" || token == "super" ||
             token == "meta")
      result.modifiers |= NSEventModifierFlagCommand;
    else if (const auto key = function_key_code(token); key && !has_key) {
      result.key_code = *key;
      has_key = true;
    } else {
      return fallback;
    }
  }
  result.modifiers = canonical_modifiers(result.modifiers);
  result.valid = has_key;
  return result.valid ? result : fallback;
}

bool hotkey_matches(const Hotkey &hotkey, NSEvent *event) {
  return hotkey.valid && event.keyCode == hotkey.key_code &&
         canonical_modifiers(event.modifierFlags) == hotkey.modifiers;
}

NSString *to_ns(const std::string &text) {
  NSString *value = [[NSString alloc] initWithBytes:text.data()
                                              length:text.size()
                                            encoding:NSUTF8StringEncoding];
  return value ? value : @"";
}

NSString *sanitize_visible_text(NSString *value) {
  if (!value || value.length == 0)
    return @"";
  NSMutableCharacterSet *ignored =
      [[NSCharacterSet controlCharacterSet] mutableCopy];
  [ignored formUnionWithCharacterSet:NSCharacterSet.illegalCharacterSet];
  [ignored addCharactersInRange:NSMakeRange(0x200B, 5)];
  [ignored addCharactersInRange:NSMakeRange(0x202A, 5)];
  [ignored addCharactersInRange:NSMakeRange(0x2060, 16)];
  [ignored addCharactersInString:@"­͏﻿"];
  NSString *withoutInvisible =
      [[value componentsSeparatedByCharactersInSet:ignored]
          componentsJoinedByString:@""];
  return [withoutInvisible
      stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
}

NSString *visible_ns(const std::string &text) {
  return sanitize_visible_text(to_ns(text));
}

std::string safe_json_string(const Json &value, const char *key) noexcept {
  try {
    if (!value.is_object())
      return {};
    const auto iterator = value.find(key);
    if (iterator == value.end() || !iterator->is_string())
      return {};
    return iterator->get<std::string>();
  } catch (const std::exception &) {
    return {};
  }
}

std::string to_utf8(NSString *text) {
  if (!text || text.length == 0)
    return {};
  NSData *data = [text dataUsingEncoding:NSUTF8StringEncoding];
  if (!data || data.length == 0)
    return {};
  return std::string(static_cast<const char *>(data.bytes), data.length);
}

std::size_t utf8_codepoint_count(NSString *text) {
  const std::string encoded = to_utf8(text);
  return static_cast<std::size_t>(std::count_if(
      encoded.begin(), encoded.end(), [](unsigned char byte) {
        return (byte & 0xc0U) != 0x80U;
      }));
}

Snapshot capture_snapshot(id<IMKTextInput, NSObject> client) {
  Snapshot result;
  if (!client)
    return result;
  const NSInteger length = [client length];
  const NSRange selection = [client selectedRange];
  if (length == NSNotFound || length < 0 ||
      selection.location == NSNotFound ||
      NSMaxRange(selection) > static_cast<NSUInteger>(length) ||
      length > 2 * 1024 * 1024)
    return result;

  NSRange actual = NSMakeRange(NSNotFound, 0);
  NSString *text = [client stringFromRange:NSMakeRange(0, length)
                                actualRange:&actual];
  if (!text || actual.location != 0 || actual.length != static_cast<NSUInteger>(length))
    return result;

  NSString *prefix = [text substringToIndex:selection.location];
  NSString *selected = [text substringWithRange:selection];
  result.valid = true;
  result.text = to_utf8(text);
  result.selected = to_utf8(selected);
  result.anchor = utf8_codepoint_count(prefix);
  result.cursor = result.anchor + utf8_codepoint_count(selected);
  result.selection = selection;
  result.document = NSMakeRange(0, static_cast<NSUInteger>(length));
  return result;
}

std::string context_id(id<IMKTextInput, NSObject> client) {
  NSString *identifier = [client uniqueClientIdentifierString];
  if (!identifier)
    identifier = [client bundleIdentifier];
  return "macos:" + to_utf8(identifier);
}

std::string resolve_recorder() {
  const auto root = vocotype::desktop::runtime_root();
  return vocotype::desktop::find_executable(
      "vocotype-audio-recorder",
      {root.empty() ? std::filesystem::path{}
                    : root / "bin/vocotype-audio-recorder"});
}

void load_configuration(ControllerState &state) {
  const Hotkey transcribe_default{kVK_F9, 0, true};
  const Hotkey polish_default{kVK_F9, NSEventModifierFlagShift, true};
  const Hotkey edit_default{kVK_F9, NSEventModifierFlagControl, true};
  state.transcribe_hotkey = transcribe_default;
  state.polish_hotkey = polish_default;
  state.edit_hotkey = edit_default;
  try {
    const Json shared = vocotype::desktop::read_shared_config(true);
    const Json platform = vocotype::desktop::read_macos_config(true);
    if (shared.contains("audio") && shared["audio"].is_object())
      state.min_recording_ms =
          std::max(0, shared["audio"].value("min_recording_ms", 500));
    if (shared.contains("slm") && shared["slm"].is_object()) {
      const auto &slm = shared["slm"];
      state.polish_min_chars = std::max(0, slm.value("min_chars", 8));
      state.polish_timeout_ms = std::max(1000, slm.value("timeout_ms", 20000));
      state.enable_thinking = slm.value("enable_thinking", false);
    }
    Json hotkeys = Json::object();
    if (platform.contains("hotkeys") && platform["hotkeys"].is_object())
      hotkeys = platform["hotkeys"];
    else if (shared.contains("hotkeys") && shared["hotkeys"].is_object())
      hotkeys = shared["hotkeys"];
    state.transcribe_hotkey = parse_hotkey(
        hotkeys.value("transcribe", "F9"), transcribe_default);
    state.polish_hotkey = parse_hotkey(
        hotkeys.value("polish", "Shift+F9"), polish_default);
    state.edit_hotkey =
        parse_hotkey(hotkeys.value("edit", "Ctrl+F9"), edit_default);
  } catch (const std::exception &) {
  }
}

Json poll_transcription(ControllerState &state, const std::string &task_id,
                        std::uint64_t generation,
                        const std::function<void(const std::string &)> &preview) {
  int after_seq = 0;
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(state.polish_timeout_ms + 120000);
  while (std::chrono::steady_clock::now() < deadline &&
         state.generation.load() == generation) {
    Json response = vocotype::desktop::unix_json_request(
        state.socket,
        {{"type", "polish_poll"},
         {"task_id", task_id},
         {"after_seq", after_seq}},
        4000);
    after_seq = response.value("last_seq", after_seq);
    const std::string text = response.value("preview", "");
    if (!text.empty() && preview)
      preview(text);
    const std::string status = response.value("status", "");
    if (vocotype::desktop::task_status_is_terminal(status))
      return response;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return {{"success", false}, {"status", "failed"}, {"error", "润色任务超时"}};
}

Json poll_edit(ControllerState &state, const std::string &task_id,
               std::uint64_t generation,
               const std::function<void(const std::string &)> &instruction) {
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(150);
  while (std::chrono::steady_clock::now() < deadline &&
         state.generation.load() == generation) {
    Json response = vocotype::desktop::unix_json_request(
        state.socket, {{"type", "edit_poll"}, {"task_id", task_id}}, 4000);
    const std::string text = response.value("instruction", "");
    if (!text.empty() && instruction)
      instruction(text);
    const std::string status = response.value("status", "");
    if (vocotype::desktop::task_status_is_terminal(status))
      return response;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  return {{"success", false}, {"status", "failed"}, {"error", "语音编辑任务超时"}};
}

std::optional<CGKeyCode> edit_key_code(const std::string &name) {
  static const std::unordered_map<std::string, CGKeyCode> keys = {
      {"left", kVK_LeftArrow},       {"right", kVK_RightArrow},
      {"up", kVK_UpArrow},          {"down", kVK_DownArrow},
      {"home", kVK_Home},           {"end", kVK_End},
      {"pageup", kVK_PageUp},       {"pagedown", kVK_PageDown},
      {"backspace", kVK_Delete},    {"delete", kVK_ForwardDelete},
      {"enter", kVK_Return},        {"tab", kVK_Tab},
      {"escape", kVK_Escape},       {"space", kVK_Space},
      {"a", kVK_ANSI_A},            {"c", kVK_ANSI_C},
      {"v", kVK_ANSI_V},            {"x", kVK_ANSI_X},
      {"z", kVK_ANSI_Z},
  };
  const auto found = keys.find(name);
  if (found == keys.end())
    return std::nullopt;
  return found->second;
}

CGEventFlags edit_modifiers(const Json &modifiers) {
  CGEventFlags flags = 0;
  if (!modifiers.is_array())
    return flags;
  for (const auto &item : modifiers) {
    if (!item.is_string())
      continue;
    const std::string value = item.get<std::string>();
    if (value == "shift")
      flags |= kCGEventFlagMaskShift;
    else if (value == "alt")
      flags |= kCGEventFlagMaskAlternate;
    else if (value == "ctrl" || value == "super")
      flags |= kCGEventFlagMaskCommand;
  }
  return flags;
}

void post_key_actions(const Json &actions) {
  if (!actions.is_array())
    return;
  for (const auto &action : actions) {
    if (!action.is_object())
      continue;
    const auto key = edit_key_code(action.value("key", ""));
    if (!key)
      continue;
    const CGEventFlags flags = edit_modifiers(action.value("modifiers", Json::array()));
    const int repeat = std::clamp(action.value("repeat", 1), 1, 100);
    for (int index = 0; index < repeat; ++index) {
      CGEventRef down = CGEventCreateKeyboardEvent(nullptr, *key, true);
      CGEventRef up = CGEventCreateKeyboardEvent(nullptr, *key, false);
      if (down && up) {
        CGEventSetFlags(down, flags);
        CGEventSetFlags(up, flags);
        CGEventPost(kCGAnnotatedSessionEventTap, down);
        CGEventPost(kCGAnnotatedSessionEventTap, up);
      }
      if (down)
        CFRelease(down);
      if (up)
        CFRelease(up);
    }
  }
}

} // namespace

UInt32 carbon_modifiers(NSEventModifierFlags modifiers) {
  UInt32 result = 0;
  if ((modifiers & NSEventModifierFlagShift) != 0)
    result |= shiftKey;
  if ((modifiers & NSEventModifierFlagControl) != 0)
    result |= controlKey;
  if ((modifiers & NSEventModifierFlagOption) != 0)
    result |= optionKey;
  if ((modifiers & NSEventModifierFlagCommand) != 0)
    result |= cmdKey;
  return result;
}

@interface VocoTypeGlobalHotKeyManager : NSObject
+ (instancetype)sharedManager;
- (void)install;
- (void)reload;
@end

OSStatus handle_carbon_hotkey(EventHandlerCallRef next_handler, EventRef event,
                              void *user_data) {
  (void)next_handler;
  (void)user_data;
  EventHotKeyID identifier{};
  const OSStatus status = GetEventParameter(
      event, kEventParamDirectObject, typeEventHotKeyID, nullptr,
      sizeof(identifier), nullptr, &identifier);
  if (status != noErr || identifier.signature != kVocoTypeHotKeySignature)
    return eventNotHandledErr;
  const BOOL pressed = GetEventKind(event) == kEventHotKeyPressed;
  if (pressed)
    g_hotkey_event_down = true;
  VocoTypeInputController *controller =
      g_voice_operation_controller ? g_voice_operation_controller
                                   : g_active_controller;
  if (!controller) {
    NSLog(@"VoCoType-linux: Carbon hotkey %u ignored; no text controller",
          static_cast<unsigned int>(identifier.id));
  } else {
    NSLog(@"VoCoType-linux: Carbon hotkey %u %@ routed=%@",
          static_cast<unsigned int>(identifier.id),
          pressed ? @"pressed" : @"released",
          g_voice_operation_controller ? @"operation-owner" : @"active-client");
    [controller handleGlobalHotKey:identifier.id pressed:pressed];
  }
  if (!pressed) {
    g_hotkey_event_down = false;
    if (g_hotkey_reload_pending)
      VocoTypeReloadGlobalHotKeys();
  }
  return noErr;
}

void handle_hotkey_reload_notification(CFNotificationCenterRef center,
                                       void *observer, CFStringRef name,
                                       const void *object,
                                       CFDictionaryRef user_info) {
  (void)center;
  (void)observer;
  (void)name;
  (void)object;
  (void)user_info;
  dispatch_async(dispatch_get_main_queue(), ^{
    VocoTypeReloadGlobalHotKeys();
  });
}

@implementation VocoTypeGlobalHotKeyManager {
  EventHandlerRef _handler;
  EventHotKeyRef _transcribe;
  EventHotKeyRef _polish;
  EventHotKeyRef _edit;
  BOOL _installed;
}

+ (instancetype)sharedManager {
  static VocoTypeGlobalHotKeyManager *manager = nil;
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    manager = [[VocoTypeGlobalHotKeyManager alloc] init];
  });
  return manager;
}

- (void)unregisterHotKeys {
  if (_transcribe) {
    UnregisterEventHotKey(_transcribe);
    _transcribe = nullptr;
  }
  if (_polish) {
    UnregisterEventHotKey(_polish);
    _polish = nullptr;
  }
  if (_edit) {
    UnregisterEventHotKey(_edit);
    _edit = nullptr;
  }
}

- (OSStatus)registerHotKey:(const Hotkey &)hotkey
               identifier:(UInt32)identifier
                reference:(EventHotKeyRef *)reference {
  if (!hotkey.valid)
    return paramErr;
  const EventHotKeyID hotkey_id{kVocoTypeHotKeySignature, identifier};
  return RegisterEventHotKey(
      hotkey.key_code, carbon_modifiers(hotkey.modifiers), hotkey_id,
      GetApplicationEventTarget(), 0, reference);
}

- (void)install {
  if (!_installed) {
    const EventTypeSpec types[] = {
        {kEventClassKeyboard, kEventHotKeyPressed},
        {kEventClassKeyboard, kEventHotKeyReleased},
    };
    const OSStatus status = InstallApplicationEventHandler(
        handle_carbon_hotkey, static_cast<UInt32>(std::size(types)), types,
        nullptr, &_handler);
    if (status != noErr) {
      NSLog(@"VoCoType-linux: cannot install global hotkey handler: %d",
            static_cast<int>(status));
      return;
    }
    CFNotificationCenterAddObserver(
        CFNotificationCenterGetDarwinNotifyCenter(), nullptr,
        handle_hotkey_reload_notification,
        CFSTR("io.github.LeonardNJU.VoCoTypeLinux.ReloadHotkeys"), nullptr,
        CFNotificationSuspensionBehaviorDeliverImmediately);
    _installed = YES;
  }
  [self reload];
}

- (void)reload {
  if (!_installed)
    return;
  [self unregisterHotKeys];
  ControllerState configuration;
  load_configuration(configuration);
  const OSStatus transcribe =
      [self registerHotKey:configuration.transcribe_hotkey
                identifier:kTranscribeHotKeyId
                 reference:&_transcribe];
  const OSStatus polish =
      [self registerHotKey:configuration.polish_hotkey
                identifier:kPolishHotKeyId
                 reference:&_polish];
  const OSStatus edit =
      [self registerHotKey:configuration.edit_hotkey
                identifier:kEditHotKeyId
                 reference:&_edit];
  if (transcribe != noErr || polish != noErr || edit != noErr) {
    NSLog(@"VoCoType-linux: hotkey registration status: F9=%d Shift+F9=%d Ctrl+F9=%d",
          static_cast<int>(transcribe), static_cast<int>(polish),
          static_cast<int>(edit));
  } else {
    NSLog(@"VoCoType-linux: palette hotkeys registered");
  }
}

- (void)dealloc {
  [self unregisterHotKeys];
  if (_handler)
    RemoveEventHandler(_handler);
}

@end

void VocoTypeInstallGlobalHotKeys(void) {
  [[VocoTypeGlobalHotKeyManager sharedManager] install];
}

void VocoTypeReloadGlobalHotKeys(void) {
  if (g_voice_operation_controller || g_hotkey_event_down) {
    if (!g_hotkey_reload_pending) {
      NSLog(@"VoCoType-linux: deferring hotkey reload until voice key cycle ends");
    }
    g_hotkey_reload_pending = true;
    return;
  }
  const bool was_pending = g_hotkey_reload_pending;
  g_hotkey_reload_pending = false;
  [[VocoTypeGlobalHotKeyManager sharedManager] reload];
  if (was_pending)
    NSLog(@"VoCoType-linux: applied deferred hotkey reload");
}

@interface VocoTypeStatusHitView : NSView
@property(nonatomic, copy) dispatch_block_t clickHandler;
@end

@implementation VocoTypeStatusHitView
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
  (void)event;
  return YES;
}
- (void)mouseDown:(NSEvent *)event {
  (void)event;
  if (self.clickHandler)
    self.clickHandler();
}
- (void)resetCursorRects {
  [super resetCursorRects];
  [self addCursorRect:self.bounds cursor:NSCursor.pointingHandCursor];
}
@end

@interface VocoTypeStatusPanel : NSObject
- (void)showText:(NSString *)text client:(id<IMKTextInput, NSObject>)client;
- (void)showText:(NSString *)text
          client:(id<IMKTextInput, NSObject>)client
   autoHideAfter:(NSTimeInterval)delay;
- (void)setCancelHandler:(dispatch_block_t)handler;
- (void)simulateClickForTesting;
- (void)hide;
- (NSDictionary<NSString *, id> *)debugMetrics;
@end

@implementation VocoTypeStatusPanel {
  NSPanel *_panel;
  NSVisualEffectView *_surface;
  NSImageView *_icon;
  NSTextField *_label;
  VocoTypeStatusHitView *_hitView;
  NSRect _lastAnchor;
  BOOL _hasAnchor;
  NSUInteger _presentationGeneration;
}

- (instancetype)init {
  self = [super init];
  if (!self)
    return nil;

  _panel = [[NSPanel alloc]
      initWithContentRect:NSMakeRect(0, 0, 180, 42)
                styleMask:NSWindowStyleMaskBorderless |
                          NSWindowStyleMaskNonactivatingPanel
                  backing:NSBackingStoreBuffered
                    defer:NO];
  _panel.opaque = NO;
  _panel.backgroundColor = NSColor.clearColor;
  _panel.hasShadow = YES;
  _panel.level = NSPopUpMenuWindowLevel;
  _panel.ignoresMouseEvents = NO;
  _panel.becomesKeyOnlyIfNeeded = YES;
  _panel.hidesOnDeactivate = NO;
  _panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                              NSWindowCollectionBehaviorFullScreenAuxiliary |
                              NSWindowCollectionBehaviorTransient;

  _surface = [[NSVisualEffectView alloc] initWithFrame:_panel.contentView.bounds];
  _surface.material = NSVisualEffectMaterialHUDWindow;
  _surface.blendingMode = NSVisualEffectBlendingModeBehindWindow;
  _surface.state = NSVisualEffectStateActive;
  _surface.wantsLayer = YES;
  _surface.layer.cornerRadius = 12.0;
  _surface.layer.masksToBounds = YES;
  _surface.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
  _panel.contentView = _surface;

  _icon = [[NSImageView alloc] initWithFrame:NSMakeRect(12.0, 12.0, 18.0, 18.0)];
  _icon.imageScaling = NSImageScaleProportionallyDown;
  [_surface addSubview:_icon];

  _label = [[NSTextField alloc] initWithFrame:NSMakeRect(38.0, 10.0, 128.0, 22.0)];
  _label.bezeled = NO;
  _label.drawsBackground = NO;
  _label.editable = NO;
  _label.selectable = NO;
  _label.textColor = NSColor.labelColor;
  _label.font = [NSFont systemFontOfSize:13.5 weight:NSFontWeightMedium];
  _label.alignment = NSTextAlignmentLeft;
  _label.maximumNumberOfLines = 2;
  _label.lineBreakMode = NSLineBreakByCharWrapping;
  _label.cell.wraps = YES;
  _label.cell.scrollable = NO;
  [_surface addSubview:_label];

  _hitView = [[VocoTypeStatusHitView alloc] initWithFrame:_surface.bounds];
  _hitView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
  _hitView.toolTip = @"点击取消语音输入";
  __weak VocoTypeStatusPanel *weakSelf = self;
  _hitView.clickHandler = ^{
    VocoTypeStatusPanel *strongSelf = weakSelf;
    if (!strongSelf)
      return;
    [strongSelf hide];
  };
  [_surface addSubview:_hitView positioned:NSWindowAbove relativeTo:nil];

  _hasAnchor = NO;
  _presentationGeneration = 0;
  return self;
}

- (void)setCancelHandler:(dispatch_block_t)handler {
  __weak VocoTypeStatusPanel *weakSelf = self;
  _hitView.clickHandler = ^{
    VocoTypeStatusPanel *strongSelf = weakSelf;
    if (!strongSelf)
      return;
    [strongSelf hide];
    if (handler)
      handler();
  };
}

- (void)simulateClickForTesting {
  if (_hitView.clickHandler)
    _hitView.clickHandler();
}

- (BOOL)validCaretRect:(NSRect)rect {
  return std::isfinite(rect.origin.x) && std::isfinite(rect.origin.y) &&
         std::isfinite(rect.size.height) && rect.size.height > 0.5;
}

- (NSRect)caretRectForClient:(id<IMKTextInput, NSObject>)client {
  if (!client)
    return NSZeroRect;
  @try {
    const NSRange selected = [client selectedRange];
    if (selected.location == NSNotFound)
      return NSZeroRect;
    NSRange actual = NSMakeRange(NSNotFound, 0);
    NSRect rect = [client firstRectForCharacterRange:NSMakeRange(selected.location, 0)
                                         actualRange:&actual];
    if ([self validCaretRect:rect]) {
      if (rect.size.width < 1.0)
        rect.size.width = 1.0;
      return rect;
    }
    rect = [client firstRectForCharacterRange:selected actualRange:&actual];
    if ([self validCaretRect:rect]) {
      if (rect.size.width < 1.0)
        rect.size.width = 1.0;
      return rect;
    }
  } @catch (NSException *exception) {
    (void)exception;
  }
  return NSZeroRect;
}

- (NSScreen *)screenForPoint:(NSPoint)point {
  for (NSScreen *screen in NSScreen.screens) {
    if (NSPointInRect(point, screen.frame))
      return screen;
  }
  NSScreen *main = NSScreen.mainScreen;
  return main ? main : NSScreen.screens.firstObject;
}

- (void)configurePresentationForText:(NSString **)text {
  NSString *value = *text ? *text : @"";
  NSString *symbol = @"mic.fill";
  NSColor *tint = NSColor.controlAccentColor;
  NSArray<NSArray<NSString *> *> *prefixes = @[
    @[ @"❌ ", @"exclamationmark.circle.fill", @"error" ],
    @[ @"⚠️ ", @"exclamationmark.triangle.fill", @"warning" ],
    @[ @"✓ ", @"checkmark.circle.fill", @"success" ],
    @[ @"⏳ ", @"hourglass", @"secondary" ],
    @[ @"✨ ", @"sparkles", @"accent" ],
    @[ @"✍️ ", @"pencil", @"accent" ],
    @[ @"🎤 ", @"mic.fill", @"accent" ],
  ];
  for (NSArray<NSString *> *entry in prefixes) {
    if ([value hasPrefix:entry[0]]) {
      value = [value substringFromIndex:entry[0].length];
      symbol = entry[1];
      if ([entry[2] isEqualToString:@"error"])
        tint = NSColor.systemRedColor;
      else if ([entry[2] isEqualToString:@"warning"])
        tint = NSColor.systemOrangeColor;
      else if ([entry[2] isEqualToString:@"success"])
        tint = NSColor.systemGreenColor;
      else if ([entry[2] isEqualToString:@"secondary"])
        tint = NSColor.secondaryLabelColor;
      break;
    }
  }
  _icon.image = [NSImage imageWithSystemSymbolName:symbol
                          accessibilityDescription:nil];
  _icon.contentTintColor = tint;
  *text = value;
}

- (NSSize)measuredTextSize:(NSString *)text width:(CGFloat)width {
  if (text.length == 0)
    return NSMakeSize(0.0, 0.0);
  NSRect bounds = [text boundingRectWithSize:NSMakeSize(width, CGFLOAT_MAX)
                                     options:NSStringDrawingUsesLineFragmentOrigin |
                                             NSStringDrawingUsesFontLeading
                                  attributes:@{NSFontAttributeName : _label.font}];
  return NSMakeSize(ceil(bounds.size.width), ceil(bounds.size.height));
}

- (NSString *)streamingTailForText:(NSString *)text
                             width:(CGFloat)width
                         maxHeight:(CGFloat)maxHeight {
  if ([self measuredTextSize:text width:width].height <= maxHeight)
    return text;

  NSUInteger low = 1;
  NSUInteger high = text.length;
  NSString *best = [@"…" stringByAppendingString:
      [text substringFromIndex:text.length - 1]];
  while (low <= high) {
    const NSUInteger keep = low + (high - low) / 2;
    const NSUInteger rawStart = text.length - keep;
    const NSRange composed = [text rangeOfComposedCharacterSequencesForRange:
        NSMakeRange(rawStart, keep)];
    NSString *candidate = [@"…" stringByAppendingString:
        [text substringWithRange:composed]];
    if ([self measuredTextSize:candidate width:width].height <= maxHeight) {
      best = candidate;
      low = keep + 1;
    } else {
      if (keep == 0)
        break;
      high = keep - 1;
    }
  }
  return best;
}

- (void)showText:(NSString *)text client:(id<IMKTextInput, NSObject>)client {
  [self showText:text client:client autoHideAfter:0.0];
}

- (void)showText:(NSString *)text
          client:(id<IMKTextInput, NSObject>)client
   autoHideAfter:(NSTimeInterval)delay {
  if (!NSThread.isMainThread) {
    dispatch_async(dispatch_get_main_queue(), ^{
      [self showText:text client:client autoHideAfter:delay];
    });
    return;
  }

  const NSUInteger presentation = ++_presentationGeneration;
  NSString *rawText = text ? text : @"";
  const BOOL streaming = [rawText hasPrefix:@"🎤 "];
  NSString *displayText = rawText;
  [self configurePresentationForText:&displayText];
  displayText = sanitize_visible_text(displayText);
  if (displayText.length == 0) {
    const BOOL hasVisibleStreamingText =
        streaming && _label.stringValue.length > 0 &&
        ![_label.stringValue isEqualToString:@"正在听…"];
    if (hasVisibleStreamingText) {
      NSLog(@"VoCoType-linux: ignored invisible streaming Pane update; preserving %@",
            _label.stringValue);
      return;
    }
    displayText = @"正在听…";
  }

  const NSRect caret = [self caretRectForClient:client];
  if ([self validCaretRect:caret]) {
    _lastAnchor = caret;
    _hasAnchor = YES;
  } else if (!_hasAnchor) {
    const NSPoint mouse = NSEvent.mouseLocation;
    _lastAnchor = NSMakeRect(mouse.x, mouse.y, 1.0, 18.0);
    _hasAnchor = YES;
  }

  const NSPoint anchorPoint =
      NSMakePoint(NSMidX(_lastAnchor), NSMidY(_lastAnchor));
  NSScreen *screen = [self screenForPoint:anchorPoint];
  NSRect visible = screen ? screen.visibleFrame : NSScreen.mainScreen.visibleFrame;
  const CGFloat maxPanelWidth =
      std::max(180.0, std::min(520.0, NSWidth(visible) - 12.0));
  const CGFloat maxTextWidth = maxPanelWidth - 52.0;
  const CGFloat lineHeight = ceil(_label.font.ascender - _label.font.descender +
                                  _label.font.leading);
  const CGFloat maxTextHeight = lineHeight * 2.0;
  const CGFloat singleLineWidth = ceil([displayText sizeWithAttributes:@{
    NSFontAttributeName : _label.font,
  }].width);
  const BOOL multiline = singleLineWidth > maxTextWidth;

  if (streaming && multiline) {
    displayText = [self streamingTailForText:displayText
                                       width:maxTextWidth
                                   maxHeight:maxTextHeight];
  }
  _label.maximumNumberOfLines = multiline ? 2 : 1;
  _label.lineBreakMode = multiline ? NSLineBreakByCharWrapping
                                   : NSLineBreakByTruncatingTail;
  _label.cell.wraps = multiline;
  _label.cell.scrollable = !multiline;
  _label.stringValue = displayText;

  const NSSize measured = [self measuredTextSize:displayText width:maxTextWidth];
  const CGFloat panelWidth = multiline
      ? maxPanelWidth
      : std::clamp(singleLineWidth + 58.0, 138.0, maxPanelWidth);
  const CGFloat labelWidth = std::max(80.0, panelWidth - 52.0);
  const CGFloat labelHeight = multiline
      ? std::clamp(measured.height, lineHeight, maxTextHeight)
      : lineHeight;
  const CGFloat panelHeight = std::max(42.0, labelHeight + 14.0);

  [_panel setContentSize:NSMakeSize(panelWidth, panelHeight)];
  _surface.frame = NSMakeRect(0.0, 0.0, panelWidth, panelHeight);
  _icon.frame = NSMakeRect(12.0, floor((panelHeight - 18.0) / 2.0), 18.0, 18.0);
  _label.frame = NSMakeRect(38.0, floor((panelHeight - labelHeight) / 2.0),
                            labelWidth, labelHeight);
  [_surface layoutSubtreeIfNeeded];

  CGFloat x = NSMidX(_lastAnchor) - panelWidth / 2.0;
  CGFloat y = NSMinY(_lastAnchor) - panelHeight - 8.0;
  if (y < NSMinY(visible) + 6.0)
    y = NSMaxY(_lastAnchor) + 8.0;
  x = std::clamp(x, NSMinX(visible) + 6.0,
                 NSMaxX(visible) - panelWidth - 6.0);
  y = std::clamp(y, NSMinY(visible) + 6.0,
                 NSMaxY(visible) - panelHeight - 6.0);
  [_panel setFrameOrigin:NSMakePoint(x, y)];
  [_panel orderFrontRegardless];
  if (delay > 0.0) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                 static_cast<int64_t>(delay * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
      if (self->_presentationGeneration == presentation &&
          self->_panel.isVisible)
        [self hide];
    });
  }
}

- (NSDictionary<NSString *, id> *)debugMetrics {
  return @{
    @"panel_width" : @(_panel.contentView.bounds.size.width),
    @"panel_height" : @(_panel.contentView.bounds.size.height),
    @"label_width" : @(_label.frame.size.width),
    @"label_height" : @(_label.frame.size.height),
    @"text" : _label.stringValue ? _label.stringValue : @"",
    @"lines" : @(_label.maximumNumberOfLines),
    @"visible" : @(_panel.isVisible),
    @"clickable" : @(!_panel.ignoresMouseEvents),
  };
}

- (void)hide {
  if (!NSThread.isMainThread) {
    dispatch_async(dispatch_get_main_queue(), ^{ [self hide]; });
    return;
  }
  ++_presentationGeneration;
  [_panel orderOut:nil];
  _hasAnchor = NO;
}
@end

NSDictionary<NSString *, id> *VocoTypeStatusPanelLongTextSmokeMetrics(void) {
  VocoTypeStatusPanel *panel = [[VocoTypeStatusPanel alloc] init];
  NSString *longPartial =
      @"🎤 这是一个用于验证流式识别面板长文本布局的超长测试句子。"
       "我们会持续加入很多已经说过的前文，模拟用户连续口述一大段内容时在线模型不断返回累积 partial。"
       "这些早期内容不应该把文字区域压缩到零，也不应该让整个面板只剩下麦克风图标。"
       "Pane 可以主动裁掉较早的上下文，但必须始终保留最近说出的部分，而且布局仍然是两行。"
       "现在继续补充更多测试文字，确保总长度显著超过两行显示容量，最后一句必须可见";
  [panel showText:longPartial client:nil];
  NSMutableDictionary<NSString *, id> *metrics =
      [[panel debugMetrics] mutableCopy];
  NSString *shown = metrics[@"text"];
  [panel showText:@"🎤 " client:nil];
  NSString *afterEmpty = [panel debugMetrics][@"text"];
  unichar invisibleCharacters[] = {0x0000, 0x200B, 0x2060, 0x3000};
  NSString *invisible = [NSString stringWithCharacters:invisibleCharacters
                                                 length:4];
  [panel showText:[@"🎤 " stringByAppendingString:invisible] client:nil];
  NSString *afterInvisible = [panel debugMetrics][@"text"];
  metrics[@"after_empty"] = afterEmpty ? afterEmpty : @"";
  metrics[@"after_invisible"] = afterInvisible ? afterInvisible : @"";

  __block NSString *asyncOwnedText = nil;
  NSString *expectedAsyncText =
      @"这是跨线程延迟派发后的完整中文流式文本，源回调栈退出后仍必须保持逐字完整。";
  {
    const std::string borrowed = to_utf8(expectedAsyncText);
    auto owned = std::make_shared<const std::string>(borrowed);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 20 * NSEC_PER_MSEC),
                   dispatch_get_main_queue(), ^{
      asyncOwnedText = to_ns(*owned);
    });
  }
  NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:1.0];
  while (!asyncOwnedText && deadline.timeIntervalSinceNow > 0.0) {
    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
  }
  metrics[@"async_owned_text"] = asyncOwnedText ? asyncOwnedText : @"";
  const BOOL success = [metrics[@"visible"] boolValue] &&
                       [metrics[@"panel_width"] doubleValue] >= 180.0 &&
                       [metrics[@"panel_height"] doubleValue] > 42.0 &&
                       [metrics[@"label_width"] doubleValue] > 100.0 &&
                       [metrics[@"label_height"] doubleValue] > 18.0 &&
                       [metrics[@"lines"] integerValue] == 2 &&
                       shown.length > 0 &&
                       [shown hasPrefix:@"…"] &&
                       [shown hasSuffix:@"最后一句必须可见"] &&
                       [afterEmpty isEqualToString:shown] &&
                       [afterInvisible isEqualToString:shown] &&
                       ![afterInvisible isEqualToString:@"正在听…"] &&
                       [asyncOwnedText isEqualToString:expectedAsyncText];
  metrics[@"success"] = @(success);
  [panel hide];
  return metrics;
}

NSDictionary<NSString *, id> *VocoTypeShortRecordingResultSmokeMetrics(void) {
  const Json empty_result;
  const Json malformed_result = Json::array({"unexpected"});
  const Json completed_result = {{"status", "completed"}, {"phase", "final"}};
  const std::string empty_status = safe_json_string(empty_result, "status");
  const std::string empty_phase = safe_json_string(empty_result, "phase");
  const std::string malformed_status =
      safe_json_string(malformed_result, "status");
  const std::string completed_status =
      safe_json_string(completed_result, "status");
  const std::string completed_phase =
      safe_json_string(completed_result, "phase");
  const BOOL success = empty_status.empty() && empty_phase.empty() &&
                       malformed_status.empty() &&
                       completed_status == "completed" &&
                       completed_phase == "final";
  return @{
    @"success" : @(success),
    @"empty_status" : to_ns(empty_status),
    @"empty_phase" : to_ns(empty_phase),
    @"malformed_status" : to_ns(malformed_status),
    @"completed_status" : to_ns(completed_status),
    @"completed_phase" : to_ns(completed_phase),
  };
}

NSDictionary<NSString *, id> *VocoTypeVoiceLifecycleSmokeMetrics(void) {
  ControllerState busyState;
  busyState.busy.store(true);
  const BOOL busyAccepted = begin_recording_state(busyState);
  const BOOL busyLeftClean = !busyState.recording.load();

  ControllerState readyState;
  const BOOL readyAccepted = begin_recording_state(readyState);
  const BOOL readyMarkedRecording = readyState.recording.load();

  ControllerState timingState;
  timingState.min_recording_ms = 500;
  const std::int64_t timingNow = steady_now_ns();
  const BOOL unstartedIsTooShort = recording_is_too_short(timingState, timingNow);
  timingState.microphone_started_ns.store(timingNow - 100000000);
  const BOOL actualShortIsTooShort = recording_is_too_short(timingState, timingNow);
  timingState.microphone_started_ns.store(timingNow - 700000000);
  const BOOL actualLongIsAccepted = !recording_is_too_short(timingState, timingNow);

  const bool savedHotkeyDown = g_hotkey_event_down;
  const bool savedReloadPending = g_hotkey_reload_pending;
  g_hotkey_event_down = true;
  g_hotkey_reload_pending = false;
  VocoTypeReloadGlobalHotKeys();
  const BOOL hotkeyReloadDeferred = g_hotkey_reload_pending;
  g_hotkey_event_down = savedHotkeyDown;
  g_hotkey_reload_pending = savedReloadPending;

  VocoTypeStatusPanel *panel = [[VocoTypeStatusPanel alloc] init];
  __block NSInteger cancelCount = 0;
  [panel setCancelHandler:^{ ++cancelCount; }];
  [panel showText:@"🎤 点击我应立即取消" client:nil];
  const BOOL clickable = [[panel debugMetrics][@"clickable"] boolValue];
  [panel simulateClickForTesting];
  const BOOL clickHidden = ![[panel debugMetrics][@"visible"] boolValue];

  [panel showText:@"⚠️ 录音过短" client:nil autoHideAfter:0.05];
  NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:0.25];
  while (deadline.timeIntervalSinceNow > 0.0) {
    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
  }
  const BOOL terminalAutoHidden =
      ![[panel debugMetrics][@"visible"] boolValue];

  [panel showText:@"⚠️ 旧提示" client:nil autoHideAfter:0.05];
  [panel showText:@"🎤 新录音仍在继续" client:nil];
  deadline = [NSDate dateWithTimeIntervalSinceNow:0.15];
  while (deadline.timeIntervalSinceNow > 0.0) {
    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
  }
  const NSDictionary<NSString *, id> *replacementMetrics = [panel debugMetrics];
  const BOOL staleTimerSafe =
      [replacementMetrics[@"visible"] boolValue] &&
      [replacementMetrics[@"text"] isEqualToString:@"新录音仍在继续"];
  [panel hide];

  const BOOL success = !busyAccepted && busyLeftClean && readyAccepted &&
                       readyMarkedRecording && clickable && clickHidden &&
                       cancelCount == 1 && terminalAutoHidden && staleTimerSafe &&
                       hotkeyReloadDeferred && unstartedIsTooShort &&
                       actualShortIsTooShort && actualLongIsAccepted;
  return @{
    @"success" : @(success),
    @"busy_accepted" : @(busyAccepted),
    @"busy_left_clean" : @(busyLeftClean),
    @"ready_accepted" : @(readyAccepted),
    @"ready_marked_recording" : @(readyMarkedRecording),
    @"clickable" : @(clickable),
    @"click_hidden" : @(clickHidden),
    @"cancel_count" : @(cancelCount),
    @"terminal_auto_hidden" : @(terminalAutoHidden),
    @"stale_timer_safe" : @(staleTimerSafe),
    @"hotkey_reload_deferred" : @(hotkeyReloadDeferred),
    @"unstarted_is_too_short" : @(unstartedIsTooShort),
    @"actual_short_is_too_short" : @(actualShortIsTooShort),
    @"actual_long_is_accepted" : @(actualLongIsAccepted),
  };
}

@interface VocoTypeInputController ()
- (void)showStatus:(NSString *)text;
- (BOOL)startVoiceMode:(VoiceMode)mode hotkey:(Hotkey)hotkey;
- (void)stopVoiceOperation;
- (void)cancelVoiceOperation;
- (void)applyEditResult:(const Json &)poll
               snapshot:(const Snapshot &)snapshot
                context:(const std::string &)clientContext;
@end

@implementation VocoTypeInputController {
  ControllerState *_state;
  VocoTypeStatusPanel *_status;
}

- (instancetype)initWithServer:(IMKServer *)server
                      delegate:(id)delegate
                        client:(id)inputClient {
  self = [super initWithServer:server delegate:delegate client:inputClient];
  if (!self)
    return nil;
  _state = new ControllerState();
  load_configuration(*_state);
  _status = [[VocoTypeStatusPanel alloc] init];
  __weak VocoTypeInputController *weakSelf = self;
  [_status setCancelHandler:^{
    VocoTypeInputController *strongSelf = weakSelf;
    if (!strongSelf)
      return;
    NSLog(@"VoCoType-linux: status pane clicked; cancelling/cleaning voice state");
    [strongSelf cancelVoiceOperation];
  }];
  return self;
}

- (void)dealloc {
  if (g_active_controller == self)
    g_active_controller = nil;
  [self cancelVoiceOperation];
  delete _state;
  _state = nullptr;
}

- (NSUInteger)recognizedEvents:(id)sender {
  (void)sender;
  return NSEventMaskKeyDown | NSEventMaskKeyUp | NSEventMaskFlagsChanged;
}

- (void)activateServer:(id)sender {
  [super activateServer:sender];
  g_active_controller = self;
  NSLog(@"VoCoType-linux: palette text client activated: %@",
        [self.client bundleIdentifier]);
  if (_state) {
    load_configuration(*_state);
    const std::string socket = _state->socket;
    const auto config = vocotype::desktop::runtime_config_path();
    std::thread([socket, config] {
      (void)vocotype::desktop::ensure_native_core(socket, config, 45000);
    }).detach();
  }
  VocoTypeReloadGlobalHotKeys();
}

- (void)deactivateServer:(id)sender {
  const bool operation_active =
      _state && (_state->recording.load() || _state->busy.load());
  // Keep the most recently activated text controller available to the Carbon
  // global-hotkey path. InputMethodKit may deactivate a Palette controller
  // while its client still owns the insertion focus; clearing it here makes
  // the first F9 work through handleEvent and every later F9 disappear.
  if (!operation_active)
    [self cancelVoiceOperation];
  [super deactivateServer:sender];
}

- (void)showStatus:(NSString *)text {
  NSTimeInterval autoHide = 0.0;
  if ([text hasPrefix:@"⚠️"])
    autoHide = 2.0;
  else if ([text hasPrefix:@"❌"])
    autoHide = 5.0;
  else if ([text hasPrefix:@"✓"])
    autoHide = 2.5;
  [_status showText:text client:self.client autoHideAfter:autoHide];
}

- (BOOL)startVoiceMode:(VoiceMode)mode hotkey:(Hotkey)hotkey {
  if (!_state ||
      (g_voice_operation_controller &&
       g_voice_operation_controller != self) ||
      !begin_recording_state(*_state))
    return NO;
  g_voice_operation_controller = self;
  _state->recorder_path = resolve_recorder();
  if (_state->recorder_path.empty()) {
    _state->recording.store(false);
    release_voice_operation(self);
    [self showStatus:@"❌ 找不到原生录音器"];
    return NO;
  }
  _state->mode = mode;
  _state->active_hotkey = hotkey;
  _state->snapshot = mode == VoiceMode::edit
                         ? capture_snapshot(self.client)
                         : Snapshot{};
  if (mode == VoiceMode::edit && !_state->snapshot.valid) {
    _state->recording.store(false);
    release_voice_operation(self);
    [self showStatus:@"❌ 当前输入框不支持语音编辑"];
    return NO;
  }

  const std::uint64_t generation = ++_state->generation;
  _state->microphone_started_ns.store(0, std::memory_order_release);
  _state->recording_started = std::chrono::steady_clock::now();
  _state->last_partial.clear();
  NSLog(@"VoCoType-linux: hotkey accepted; showing pane immediately");
  if (mode == VoiceMode::edit)
    [self showStatus:@"🎤 正在听编辑指令…"];
  else if (mode == VoiceMode::polish)
    [self showStatus:@"✨ 正在听，将自动润色…"];
  else
    [self showStatus:@"🎤 正在听…"];

  const std::string socket = _state->socket;
  const auto config = vocotype::desktop::runtime_config_path();
  std::thread([socket, config] {
    (void)vocotype::desktop::ensure_native_core(socket, config, 45000);
  }).detach();

  __weak VocoTypeInputController *weak_self = self;
  try {
    std::lock_guard lock(_state->recorder_mutex);
    _state->recorder = std::make_unique<vocotype::desktop::RecorderProcess>();
    _state->recorder->start(
        _state->recorder_path,
        [weak_self, generation](const std::string &type, const std::string &value) {
          if (type != "partial" && type != "error" && type != "recording" &&
              type != "preview_recovering" && type != "preview_recovered" &&
              type != "preview_unavailable")
            return;
          if (type == "recording") {
            VocoTypeInputController *operation = weak_self;
            if (operation && operation->_state &&
                operation->_state->generation.load() == generation) {
              std::int64_t expected = 0;
              operation->_state->microphone_started_ns.compare_exchange_strong(
                  expected, steady_now_ns(), std::memory_order_acq_rel,
                  std::memory_order_acquire);
            }
          }
          auto event = std::make_shared<const std::pair<std::string, std::string>>(
              type, value);
          dispatch_async(dispatch_get_main_queue(), ^{
            VocoTypeInputController *strong_self = weak_self;
            if (!strong_self || !strong_self->_state ||
                strong_self->_state->generation.load() != generation)
              return;
            const std::string &event_type = event->first;
            const std::string &event_value = event->second;
            if (event_type == "partial") {
              const std::string normalized =
                  vocotype::desktop::trim_preview_text(event_value);
              NSString *visible = visible_ns(normalized);
              if (visible.length == 0) {
                NSLog(@"VoCoType-linux: ignored invisible partial bytes=%zu",
                      normalized.size());
                return;
              }
              strong_self->_state->last_partial = to_utf8(visible);
              const auto latency = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now() -
                  strong_self->_state->recording_started).count();
              NSLog(@"VoCoType-linux: first/updated partial at %lld ms chars=%lu",
                    static_cast<long long>(latency),
                    static_cast<unsigned long>(visible.length));
              [strong_self showStatus:[@"🎤 " stringByAppendingString:visible]];
            } else if (event_type == "preview_recovering") {
              NSLog(@"VoCoType-linux: recovering streaming preview: %@", to_ns(event_value));
              if (!strong_self->_state->last_partial.empty()) {
                NSString *status = [[@"🎤 " stringByAppendingString:
                    to_ns(strong_self->_state->last_partial)]
                    stringByAppendingString:@" · 正在恢复实时预览…"];
                [strong_self showStatus:status];
              }
            } else if (event_type == "preview_recovered") {
              NSLog(@"VoCoType-linux: streaming preview recovered");
              if (!strong_self->_state->last_partial.empty())
                [strong_self showStatus:[@"🎤 " stringByAppendingString:
                    to_ns(strong_self->_state->last_partial)]];
            } else if (event_type == "preview_unavailable") {
              NSLog(@"VoCoType-linux: streaming preview unavailable: %@", to_ns(event_value));
              if (!strong_self->_state->last_partial.empty()) {
                NSString *status = [[@"🎤 " stringByAppendingString:
                    to_ns(strong_self->_state->last_partial)]
                    stringByAppendingString:@" · 实时预览暂不可用"];
                [strong_self showStatus:status];
              }
            } else if (event_type == "recording") {
              const auto latency = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::steady_clock::now() -
                  strong_self->_state->recording_started).count();
              NSLog(@"VoCoType-linux: microphone recording at %lld ms via %@",
                    static_cast<long long>(latency), to_ns(event_value));
            } else {
              [strong_self showStatus:[@"❌ " stringByAppendingString:to_ns(event_value)]];
            }
          });
        });
  } catch (const std::exception &error) {
    _state->recording.store(false);
    release_voice_operation(self);
    [self showStatus:[@"❌ 启动录音失败：" stringByAppendingString:to_ns(error.what())]];
    return NO;
  }
  return YES;
}

- (void)stopVoiceOperation {
  if (!_state || !_state->recording.load() || _state->busy.load())
    return;
  _state->recording.store(false);
  _state->busy.store(true);
  const auto release_started = std::chrono::steady_clock::now();
  NSLog(@"VoCoType-linux: key released; final pipeline started");
  const std::int64_t microphone_started_ns =
      _state->microphone_started_ns.load(std::memory_order_acquire);
  const bool too_short = recording_is_too_short(*_state, steady_now_ns());
  if (microphone_started_ns <= 0) {
    NSLog(@"VoCoType-linux: key released before microphone became ready; cancelling");
  }
  const VoiceMode mode = _state->mode;
  const Snapshot snapshot = _state->snapshot;
  const std::uint64_t generation = _state->generation.load();
  id<IMKTextInput, NSObject> target_client = self.client;
  const std::string client_context = context_id(target_client);
  if (too_short) {
    [self showStatus:@"⚠️ 录音过短"];
  } else if (!_state->last_partial.empty()) {
    NSString *partial = to_ns(_state->last_partial);
    NSString *confirming = [[@"🎤 " stringByAppendingString:partial]
        stringByAppendingString:@" · 正在确认…"];
    [self showStatus:confirming];
  } else {
    [self showStatus:@"⏳ 正在识别…"];
  }

  VocoTypeInputController *controller = self;
  std::thread([controller, generation, too_short, mode, snapshot,
               client_context, target_client, release_started] {
    ControllerState *state = controller->_state;
    if (!state)
      return;
    std::string audio_path;
    std::string error;
    Json final_result = Json::object();
    try {
      {
        std::lock_guard lock(state->recorder_mutex);
        if (state->recorder) {
          if (too_short) {
            state->recorder->cancel_async();
            state->recorder.reset();
            const auto cancel_ms =
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - release_started)
                    .count();
            NSLog(@"VoCoType-linux: short recording cancelled after %lld ms",
                  static_cast<long long>(cancel_ms));
          } else {
            audio_path = state->recorder->stop();
            const auto wav_ms =
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - release_started)
                    .count();
            NSLog(@"VoCoType-linux: WAV ready after %lld ms",
                  static_cast<long long>(wav_ms));
            state->recorder.reset();
          }
        }
      }
      if (!too_short && audio_path.empty()) {
        error = "录音失败";
      } else if (!too_short && !vocotype::desktop::ensure_native_core(
                     state->socket, vocotype::desktop::runtime_config_path(),
                     45000)) {
        error = "原生语音核心启动失败";
      } else if (!too_short && mode == VoiceMode::edit) {
        Json started = vocotype::desktop::unix_json_request(
            state->socket,
            {{"type", "edit_start"},
             {"audio_path", audio_path},
             {"context_id", client_context},
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
          __weak VocoTypeInputController *weak_controller = controller;
          final_result = poll_edit(
              *state, started.value("task_id", ""), generation,
              [weak_controller, generation](const std::string &text) {
                auto owned_text = std::make_shared<const std::string>(text);
                dispatch_async(dispatch_get_main_queue(), ^{
                  VocoTypeInputController *target = weak_controller;
                  if (target && target->_state &&
                      target->_state->generation.load() == generation)
                    [target showStatus:[@"✍️ " stringByAppendingString:
                        to_ns(*owned_text)]];
                });
              });
        }
      } else if (!too_short && mode == VoiceMode::polish) {
        Json started = vocotype::desktop::unix_json_request(
            state->socket,
            {{"type", "transcribe_start"},
             {"audio_path", audio_path},
             {"long_mode", true},
             {"polish_min_chars", state->polish_min_chars},
             {"polish_timeout_ms", state->polish_timeout_ms},
             {"enable_thinking", state->enable_thinking}},
            5000);
        if (!started.value("success", false))
          error = started.value("error", "转录启动失败");
        else {
          __weak VocoTypeInputController *weak_controller = controller;
          final_result = poll_transcription(
              *state, started.value("task_id", ""), generation,
              [weak_controller, generation](const std::string &text) {
                auto owned_text = std::make_shared<const std::string>(text);
                dispatch_async(dispatch_get_main_queue(), ^{
                  VocoTypeInputController *target = weak_controller;
                  if (target && target->_state &&
                      target->_state->generation.load() == generation)
                    [target showStatus:[@"✨ " stringByAppendingString:
                        to_ns(*owned_text)]];
                });
              });
        }
      } else if (!too_short) {
        const auto asr_started = std::chrono::steady_clock::now();
        final_result = vocotype::desktop::unix_json_request(
            state->socket,
            {{"type", "transcribe"},
             {"audio_path", audio_path},
             {"long_mode", false}},
            120000);
        const auto asr_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - asr_started).count();
        const auto total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - release_started).count();
        NSLog(@"VoCoType-linux: final ASR finished in %lld ms; release total %lld ms",
              static_cast<long long>(asr_ms),
              static_cast<long long>(total_ms));
        std::filesystem::remove(audio_path);
      }
    } catch (const std::exception &exception) {
      error = exception.what();
      if (!audio_path.empty())
        std::filesystem::remove(audio_path);
    }

    const auto pipeline_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - release_started).count();
    NSLog(@"VoCoType-linux: final pipeline completed after %lld ms mode=%d status=%@ phase=%@",
          static_cast<long long>(pipeline_ms), static_cast<int>(mode),
          to_ns(safe_json_string(final_result, "status")),
          to_ns(safe_json_string(final_result, "phase")));

    dispatch_async(dispatch_get_main_queue(), ^{
      if (!controller->_state ||
          controller->_state->generation.load() != generation)
        return;
      controller->_state->busy.store(false);
      controller->_state->microphone_started_ns.store(0, std::memory_order_release);
      controller->_state->last_partial.clear();
      if (too_short) {
        [controller showStatus:@"⚠️ 录音过短"];
        release_voice_operation(controller);
        return;
      }
      if (!error.empty()) {
        [controller showStatus:[@"❌ " stringByAppendingString:to_ns(error)]];
        release_voice_operation(controller);
        return;
      }
      if (mode == VoiceMode::edit) {
        [controller applyEditResult:final_result snapshot:snapshot context:client_context];
        release_voice_operation(controller);
      } else {
        const std::string text =
            mode == VoiceMode::polish
                ? final_result.value("final_text",
                                     final_result.value("original_text", ""))
                : final_result.value("text", "");
        if (final_result.value("success", false) && !text.empty()) {
          BOOL committed = NO;
          NSString *commitError = nil;
          @try {
            if (target_client) {
              [target_client insertText:to_ns(text)
                       replacementRange:NSMakeRange(NSNotFound, NSNotFound)];
              committed = YES;
            } else {
              commitError = @"目标输入框已失效";
            }
          } @catch (NSException *exception) {
            commitError = exception.reason ? exception.reason : @"文本提交失败";
          }
          if (committed) {
            NSLog(@"VoCoType-linux: committed final text chars=%zu context=%@",
                  text.size(), to_ns(client_context));
            [controller->_status hide];
          } else {
            [controller showStatus:[@"❌ " stringByAppendingString:
                commitError ? commitError : @"文本提交失败"]];
          }
        } else {
          [controller showStatus:[@"❌ " stringByAppendingString:
              to_ns(final_result.value("error", "识别失败"))]];
        }
        release_voice_operation(controller);
      }
    });
  }).detach();
}

- (void)applyEditResult:(const Json &)poll
               snapshot:(const Snapshot &)snapshot
                context:(const std::string &)client_context {
  Json result = poll.contains("result") && poll["result"].is_object()
                    ? poll["result"]
                    : poll;
  if (!result.value("success", false)) {
    [self showStatus:[@"❌ " stringByAppendingString:
        to_ns(result.value("error", poll.value("error", "语音编辑失败")))]];
    return;
  }
  const std::string mode = result.value("mode", "no_op");
  if (mode == "key_actions") {
    post_key_actions(result.value("key_actions", Json::array()));
  } else if (mode == "replace") {
    const Snapshot current = capture_snapshot(self.client);
    if (!current.valid || current.text != snapshot.text ||
        current.selection.location != snapshot.selection.location ||
        current.selection.length != snapshot.selection.length) {
      [self showStatus:@"❌ 输入框内容已变化，请重试"];
      return;
    }
    const std::string replacement = result.value("new_text", "");
    [self.client insertText:to_ns(replacement)
           replacementRange:snapshot.document];
    try {
      (void)vocotype::desktop::unix_json_request(
          _state->socket,
          {{"type", "edit_applied"},
           {"context_id", client_context},
           {"original_text", snapshot.text},
           {"new_text", replacement},
           {"record_history", result.value("record_history", true)}},
          2000);
    } catch (const std::exception &) {
    }
  } else if (mode == "commit_only") {
    const std::string text = result.value("new_text", "");
    if (!text.empty())
      [self.client insertText:to_ns(text)
             replacementRange:NSMakeRange(NSNotFound, NSNotFound)];
  }
  const std::string hint = result.value("hint", "");
  if (!hint.empty())
    [self showStatus:[@"✓ " stringByAppendingString:to_ns(hint)]];
  else
    [_status hide];
}

- (void)cancelVoiceOperation {
  if (!_state) {
    [_status hide];
    release_voice_operation(self);
    return;
  }
  ++_state->generation;
  _state->recording.store(false);
  _state->busy.store(false);
  _state->microphone_started_ns.store(0, std::memory_order_release);
  _state->last_partial.clear();
  [_status hide];
  release_voice_operation(self);

  // Do not let a stale recorder delay the click-to-dismiss path. If the final
  // pipeline currently owns the mutex, it is already stopping the same child;
  // generation invalidation above guarantees that its late result is ignored.
  std::unique_lock lock(_state->recorder_mutex, std::try_to_lock);
  if (lock.owns_lock() && _state->recorder) {
    _state->recorder->cancel_async();
    _state->recorder.reset();
  }
}

- (BOOL)handleGlobalHotKey:(NSUInteger)identifier pressed:(BOOL)pressed {
  if (!_state)
    return NO;
  VoiceMode mode = VoiceMode::none;
  Hotkey hotkey;
  if (identifier == kTranscribeHotKeyId) {
    mode = VoiceMode::transcribe;
    hotkey = _state->transcribe_hotkey;
  } else if (identifier == kPolishHotKeyId) {
    mode = VoiceMode::polish;
    hotkey = _state->polish_hotkey;
  } else if (identifier == kEditHotKeyId) {
    mode = VoiceMode::edit;
    hotkey = _state->edit_hotkey;
  }
  if (mode == VoiceMode::none)
    return NO;
  if (pressed)
    return [self startVoiceMode:mode hotkey:hotkey];
  if (_state->recording.load() && _state->mode == mode) {
    [self stopVoiceOperation];
    return YES;
  }
  return NO;
}

- (BOOL)handleEvent:(NSEvent *)event client:(id)sender {
  (void)sender;
  if (!_state)
    return NO;
  if (event.type == NSEventTypeKeyUp && _state->recording.load() &&
      event.keyCode == _state->active_hotkey.key_code) {
    [self stopVoiceOperation];
    return YES;
  }
  if (event.type != NSEventTypeKeyDown || event.isARepeat)
    return NO;
  if (event.keyCode == kVK_Escape && g_voice_operation_controller) {
    [g_voice_operation_controller cancelVoiceOperation];
    return YES;
  }
  if (_state->recording.load() || _state->busy.load())
    return NO;

  VoiceMode mode = VoiceMode::none;
  Hotkey hotkey;
  if (hotkey_matches(_state->edit_hotkey, event)) {
    mode = VoiceMode::edit;
    hotkey = _state->edit_hotkey;
  } else if (hotkey_matches(_state->polish_hotkey, event)) {
    mode = VoiceMode::polish;
    hotkey = _state->polish_hotkey;
  } else if (hotkey_matches(_state->transcribe_hotkey, event)) {
    mode = VoiceMode::transcribe;
    hotkey = _state->transcribe_hotkey;
  }
  if (mode == VoiceMode::none)
    return NO;
  const BOOL started = [self startVoiceMode:mode hotkey:hotkey];
  if (started)
    NSLog(@"VoCoType-linux: IMK event hotkey accepted mode=%d",
          static_cast<int>(mode));
  return started;
}

- (NSMenu *)menu {
  NSMenu *menu = [[NSMenu alloc] initWithTitle:@"VoCoType-linux"];
  NSMenuItem *download = [[NSMenuItem alloc]
      initWithTitle:@"下载/修复语音模型"
             action:@selector(downloadModels:)
      keyEquivalent:@""];
  download.target = self;
  [menu addItem:download];
  NSMenuItem *configuration = [[NSMenuItem alloc]
      initWithTitle:@"打开配置目录"
             action:@selector(openConfiguration:)
      keyEquivalent:@""];
  configuration.target = self;
  [menu addItem:configuration];
  return menu;
}

- (void)downloadModels:(id)sender {
  (void)sender;
  const auto root = vocotype::desktop::runtime_root();
  const auto manager = root / "bin/vocotype-model-manager";
  if (!std::filesystem::is_regular_file(manager)) {
    [self showStatus:@"❌ 找不到模型管理器"];
    return;
  }
  [self showStatus:@"⬇️ 正在下载/校验模型…"];
  VocoTypeInputController *controller = self;
  std::thread([controller, manager] {
    const std::string command = "\"" + manager.string() + "\" --download --all";
    const int status = std::system(command.c_str());
    dispatch_async(dispatch_get_main_queue(), ^{
      [controller showStatus:status == 0 ? @"✓ 模型已就绪"
                                          : @"❌ 模型下载失败"];
    });
  }).detach();
}

- (void)openConfiguration:(id)sender {
  (void)sender;
  const auto path = vocotype::desktop::config_dir();
  std::filesystem::create_directories(path);
  NSString *directory = [NSString stringWithUTF8String:path.c_str()];
  [[NSWorkspace sharedWorkspace] openURL:
      [NSURL fileURLWithPath:(directory ? directory : @"")]];
}

- (void)showPreferences:(id)sender {
  (void)sender;
  [NSNotificationCenter.defaultCenter
      postNotificationName:@"VoCoTypeLinuxShowSettings"
                    object:nil];
}

@end
