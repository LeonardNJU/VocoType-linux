# macOS microphone startup: verified failure and recovery boundary

On 2026-09-13 a macOS 26.5 machine running the Apple-signed local 5.0.8 build failed to start recording repeatedly. The input method, Settings app, and recorder all passed code-signature verification and had the audio-input entitlement. The actual input-method microphone request was approved by TCC (authValue=2, authReason=2). A Settings microphone smoke test reported authorization=authorized, success=false, waveform_blocks=0 after the eight-second startup deadline.

The recorder thread was waiting in AudioOutputUnitStart / AudioDeviceStart / HALB_IOThread::StartAndWaitForState. An independent AVAudioRecorder probe reported authorized access and then failed to return from recordForDuration before its ten-second external deadline. No other process was reported as actively recording by the public CoreAudio process-list API at the time checked.

A later HAL StartIO/Running message emitted while the cancelled client was being torn down is not evidence that microphone samples were captured. In this incident the same teardown reported zero frames. Earlier explanations that treated this as proof of ordinary multi-second DSP wake latency were too strong. The current evidence places the stall below the app's permission check, but does not establish which component originally caused it.

The app previously displayed “listening” before receiving audio and called the no-audio case “recording too short”. Cancellation sent SIGTERM and followed it with SIGKILL after 250 ms. The new behavior only shows listening after real PCM, gives an explicit startup-failure message otherwise, and allows cancelled startup to unwind without an early forced kill. The existing recorder startup watchdog is eight seconds; a nine-second parent cleanup deadline remains as a final bound. A new recording cannot overlap a cancelled helper's cleanup.

Tests cover UI timing classification and an artificial recorder that needs 600 ms to finish cancellation cleanup. These are regression tests, not evidence that the system-level recurrence is permanently solved. A main-run-loop pumping experiment did not restore capture in the failing state and was not included in the fix.

Restarting coreaudiod is a recovery operation, not a proven permanent fix. It requires normal user administrator authorization and interrupts audio across applications. Do not reset microphone permissions, replace Apple signatures with ad-hoc signatures, disable platform security, or repeatedly kill unrelated applications as a substitute for diagnosis. Verify recovery through real PCM and the signed Settings microphone smoke test, not only a green permission toggle or device enumeration.

## Recovery verification for this incident

The user-authorized coreaudiod restart succeeded. Before deploying the lifecycle patch, the same installed 5.0.8 recorder completed three one-second tests in 1.375, 1.398 and 1.411 seconds, producing 51,840, 51,840 and 53,760 frames at 48 kHz. The independent AVAudioRecorder probe captured 48,000 frames; the signed Settings smoke captured 73,920 frames across 77 waveform blocks. The successful recovery therefore predates this patch and cannot be credited to the patch. Test WAVs were removed.

The application patch passed all 11 macOS CTest cases and repository native contracts, including the new cancellation-cleanup test. A long-idle recurrence test is still needed before drawing any conclusion about prevention.

## Later recurrence: system-audio capture component comparison

On 2026-09-13 the installed input method was replaced by a mainline build from `fa430e3` (delayed startup-status UI), which did not include the separate `895c86e` cancellation-cleanup patch. This change integrates both code paths, rather than attributing the later failure to a test of the combined version.

During the later failure, an independently signed AVAudioRecorder probe reported authorized access but never returned from recordForDuration before its 10-second process deadline. The system audio daemon PID was 89799. At 02:53, terminating only the running `/Applications/ChatGPT.app/Contents/Resources/native/system-audio-spectrum` helper restored the same native probe: it captured 48,000 frames. The main ChatGPT application, the audio daemon, microphone permissions, and VocoType binaries were not restarted or changed for this comparison. The daemon PID remained 89799.

The existing VocoType recorder then captured 52,800 frames at 48 kHz in each of two one-second tests (1.471 and 1.497 seconds wall time). The existing VocoType recorder then captured 52,800 frames at 48 kHz in each of two one-second tests (1.471 and 1.497 seconds wall time). The existing VocoType recorder then captured 52,800 frames at 48 kHz in each of two one-second tests (1.471 and 1.497 seconds wall time). The existing VocoType recordehis occurrence.

This is evidence for a system-audio capture interaction, not proof that every startup failure has this cause or that the daemon can never fail independently. The helper was not re-enabled for a reverse reproduction. Its presence on timeout is reported only as a possible conflict. The recorder must never terminate another application's processes automatically. The component may reappear when its owning application starts system-audio capture again.
