"""Language and text helpers shared by translation, TTS, media and the gateway."""

from __future__ import annotations

import math
import re
from typing import Iterable

_ABBREVIATIONS = {
    # English
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
    "fig", "no", "vol", "approx", "dept", "inc", "ltd", "co", "u.s", "u.k", "jan",
    "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    # Portuguese
    "sr", "sra", "dra", "profa", "eng", "ex", "exmo", "exma", "ilmo", "ilma",
    "pág", "pag", "cap", "art", "al", "av", "r", "tv", "a.c", "d.c", "nº", "no",
    "fig", "tab", "ref", "ed", "org", "coord", "p.ex", "v.g", "jan", "fev", "mar",
    "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez",
}

_ILLEGAL_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def clean_text(value: str) -> str:
    """Normalize line endings, remove NULs and surplus spaces."""
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", " ")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        return []
    return paragraphs


def _protect_periods(text: str) -> str:
    # Decimal numbers and version strings such as 3.14 or v2.1
    text = re.sub(r"(?<=\d)\.(?=\d)", "\uE000", text)
    # Common abbreviations followed by a period
    def protect(match: re.Match[str]) -> str:
        return match.group(0)[:-1] + "\uE000"

    text = re.sub(
        r"(?i)\b(?:mr|mrs|ms|dr|dra|prof|profa|sr|sra|jr|st|vs|etc|e\.g|i\.e|fig|no|vol|"
        r"approx|dept|inc|ltd|co|u\.s|u\.k|pág|pag|cap|art|al|av|tv|a\.c|d\.c|nº|tab|"
        r"ref|ed|org|coord|p\.ex|v\.g|jan|fev|feb|mar|abr|apr|mai|may|jun|jul|ago|aug|"
        r"set|sep|sept|out|oct|nov|dez|dec)\.",
        protect,
        text,
    )
    # Initials: J. K. Rowling
    text = re.sub(r"(?<=\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ])\.(?=\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]\.)", "\uE000", text)
    # Initial followed by surname
    text = re.sub(r"(?<=\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ])\.(?=\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç])", "\uE000", text)
    return text


def sentence_split(text: str, language: str | None = None) -> list[str]:
    """Split text into sentences using conservative punctuation rules.

    The splitter deliberately avoids splitting common abbreviations, decimals,
    initials and ellipses. It works for English and the Portuguese variants.
    """
    del language  # Rules are shared by the supported languages.
    text = clean_text(text)
    if not text:
        return []
    protected = _protect_periods(text)
    sentences: list[str] = []
    start = 0
    closers = "\"'”’»)]}"
    i = 0
    length = len(protected)
    while i < length:
        char = protected[i]
        if char in ".!?…":
            j = i + 1
            while j < length and protected[j] in "…":
                j += 1
            while j < length and protected[j] in closers:
                j += 1
            next_char = ""
            k = j
            while k < length and protected[k] in " \t":
                k += 1
            if k < length and protected[k] == "\n":
                next_char = "\n"
            elif k < length:
                next_char = protected[k]
            should_split = False
            if k >= length:
                should_split = True
            elif next_char == "\n":
                should_split = True
            elif next_char and (next_char.isupper() or next_char.isdigit() or next_char in "\"'“«("):
                should_split = True
            if should_split:
                piece = protected[start:j].strip()
                if piece:
                    piece = piece.replace("\uE000", ".")
                    piece = piece.replace("\n", " ")
                    sentences.append(piece)
                start = j
                i = j
                continue
        i += 1
    tail = protected[start:].strip().replace("\uE000", ".").replace("\n", " ")
    if tail:
        sentences.append(tail)
    # If the heuristic found nothing useful, return the original paragraph.
    return [s.strip() for s in sentences if s.strip()]


def split_long_piece(piece: str, max_chars: int) -> list[str]:
    """Split an overly long sentence at clauses, then words, without data loss."""
    piece = piece.strip()
    if len(piece) <= max_chars:
        return [piece]
    parts: list[str] = []
    remainder = piece
    while len(remainder) > max_chars:
        window = remainder[: max_chars + 1]
        cut = max(
            window.rfind("; "),
            window.rfind(": "),
            window.rfind(", "),
            window.rfind(" — "),
            window.rfind(" – "),
        )
        if cut < max_chars // 2:
            cut = window.rfind(" ")
        if cut < max_chars // 3:
            cut = max_chars
        candidate = remainder[:cut].strip()
        if candidate:
            parts.append(candidate)
        remainder = remainder[cut:].strip()
    if remainder:
        parts.append(remainder)
    return parts


def chunk_paragraph(paragraph: str, max_chars: int = 900, language: str | None = None) -> list[str]:
    """Pack whole sentences into chunks, preserving sentence boundaries."""
    if max_chars < 80:
        raise ValueError("max_chars must be at least 80")
    sentences = sentence_split(paragraph, language)
    if not sentences:
        return []
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(split_long_piece(sentence, max_chars))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def chunk_paragraphs(
    paragraphs: Iterable[str], max_chars: int = 900, language: str | None = None
) -> list[str]:
    text = "\n\n".join(p for p in paragraphs if p and p.strip())
    if not text:
        return []
    # Join adjacent paragraphs only at paragraph boundaries, keeping the final
    # chunks below max_chars. Paragraph boundaries remain represented by \n\n.
    pieces: list[str] = []
    for paragraph in split_paragraphs(text):
        pieces.extend(chunk_paragraph(paragraph, max_chars, language))
    # Keep paragraph boundaries (do not merge across them) to preserve structure.
    return pieces


def chunk_text_by_sentences(text: str, max_chars: int = 900, language: str | None = None) -> list[str]:
    return chunk_paragraphs(split_paragraphs(text), max_chars, language)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text or ""))


def format_srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    return format_srt_timestamp(seconds).replace(",", ".")


def srt_block(index: int, start: float, end: float, text: str) -> str:
    safe_text = re.sub(r"\s*\n+\s*", " ", text or "").strip()
    return f"{index}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{safe_text}\n"


def build_srt(segments: Iterable[dict], total_duration: float | None = None) -> str:
    blocks: list[str] = []
    index = 0
    previous_end = 0.0
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(segment.get("start", previous_end)))
        end = max(start, float(segment.get("end", start + 1.0)))
        if total_duration is not None:
            start = min(start, total_duration)
            end = min(end, total_duration)
        if end <= start:
            end = min(start + 0.5, total_duration if total_duration is not None else start + 0.5)
        index += 1
        blocks.append(srt_block(index, start, end, text))
        previous_end = end
    return "\n".join(blocks)


def normalize_language(value: str | None, default: str = "en") -> str:
    raw = (value or default).strip().replace("_", "-").lower()
    aliases = {
        "auto": "auto",
        "pt": "pt",
        "pt-pt": "pt",
        "pt-br": "pt",
        "portuguese": "pt",
        "português": "pt",
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
        "english": "en",
        "inglês": "en",
    }
    if raw not in aliases:
        raise ValueError(f"Unsupported language: {value}")
    return aliases[raw]


def full_language_name(value: str) -> str:
    return {"pt": "Portuguese", "en": "English"}.get(normalize_language(value), value)


def safe_filename(stem: str, suffix: str, fallback: str = "document") -> str:
    cleaned = _ILLEGAL_FILENAME.sub("_", stem or "").strip(" ._-")
    cleaned = re.sub(r"_{2,}", "_", cleaned)[:100] or fallback
    if not suffix.startswith("."):
        suffix = "." + suffix
    return cleaned + suffix


def estimate_speech_duration(text: str, words_per_minute: float = 150.0) -> float:
    """Fallback timing for text-only SRT generation."""
    words = max(1, word_count(text))
    return max(0.8, (words / max(words_per_minute, 40.0)) * 60.0)


def distribute_duration(total_seconds: float, texts: Iterable[str], minimum: float = 0.6) -> list[tuple[float, float]]:
    """Allocate a real audio duration proportionally to text length."""
    items = [max(1, len(t.strip())) for t in texts]
    if not items:
        return []
    total_units = float(sum(items))
    duration = max(total_seconds, minimum * len(items))
    results: list[tuple[float, float]] = []
    cursor = 0.0
    for index, units in enumerate(items):
        if index == len(items) - 1:
            end = duration
        else:
            share = duration * units / total_units
            end = min(duration, cursor + max(minimum, share))
        results.append((cursor, end))
        cursor = end
    return results


def split_into_managed_chunks(text: str, max_chars: int, language: str | None = None) -> list[str]:
    """Public alias used by multiple services."""
    return chunk_text_by_sentences(text, max_chars=max_chars, language=language)


def count_tokens_approx(text: str) -> int:
    """Safe, tokenizer-free approximation used for emergency limits."""
    return max(1, math.ceil(len(text) / 4))
