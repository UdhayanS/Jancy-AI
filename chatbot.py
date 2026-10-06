"""Simple terminal chatbot using the Groq API (no third-party dependencies)."""
import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def chat(messages, api_key):
    body = json.dumps({"model": MODEL, "messages": messages}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "simple-chatbot/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def main():
    load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY not set (put it in .env)")

    messages = [{"role": "system", "content": "You are a helpful, friendly assistant."}]
    print(f"Chatbot ready ({MODEL}). Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        try:
            reply = chat(messages, api_key)
        except urllib.error.HTTPError as e:
            print(f"Error {e.code}: {e.read().decode()}\n")
            messages.pop()
            continue
        except urllib.error.URLError as e:
            print(f"Network error: {e.reason}\n")
            messages.pop()
            continue

        messages.append({"role": "assistant", "content": reply})
        print(f"Bot: {reply}\n")


if __name__ == "__main__":
    main()
