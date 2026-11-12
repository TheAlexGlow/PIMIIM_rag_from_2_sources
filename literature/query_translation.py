import re
from difflib import SequenceMatcher
from time import monotonic
from unicodedata import combining, normalize

from .bergamot import BergamotTranslator, TranslationError
from .models import QueryTranslation, TranslationCheck
from .pipeline import english_terms
from .sources import clean

CYRILLIC = re.compile(r"[а-яё]", re.I)
NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")
LATIN_WORDS = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)*\b")
RU_WORDS = re.compile(r"[а-яё]+")
RU_STOP = frozenset("""
а в во для до за и из или как к ли на над о об от по под при про с со у что это
найди найти найдите покажи статьи статью исследования публикации про
связь связи связано влияние влияет эффект эффекты между какой какие какая
""".split())
RU_NEGATION = frozenset({"не", "нет", "без", "кроме"})
EN_NEGATION = frozenset({"not", "no", "without", "non", "except", "excluding"})


def needs_translation(text: str) -> bool:
    return bool(CYRILLIC.search(text))


def normalized_english(text: str) -> str:
    punctuation = str.maketrans("‘’“”–—−", "''\"\"---")
    decomposed = normalize("NFKD", text.translate(punctuation))
    return clean("".join(char for char in decomposed if not combining(char)))


def number_set(text: str) -> set[str]:
    return {number.replace(",", ".") for number in NUMBERS.findall(text)}


def russian_words(text: str) -> set[str]:
    return set(RU_WORDS.findall(text.casefold().replace("ё", "е")))


def term_overlap(original: str, reverse: str) -> float:
    first = russian_words(original) - RU_STOP - RU_NEGATION
    second = russian_words(reverse) - RU_STOP - RU_NEGATION
    if not first or not second:
        return 0.0

    def coverage(source, target):
        return sum(
            any(word == candidate or (
                min(len(word), len(candidate)) >= 4
                and word[:3] == candidate[:3]
                and SequenceMatcher(None, word, candidate).ratio() >= 0.68
            ) for candidate in target)
            for word in source
        ) / len(source)

    recall = coverage(first, second)
    precision = coverage(second, first)
    return 2 * recall * precision / (recall + precision) if recall + precision else 0.0


class QueryFormatVerifier:
    name = "Верификатор формата"

    def verify(self, original: str, english: str) -> tuple[bool, str]:
        try:
            english_terms(english)
        except ValueError as error:
            return False, str(error)

        if number_set(original) != number_set(english):
            return False, "Числа в английском запросе отличаются от исходного запроса."

        identifiers = {
            word.casefold() for word in LATIN_WORDS.findall(original)
            if sum(char.isupper() for char in word) >= 2 or any(char.isdigit() for char in word)
        }
        words = {word.casefold() for word in LATIN_WORDS.findall(english)}
        if missing := identifiers - words:
            return False, f"Не сохранены обозначения: {', '.join(sorted(missing))}."

        negative = bool(russian_words(original) & RU_NEGATION)
        english_negative = bool(set(re.findall(r"[a-z]+", english.casefold())) & EN_NEGATION)
        if negative != english_negative:
            return False, "Проверьте, что смысл отрицания сохранился."
        return True, "Английский запрос пригоден для поиска. Числа и обозначения совпадают."


class RoundTripVerifier:
    name = "Верификатор обратного перевода"

    def verify(self, original: str, reverse: str) -> tuple[bool, str]:
        score = term_overlap(original, reverse)
        same_polarity = bool(russian_words(original) & RU_NEGATION) == bool(russian_words(reverse) & RU_NEGATION)
        passed = same_polarity and score >= 0.60
        if not same_polarity:
            return False, "Проверьте отрицание в обратном переводе."
        return passed, f"Совпадение ключевых слов: {score:.0%}. Порог автоматического поиска: 60%."


def review_translation(original: str, translator: BergamotTranslator) -> QueryTranslation:
    original = clean(original)
    started = monotonic()
    try:
        translated, reverse = translator.translate(original)
    except TranslationError as error:
        elapsed = monotonic() - started
        check = TranslationCheck("Перевод", False, str(error), 0.0)
        return QueryTranslation(original, "", "", (check,), elapsed)
    english = normalized_english(translated)
    elapsed = monotonic() - started

    checks = []
    for verifier, value in ((QueryFormatVerifier(), english), (RoundTripVerifier(), reverse)):
        started = monotonic()
        passed, detail = verifier.verify(original, value)
        checks.append(TranslationCheck(verifier.name, passed, detail, monotonic() - started))
    return QueryTranslation(original, english, clean(reverse), tuple(checks), elapsed)
