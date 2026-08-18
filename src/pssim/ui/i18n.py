"""UI translations.

Uses the **standard Qt mechanism**, not a dictionary of our own. The reason: Qt already
solves the things a homegrown implementation would get wrong — plurals, falling back to the
source text, translating Qt's own dialogs (`OK`, `Cancel`, the button names in
`QFileDialog`) and the tooling for extracting strings.

**The source language is English.** The strings in the code are wrapped in `tr()` or
`QCoreApplication.translate()`; with no translation installed they are used as they are.
That way the application works even when no `.qm` file exists.

## How to add a language

1. Extract the strings into a `.ts` file:

   ```
   uv run pyside6-lupdate src/pssim/ui/*.py -ts src/pssim/ui/translations/pssim_sk.ts
   ```

2. Translate it (Qt Linguist, or by hand in the XML).
3. Compile it to `.qm`:

   ```
   uv run pyside6-lrelease src/pssim/ui/translations/pssim_sk.ts
   ```

4. Add the language code to `LANGUAGES` below.

A language choice in the menu does not exist yet — `install_translator()` is where it will
hook in. Until then the language can be picked with the `pssim ui --lang` switch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pssim.observability import get_logger

logger = get_logger(__name__)

#: The source language. The strings in the code are written in it, so it needs no translation.
SOURCE_LANGUAGE: Final = "en"

#: The languages the application offers. The key is the ISO code, the value the name in that
#: language — a future language menu should show "Slovenčina", not "Slovak".
LANGUAGES: Final[dict[str, str]] = {
    "en": "English",
    "sk": "Slovenčina",
}

TRANSLATIONS_DIR: Final = Path(__file__).parent / "translations"

#: The prefix of the `.qm` file names: `pssim_sk.qm`, `pssim_de.qm`, …
FILE_PREFIX: Final = "pssim_"


def available_languages() -> dict[str, str]:
    """The languages that can actually be used.

    The source language is always available; the others only when a compiled `.qm` file
    exists for them. The menu therefore never offers a language that would leave the UI in
    English after switching to it.
    """
    usable = {SOURCE_LANGUAGE: LANGUAGES[SOURCE_LANGUAGE]}
    for code, name in LANGUAGES.items():
        if code != SOURCE_LANGUAGE and translation_file(code).is_file():
            usable[code] = name
    return usable


def translation_file(language: str) -> Path:
    """The path to a language's `.qm` file. It need not exist."""
    return TRANSLATIONS_DIR / f"{FILE_PREFIX}{language}.qm"


def install_translator(application: Any, language: str = SOURCE_LANGUAGE) -> bool:
    """Install a translation into the application. Returns `True` if it really loaded.

    A missing translation **is not an error** — the application stays in the source
    language. Failing hard would mean a typo in a language code brings down startup.

    The application has to hold on to the translator (`application._pssim_translator`), or
    Python deallocates it and the translations stop working without any error at all.
    """
    if language == SOURCE_LANGUAGE:
        return False

    if language not in LANGUAGES:
        logger.warning(
            "unknown language, staying with the source one",
            language=language,
            known=sorted(LANGUAGES),
        )
        return False

    path = translation_file(language)
    if not path.is_file():
        logger.warning("translation is not compiled", language=language, file=str(path))
        return False

    from PySide6.QtCore import QTranslator

    translator = QTranslator(application)
    if not translator.load(str(path)):
        logger.warning("translation cannot be loaded", file=str(path))
        return False

    application.installTranslator(translator)
    # The application holds the reference — without it the translator would vanish with
    # the garbage collector.
    application._pssim_translator = translator  # noqa: SLF001
    logger.info("translation installed", language=language)
    return True
