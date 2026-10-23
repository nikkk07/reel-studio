"""Apply the pronunciation dictionary to text that goes to the voice model (never to captions)."""
import json, re
def load(path): return json.load(open(path, encoding="utf-8"))["entries"]
def apply(text, entries):
    pairs = sorted(((m, e["say"]) for e in entries for m in e["match"]), key=lambda p: -len(p[0]))
    for m, say in pairs:
        if m == say: continue
        flags = 0 if any(ch.isupper() for ch in m) and m.isupper() else re.I   # acronyms match case-sensitively
        text = re.sub(r"(?<![\w-])" + re.escape(m) + r"(?![\w-])", say, text, flags=flags)
    return text
def unknown_english(text, entries):
    """English words in a line that are not in the dictionary yet -> candidates for a one-time ear test."""
    known = {m.lower() for e in entries for m in e["match"]} | {w.lower() for e in entries for m in e["match"] for w in m.split()}
    return sorted({w for w in re.findall(r"[A-Za-z][A-Za-z'.-]*", text) if w.lower().strip(".") not in known})
if __name__ == "__main__":
    E = load("/home/claude/lexicon/pronunciation_lexicon.json")
    for t in ["January two thousand nine, New York. US Airways की एक flight, Canada geese के झुंड से टकराई।",
              "ऐसी ही aviation stories के लिए, follow करें We One Aviation।",
              "Mayday बना French शब्द m'aider से, ATC की radar screens पर alarm बज जाता है।"]:
        print(apply(t, E)); print("  new words:", unknown_english(t, E))
