"""The gag director: reads what was said and decides the visual gags.

A gag is something that pops onto the edited video for a couple of seconds
because of what the host is saying:
  drawing    a hand-made-looking picture of a thing that was mentioned
  character  someone who appears and answers the host in a speech bubble
  word       a shouted word, big on screen
  photo      a picture from your own gag library
  warp       the picture itself bends: stretch, swirl or wave

Two directors write the same gag list:
  claude    your own Claude Code (the `claude` command, signed in with your
            Claude subscription) reads the timed transcript and plans gags
            in the style of David Choe's editors. No API key: it runs the
            local CLI with every tool disabled and a JSON schema for output.
  keywords  offline and free: drawings for words it recognises, characters
            for people mentioned, big words for exclamations.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from captions.effects import FUN_EFFECTS
from core.gag_paint import STYLES

KINDS = ("drawing", "character", "word", "photo", "warp", "math")
DRAWING_GAP = 20.0     # seconds between drawings/characters: each one is drawn, which takes a while
POSITIONS = ("left", "right", "top-left", "top-right", "bottom-left", "bottom-right")
EFFECTS = tuple(name for name in FUN_EFFECTS if name != "none")
WARPS = ("stretch", "swirl", "wave")
STYLE_NAMES = tuple(STYLES)

GAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "gags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "at": {"type": "number", "description": "seconds on the edited video when it appears"},
                    "duration": {"type": "number", "description": "seconds on screen, 1.5 to 3.5"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "subject": {"type": "string", "description": "drawing/character: what to paint, in English, concrete and visual, max 12 words"},
                    "style": {"type": "string", "enum": list(STYLE_NAMES)},
                    "says": {"type": "string", "description": "character: its short reply to the host, in the language of the talk, max 8 words"},
                    "text": {"type": "string", "description": "word: one to three words shouted on screen, in the language of the talk"},
                    "query": {"type": "string", "description": "photo: the library picture's name"},
                    "warp": {"type": "string", "enum": list(WARPS)},
                    "position": {"type": "string", "enum": list(POSITIONS)},
                    "size": {"type": "number", "description": "share of the frame width, 0.15 to 0.4"},
                    "effect": {"type": "string", "enum": list(EFFECTS)},
                },
                "required": ["at", "duration", "kind"],
            },
        },
    },
    "required": ["gags"],
}

SYSTEM = (
    "You are the gag editor of a talking-to-camera YouTube show, editing in the style of David Choe's "
    "channel: hand-drawn and painted pictures that pop in, characters that appear and talk back to the "
    "host, big shouted words, the picture bending when the talk gets weird. Playful, absurd, quick. "
    "You only answer with the JSON the schema asks for."
)

BRIEF = """Plan the visual gags for this edited talk.

Rules:
- About one gag every 6 to 9 seconds; more around jokes, punchlines, exclamations and things the host names; none in the first 1.5 s.
- Never two pictures on screen at once: each starts after the previous one ends (a warp may run under a picture).
- Drawings and characters are drawn one by one and take time: at most one of them every {drawing_gap} seconds, on the strongest moments. Between them use shouted words, formulas and warps.
- The host's face is on the {face_side} of the frame: put pictures on the {free_side} side ("{free_side}", "top-{free_side}", "bottom-{free_side}"), small ones only on the other side.
- "drawing": a picture of something said or implied; "subject" says what to paint (English, concrete: "a crying cup of coffee", not "sadness"). Literal or absurd, both work.
- "character": someone who reacts to the host (the person they talk about, a random witness, an animal, an object with a face...). "subject" describes them visually (English); "says" is their short funny reply, in the language of the talk.
- "word": one to three words, shouted, in the language of the talk.
- "photo": only for a picture listed in the library below ("query" = its name).
- "math": hand-written formulas orbit the host's head for 3-5 s when they think, explain, philosophise or talk about time, numbers or ideas. At most one every 45 s.
- "warp": the whole picture bends for 2-4 s when the talk invites it ("stretch" for stretching/pulling/long things, "swirl" for spirals/dizzy/crazy/time, "wave" for shaking/moving/drunk/dreams). At most one every 40 s.
- "style": mix them all, never the same style twice in a row: {styles}.
- "effect": vary them; entrances (pop, spin, drop, rise, fly_left, fly_right) or loops (pulse, jelly, bounce, wobble, shake, blink).
- "size": words and drawings 0.18-0.3, characters 0.25-0.32.

Library pictures:
{library}

Timed transcript of the EDITED video (seconds):
{transcript}
"""


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _timed_lines(words: list[dict[str, Any]], max_gap: float = 0.6) -> list[tuple[float, float, str]]:
    lines: list[tuple[float, float, str]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if current and (word["start"] - current[-1]["end"] > max_gap or re.search(r"[.!?…]$", current[-1]["text"].strip()) or len(current) >= 14):
            lines.append((current[0]["start"], current[-1]["end"], " ".join(w["text"].strip() for w in current)))
            current = []
        current.append(word)
    if current:
        lines.append((current[0]["start"], current[-1]["end"], " ".join(w["text"].strip() for w in current)))
    return lines


def build_prompt(words: list[dict[str, Any]], library: list[dict[str, Any]], *, face_side: str = "right") -> str:
    transcript = "\n".join(f"[{a:6.2f}-{b:6.2f}] {text}" for a, b, text in _timed_lines(words))
    names = [f"- {Path(entry['path']).stem}" for entry in library[:200]]
    free_side = "left" if face_side == "right" else "right"
    return BRIEF.format(drawing_gap=int(DRAWING_GAP), styles=", ".join(STYLE_NAMES), library="\n".join(names) or "- (empty)", transcript=transcript,
                        face_side=face_side, free_side=free_side)


def claude_cli() -> str | None:
    for candidate in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def claude_status(timeout: float = 20.0) -> dict[str, Any]:
    """Whether Claude Code is installed and signed in (asks nothing of the model)."""
    cli = claude_cli()
    if not cli:
        return {"installed": False, "signed_in": False}
    try:
        result = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=timeout, check=False)
        status = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"installed": True, "signed_in": False}
    return {"installed": True, "signed_in": bool(status.get("loggedIn")), "method": str(status.get("authMethod") or "")}


def open_sign_in() -> None:
    """Open a terminal running Claude Code's sign-in with the Claude
    subscription; the person signs in themselves in the browser it opens."""
    import sys
    cli = claude_cli()
    if not cli:
        raise DirectorError("Claude Code is not installed on this computer.")
    if sys.platform == "darwin":
        script = f'tell application "Terminal" to do script "{cli} auth login --claudeai"\ntell application "Terminal" to activate'
        subprocess.run(["osascript", "-e", script], check=True, timeout=20)
    elif sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cli, "auth", "login", "--claudeai"])
    else:
        raise DirectorError("Open a terminal and run: claude auth login --claudeai")


class DirectorError(RuntimeError):
    pass


SIGN_IN_HELP = ("Claude Code is not signed in. Open Terminal, run `claude`, type /login and sign in "
                "with your Claude account, then try again.")


def ask_claude(prompt: str, *, timeout: float = 420.0) -> list[dict[str, Any]]:
    """Plan gags with the local Claude Code (the user's own subscription)."""
    cli = claude_cli()
    if not cli:
        raise DirectorError("Claude Code is not installed on this computer. Install it and sign in once with `claude`, then try again.")
    command = [cli, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(GAG_SCHEMA),
               "--tools", "", "--no-session-persistence", "--system-prompt", SYSTEM]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()), check=False)
    except subprocess.TimeoutExpired as exc:
        raise DirectorError("Claude took too long to plan the gags; try again.") from exc
    except OSError as exc:
        raise DirectorError(f"Claude Code could not start: {exc}") from exc
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise DirectorError((result.stderr or result.stdout or "Claude Code did not answer").strip()[-400:]) from exc
    if not isinstance(payload, dict):
        raise DirectorError("Claude Code gave an unexpected answer")
    if payload.get("is_error"):
        message = str(payload.get("result") or "Claude Code failed")
        if re.search(r"authenticat|oauth|log ?in|sign ?in|credential", message, re.I):
            message = SIGN_IN_HELP
        raise DirectorError(message)
    plan = payload.get("structured_output")
    if not isinstance(plan, dict):
        match = re.search(r"\{.*\}", str(payload.get("result") or ""), re.S)
        try:
            plan = json.loads(match.group(0)) if match else {}
        except ValueError as exc:
            raise DirectorError("Claude's answer was not a gag list; try again.") from exc
    gags = plan.get("gags") if isinstance(plan, dict) else None
    if not isinstance(gags, list):
        raise DirectorError("Claude's answer was not a gag list; try again.")
    return gags


# Offline director: words heard -> what to paint.
DRAWINGS: dict[str, tuple[str, ...]] = {
    "a cup of hot coffee": ("coffee", "cafe", "breakfast", "desayuno"),
    "a pile of money": ("money", "dinero", "pay", "pagar", "rich", "rico", "price", "precio", "pasta", "euros", "dollars", "millionaire", "millionaires", "millonario"),
    "an electric guitar with music notes": ("music", "musica", "song", "cancion", "guitar", "guitarra", "band", "banda", "concierto", "concert"),
    "a big red heart": ("love", "amor", "heart", "corazon", "quiero", "hearts"),
    "a big fire": ("fire", "fuego", "hot", "caliente", "brutal", "increible", "amazing"),
    "a golden crown": ("king", "rey", "queen", "reina", "winner", "ganar", "ganador", "famous", "famoso"),
    "a big question mark": ("why", "porque", "pregunta", "question"),
    "a cartoon explosion": ("boom", "explode", "explota", "bang", "pum"),
    "a hairy spider": ("spider", "arana", "bug", "bicho"),
    "a sleeping moon": ("sleep", "dormir", "tired", "cansado", "boring", "aburrido", "noche", "night"),
    "a lightbulb": ("idea", "think", "pensar", "imagine", "imagina"),
    "a smiling sun": ("morning", "manana", "sun", "sol", "summer", "verano"),
    "a funny dog": ("dog", "perro", "perrito"),
    "a grumpy cat": ("cat", "gato", "gatito"),
    "a tiny old car": ("car", "coche", "drive", "conducir"),
    "a slice of pizza": ("pizza", "food", "comida", "comer", "eat", "hambre", "hungry", "lunch"),
    "a glass of beer": ("beer", "cerveza", "drink", "beber", "bar"),
    "a glass of red wine": ("wine", "vino"),
    "an airplane": ("plane", "avion", "flight", "vuelo", "travel", "viaje", "viajar"),
    "a palm tree on a beach": ("beach", "playa", "sea", "mar", "holiday", "vacaciones"),
    "green mountains and pine trees": ("nature", "naturaleza", "mountain", "montana", "forest", "bosque"),
    "crowded skyscrapers": ("city", "cities", "ciudad", "ciudades"),
    "a ringing old telephone": ("phone", "telefono", "llamar", "call", "movil"),
    "an old computer": ("computer", "ordenador", "internet", "online", "gamers", "gamer"),
    "a laughing skull": ("death", "muerte", "dead", "muerto", "morir"),
    "a ghost": ("fear", "miedo", "scary", "ghost", "fantasma"),
    "a little devil": ("devil", "diablo", "hell", "infierno"),
    "an angel with wings": ("angel", "heaven", "cielo", "god", "dios"),
    "a human brain": ("brain", "cerebro", "smart", "listo", "mind", "mente", "philosophical", "filosofia"),
    "an alarm clock": ("time", "tiempo", "late", "tarde", "hour", "hora", "future", "futuro"),
    "a colourful hypnotic spiral": ("spiral", "espiral", "loop", "circle", "circulo"),
    "a tornado": ("tornado", "storm", "tormenta"),
    "a rain cloud": ("rain", "lluvia", "sad", "triste", "llorar", "cry"),
    "a party with balloons": ("party", "fiesta", "birthday", "cumpleanos", "celebrate", "celebrar"),
    "a stack of old books": ("book", "libro", "read", "leer", "school", "colegio", "words", "palabras"),
    "a paint brush and palette": ("paint", "pintar", "art", "arte", "painting", "paintings", "cuadro"),
    "a video camera": ("video", "camera", "camara", "film", "grabar", "youtube", "channel", "canal"),
    "a big thumbs up": ("like", "subscribe", "suscribete", "dale"),
    "a pair of scissors": ("cut", "cortar", "editing", "edit", "editar"),
}
CHARACTERS: dict[str, tuple[str, ...]] = {
    "a funny old mother with curlers": ("mother", "mom", "madre", "mama"),
    "a grumpy old father with a moustache": ("father", "dad", "padre", "papa"),
    "a tiny old grandmother": ("grandmother", "grandma", "abuela", "abuelo", "grandfather"),
    "a sweaty angry boss in a tie": ("boss", "jefe", "work", "trabajo", "office", "oficina"),
    "a bored teenager": ("friend", "friends", "amigo", "amiga", "colega", "bro", "cousin", "primo", "prima"),
    "a dramatic girlfriend": ("girlfriend", "novia", "boyfriend", "novio", "wife", "mujer", "marido", "husband"),
    "a suspicious police officer": ("police", "policia", "law", "ley"),
    "a crazy doctor": ("doctor", "medico", "hospital", "sick", "enfermo"),
    "an old rich tycoon with a top hat": ("rockefeller", "millionaire", "banker", "banquero", "rich"),
}
MATH_WORDS = ("think", "thinking", "pensar", "pienso", "idea", "philosophical", "philosophy", "filosofia", "mean", "meaning",
              "significado", "time", "tiempo", "future", "futuro", "loop", "spiral", "espiral", "circle", "numbers", "numeros",
              "calculate", "calcular", "learn", "learned", "aprender", "explain", "explicar", "physics", "fisica", "math", "matematicas")
WARP_WORDS: dict[str, tuple[str, ...]] = {
    "stretch": ("stretch", "estirar", "estira", "pull", "long", "largo", "elastic", "elastico"),
    "swirl": ("spiral", "espiral", "dizzy", "mareo", "crazy", "loco", "tornado", "loop"),
    "wave": ("moving", "shake", "temblar", "drunk", "borracho", "dream", "sueno"),
}
REPLIES = {
    "es": ("¿En serio?", "¡No me digas!", "Ya te lo dije...", "¡Venga ya!", "¿Y a mí qué?", "¡Qué fuerte!", "Eso no es verdad", "¡Cállate ya!"),
    "en": ("Seriously?", "No way!", "Told you so...", "Oh, come on!", "Who cares?", "That's wild!", "That's not true", "Just stop!"),
}


def _first_match(table: dict[str, tuple[str, ...]], tokens: set[str]) -> str | None:
    return next((subject for subject, keys in table.items() if tokens & set(keys)), None)


def keyword_gags(words: list[dict[str, Any]], library: list[dict[str, Any]], *, language: str = "es",
                 face_side: str = "right", every: float = 10.0, seed: int = 0) -> list[dict[str, Any]]:
    """Offline director: drawings for known words, characters for people, big words for exclamations."""
    rng = random.Random(seed)
    styles = list(STYLE_NAMES)
    rng.shuffle(styles)
    effects = ["pop", "bounce", "spin", "wobble", "drop", "jelly", "fly_right", "pulse", "rise", "shake", "fly_left"]
    free = "left" if face_side == "right" else "right"
    sides = [free, f"top-{free}", f"bottom-{free}"]
    replies = REPLIES.get((language or "es")[:2], REPLIES["en"])
    names = {Path(entry["path"]).stem: set(entry.get("words") or []) for entry in library}
    gags: list[dict[str, Any]] = []
    free_at, warp_free_at, math_free_at = 1.5, 10.0, 8.0
    for start, end, text in _timed_lines(words):
        tokens = {token for token in re.split(r"[^a-z]+", _ascii(text)) if token}
        warp = _first_match(WARP_WORDS, tokens)
        if warp and start >= warp_free_at:
            gags.append({"kind": "warp", "warp": warp, "at": round(start, 2), "duration": 3.0})
            warp_free_at = start + 40.0
        if tokens & set(MATH_WORDS) and start >= math_free_at:
            gags.append({"kind": "math", "at": round(start, 2), "duration": 4.0})
            math_free_at = start + 45.0
        if start < free_at:
            continue
        photo = next((name for name, keys in names.items() if keys and keys <= tokens), None)
        character = _first_match(CHARACTERS, tokens)
        drawing = _first_match(DRAWINGS, tokens)
        n = len(gags)
        if photo:
            gag = {"kind": "photo", "query": photo, "size": 0.28}
        elif character:
            gag = {"kind": "character", "subject": character, "says": replies[n % len(replies)], "size": 0.3}
        elif drawing:
            gag = {"kind": "drawing", "subject": drawing, "size": 0.24}
        elif re.search(r"[!¡]", text):
            shout = " ".join(re.sub(r"[¡!¿?.,]", "", text).split()[-2:])
            if not shout:
                continue
            gag = {"kind": "word", "text": shout, "size": 0.3}
        else:
            continue
        duration = round(min(3.0, max(1.8, end - start + 0.8)), 2)
        gags.append({**gag, "at": round(start, 2), "duration": duration, "style": styles[n % len(styles)],
                     "position": sides[n % len(sides)], "effect": effects[n % len(effects)]})
        free_at = start + duration + every
    return gags


def clean_gags(raw: list[Any], duration: float, *, face_side: str = "right") -> list[dict[str, Any]]:
    """Valid, sorted gags inside the video: pictures never overlap, drawings and
    characters at most one every DRAWING_GAP seconds, warps at most every 30 s,
    styles never repeated back to back."""
    free = "left" if face_side == "right" else "right"
    pictures, warps = [], []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            continue
        try:
            at = max(0.0, float(item.get("at", 0)))
            length = min(4.5, max(1.2, float(item.get("duration", 2))))
            size = min(0.42, max(0.12, float(item.get("size", 0.25))))
        except (TypeError, ValueError):
            continue
        if at >= duration - 0.6:
            continue
        kind = item["kind"]
        if kind == "warp":
            if item.get("warp") in WARPS:
                warps.append({"kind": "warp", "warp": item["warp"], "at": round(at, 2), "duration": round(min(length, duration - at), 2)})
            continue
        if kind == "math":
            # Around the host's head: it overlays the person, not the free side.
            warps.append({"kind": "math", "at": round(at, 2), "duration": round(min(max(length, 3.0), 5.0, duration - at), 2)})
            continue
        subject = str(item.get("subject") or "").strip()[:120]
        text = str(item.get("text") or "").strip()[:40]
        says = str(item.get("says") or "").strip()[:80]
        query = str(item.get("query") or "").strip()[:80]
        if (kind in ("drawing", "character") and not subject) or (kind == "word" and not text) or (kind == "photo" and not query):
            continue
        position = item.get("position") if item.get("position") in POSITIONS else free
        if face_side in position and size > 0.2:
            position = position.replace(face_side, free)  # big pictures never cover the face
        pictures.append({
            "kind": kind, "at": round(at, 2), "duration": round(min(length, duration - at), 2), "size": round(size, 3),
            "subject": subject, "says": says, "text": text, "query": query,
            "style": item.get("style") if item.get("style") in STYLES else "",
            "position": position,
            "effect": item.get("effect") if item.get("effect") in EFFECTS else "pop",
        })
    pictures.sort(key=lambda gag: gag["at"])
    kept: list[dict[str, Any]] = []
    drawn_at = -DRAWING_GAP
    for gag in pictures:
        if kept and gag["at"] < kept[-1]["at"] + kept[-1]["duration"] + 0.2:
            continue
        if gag["kind"] in ("drawing", "character"):
            if gag["at"] < drawn_at + DRAWING_GAP:          # each one is drawn: few, on the best moments
                continue
            drawn_at = gag["at"]
        kept.append(gag)
    rotation = list(STYLE_NAMES)
    random.Random(len(kept)).shuffle(rotation)
    previous = ""
    for index, gag in enumerate(kept):
        if gag["kind"] in ("drawing", "character") and (not gag["style"] or gag["style"] == previous):
            gag["style"] = next(name for name in rotation[index % len(rotation):] + rotation if name != previous)
        previous = gag["style"] or previous
    warps.sort(key=lambda gag: gag["at"])
    spaced: list[dict[str, Any]] = []
    for warp in warps:
        same = [kept for kept in spaced if kept["kind"] == warp["kind"]]
        if not same or warp["at"] >= same[-1]["at"] + (45.0 if warp["kind"] == "math" else 30.0):
            spaced.append(warp)
    return sorted(kept + spaced, key=lambda gag: gag["at"])
