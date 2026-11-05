"""Core metric metadata and invocation models."""

import unicodedata


def normalize_metric_search_tokens(value: str) -> tuple[str, ...]:
    """Return stable, accent-insensitive tokens for lexical metric search.

    Identifier boundaries are treated like whitespace, including snake_case,
    kebab-case, and camelCase boundaries. Repeated tokens are removed while
    preserving their first-seen order so callers cannot change ranking by
    repeating a search term.
    """
    segments: list[str] = []
    current: list[str] = []

    def finish_segment() -> None:
        if current:
            segments.append("".join(current))
            current.clear()

    for index, character in enumerate(value):
        if not character.isalnum():
            finish_segment()
            continue

        previous = current[-1] if current else None
        following = value[index + 1] if index + 1 < len(value) else None
        starts_camel_word = (
            character.isupper()
            and previous is not None
            and (
                previous.islower()
                or previous.isdigit()
                or (
                    previous.isupper() and following is not None and following.islower()
                )
            )
        )
        if starts_camel_word:
            finish_segment()
        current.append(character)
    finish_segment()

    tokens: list[str] = []
    for segment in segments:
        normalized = unicodedata.normalize("NFKD", segment).casefold()
        token = "".join(
            character
            for character in normalized
            if character.isalnum() and not unicodedata.combining(character)
        )
        if token:
            tokens.append(token)
    return tuple(dict.fromkeys(tokens))
