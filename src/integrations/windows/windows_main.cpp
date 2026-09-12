#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mmsystem.h>

#include <atomic>
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {
std::atomic_bool f9_down{false};
HHOOK keyboard_hook = nullptr;

std::string utf8_from_wide(std::wstring_view value) {
  if (value.empty())
    return {};
  const int bytes = WideCharToMultiByte(CP_UTF8, 0, value.data(),
                                        static_cast<int>(value.size()), nullptr,
                                        0, nullptr, nullptr);
  if (bytes <= 0)
    return {};
  std::string output(static_cast<std::size_t>(bytes), '\0');
  WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()),
                      output.data(), bytes, nullptr, nullptr);
  return output;
}

std::wstring wide_from_utf8(std::string_view value) {
  if (value.empty())
    return {};
  const int chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
                                        value.data(), static_cast<int>(value.size()),
                                        nullptr, 0);
  if (chars <= 0)
    return {};
  std::wstring output(static_cast<std::size_t>(chars), L'\0');
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value.data(),
                      static_cast<int>(value.size()), output.data(), chars);
  return output;
}

bool inject_unicode(std::wstring_view text) {
  std::vector<INPUT> inputs;
  inputs.reserve(text.size() * 2);
  for (const wchar_t unit : text) {
    INPUT down{};
    down.type = INPUT_KEYBOARD;
    down.ki.wScan = static_cast<WORD>(unit);
    down.ki.dwFlags = KEYEVENTF_UNICODE;
    inputs.push_back(down);

    INPUT up = down;
    up.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP;
    inputs.push_back(up);
  }
  if (inputs.empty())
    return true;
  const UINT sent = SendInput(static_cast<UINT>(inputs.size()), inputs.data(),
                              static_cast<int>(sizeof(INPUT)));
  return sent == inputs.size();
}

void print_audio_devices() {
  const UINT count = waveInGetNumDevs();
  std::cout << "{\"type\":\"audio_devices\",\"count\":" << count
            << ",\"devices\":[";
  bool first = true;
  for (UINT index = 0; index < count; ++index) {
    WAVEINCAPSW caps{};
    if (waveInGetDevCapsW(index, &caps, sizeof(caps)) != MMSYSERR_NOERROR)
      continue;
    if (!first)
      std::cout << ',';
    first = false;
    std::string name = utf8_from_wide(caps.szPname);
    for (char &ch : name) {
      if (ch == '"' || ch == '\\')
        ch = '_';
    }
    std::cout << "{\"id\":" << index << ",\"name\":\"" << name
              << "\",\"channels\":" << caps.wChannels << '}';
  }
  std::cout << "]}\n";
}

LRESULT CALLBACK keyboard_proc(int code, WPARAM message, LPARAM data) {
  if (code == HC_ACTION) {
    const auto *event = reinterpret_cast<const KBDLLHOOKSTRUCT *>(data);
    if (event && event->vkCode == VK_F9 &&
        (event->flags & LLKHF_INJECTED) == 0) {
      if (message == WM_KEYDOWN || message == WM_SYSKEYDOWN) {
        if (!f9_down.exchange(true))
          std::cout << "{\"type\":\"hotkey_down\",\"key\":\"F9\"}\n"
                    << std::flush;
      } else if (message == WM_KEYUP || message == WM_SYSKEYUP) {
        if (f9_down.exchange(false))
          std::cout << "{\"type\":\"hotkey_up\",\"key\":\"F9\"}\n"
                    << std::flush;
      }
    }
  }
  return CallNextHookEx(keyboard_hook, code, message, data);
}

int self_test() {
  const std::string sample =
      "VocoType Windows \xE4\xB8\xAD\xE6\x96\x87 \xE2\x9C\x85";
  const auto wide = wide_from_utf8(sample);
  const auto roundtrip = utf8_from_wide(wide);
  if (wide.empty() || roundtrip != sample) {
    std::cerr << "UTF-8/UTF-16 roundtrip failed\n";
    return 2;
  }
  const UINT microphones = waveInGetNumDevs();
  std::cout << "{\"success\":true,\"platform\":\"windows\","
               "\"unicode_roundtrip\":true,\"microphone_count\":"
            << microphones << "}\n";
  return 0;
}
} // namespace

int wmain(int argc, wchar_t **argv) {
  for (int i = 1; i < argc; ++i) {
    const std::wstring_view arg(argv[i]);
    if (arg == L"--self-test")
      return self_test();
    if (arg == L"--audio-probe") {
      print_audio_devices();
      return 0;
    }
    if (arg == L"--inject" && i + 1 < argc) {
      return inject_unicode(argv[++i]) ? 0 : 3;
    }
    if (arg == L"--help") {
      std::cout << "Usage: vocotype-windows [--self-test|--audio-probe|--inject TEXT|--hotkey-listen]\n";
      return 0;
    }
  }

  keyboard_hook = SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_proc,
                                    GetModuleHandleW(nullptr), 0);
  if (!keyboard_hook) {
    std::cerr << "cannot install F9 keyboard hook: " << GetLastError() << '\n';
    return 4;
  }
  std::cout << "{\"type\":\"ready\",\"hotkey\":\"F9\"}\n" << std::flush;
  MSG message{};
  while (GetMessageW(&message, nullptr, 0, 0) > 0) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }
  UnhookWindowsHookEx(keyboard_hook);
  keyboard_hook = nullptr;
  return 0;
}
