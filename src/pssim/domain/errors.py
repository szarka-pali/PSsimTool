"""Typované doménové chyby.

Každá chyba, ktorú aplikácia vyhodí zámerne, dedí z `PSsimError`. Vďaka tomu sa
dá na hranici (CLI, render loop) rozlíšiť „očakávaná chyba s použiteľnou správou"
od „bug, ktorý treba nahlásiť".
"""


class PSsimError(Exception):
    """Základ pre všetky zámerné chyby aplikácie."""


class ConfigError(PSsimError):
    """Neplatná definícia stroja alebo konfigurácia.

    Vyhadzuje sa pri načítaní, nikdy nie za behu. Správa musí povedať,
    ktorý súbor a ktoré pole je zlé.
    """


class KinematicsError(PSsimError):
    """Neplatná kinematická operácia alebo neplatný kinematický reťazec."""


class CadImportError(PSsimError):
    """Import CAD súboru zlyhal alebo je súbor nepoužiteľný."""


class CacheError(PSsimError):
    """Cache je poškodená, nekompletná alebo z nekompatibilnej verzie importéra."""


class DataSourceError(PSsimError):
    """Zdroj dát sa nedá otvoriť alebo je jeho konfigurácia neplatná.

    Pozor: **odpadnutie spojenia počas behu nie je chyba** — je to normálny stav,
    ktorý zdroj rieši reconnectom. Táto chyba je pre prípady, keď sa nedá ani začať.
    """
