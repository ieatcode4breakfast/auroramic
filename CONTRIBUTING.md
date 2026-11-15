# Contributing to AuroraMic

Thanks for helping improve **AuroraMic**! Below is everything you need to set up a local development environment, run tests, build the native Windows launcher, and submit clean pull requests.

## Development Setup

1. Ensure you are on **Windows 10/11** with **Python 3.10+**.
2. Clone the repository and activate a virtual environment:

   ```powershell
   git clone https://github.com/your-org/auroramic.git
   cd auroramic
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

3. Install runtime and test dependencies:

   ```powershell
   pip install -r requirements.txt
   pip install pytest
   ```

4. Copy `.env.example` to `.env` and add your local Groq API key for manual or live-API testing:

   ```powershell
   copy .env.example .env
   ```

## Running Tests

Run the automated test suite with `pytest` or run the end-to-end verification script directly:

```powershell
python -m pytest
python tests/test_e2e.py
```

*Note: Keep Win32 `SendInput` and focus-stealing calls mocked in automated tests so running the suite does not inject keystrokes into active desktop windows.*

## Building `AuroraMic.exe`

To compile the native Windows launcher binary (`AuroraMic.exe`):

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

If you update icon artwork, regenerate `auroramic.ico` first:

```powershell
python scripts/generate_icon.py
```

## Pull Request Expectations

- **Never commit secrets or artifacts**: Do not commit `.env`, API keys or bearer tokens, log files (`*.log`), or compiled binaries (`*.exe`).
- **Preserve non-activating window behavior**: Changes to the overlay or text injection must preserve `WS_EX_NOACTIVATE`, `WS_EX_TOPMOST`, and `UpdateLayeredWindow` per-pixel alpha rendering.
- **Verify thread safety**: Background recording, countdown timers, and transcription callbacks run off the Tk main thread and must marshal UI updates safely.
- **Test before submitting**: Confirm `python -m pytest` / `python tests/test_e2e.py` passes cleanly before opening a PR.
