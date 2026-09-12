#pragma once
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <algorithm>
#include <iostream>
#include <stdexcept>
// Requests are serialized JSONL. PeekNamedPipe keeps the existing idle-memory
// reclamation policy without assuming POSIX poll() is present on Windows.
inline int vocotype_wait_stdin(int timeout_ms) {
  const auto deadline=GetTickCount64()+static_cast<ULONGLONG>(std::max(1,timeout_ms));
  for(;;) {
    if(std::cin.rdbuf()->in_avail()>0) return 1;
    DWORD available=0;
    if(!PeekNamedPipe(GetStdHandle(STD_INPUT_HANDLE),nullptr,0,nullptr,&available,nullptr)) return -1;
    if(available>0) return 1;
    if(GetTickCount64()>=deadline) return 0;
    Sleep(10);
  }
}
#endif
