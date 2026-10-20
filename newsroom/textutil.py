"""Text helpers: normalisation, similarity, numbers, HTML extraction."""
from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "to", "for", "from", "by", "with",
    "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those",
    "has", "have", "had", "will", "would", "can", "could", "may", "might", "after", "before",
    "over", "into", "about", "says", "said", "new", "how", "why", "what", "who", "than", "up",
    "out", "not", "no", "now", "more", "most", "just", "also", "amid", "per", "vs", "his", "her",
    "their", "our", "your", "you", "we", "they", "he", "she", "i",
}

TWO_LEVEL_TLDS = {"co.in", "co.uk", "com.au", "co.jp", "com.br", "co.nz", "org.in", "gov.in", "nic.in", "ac.in"}
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "source", "cmpid", "ocid", "guccounter", "mc_cid", "mc_eid"}

_SUFFIX_RE = re.compile(r"\s+[-|\u2013\u2014]\s+[^-|\u2013\u2014]{2,40}$")
_WORD_RE = re.compile(r"[a-z0-9]+")
_NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w])")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u20b9$\"'\u201c])")


def strip_publisher_suffix(title: str) -> str:
    return _SUFFIX_RE.sub("", title or "").strip()


def _stem(word: str) -> str:
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    cleaned = strip_publisher_suffix(text or "").lower().replace("'", "").replace("\u2019", "")
    for word in _WORD_RE.findall(cleaned):
        if word in STOPWORDS or len(word) < 2:
            continue
        out.add(_stem(word))
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap(a: set[str], b: set[str]) -> tuple[float, int]:
    if not a or not b:
        return 0.0, 0
    shared = len(a & b)
    return shared / min(len(a), len(b)), shared


def fingerprint(text: str) -> str:
    return hashlib.sha1(" ".join(sorted(tokens(text))).encode("utf-8")).hexdigest()[:16]


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url or "").netloc.lower().split(":")[0]
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_LEVEL_TLDS:
        return ".".join(labels[-3:])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host


def extract_numbers(text: str) -> set[str]:
    """Numbers worth grounding: two or more digits, or any decimal. Commas removed."""
    found: set[str] = set()
    for match in _NUM_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        digits = raw.replace(".", "")
        if "." in raw or len(digits) >= 2:
            found.add(raw)
    return found


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'\u2019\u20b9$%.-]+\b", text or ""))


def normalize_for_match(text: str) -> str:
    text = (text or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%.$\u20b9' ]+", " ", text)).strip()


def quote_supported(evidence: str, text: str, threshold: float = 0.85) -> bool:
    """True when `evidence` appears in `text` (exactly after normalising, or nearly)."""
    from difflib import SequenceMatcher

    ev = normalize_for_match(evidence)
    body = normalize_for_match(text)
    if not ev or not body:
        return False
    if ev in body:
        return True
    matcher = SequenceMatcher(None, ev, body, autojunk=False)
    block = matcher.find_longest_match(0, len(ev), 0, len(body))
    return block.size / max(len(ev), 1) >= threshold


def truncate_words(text: str, limit: int) -> str:
    words = (text or "").split()
    return " ".join(words[:limit])


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe", "button"}
    BLOCK = {"p", "li", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.article_depth = 0
        self.block_depth = 0
        self.buffer: list[str] = []
        self.all_paragraphs: list[str] = []
        self.article_paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag == "article":
            self.article_depth += 1
        elif tag in self.BLOCK:
            self.block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == "article":
            self.article_depth = max(0, self.article_depth - 1)
        elif tag in self.BLOCK:
            self._flush()
            self.block_depth = max(0, self.block_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0 and self.block_depth > 0:
            self.buffer.append(data)

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buffer)).strip()
        self.buffer = []
        if len(text) >= 40:
            self.all_paragraphs.append(text)
            if self.article_depth > 0:
                self.article_paragraphs.append(text)


def html_to_text(html: str, max_chars: int = 6000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # malformed HTML must never crash the pipeline
        pass
    article = " ".join(parser.article_paragraphs)
    text = article if len(article) >= 400 else " ".join(parser.all_paragraphs)
    return text[:max_chars]
