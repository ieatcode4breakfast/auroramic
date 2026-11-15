# AuroraMic

**AuroraMic** is a lightweight, floating always-on-top voice-to-text overlay for Windows powered by Groq Whisper (`whisper-large-v3-turbo`). It stays out of your way without stealing window focus, pulses with a cyan aurora glow whenever a text field is active, and types transcribed speech directly into whatever app you are using.

## Features

- **Floating Cyan Aurora Pill**: Frameless, per-pixel alpha layered window (`UpdateLayeredWindow` + analytical SDF rendering) that pulses with a cyan aurora ring when a text input field is focused.
- **Always-Visible Messenger-Blue Send / Enter Button**:
  - **When idle**: Taps `Enter` in the active target window.
  - **While recording**: Stops recording, transcribes, injects the transcribed text, and automatically presses `Enter` in one click.
  - **While transcribing**: Queues `Enter` to fire as soon as transcription finishes and types into the target window.
- **1-Minute Countdown & `X` Cancel Bar**: Displays a live 9-bar audio-reactive waveform, a `1:00` countdown timer (auto-stopping and transcribing when it reaches `0:00`), and an `X` button to discard a recording cleanly without calling the API.
- **Inline Error Toast Pill**: Displays concise, single-line error messages (such as `No API key — check .env`, `API key rejected`, `Rate limited`, or `Mic unavailable`) in the bottom pill synchronized with the red/amber ring pulse for `2.6s` (click to dismiss).
- **In-App Groq API Key Setup**: Right-click the overlay to enter or update your Groq API key (`Enter Groq API key` / `Update Groq API key`) in-place inside the reused bottom container, with masked input, `.env` persistence, live hot-reload, and a **Sign up for a free Groq API key** button.
- **Automatic Multilingual Transcription**: Uses Groq's `whisper-large-v3-turbo` endpoint with automatic language detection across 99+ languages—no manual language switching needed.
- **Non-Activating Windows Overlay**: Configured with `WS_EX_NOACTIVATE`, `WS_EX_TOPMOST`, and `WS_EX_TOOLWINDOW` so clicking AuroraMic never steals focus from editors, browsers, chats, or terminals, and Unicode text is injected cleanly via Win32 `SendInput`.
- **Reliable Audio Capture**: Records 16 kHz mono 16-bit PCM via `sounddevice` with an automatic `winmm.dll` (`waveIn`) fallback on Windows.

## Requirements

- **OS**: Windows 10 or Windows 11
- **Python**: 3.10 or newer
- **API Key**: A [Groq API key](https://console.groq.com/keys)
- **Hardware**: A working microphone / audio input device

## Quick Start

1. **Clone the repository and create a virtual environment**:

   ```powershell
   git clone https://github.com/your-org/auroramic.git
   cd auroramic
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

2. **Install dependencies**:

   ```powershell
   pip install -r requirements.txt
   ```

3. **Configure your Groq API key** (either in `.env` or in-app):

   ```powershell
   copy .env.example .env
   ```

   Open `.env` and set your key:

   ```dotenv
   GROQ_API_KEY=your_groq_api_key_here
   ```

   *(Note: `GROQ_API` is also supported as an environment variable alias. You can also launch AuroraMic directly, right-click the overlay, choose **Enter Groq API key** — or click **Sign up for a free Groq API key** — and save to write `.env` and activate immediately without restarting.)*

4. **Run AuroraMic**:

   ```powershell
   python main.py
   ```

   Or launch without a console window:

   ```powershell
   pythonw main.py
   ```

5. **Optional — Build a native `AuroraMic.exe` launcher**:

   ```powershell
   powershell -ExecutionPolicy Bypass -File build.ps1
   ```

   This compiles the lightweight Windows GUI launcher (`AuroraMic.exe`) with the AuroraMic icon so you can pin or double-click it directly.

## Controls

| Action | Behavior |
| --- | --- |
| **Left-click mic button (idle)** | Starts recording (60-second countdown begins). |
| **Left-click stop / pill (recording)** | Stops recording, transcribes via Groq Whisper, and types into the last active text field. |
| **Click `X` button (recording)** | Cancels immediately and discards captured audio without transcribing or typing. |
| **Click blue Send / Enter tab (idle)** | Presses `Enter` in the active target window. |
| **Click blue Send / Enter tab (recording / transcribing)** | Finishes dictation (if recording), types the transcription, and presses `Enter`. |
| **Drag (> 5 px)** | Moves the floating AuroraMic overlay anywhere on screen. |
| **Right-click** | Opens the dark-glass context menu with **Quit AuroraMic** and **Enter Groq API key** (or **Update Groq API key** when a key is configured). |
| **Click `✓` / `✕` or `Enter` / `Esc` (API key)** | Saves the entered Groq API key to `.env` (or cancels back to idle); clicking **Sign up for a free Groq API key** opens `console.groq.com/keys`. |
| **Left-click button (error)** | Dismisses the error toast immediately and returns to idle. |

## Privacy & Security

- **On-demand audio upload only**: Audio is captured locally in memory only while you are actively recording and is sent exclusively to Groq's Whisper transcription endpoint (`https://api.groq.com/openai/v1/audio/transcriptions`) when you stop to transcribe. Discarded (`X`) or `< 0.15s` clips are never uploaded.
- **Local secret storage**: Your API key is stored locally in `.env` (masked when entered in-app) and automatically redacted from error messages and local log files (`*.log`).
- **Zero telemetry**: AuroraMic collects no analytics, usage metrics, or background telemetry.

For vulnerability reporting details, see [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

AuroraMic is released under the [MIT License](LICENSE).
