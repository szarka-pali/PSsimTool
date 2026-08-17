"""Preklady UI.

Používa **štandardný mechanizmus Qt**, nie vlastný slovník. Dôvod: Qt už rieši
veci, ktoré by vlastná implementácia riešila zle — množné čísla, fallback na
zdrojový text, preklad vlastných dialógov Qt (`OK`, `Cancel`, názvy tlačidiel
v `QFileDialog`) a nástroje na extrakciu textov.

**Zdrojový jazyk je angličtina.** Texty v kóde sú obalené v `tr()` alebo
`QCoreApplication.translate()`; bez nainštalovaného prekladu sa použijú tak,
ako sú. Vďaka tomu appka funguje aj keď žiadny `.qm` súbor neexistuje.

## Ako pridať jazyk

1. Vyextrahuj texty do `.ts` súboru:

   ```
   uv run pyside6-lupdate src/pssim/ui/*.py -ts src/pssim/ui/translations/pssim_sk.ts
   ```

2. Prelož ho (Qt Linguist alebo ručne v XML).
3. Skompiluj na `.qm`:

   ```
   uv run pyside6-lrelease src/pssim/ui/translations/pssim_sk.ts
   ```

4. Pridaj kód jazyka do `LANGUAGES` nižšie.

Voľba jazyka v menu zatiaľ nie je — `install_translator()` je miesto, na ktoré
sa napojí. Dovtedy sa dá jazyk vybrať prepínačom `pssim ui --lang`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pssim.observability import get_logger

logger = get_logger(__name__)

#: Zdrojový jazyk. Texty v kóde sú v ňom napísané, takže preklad netreba.
SOURCE_LANGUAGE: Final = "en"

#: Jazyky, ktoré appka ponúka. Kľúč je ISO kód, hodnota názov v tom jazyku —
#: budúce menu voľby jazyka má zobrazovať „Slovenčina", nie „Slovak".
LANGUAGES: Final[dict[str, str]] = {
    "en": "English",
    "sk": "Slovenčina",
}

TRANSLATIONS_DIR: Final = Path(__file__).parent / "translations"

#: Prefix názvov `.qm` súborov: `pssim_sk.qm`, `pssim_de.qm`, …
FILE_PREFIX: Final = "pssim_"


def available_languages() -> dict[str, str]:
    """Jazyky, ktoré sa naozaj dajú použiť.

    Zdrojový jazyk je vždy k dispozícii; ostatné len ak k nim existuje
    skompilovaný `.qm` súbor. Menu tak nikdy nenabídne jazyk, po prepnutí
    na ktorý by zostala angličtina.
    """
    usable = {SOURCE_LANGUAGE: LANGUAGES[SOURCE_LANGUAGE]}
    for code, name in LANGUAGES.items():
        if code != SOURCE_LANGUAGE and translation_file(code).is_file():
            usable[code] = name
    return usable


def translation_file(language: str) -> Path:
    """Cesta k `.qm` súboru daného jazyka. Existovať nemusí."""
    return TRANSLATIONS_DIR / f"{FILE_PREFIX}{language}.qm"


def install_translator(application: Any, language: str = SOURCE_LANGUAGE) -> bool:
    """Nainštaluje preklad do aplikácie. Vracia `True`, ak sa naozaj načítal.

    Chýbajúci preklad **nie je chyba** — appka zostane v zdrojovom jazyku.
    Tvrdé zlyhanie by znamenalo, že preklep v kóde jazyka zhodí štart.

    Prekladač si aplikácia musí držať (`application._pssim_translator`), inak
    ho Python odalokuje a preklady prestanú fungovať bez akejkoľvek chyby.
    """
    if language == SOURCE_LANGUAGE:
        return False

    if language not in LANGUAGES:
        logger.warning(
            "neznámy jazyk, ostávam pri zdrojovom",
            language=language,
            known=sorted(LANGUAGES),
        )
        return False

    path = translation_file(language)
    if not path.is_file():
        logger.warning("preklad nie je skompilovaný", language=language, file=str(path))
        return False

    from PySide6.QtCore import QTranslator

    translator = QTranslator(application)
    if not translator.load(str(path)):
        logger.warning("preklad sa nedá načítať", file=str(path))
        return False

    application.installTranslator(translator)
    # Referenciu drží aplikácia — bez nej by prekladač zmizol s garbage collectorom.
    application._pssim_translator = translator  # noqa: SLF001
    logger.info("preklad nainštalovaný", language=language)
    return True
