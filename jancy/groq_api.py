"""Minimal Groq API client (OpenAI-compatible endpoints), standard library only."""
import json
import urllib.error
import urllib.request
import uuid

BASE_URL = "https://api.groq.com/openai/v1"


class GroqError(Exception):
    def __init__(self, message, status=0, retry_after=0.0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after(e):
    try:
        return float(e.headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        return 0.0


class Groq:
    def __init__(self, key_provider):
        self.key_provider = key_provider

    def _request(self, path, body=None, content_type="application/json", key=None, timeout=60):
        key = key or self.key_provider()
        if not key:
            raise GroqError("No API key set. Open Jancy and add your Groq API key in Settings.")
        headers = {"Authorization": f"Bearer {key}", "User-Agent": "Jancy/1.0"}
        if body is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(BASE_URL + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", {}).get("message", "")
            except Exception:
                msg = ""
            if e.code == 401:
                raise GroqError("Invalid Groq API key. Update it in Jancy Settings.", 401) from None
            if e.code == 429:
                raise GroqError("Groq's free-tier limit was reached. Try again in a minute.", 429,
                                _retry_after(e)) from None
            raise GroqError(f"Groq error {e.code}: {msg or e.reason}", e.code) from None
        except urllib.error.URLError as e:
            raise GroqError(f"Network error: {e.reason}") from None
        except TimeoutError:
            raise GroqError("Groq request timed out.") from None

    def chat(self, model, messages, tools=None, **extra):
        payload = {"model": model, "messages": messages, **extra}
        if model.startswith("openai/gpt-oss"):
            payload.setdefault("reasoning_effort", "low")
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = self._request("/chat/completions", json.dumps(payload).encode())
        return data["choices"][0]["message"]

    def transcribe(self, wav_bytes, model, language=None, prompt=None):
        boundary = uuid.uuid4().hex
        fields = {"model": model, "response_format": "json", "temperature": "0"}
        if language:
            fields["language"] = language
        if prompt:
            fields["prompt"] = prompt[:800]
        body = b""
        for k, v in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
                 "Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
        data = self._request("/audio/transcriptions", body, f"multipart/form-data; boundary={boundary}")
        return (data.get("text") or "").strip()

    def list_models(self, key=None):
        data = self._request("/models", key=key, timeout=20)
        return sorted(m["id"] for m in data.get("data", []))
