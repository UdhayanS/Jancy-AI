"""AI logic: wake word parsing and the tool-using assistant."""
import difflib
import json
import re
import time

from .actions import TOOL_FUNCS, TOOLS
from .groq_api import GroqError

# Whisper tends to "hear" these in silence or noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching",
                  "bye", "okay", "ok", "so", "uh", "um", "hmm", "subtitles by the amaraorg community"}


def is_noise(text):
    return re.sub(r"[^a-z ]", "", text.lower()).strip() in HALLUCINATIONS


# ---------------------------------------------------------------- wake word

GREETINGS = {"hey", "hi", "hai", "hay", "hello", "helo", "hallo", "hullo", "ok", "okay"}
HELLO_ALONE = {"hello", "helo", "hallo", "hullo", "hi", "hai"}
JANCY_VARIANTS = {"jancy", "jancey", "jancie", "jancee", "jansy", "jansi", "jansie", "jansee",
                  "yancy", "yancey", "jency", "jensi", "jantsy", "chancy", "janzy"}


def _is_jancy(word):
    return word in JANCY_VARIANTS or difflib.SequenceMatcher(None, word, "jancy").ratio() >= 0.75


def parse_wake(text, loose=False):
    """Returns (woke, rest_of_command). "Hey/Hi/Hello Jancy ..." or "Jancy ...";
    loose also accepts a bare "Hello" said on its own."""
    orig = text.split()
    norm = [re.sub(r"[^\w']", "", t.lower()) for t in orig]
    pairs = [(o, n) for o, n in zip(orig, norm) if n]
    orig, norm = [o for o, _ in pairs], [n for _, n in pairs]
    if not norm:
        return False, ""
    if len(norm) >= 2 and norm[0] in GREETINGS and _is_jancy(norm[1]):
        n = 2
    elif _is_jancy(norm[0]):
        n = 1
    elif loose and norm[0] in HELLO_ALONE and len(norm) == 1:
        n = 1
    else:
        return False, ""
    return True, " ".join(orig[n:]).strip(" ,.!?;:-")


# ---------------------------------------------------------------- request filter

FILTER_PROMPT = """You screen speech picked up by an always-on microphone next to a Windows PC that has a voice \
assistant. Decide whether the speech is a request, command or question for the assistant or the computer \
(e.g. "open Excel", "what's the weather", "scroll down", "type hello", "close it", "volume up") or just \
background talk (people chatting, a phone call, TV, fragments, thinking aloud). Answer only YES or NO."""


def is_for_assistant(groq, model, text, last_exchange=None):
    """Cheap pre-check with a small model so background talk doesn't use the main model's budget."""
    context = ""
    if last_exchange:
        context = f"The user's previous request a moment ago was: {last_exchange}\n"
    try:
        msg = groq.chat(model, [{"role": "system", "content": FILTER_PROMPT},
                                {"role": "user", "content": f"{context}Speech: {text}"}], max_tokens=300)
        return "NO" not in (msg.get("content") or "").strip().upper()[:5]
    except GroqError:
        return True  # when unsure, let the assistant decide


# ---------------------------------------------------------------- assistant

ASSISTANT_PROMPT = """You are Jancy, a voice assistant that controls the user's Windows PC so they never need \
a keyboard or mouse. You hear their speech (as a transcript) and act with the provided tools. DO things with \
tools instead of explaining how.

How to work:
- Transcripts may contain recognition mistakes; interpret sensibly ("open exel" = Excel).
- To click something on screen: call list_screen_items to see what's in the focused window, then click_item \
with the item's name. To fill a field: click_item the field, then type_text.
- To use an app that's already open, switch_to_window first. After open_application or open_website, call \
wait (2-3 s) before interacting with it.
- To read or describe what's on screen, call list_screen_items with include_text=true.
- For "rewrite / translate / summarize this", call get_selected_text, then insert_text with the new text.
- Searching sites: build URLs directly, e.g. https://www.youtube.com/results?search_query=...
- Useful keys: new tab ctrl+t, close tab ctrl+w, close window alt+f4, address bar ctrl+l, save ctrl+s, \
undo ctrl+z, show desktop win+d, switch apps alt+tab.
- If the user just asks a question, answer it directly.
- Your reply is shown in a small popup and read aloud: one or two short sentences, plain text, no markdown, \
no URLs. Don't read back long text you inserted."""

ALWAYS_ON_NOTE = """

The microphone is always on, so you hear everything said near the PC. If the speech is clearly NOT a request \
or question for you (people talking to each other, a phone call, TV, a half sentence, small talk not aimed at \
an assistant), reply with exactly IGNORE and call no tools. If it sounds like an instruction about the \
computer, act on it."""


class Assistant:
    MAX_STEPS = 15
    MEMORY_TURNS = 5
    MEMORY_TTL = 600

    def __init__(self, groq, fallback_models=()):
        self.groq = groq
        self.fallback_models = list(fallback_models)
        self.memory = []
        self.last_time = 0.0
        self.on_wait = None

    def recent_request(self, within=90):
        if self.memory and time.time() - self.last_time < within:
            return self.memory[-2]["content"].replace("User said: ", "")
        return None

    def _chat(self, model, messages):
        """Chat with automatic fallback to other models (each has its own rate limit), then a short wait."""
        models = [model] + [m for m in self.fallback_models if m != model]
        for attempt in range(2):
            wait = 0.0
            for m in models:
                try:
                    return self.groq.chat(m, messages, tools=TOOLS)
                except GroqError as e:
                    if e.status != 429:
                        raise
                    wait = max(wait, e.retry_after)
            if attempt == 0 and wait <= 20:
                if self.on_wait:
                    self.on_wait()
                time.sleep(max(2.0, wait))
        raise GroqError("Groq's free-tier limit was reached. Try again in a minute.", 429)

    def run(self, model, command, app_info=None, on_action=None, addressed=True):
        """Returns the reply, or None when the speech wasn't meant for the assistant."""
        if time.time() - self.last_time > self.MEMORY_TTL:
            self.memory = []
        context = ""
        if app_info:
            _, exe, title = app_info
            context = f"Focused app: {exe or 'unknown'} - window title: {title or 'none'}\n\n"
        system = ASSISTANT_PROMPT + ("" if addressed else ALWAYS_ON_NOTE)
        messages = [{"role": "system", "content": system}, *self.memory,
                    {"role": "user", "content": f"{context}User said: {command}"}]
        reply = "Done."
        acted = False
        for _ in range(self.MAX_STEPS):
            msg = self._chat(model, messages)
            calls = msg.get("tool_calls") or []
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                entry["tool_calls"] = calls
            messages.append(entry)
            if not calls:
                reply = (msg.get("content") or "Done.").strip()
                break
            acted = True
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if on_action:
                    on_action(name, args)
                func = TOOL_FUNCS.get(name)
                try:
                    result = func(**args) if func else f"Unknown tool {name}"
                except Exception as e:
                    result = f"Error: {e}"
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})
        else:
            reply = "I stopped because that took too many steps."

        if reply.upper().strip(" .!\"'").startswith("IGNORE"):
            if not acted:
                return None
            reply = "Okay, done."
        self.memory = (self.memory + [{"role": "user", "content": f"User said: {command}"},
                                      {"role": "assistant", "content": reply}])[-self.MEMORY_TURNS * 2:]
        self.last_time = time.time()
        return reply
