# UI translations

The source language is **English** — the strings are written that way directly in the code.
This directory holds the translations into the other languages.

## The files

| Extension | What it is | Version it? |
|---|---|---|
| `.ts` | XML with the strings and translations, this is what gets edited | **yes** |
| `.qm` | the compiled binary, read by the application | **yes** — otherwise everyone would have to install the Qt tools |

## Adding a language

Extracting the strings from the sources into a `.ts`:

```bash
uv run pyside6-lupdate src/pssim/ui/main_window.py src/pssim/ui/placement_dialog.py src/pssim/ui/loader.py -ts src/pssim/ui/translations/pssim_sk.ts
```

Translating: open the `.ts` in Qt Linguist (`uv run pyside6-linguist`), or edit it by hand —
it is readable XML.

Compiling to `.qm`:

```bash
uv run pyside6-lrelease src/pssim/ui/translations/pssim_sk.ts
```

Finally add the language code to `LANGUAGES` in `src/pssim/ui/i18n.py`. Without that the
application will not offer it, even if the `.qm` exists.

## After changing strings in the code

Run `lupdate` again — it adds the new strings and marks the changed ones as
`type="unfinished"`. Existing translations are not thrown away.

## What not to forget

- **Always** wrap new user-facing text in `self.tr()` (inside a `QObject`) or
  `QCoreApplication.translate("Context", "text")` (at module level). Unwrapped text never
  reaches the `.ts` and will never be translated.
- Do not assemble sentences from pieces (`tr("Loaded") + " " + name`) — another language may
  need a different order. Use placeholders: `tr("Loaded {0}").format(name)`.
- Logs are **not translated**. They are for the developer, not for the user.
