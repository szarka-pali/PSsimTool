"""Cache tesselovanej geometrie.

Tesselácia veľkého assembly trvá minúty, takže sa robí raz (`pssim import-step`)
a výsledok sa cachuje. Viď docs/architecture.md R2.

Cache je **plne zahoditeľná**: nič, čo sa nedá znovu vyrobiť z `models/`
a `machines/`, do nej nepatrí.

Modul je čistý (žiadne OCP, žiadne Panda3D) a plne testovateľný.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pssim.cad.model import CadAssembly, CadNode
from pssim.domain.errors import CacheError

IMPORTER_VERSION: Final = 2
"""Zvýš pri každej zmene importéra, ktorá mení výstup.

Bez toho sa bude ticho používať stará cache a nikto nepochopí, prečo sa zmena
neprejavila. Verzia je súčasťou cache kľúča.

História:
  1 — prvá verzia, len assembly tree bez geometrie
  2 — zápis geometrie (.npz), mesh zdieľaný medzi inštanciami toho istého dielu
"""

_METADATA_FILENAME: Final = "meta.json"
_HASH_CHUNK_BYTES: Final = 1 << 20


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Identita jedného importu.

    Kľúč zahŕňa obsah súboru, parametre tesselácie a verziu importéra. Zmena
    ktoréhokoľvek z nich musí viesť na iný adresár cache.
    """

    source_sha256: str
    scale_to_m: float
    linear_deflection_mm: float
    angular_deflection_rad: float
    importer_version: int = IMPORTER_VERSION

    @property
    def digest(self) -> str:
        """Krátky hash použitý ako názov adresára."""
        payload = json.dumps(
            {
                "source": self.source_sha256,
                "scale": self.scale_to_m,
                "linear": self.linear_deflection_mm,
                "angular": self.angular_deflection_rad,
                "version": self.importer_version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class CacheMetadata:
    """Obsah `meta.json`. Bez neho je cache neinterpretovateľná."""

    key: CacheKey
    source_file: str
    units_used: str
    assembly: CadAssembly

    def to_dict(self) -> dict[str, Any]:
        return {
            "importer_version": self.key.importer_version,
            "source_file": self.source_file,
            "source_sha256": self.key.source_sha256,
            "units_used": self.units_used,
            "scale_to_m": self.key.scale_to_m,
            "tessellation": {
                "linear_deflection_mm": self.key.linear_deflection_mm,
                "angular_deflection_rad": self.key.angular_deflection_rad,
            },
            "roots": list(self.assembly.roots),
            "nodes": [node.to_dict() for node in self.assembly.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheMetadata:
        try:
            tessellation = data["tessellation"]
            key = CacheKey(
                source_sha256=str(data["source_sha256"]),
                scale_to_m=float(data["scale_to_m"]),
                linear_deflection_mm=float(tessellation["linear_deflection_mm"]),
                angular_deflection_rad=float(tessellation["angular_deflection_rad"]),
                importer_version=int(data["importer_version"]),
            )
            return cls(
                key=key,
                source_file=str(data["source_file"]),
                units_used=str(data["units_used"]),
                assembly=CadAssembly(
                    nodes=tuple(CadNode.from_dict(node) for node in data["nodes"]),
                    roots=tuple(str(root) for root in data.get("roots", ())),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CacheError(f"meta.json je poškodený alebo neúplný: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Adresár cache pre jeden konkrétny import."""

    root: Path
    key: CacheKey

    @property
    def directory(self) -> Path:
        return self.root / self.key.digest

    @property
    def metadata_path(self) -> Path:
        return self.directory / _METADATA_FILENAME

    @property
    def exists(self) -> bool:
        return self.metadata_path.is_file()

    def mesh_path(self, mesh: str) -> Path:
        return self.directory / mesh

    def read(self) -> CacheMetadata:
        """Načíta metadáta. Overí, že cache patrí k tomuto kľúču."""
        if not self.exists:
            raise CacheError(
                f"cache pre tento import neexistuje: {self.directory}. "
                f"Spusti `uv run pssim import-step <subor.step>`."
            )
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CacheError(f"{self.metadata_path}: nedá sa prečítať: {exc}") from exc

        metadata = CacheMetadata.from_dict(raw)
        if metadata.key.importer_version != self.key.importer_version:
            raise CacheError(
                f"{self.metadata_path}: cache je z importéra verzie "
                f"{metadata.key.importer_version}, aktuálna je {self.key.importer_version}. "
                f"Zmaž adresár cache a importuj znovu."
            )
        return metadata

    def write(self, metadata: CacheMetadata) -> None:
        """Zapíše metadáta atomicky.

        Atomicky preto, že prerušený zápis by zanechal cache, ktorá „existuje",
        ale nedá sa prečítať — a to je horšie než žiadna cache.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.metadata_path)
        except OSError as exc:
            raise CacheError(f"{self.metadata_path}: nedá sa zapísať: {exc}") from exc


def file_sha256(path: str | Path) -> str:
    """Hash obsahu súboru. Číta po blokoch — STEP súbory majú stovky MB."""
    file_path = Path(path)
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise CacheError(f"{file_path}: nedá sa prečítať: {exc}") from exc
    return digest.hexdigest()


def mesh_filename(mesh_key: str) -> str:
    """Bezpečný názov súboru pre geometriu jednej **definície** dielu.

    `mesh_key` je XCAF entry (`0:1:1:3`), nie cesta uzla — inštancie toho istého
    dielu tak ukazujú na jeden zdieľaný súbor. Zostava s tisíckou skrutiek má
    v cache jednu skrutku, nie tisíc kópií.

    Kľúč môže obsahovať znaky, ktoré sa v názvoch súborov nepoužívajú (`:`),
    preto sa sanitizuje. Krátky hash na konci zaručuje, že dva rôzne kľúče
    neskončia po sanitizácii v tom istom súbore.
    """
    slug = "".join(char if char.isalnum() or char in "-_" else "_" for char in mesh_key)
    suffix = hashlib.sha256(mesh_key.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:60]}_{suffix}.npz"
