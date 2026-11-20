"""Pure equivalence helpers for skill-library deduplication.

Adapted from the collaborator mindcraft implementation in
`src/agent/library/dedup_keys.js`. The effect key here intentionally matches
this repository's existing behavior; both implementations came from the shared
curation proposal.
"""

import re


AUTO_DEDUP_FINGERPRINT_CONFIDENCE = {"high", "medium"}
NON_INVENTORY_DEDUP_ACTIONS = {
    "approach",
    "equip",
    "explore",
    "find",
    "go",
    "locate",
    "move",
    "navigate",
    "open",
    "place",
    "search",
    "travel",
}
VALID_DEDUP_MODES = {"effect", "semantic", "syntactic", "none"}


def target_action_for_dedup(entry):
    entry = entry or {}
    for intent_key in ("skill_intent", "task_intent"):
        intent = entry.get(intent_key) or {}
        action = str(intent.get("target_action", "") or "").strip().lower()
        if action:
            return action
    return ""


def effect_key(entry):
    fingerprint = entry.get("fingerprint", [])
    if not fingerprint:
        return None

    confidence = str(entry.get("fingerprint_confidence", "unknown")).lower()
    if confidence not in AUTO_DEDUP_FINGERPRINT_CONFIDENCE:
        return None

    intent_consistency = str(entry.get("intent_consistency", "unknown")).lower()
    if intent_consistency == "conflict":
        return None

    target_action = target_action_for_dedup(entry)
    if not target_action:
        return None
    if target_action in NON_INVENTORY_DEDUP_ACTIONS:
        return None

    return (target_action, tuple(sorted(set(fingerprint))))


def semantic_text(entry):
    description = str(entry.get("description") or "")
    if not description:
        name = str(entry.get("name", "") or "")
        target = target_action_for_dedup(entry)
        description = f"{name} {target}"

    kept_segments = []
    for segment in description.split(";"):
        if re.search(r"net\s+effect\s*:", segment, flags=re.IGNORECASE):
            continue
        stripped = segment.strip()
        if stripped:
            kept_segments.append(stripped)
    return "; ".join(kept_segments)


def semantic_tokens(entry):
    return set(re.findall(r"[a-z0-9_]+", semantic_text(entry).lower()))


def jaccard(a, b):
    a = set(a)
    b = set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_code(src):
    src = re.sub(r"/\*.*?\*/", " ", str(src), flags=re.DOTALL)
    src = re.sub(r"//[^\r\n]*", " ", src)
    src = re.sub(r'"(?:\\.|[^"\\])*"', "#", src, flags=re.DOTALL)
    src = re.sub(r"'(?:\\.|[^'\\])*'", "#", src, flags=re.DOTALL)
    src = re.sub(r"`(?:\\.|[^`\\])*`", "#", src, flags=re.DOTALL)
    src = re.sub(r"\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b", "#", src)
    src = re.sub(r"([{}()\[\];,.:+\-*/%<>=!&|?])", r" \1 ", src)
    return re.sub(r"\s+", " ", src).strip()


def code_signature(entry):
    code = str(entry.get("code") or "")
    if code:
        return "src:" + normalize_code(code)
    return "tmpl:" + str(entry.get("name", "") or "") + "(#)"


def _validate_mode(mode):
    if mode not in VALID_DEDUP_MODES:
        raise ValueError(f"Invalid dedup_mode: {mode}")


def equivalence_key(entry, mode):
    _validate_mode(mode)
    if mode == "effect":
        return effect_key(entry)
    if mode == "syntactic":
        return code_signature(entry)
    if mode == "semantic":
        return None
    return None


def are_equivalent(a, b, mode, threshold=0.5):
    _validate_mode(mode)
    if mode == "none":
        return False
    if mode == "semantic":
        a_tokens = semantic_tokens(a)
        b_tokens = semantic_tokens(b)
        return bool(a_tokens and b_tokens) and jaccard(a_tokens, b_tokens) >= threshold

    a_key = equivalence_key(a, mode)
    b_key = equivalence_key(b, mode)
    return a_key is not None and b_key is not None and a_key == b_key


def dedup_skip_reason(entry, mode):
    _validate_mode(mode)
    if mode != "effect":
        return None

    fingerprint = entry.get("fingerprint", [])
    if not fingerprint:
        return "empty_fingerprint"

    confidence = str(entry.get("fingerprint_confidence", "unknown")).lower()
    if confidence not in AUTO_DEDUP_FINGERPRINT_CONFIDENCE:
        return f"weak_fingerprint:{confidence}"

    intent_consistency = str(entry.get("intent_consistency", "unknown")).lower()
    if intent_consistency == "conflict":
        return "intent_conflict"

    target_action = target_action_for_dedup(entry)
    if not target_action:
        return "missing_target_action"
    if target_action in NON_INVENTORY_DEDUP_ACTIONS:
        return f"non_inventory_action:{target_action}"

    return None
