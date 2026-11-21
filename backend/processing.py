import os
import re
import statistics
import math
from collections import Counter
from pathlib import Path

import pdfplumber
from rapidfuzz import fuzz

UPLOAD_DIR = str(Path(__file__).resolve().parent / "uploads")

# -------------------------
# Stopwords (small built-in list to reduce false positives)
# -------------------------
_STOPWORDS = {
    "the","a","an","and","or","but","if","then","else","when","while","for","to","of","in","on","at","by","with",
    "as","is","are","was","were","be","been","being","it","this","that","these","those","from","into","over","under",
    "between","about","against","during","before","after","above","below","up","down","out","off","again","further",
    "once","here","there","why","how","all","any","both","each","few","more","most","other","some","such","no","nor",
    "not","only","own","same","so","than","too","very","can","will","just","do","does","did","doing","have","has","had",
    "having","i","you","he","she","they","we","me","him","her","them","my","your","his","their","our"
}

# -------------------------
# Text cleanup helpers
# -------------------------
def _normalize_whitespace(text: str) -> str:
    text = (text or "")
    text = text.replace("\u00ad", "")  # soft hyphen
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _fix_pdf_linebreaks(text: str) -> str:
    """
    Improves pdfplumber output:
    - join hyphenated line breaks: "exam-\nple" => "example"
    - collapse single newlines into spaces (keep paragraph breaks)
    """
    t = text or ""
    # join hyphenated line breaks
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    # keep paragraph breaks but merge single newlines
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)
    return t

def remove_repeated_lines(raw: str, min_repeats: int = 3) -> str:
    """Remove lines that repeat often (common PDF header/footer artifacts)."""
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    non_empty = [ln for ln in lines if ln]
    if not non_empty:
        return raw or ""

    freq = Counter(non_empty)
    kept = []
    for ln in lines:
        s = ln.strip()
        if not s:
            kept.append("")
            continue
        if freq.get(s, 0) >= min_repeats:
            continue
        kept.append(ln)
    return "\n".join(kept)

def strip_references_section(text: str) -> str:
    """Best-effort removal of bibliography/references which often dominate similarity."""
    t = text or ""
    m = re.search(r"\n\s*(references|bibliography|works\s+cited)\s*\n", t, flags=re.I)
    if m:
        return t[: m.start()].rstrip()
    return t

def clean_text_for_compare(text: str) -> str:
    """Clean text for similarity/AI heuristics without destroying meaning."""
    t = _normalize_whitespace(text)
    # Remove standalone page numbers and 'Page X of Y'
    t = re.sub(r"(?im)^\s*page\s*\d+(\s*of\s*\d+)?\s*$", "", t)
    t = re.sub(r"(?im)^\s*\d+\s*$", "", t)
    t = _normalize_whitespace(t)
    return t

def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", (text or "").lower())

def _content_words(text: str) -> list[str]:
    words = _tokenize_words(text)
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]

def _low_quality_extraction(text: str) -> bool:
    """
    If extraction is too short or looks like junk, scores become unreliable.
    You can tune these thresholds later.
    """
    t = text or ""
    if len(t) < 500:
        return True
    words = _tokenize_words(t)
    if len(words) < 120:
        return True
    # too many 1-2 char tokens often indicates broken extraction
    short_ratio = sum(1 for w in words if len(w) <= 2) / max(1, len(words))
    if short_ratio > 0.35:
        return True
    return False

# -------------------------
# IO
# -------------------------
def extract_pdf_text(file_id: str) -> str:
    path = os.path.join(UPLOAD_DIR, f"{file_id}.pdf")
    if not os.path.exists(path):
        return ""

    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                texts.append(t)

    full = "\n".join(texts).strip()
    full = _fix_pdf_linebreaks(full)

    # Clean up PDF artifacts BEFORE caching/analysis
    full = remove_repeated_lines(full, min_repeats=3)
    full = strip_references_section(full)
    full = clean_text_for_compare(full)

    # cache as txt
    txt_path = os.path.join(UPLOAD_DIR, f"{file_id}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full)

    return full

def load_cached_text(file_id: str) -> str:
    txt_path = os.path.join(UPLOAD_DIR, f"{file_id}.txt")
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

# -------------------------
# Similarity (IMPROVED)
# -------------------------
def chunk_text(text: str, chunk_words: int = 35, overlap: int = 10) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, chunk_words - overlap)
    for i in range(0, len(words), step):
        chunk = words[i : i + chunk_words]
        if len(chunk) < 10:
            continue
        chunks.append(" ".join(chunk))
    return chunks

def _shingle_jaccard(text_a: str, text_b: str, k: int = 5) -> float:
    a = _content_words(text_a)
    b = _content_words(text_b)
    if len(a) < k or len(b) < k:
        return 0.0
    sa = {" ".join(a[i:i+k]) for i in range(0, len(a) - k + 1)}
    sb = {" ".join(b[i:i+k]) for i in range(0, len(b) - k + 1)}
    inter = len(sa & sb)
    union = len(sa | sb) or 1
    return inter / union  # 0..1

def _ngram_cosine(text_a: str, text_b: str, n: int = 2) -> float:
    """
    Cosine similarity over word n-grams using simple TF-IDF-ish weighting (no extra deps).
    Works as a stabilizer against "same topic" but not copied.
    """
    def ngrams(words: list[str]) -> list[str]:
        if len(words) < n:
            return []
        return [" ".join(words[i:i+n]) for i in range(0, len(words)-n+1)]

    wa = _content_words(text_a)
    wb = _content_words(text_b)
    na = ngrams(wa)
    nb = ngrams(wb)
    if not na or not nb:
        return 0.0

    ca = Counter(na)
    cb = Counter(nb)

    # document frequency across the two docs (N=2)
    vocab = set(ca) | set(cb)
    if not vocab:
        return 0.0

    # build weighted vectors
    va = {}
    vb = {}
    for term in vocab:
        df = (1 if term in ca else 0) + (1 if term in cb else 0)
        idf = math.log((2 + 1) / (df + 1)) + 1.0  # smooth IDF
        va[term] = (math.log(1 + ca.get(term, 0)) * idf)
        vb[term] = (math.log(1 + cb.get(term, 0)) * idf)

    dot = sum(va[t] * vb[t] for t in vocab)
    na_norm = math.sqrt(sum(v * v for v in va.values()))
    nb_norm = math.sqrt(sum(v * v for v in vb.values()))
    if na_norm <= 1e-9 or nb_norm <= 1e-9:
        return 0.0
    return dot / (na_norm * nb_norm)  # 0..1

def _fuzzy_chunk_similarity(text_a: str, text_b: str) -> float:
    """
    Your original approach, improved defaults:
    - smaller chunks catch smaller copied sections
    - stronger threshold reduces false positives
    Returns 0..1
    """
    a = clean_text_for_compare(text_a)
    b = clean_text_for_compare(text_b)

    a_chunks = chunk_text(a, chunk_words=35, overlap=10)
    b_chunks = chunk_text(b, chunk_words=35, overlap=10)
    if not a_chunks or not b_chunks:
        return 0.0

    best_scores: list[int] = []
    for a_ck in a_chunks:
        best = 0
        for b_ck in b_chunks:
            score = fuzz.token_set_ratio(a_ck, b_ck)  # 0-100
            if score > best:
                best = score
            if best >= 97:
                break
        best_scores.append(best)

    # Raise "strong match" threshold a bit to reduce false positives
    strong = [s for s in best_scores if s >= 90]
    match_ratio = len(strong) / max(1, len(best_scores))  # 0..1

    k = max(3, len(best_scores) // 5)
    top_k = sorted(best_scores, reverse=True)[:k]
    top_k_mean = sum(top_k) / max(1, len(top_k))  # 0..100

    # convert to 0..1
    score01 = (match_ratio * 0.5) + ((top_k_mean / 100.0) * 0.5)
    return max(0.0, min(1.0, score01))

def similarity_between_texts(text_a: str, text_b: str) -> float:
    """
    More accurate 0..100 similarity score by blending:
    - Shingle Jaccard (copy/paste overlap)
    - Bigram cosine (topic overlap stabilizer)
    - Fuzzy chunk match (backup)
    """
    a = clean_text_for_compare(text_a)
    b = clean_text_for_compare(text_b)

    # If extraction looks bad, avoid producing misleading high scores
    if _low_quality_extraction(a) or _low_quality_extraction(b):
        # Still compute, but heavily dampen
        damp = 0.55
    else:
        damp = 1.0

    j = _shingle_jaccard(a, b, k=5)          # 0..1
    c = _ngram_cosine(a, b, n=2)             # 0..1
    f = _fuzzy_chunk_similarity(a, b)        # 0..1

    blended = (0.45 * j) + (0.35 * c) + (0.20 * f)
    score = 100.0 * blended * damp
    return float(max(0.0, min(100.0, score)))

# -------------------------
# AI detection (your fuzzy rule-based; slightly stabilized)
# -------------------------
def _trapmf(x: float, a: float, b: float, c: float, d: float) -> float:
    if x <= a or x >= d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if a < x < b:
        return (x - a) / (b - a + 1e-9)
    return (d - x) / (d - c + 1e-9)

def _trimf(x: float, a: float, b: float, c: float) -> float:
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    if a < x < b:
        return (x - a) / (b - a + 1e-9)
    return (c - x) / (c - b + 1e-9)

def ai_likelihood_fuzzy(text: str) -> float:
    text = clean_text_for_compare(text or "")
    words = re.findall(r"\b\w+\b", text.lower())
    if len(words) < 80:
        return 0.0

    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # -------------------------
    # Grammar Score (light weight)
    # -------------------------
    error_patterns = [
        r"\bi\s+is\b", r"\bhe\s+are\b", r"\bshe\s+are\b",
        r"\bit\s+are\b", r"\bwe\s+was\b", r"\bthey\s+was\b"
    ]

    grammar_errors = sum(len(re.findall(p, text.lower())) for p in error_patterns)
    grammar_score = grammar_errors / max(1, len(sentences))
    grammar_score = min(1.0, grammar_score)

    # -------------------------
    # Vocabulary Richness (MAIN FEATURE)
    # -------------------------
    unique_ratio = len(set(words)) / len(words)

    # -------------------------
    # Sentence Length Consistency
    # -------------------------
    lengths = [len(s.split()) for s in sentences if len(s.split()) > 3]
    if len(lengths) > 1:
        stdev = statistics.pstdev(lengths)
        mean = statistics.mean(lengths)
        uniformity = 1.0 - min(1.0, stdev / (mean + 1e-6))
    else:
        uniformity = 0.5

    # -------------------------
    # FUZZIFICATION (Calibrated to your real values)
    # -------------------------

    # Vocabulary (dominant)
    V_low  = _trapmf(unique_ratio, 0.00, 0.00, 0.45, 0.48)
    V_med  = _trimf(unique_ratio, 0.46, 0.50, 0.54)
    V_high = _trapmf(unique_ratio, 0.52, 0.55, 1.00, 1.00)

    # Grammar penalty
    G_low  = _trapmf(grammar_score, 0.00, 0.00, 0.05, 0.10)
    G_high = _trapmf(grammar_score, 0.15, 0.20, 1.00, 1.00)

    # Uniformity (secondary)
    U_high = _trapmf(uniformity, 0.60, 0.70, 1.00, 1.00)
    U_low  = _trapmf(uniformity, 0.00, 0.00, 0.45, 0.55)

    # -------------------------
    # RULE BASE
    # -------------------------

    # Strong AI
    r1 = min(V_high, G_low)
    r2 = min(V_high, U_high)

    # Medium AI
    r3 = V_med

    # Human
    r4 = G_high
    r5 = min(V_low, U_low)

    out_low = max(r4, r5)
    out_med = r3
    out_high = max(r1, r2)

    # -------------------------
    # DEFUZZIFICATION
    # -------------------------
    num = 0.0
    den = 0.0

    for x in range(0, 101):
        mu_low  = _trapmf(x, 0, 0, 20, 35)
        mu_med  = _trimf(x, 30, 50, 70)
        mu_high = _trapmf(x, 60, 75, 100, 100)

        mu = max(
            min(out_low, mu_low),
            min(out_med, mu_med),
            min(out_high, mu_high),
        )

        num += x * mu
        den += mu

    if den == 0:
        return 0.0

    return float(num / den)
