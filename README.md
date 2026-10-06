# Jancy AI

Jancy is a hands-free, always-listening voice assistant for Windows. It controls your PC entirely by voice — opening apps, navigating to pages, clicking things, typing, and running multi-step tasks — with no keyboard, mouse, or hotkeys needed.

It's not a dictation tool and it doesn't collect or exfiltrate your data. Jancy only acts on what you ask: it interprets the request, figures out which action(s) on your screen accomplish it, and performs them — the same way you would by hand, just driven by speech.

## What it can do

- Open apps, websites, and files by voice
- Click on-screen elements, type text, and press key combinations
- Draft emails, write documentation, and browse to specific pages
- Work through multi-step tasks end-to-end from a single spoken prompt
- Mute/unmute on demand, with a small floating widget showing what it's hearing/doing

## How it works

- **Speech-to-text & reasoning:** [Groq](https://groq.com/) API (Whisper for STT, an open LLM with tool-calling for the "brain"). Swappable — `jancy/groq_api.py` is the only place that talks to Groq, so another provider can be dropped in behind the same interface.
- **Wake word:** say "Hey Jancy" (or run in always-listening mode with no wake word at all).
- **UI:** a small floating widget (PySide6) that shows listening/thinking/acting state, plus a Settings window for API key, listen mode, language, and widget position.
- **PC control:** `jancy/actions.py` implements the actual tool calls (window/app control, UI automation, typing, media keys, etc.).

## Setup

```powershell
pip install -r requirements.txt
python run_jancy.pyw
```

On first run, open Settings and add your free [Groq API key](https://console.groq.com/keys).

## Build a standalone .exe

```powershell
.\build.ps1      # produces dist\Jancy\Jancy.exe
.\install.ps1    # installs to %LOCALAPPDATA%\Programs\Jancy with Start Menu + Desktop shortcuts
```

Config, history, and logs are stored in `%APPDATA%\Jancy`. Your Groq API key is encrypted at rest with Windows DPAPI.

## Known limitations

- Latency depends on the Groq model/tier you're on — there's a small-model pre-filter plus fallback across models to stay within free-tier limits, but round-trips aren't instant.
- Windows-only (relies on Windows UI Automation and DPAPI).

## Contributing

This is an early-stage personal project, released open source. Issues, PRs, and ideas are welcome — especially around latency, additional provider support, and new actions.

## License

MIT — see [LICENSE](LICENSE).
