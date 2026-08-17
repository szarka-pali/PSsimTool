# models/

Vstupné CAD súbory (STEP). **Nie sú vo verzovaní** — sú veľké a často obsahujú
duševné vlastníctvo zákazníka. Patria do artefaktového úložiska, nie do gitu.

Cesty na ne sa uvádzajú v `machines/*.yaml` v poli `step_file`.

Po nakopírovaní súboru spusti import do cache:

```bash
uv run pssim import-step models/priklad.step --machine machines/priklad.yaml
```
