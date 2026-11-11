# =============================================================================
# FILE: app/services/ai_translator.py  -  LOCAL AI (plain-English explanations)
# -----------------------------------------------------------------------------
# Turns technical facts (an alert, a device's open ports, a scan result) into
# short plain-English text, using a small AI model that runs ON THIS COMPUTER.
# No internet, no account, nothing sent anywhere.
#
#   Engine: llama-cpp-python   (pip package "llama-cpp-python")
#   Model:  Qwen2.5-1.5B-Instruct, GGUF Q4_K_M  (~1 GB, Apache-2.0 licence)
#           file: models/qwen2.5-1.5b-instruct-q4_k_m.gguf
#
# Flow:  a route (planned: routes/ai.py, POST /api/ai/explain)
#        -> explain(task, facts)                               [GROUP 4]
#           -> answer cached?  return it straight away         [GROUP 3]
#           -> AI busy?        return reason "busy"            (never queues up)
#           -> _load_model() the first time (a few seconds)    [GROUP 2]
#           -> the model writes the text -> cached -> returned
#        -> after IDLE_UNLOAD_SECONDS unused, the model is unloaded to free ~1.5 GB
#
# Safety rules (BUILD_GUIDE Step 38):
#   1. Facts in, words out: the model only sees the facts it is given.
#   2. Always a fallback: explain() never raises; if the AI can't answer, the
#      caller gets ok=False and shows the hand-written knowledge-base text.
#   3. One question at a time (a lock), so memory use stays predictable.
#   4. Answers are cached, so the same question twice is instant.
#   5. Low temperature (0.1): factual and consistent, not creative.
#   6. Loaded on first use, unloaded when idle.
# =============================================================================

import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict


# =============================================================================
# GROUP 1: SETTINGS
# =============================================================================

MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

CONTEXT_TOKENS = 4096          # how much text (question + answer) the model can hold
TEMPERATURE = 0.1              # low = factual and repeatable (0.1 tested better than 0.2)
MAX_FACTS_CHARS = 6000         # facts longer than this are cut, so they fit in CONTEXT_TOKENS
IDLE_UNLOAD_SECONDS = 15 * 60  # free the memory after 15 minutes without questions
CACHE_SIZE = 200               # remember this many answers

# The rules the model must follow. A small model needs the easy-to-confuse
# points spelled out: tests showed it swapping "sent" and "received", saying
# home-network ports were open "to the internet", and guessing at hackers.
SYSTEM_PROMPT = (
    "You explain home-network security information to people with no technical "
    "background. Use ONLY the facts provided. Do not guess or add facts. Use short, "
    "plain sentences and explain any technical word you must use. Never tell the user "
    "to run commands. Advice is optional and must be safe. If the facts are not enough "
    "to answer, say so.\n"
    "Important background:\n"
    "- LanWatch watches the user's HOME network. Every device in the facts is on that home network.\n"
    "- 'out', 'outbound' or 'sent' means data the device SENT. 'in', 'inbound' or "
    "'received' means data the device RECEIVED. Keep the direction exactly as the facts say.\n"
    "- An open port means other devices on the HOME network can connect to it. Do not say "
    "it is reachable from the internet unless the facts say so.\n"
    "- Do not mention hackers, viruses or attacks unless the facts mention them. "
    "Stay calm and match how worried the user should be to the severity in the facts."
)

# A short copy of the key rules, placed right after the question. Small models
# pay most attention to the end of the message: in testing, this stopped the
# model inventing answers when the facts didn't contain them.
# The port and sent/received rules are only added when the facts are about
# ports or traffic: otherwise the model tended to repeat them for no reason.
RULES_REMINDER = (
    "Rules: use only the FACTS; do not invent devices, numbers or causes. No guessing about "
    "hackers. Do not repeat these rules. If the FACTS do not contain the answer, say clearly "
    "that LanWatch does not have that information."
)
PORT_RULE = " Open ports are reachable from the home network only, not the internet."
DIRECTION_RULE = " Keep sent/received exactly as written, and say WHICH device sent or received it."


def _rules_reminder(facts_text):
    """RULES_REMINDER plus only the extra rules these facts need."""
    text = facts_text.lower()
    reminder = RULES_REMINDER
    if "port" in text:
        reminder += PORT_RULE
    if any(word in text for word in ("sent", "sending", "outbound", "inbound", "received", "receiving", "upload", "download")):
        reminder += DIRECTION_RULE
    return reminder

# Tip for callers: the AI is most accurate when the facts already include what
# things MEAN (e.g. a port's name, risk and advice from the knowledge base,
# BUILD_GUIDE Step 34). It is good at rewording; it is weak at working out meaning.


def _model_path():
    """Where the model file is, checked in this order:
      1. the LANWATCH_MODEL environment variable (handy for testing another model)
      2. inside the installed app (PyInstaller unpacks files to sys._MEIPASS)
      3. the project folder:  LanWatch_Mrk1/models/<MODEL_FILENAME>
    (2 and 3 are worked out by resource_path() in app/paths.py)
    """
    if os.environ.get("LANWATCH_MODEL"):
        return os.environ["LANWATCH_MODEL"]
    from app.paths import resource_path
    return resource_path("models", MODEL_FILENAME)


# =============================================================================
# GROUP 2: LOADING AND UNLOADING THE MODEL
# Everything here runs while _lock is held, so only one thread touches _llm.
# =============================================================================

_llm = None                    # the loaded model (None = not in memory)
_lock = threading.Lock()       # the model answers one question at a time
_last_used = 0.0               # time.time() of the last answer
_unload_timer = None
_last_error = None             # why the last attempt failed (shown by status())


def _load_model():
    """Load the model the first time it's needed (takes a few seconds)."""
    global _llm
    if _llm is None:
        from llama_cpp import Llama
        _llm = Llama(
            model_path=_model_path(),
            n_ctx=CONTEXT_TOKENS,
            n_threads=max(2, (os.cpu_count() or 4) // 2),   # leave half the CPU for the rest of LanWatch
            verbose=False,
        )
    return _llm


def _schedule_unload():
    """(Re)start the idle timer; when it fires, the model is unloaded."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(IDLE_UNLOAD_SECONDS + 1, _unload_if_idle)
    _unload_timer.daemon = True
    _unload_timer.start()


def _unload_if_idle():
    """Free the model's memory if nobody has asked anything for a while."""
    global _llm
    if not _lock.acquire(blocking=False):
        return                          # busy answering: it will be re-scheduled afterwards
    try:
        if _llm is not None and time.time() - _last_used >= IDLE_UNLOAD_SECONDS:
            if hasattr(_llm, "close"):
                _llm.close()            # release the model's memory straight away
            _llm = None
    finally:
        _lock.release()


# =============================================================================
# GROUP 3: ANSWER CACHE
# =============================================================================

_cache = OrderedDict()                  # fingerprint -> text (oldest first)
_cache_lock = threading.Lock()


def _facts_text(facts):
    """Facts as readable JSON. default=str copes with dates and other objects;
    very long facts are cut so the question always fits in the model's memory."""
    text = json.dumps(facts, indent=1, ensure_ascii=False, default=str)
    if len(text) > MAX_FACTS_CHARS:
        text = text[:MAX_FACTS_CHARS] + "\n... (more facts not shown)"
    return text


def _fingerprint(task, facts_text, max_words):
    """Same task + same facts + same length = same fingerprint = cached answer."""
    # the prompts are included so changing them never returns an old answer
    raw = f"{MODEL_FILENAME}\n{SYSTEM_PROMPT}\n{_rules_reminder(facts_text)}\n{task}\n{max_words}\n{facts_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)     # recently used = kept longest
            return _cache[key]
    return None


def _cache_put(key, text):
    with _cache_lock:
        _cache[key] = text
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)  # forget the oldest answer


# =============================================================================
# GROUP 4: PUBLIC FUNCTIONS (what the rest of LanWatch calls)
# =============================================================================

def is_available():
    """True if the AI engine is installed AND the model file is present."""
    try:
        import llama_cpp  # noqa: F401  (only checking that it can be imported)
    except Exception:
        return False
    return os.path.isfile(_model_path())


def status():
    """A small report for the Settings / About page and for troubleshooting."""
    return {
        "available": is_available(),
        "model_file": _model_path(),
        "loaded": _llm is not None,
        "busy": _lock.locked(),
        "cached_answers": len(_cache),
        "last_error": _last_error,
    }


def explain(task, facts, max_words=200):
    """Write a plain-English answer to `task` using only `facts`.

    task:  what to write, e.g. "Explain this alert to a home user."
    facts: a dict (or list) from the database / knowledge base.

    Never raises. Always returns a dict:
      {"ok": True,  "text": "...", "cached": bool, "reason": None}
      {"ok": False, "text": None,  "cached": False,
       "reason": "unavailable" | "busy" | "error"}
    On ok=False the caller shows its hand-written fallback text instead.
    """
    global _last_used, _last_error

    facts_text = _facts_text(facts)
    key = _fingerprint(task, facts_text, max_words)

    cached = _cache_get(key)
    if cached is not None:
        return {"ok": True, "text": cached, "cached": True, "reason": None}

    if not is_available():
        return {"ok": False, "text": None, "cached": False, "reason": "unavailable"}

    # one question at a time: a second caller is told "busy" instead of waiting
    if not _lock.acquire(blocking=False):
        return {"ok": False, "text": None, "cached": False, "reason": "busy"}
    try:
        result = _load_model().create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                # facts first, then the task, then the rules (see _rules_reminder)
                {"role": "user", "content": f"FACTS:\n{facts_text}\n\n"
                                            f"TASK: {task}\nKeep it under {max_words} words.\n"
                                            f"{_rules_reminder(facts_text)}"},
            ],
            temperature=TEMPERATURE,
            max_tokens=int(max_words * 2),   # ~1.3 tokens per English word, plus room
        )
        text = (result["choices"][0]["message"]["content"] or "").strip()
        if not text:
            raise RuntimeError("the model returned an empty answer")
    except Exception as error:
        # broken/missing model file, out of memory, ...: report it, never crash the caller
        _last_error = f"{type(error).__name__}: {error}"
        return {"ok": False, "text": None, "cached": False, "reason": "error"}
    finally:
        _last_used = time.time()
        _lock.release()
        _schedule_unload()

    _last_error = None
    _cache_put(key, text)
    return {"ok": True, "text": text, "cached": False, "reason": None}


# =============================================================================
# GROUP 5: SELF-TEST
#     python -m app.services.ai_translator
# =============================================================================

if __name__ == "__main__":
    print("Status:", json.dumps(status(), indent=1))
    if not is_available():
        print("\nThe model file isn't there yet. Download it into:\n  " + _model_path())
        sys.exit(1)

    sample_facts = {
        "alert_type": "traffic_spike",
        "severity": "high",
        "device": {"name": "Living-room TV", "ip": "192.168.1.23", "maker": "Samsung"},
        "measured": "48 MB/s for 30 seconds",
        "normal_for_this_device": "about 1.5 MB/s",
    }
    for attempt in ("first (loads the model, slower)", "second (should come from the cache)"):
        started = time.time()
        answer = explain("Explain this alert to a home user and say whether they should worry.",
                         sample_facts, max_words=120)
        print(f"\n--- {attempt}: {time.time() - started:.1f}s, cached={answer['cached']} ---")
        print(answer["text"] if answer["ok"] else f"Failed: {answer['reason']} {_last_error or ''}")
