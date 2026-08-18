"""Integračné testy importu STEP cez OpenCASCADE.

Vyžadujú `uv sync --extra cad`. Spustenie: ``uv run pytest -m cad``

Fixture `tests/data/fixture.step` je vygenerovaný `tools/make_step_fixture.py`
a je vo verzovaní — je malý (50 kB) a testy ho potrebujú. Reálne CAD súbory
v repozitári nie sú.

Zostava vo fixture:

    base                     assembly, koreň
      portal                 assembly, +100 mm v X
        Part1                dvaja rovnomenní siblingovia
        Part1
        hlava                otočený o 90° okolo Z
      kryt                   jediný diel s farbou (0.2, 0.4, 0.8)
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pssim.cad.cache import CacheEntry
from pssim.cad.mesh import read_mesh
from pssim.cad.model import DEFAULT_COLOR, CadAssembly
from pssim.cad.step_import import (
    ImportSettings,
    build_paths,
    cache_key_for,
    import_step,
    read_step_assembly,
)
from pssim.domain.errors import CadImportError

pytestmark = pytest.mark.cad

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixture.step"

#: Hodnoty z tools/make_step_fixture.py, prevedené na metre.
PORTAL_OFFSET_M = 0.1
PART_SPACING_M = 0.04
COVER_OFFSET_M = -0.04
COVER_COLOR = (0.2, 0.4, 0.8, 1.0)
BOX_TRIANGLES = 12
"""Kváder = 6 stien × 2 trojuholníky."""


def settings(scale_to_m: float = 1e-3, units: str = "mm") -> ImportSettings:
    return ImportSettings(step_file=FIXTURE, scale_to_m=scale_to_m, units=units)


@pytest.fixture(scope="module")
def assembly() -> CadAssembly:
    """Naimportovaný fixture. Modulový scope — import trvá sekundy."""
    return build_paths(read_step_assembly(settings()))


class TestAssemblyTree:
    def test_fixture_existuje(self) -> None:
        # Bez neho nemá zmysel púšťať zvyšok — vygeneruj ho make_step_fixture.py.
        assert FIXTURE.is_file(), f"chýba {FIXTURE}; spusti tools/make_step_fixture.py"

    def test_ma_jediny_koren(self, assembly: CadAssembly) -> None:
        assert assembly.roots == ("base",)

    def test_zachova_hierarchiu(self, assembly: CadAssembly) -> None:
        assert {node.path for node in assembly.nodes} == {
            "base",
            "base/portal",
            "base/portal/Part1[1]",
            "base/portal/Part1[2]",
            "base/portal/hlava",
            "base/kryt",
        }

    def test_nazvy_dielov_prezili_roundtrip(self, assembly: CadAssembly) -> None:
        # STEP názvy sedia na definícii, nie na inštancii — ak by sa to čítalo
        # zle, dostali by sme samé Unnamed_*.
        assert not any(node.name.startswith("Unnamed") for node in assembly.nodes)

    def test_deti_su_zaznamenane_v_rodicovi(self, assembly: CadAssembly) -> None:
        portal = assembly.node("base/portal")

        assert portal is not None
        assert set(portal.children) == {
            "base/portal/Part1[1]",
            "base/portal/Part1[2]",
            "base/portal/hlava",
        }


class TestDuplicitneNazvy:
    def test_rovnomenni_siblingovia_dostanu_index(self, assembly: CadAssembly) -> None:
        # Ten istý diel použitý dvakrát = jedna definícia, dve inštancie.
        paths = {node.path for node in assembly.nodes}

        assert {"base/portal/Part1[1]", "base/portal/Part1[2]"} <= paths

    def test_unikatny_nazov_index_nedostane(self, assembly: CadAssembly) -> None:
        assert assembly.node("base/portal/hlava") is not None

    def test_instancie_maju_rozne_polohy(self, assembly: CadAssembly) -> None:
        first = assembly.node("base/portal/Part1[1]")
        second = assembly.node("base/portal/Part1[2]")

        assert first is not None
        assert second is not None
        assert second.transform.xyz[1] - first.transform.xyz[1] == pytest.approx(
            PART_SPACING_M, abs=1e-9
        )


class TestJednotky:
    def test_translacia_sa_prevedie_z_mm_na_metre(self, assembly: CadAssembly) -> None:
        # Vo fixture je portál posunutý o 100 mm.
        portal = assembly.node("base/portal")

        assert portal is not None
        assert portal.transform.xyz[0] == pytest.approx(PORTAL_OFFSET_M, abs=1e-9)

    def test_zaporny_posun_zachova_znamienko(self, assembly: CadAssembly) -> None:
        cover = assembly.node("base/kryt")

        assert cover is not None
        assert cover.transform.xyz[1] == pytest.approx(COVER_OFFSET_M, abs=1e-9)

    def test_bez_skalovania_zostanu_milimetre(self) -> None:
        # Kontrolná vzorka: keby škálovanie nefungovalo, predchádzajúce testy
        # by prešli aj tak. Tento ukáže, že sa naozaj aplikuje.
        raw = build_paths(read_step_assembly(settings(scale_to_m=1.0, units="m")))
        portal = raw.node("base/portal")

        assert portal is not None
        assert portal.transform.xyz[0] == pytest.approx(100.0, abs=1e-6)


class TestRotacia:
    def test_otoceny_diel_ma_rotaciu_okolo_z(self, assembly: CadAssembly) -> None:
        head = assembly.node("base/portal/hlava")

        assert head is not None
        assert head.transform.rpy[2] == pytest.approx(math.pi / 2, abs=1e-9)

    def test_neotoceny_diel_ma_nulovu_rotaciu(self, assembly: CadAssembly) -> None:
        portal = assembly.node("base/portal")

        assert portal is not None
        assert portal.transform.rpy == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)

    def test_rotacia_sa_neskaluje_jednotkami(self) -> None:
        # Uhly sú bezrozmerné — škálovanie dĺžky sa ich nesmie dotknúť.
        raw = build_paths(read_step_assembly(settings(scale_to_m=1.0, units="m")))
        head = raw.node("base/portal/hlava")

        assert head is not None
        assert head.transform.rpy[2] == pytest.approx(math.pi / 2, abs=1e-9)


class TestFarby:
    def test_diel_s_farbou_ju_ma(self, assembly: CadAssembly) -> None:
        cover = assembly.node("base/kryt")

        assert cover is not None
        assert cover.color == pytest.approx(COVER_COLOR, abs=1e-6)

    def test_diel_bez_farby_dostane_default(self, assembly: CadAssembly) -> None:
        # Diel bez farby je v reálnych súboroch úplne bežný.
        head = assembly.node("base/portal/hlava")

        assert head is not None
        assert head.color == pytest.approx(DEFAULT_COLOR, abs=1e-6)


class TestGeometria:
    def test_diely_su_tesselovane(self, assembly: CadAssembly) -> None:
        head = assembly.node("base/portal/hlava")

        assert head is not None
        assert head.triangle_count == BOX_TRIANGLES

    def test_assembly_uzol_nema_vlastnu_geometriu(self, assembly: CadAssembly) -> None:
        portal = assembly.node("base/portal")

        assert portal is not None
        assert portal.has_geometry is False

    def test_celkovy_pocet_trojuholnikov(self, assembly: CadAssembly) -> None:
        # Štyri kvádre × 12 trojuholníkov.
        assert assembly.triangle_count == 4 * BOX_TRIANGLES

    def test_jemnejsia_deflection_nezhorsi_geometriu(self) -> None:
        fine = ImportSettings(
            step_file=FIXTURE, scale_to_m=1e-3, units="mm", linear_deflection_mm=0.01
        )
        result = build_paths(read_step_assembly(fine))

        assert result.triangle_count >= 4 * BOX_TRIANGLES


class TestZapisGeometrie:
    def test_import_zapise_mesh_subory(self, tmp_path: Path) -> None:
        import_step(settings(), tmp_path)

        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        meshes = list(entry.directory.glob("*.npz"))

        # Tri definície dielov: Part1, hlava, kryt. Nie štyri — dve inštancie
        # Part1 zdieľajú jeden súbor.
        assert len(meshes) == 3

    def test_instancie_toho_isteho_dielu_ukazuju_na_jeden_subor(self, tmp_path: Path) -> None:
        metadata = import_step(settings(), tmp_path)

        first = metadata.assembly.node("base/portal/Part1[1]")
        second = metadata.assembly.node("base/portal/Part1[2]")

        assert first is not None
        assert second is not None
        assert first.mesh == second.mesh

    def test_mesh_sa_da_nacitat_spat(self, tmp_path: Path) -> None:
        metadata = import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        cover = metadata.assembly.node("base/kryt")

        assert cover is not None
        assert cover.mesh is not None
        mesh = read_mesh(entry.mesh_path(cover.mesh))

        assert mesh.triangle_count == BOX_TRIANGLES

    def test_rozmery_meshu_su_v_metroch(self, tmp_path: Path) -> None:
        # Kryt je vo fixture 200 x 5 x 80 mm.
        metadata = import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        cover = metadata.assembly.node("base/kryt")

        assert cover is not None
        assert cover.mesh is not None
        low, high = read_mesh(entry.mesh_path(cover.mesh)).bounding_box()

        assert high[0] - low[0] == pytest.approx(0.2, abs=1e-6)
        assert high[1] - low[1] == pytest.approx(0.005, abs=1e-6)
        assert high[2] - low[2] == pytest.approx(0.08, abs=1e-6)

    def test_kvader_ma_ocakavany_pocet_vrcholov(self, tmp_path: Path) -> None:
        # 6 stien × 4 vrcholy, medzi stenami sa nezdieľajú — inak by sa
        # normály na hranách spriemerovali a kváder by vyzeral zaoblene.
        metadata = import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        head = metadata.assembly.node("base/portal/hlava")

        assert head is not None
        assert head.mesh is not None
        assert read_mesh(entry.mesh_path(head.mesh)).vertex_count == 24

    def test_normaly_su_jednotkove(self, tmp_path: Path) -> None:
        metadata = import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        head = metadata.assembly.node("base/portal/hlava")

        assert head is not None
        assert head.mesh is not None
        mesh = read_mesh(entry.mesh_path(head.mesh))

        lengths = np.linalg.norm(mesh.normals, axis=1)

        assert lengths == pytest.approx(np.ones(mesh.vertex_count), abs=1e-5)

    def test_normaly_kvadra_mieria_von(self, tmp_path: Path) -> None:
        # Ak by bol winding zle opravený, normály by mierili dovnútra
        # a diel by v scéne vyzeral „naruby".
        metadata = import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        head = metadata.assembly.node("base/portal/hlava")

        assert head is not None
        assert head.mesh is not None
        mesh = read_mesh(entry.mesh_path(head.mesh))

        low, high = mesh.bounding_box()
        center = np.array([(low[i] + high[i]) / 2 for i in range(3)])
        outward = mesh.vertices - center
        # Skalárny súčin normály so smerom von zo stredu musí byť kladný.
        alignment = np.einsum("ij,ij->i", outward, mesh.normals)

        assert (alignment > 0).all()

    def test_prazdny_mesh_sa_nezapisuje(self, tmp_path: Path) -> None:
        import_step(settings(), tmp_path)
        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))

        for mesh_file in entry.directory.glob("*.npz"):
            assert read_mesh(mesh_file).triangle_count > 0


class TestCache:
    def test_import_zapise_cache(self, tmp_path: Path) -> None:
        metadata = import_step(settings(), tmp_path)

        entry = CacheEntry(root=tmp_path, key=cache_key_for(settings()))
        assert entry.exists
        assert metadata.units_used == "mm"

    def test_opakovany_import_pouzije_cache(self, tmp_path: Path) -> None:
        first = import_step(settings(), tmp_path)
        second = import_step(settings(), tmp_path)

        assert first.to_dict() == second.to_dict()

    def test_cache_prezije_roundtrip_na_disk(self, tmp_path: Path) -> None:
        import_step(settings(), tmp_path)

        loaded = CacheEntry(root=tmp_path, key=cache_key_for(settings())).read()

        assert {node.path for node in loaded.assembly.nodes} == {
            "base",
            "base/portal",
            "base/portal/Part1[1]",
            "base/portal/Part1[2]",
            "base/portal/hlava",
            "base/kryt",
        }

    def test_ine_parametre_tesselacie_daju_inu_cache(self, tmp_path: Path) -> None:
        coarse = settings()
        fine = ImportSettings(
            step_file=FIXTURE, scale_to_m=1e-3, units="mm", linear_deflection_mm=0.01
        )

        assert cache_key_for(coarse).digest != cache_key_for(fine).digest

    def test_transformacie_prezijú_serializaciu(self, tmp_path: Path) -> None:
        import_step(settings(), tmp_path)

        loaded = CacheEntry(root=tmp_path, key=cache_key_for(settings())).read()
        head = loaded.assembly.node("base/portal/hlava")

        assert head is not None
        assert head.transform.rpy[2] == pytest.approx(math.pi / 2, abs=1e-9)


class TestChybneVstupy:
    def test_neexistujuci_subor_je_chyba(self, tmp_path: Path) -> None:
        missing = ImportSettings(step_file=tmp_path / "nic.step", scale_to_m=1e-3, units="mm")

        with pytest.raises(CadImportError, match="does not exist"):
            import_step(missing, tmp_path)

    def test_nezmyselny_obsah_je_chyba(self, tmp_path: Path) -> None:
        broken = tmp_path / "rozbity.step"
        broken.write_text("toto rozhodne nie je STEP", encoding="utf-8")
        broken_settings = ImportSettings(step_file=broken, scale_to_m=1e-3, units="mm")

        with pytest.raises(CadImportError):
            import_step(broken_settings, tmp_path)
