"""Testy skladania stabilných ciest uzlov.

Cesty musia byť medzi importmi deterministické — `machines/*.yaml` sa na ne
odkazuje. Toto je jediná časť `step_import.py`, ktorá je čistá a testovateľná
bez OpenCASCADE.
"""

from __future__ import annotations

import pytest

from pssim.cad.step_import import RawNode, build_paths, scale_transform
from pssim.domain.machine import Transform


def raw(
    name: str, *children: RawNode, geometry: bool = True, mesh_key: str | None = None
) -> RawNode:
    """Uzol pre testy. Listy majú geometriu, uzly s deťmi sú organizačné.

    `mesh_key` sa dá zadať explicitne, aby sa dalo otestovať zdieľanie meshu
    medzi inštanciami toho istého dielu.
    """
    is_leaf = geometry and not children
    return RawNode(
        name=name,
        transform=Transform(),
        children=children,
        mesh_key=(mesh_key or f"def:{name}") if is_leaf else None,
        triangle_count=12 if is_leaf else 0,
    )


class TestCesty:
    def test_jeden_koren(self) -> None:
        assembly = build_paths((raw("base"),))

        assert assembly.roots == ("base",)

    def test_vnorenie_sa_spoji_lomitkom(self) -> None:
        assembly = build_paths((raw("base", raw("portal", raw("vozik"))),))

        assert {node.path for node in assembly.nodes} == {
            "base",
            "base/portal",
            "base/portal/vozik",
        }

    def test_unikatny_nazov_nedostane_index(self) -> None:
        assembly = build_paths((raw("base", raw("portal")),))

        assert assembly.node("base/portal") is not None

    def test_rovnomenni_siblingovia_dostanu_index_od_jednotky(self) -> None:
        # Desať `Part1` v jednej zostave je v praxi bežné.
        assembly = build_paths((raw("base", raw("Part1"), raw("Part1"), raw("Part1")),))

        paths = {node.path for node in assembly.nodes}

        assert {"base/Part1[1]", "base/Part1[2]", "base/Part1[3]"} <= paths

    def test_indexovanie_je_lokalne_pre_uroven(self) -> None:
        assembly = build_paths(
            (raw("base", raw("A", raw("X"), raw("X")), raw("B", raw("X"), raw("X"))),)
        )

        paths = {node.path for node in assembly.nodes}

        assert {"base/A/X[1]", "base/A/X[2]", "base/B/X[1]", "base/B/X[2]"} <= paths

    def test_poradie_je_deterministicke(self) -> None:
        tree = (raw("base", raw("P"), raw("P")),)

        first = [node.path for node in build_paths(tree).nodes]
        second = [node.path for node in build_paths(tree).nodes]

        assert first == second

    def test_deti_su_zaznamenane_v_rodicovi(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))
        base = assembly.node("base")

        assert base is not None
        assert base.children == ("base/a", "base/b")


class TestPoradieUzlov:
    def test_nodes_je_zdola_nahor(self) -> None:
        # Rekurzívny prechod zapisuje uzol až po jeho deťoch. Kto stavia
        # hierarchiu, musí použiť nodes_parents_first — inak vyjde plochý strom.
        assembly = build_paths((raw("base", raw("diel")),))

        paths = [node.path for node in assembly.nodes]

        assert paths.index("base/diel") < paths.index("base")

    def test_nodes_parents_first_ma_rodica_pred_potomkom(self) -> None:
        assembly = build_paths((raw("base", raw("portal", raw("vozik"))),))

        paths = [node.path for node in assembly.nodes_parents_first]

        assert paths.index("base") < paths.index("base/portal") < paths.index("base/portal/vozik")

    def test_nodes_parents_first_obsahuje_vsetky_uzly(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b", raw("c"))),))

        assert len(assembly.nodes_parents_first) == len(assembly.nodes)

    def test_nodes_parents_first_je_deterministicke(self) -> None:
        tree = (raw("base", raw("P"), raw("P"), raw("Q")),)

        first = [node.path for node in build_paths(tree).nodes_parents_first]
        second = [node.path for node in build_paths(tree).nodes_parents_first]

        assert first == second


class TestZdielanieMeshu:
    def test_instancie_toho_isteho_dielu_zdielaju_subor(self) -> None:
        # Zostava s tisíckou skrutiek má mať v cache jednu skrutku, nie tisíc kópií.
        assembly = build_paths(
            (raw("base", raw("Bolt", mesh_key="def:bolt"), raw("Bolt", mesh_key="def:bolt")),)
        )

        first = assembly.node("base/Bolt[1]")
        second = assembly.node("base/Bolt[2]")

        assert first is not None
        assert second is not None
        assert first.mesh == second.mesh

    def test_rozne_diely_maju_rozne_subory(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))

        first = assembly.node("base/a")
        second = assembly.node("base/b")

        assert first is not None
        assert second is not None
        assert first.mesh != second.mesh

    def test_rovnomenne_ale_rozne_diely_sa_nezlejú(self) -> None:
        # Dva rôzne diely môžu mať v STEP rovnaké meno — rozlišuje ich mesh_key.
        assembly = build_paths(
            (raw("base", raw("Part", mesh_key="def:1"), raw("Part", mesh_key="def:2")),)
        )

        first = assembly.node("base/Part[1]")
        second = assembly.node("base/Part[2]")

        assert first is not None
        assert second is not None
        assert first.mesh != second.mesh


class TestGeometria:
    def test_uzol_s_geometriou_ma_mesh(self) -> None:
        assembly = build_paths((raw("diel"),))
        node = assembly.node("diel")

        assert node is not None
        assert node.has_geometry is True

    def test_organizacny_uzol_nema_mesh(self) -> None:
        assembly = build_paths((raw("base", raw("diel")),))
        base = assembly.node("base")

        assert base is not None
        assert base.mesh is None

    def test_pocet_trojuholnikov_sa_scitava(self) -> None:
        assembly = build_paths((raw("base", raw("a"), raw("b")),))

        assert assembly.triangle_count == 24


class TestSkalovanie:
    def test_translacia_sa_skaluje(self) -> None:
        # Ak sa vrcholy zmenšia 1000x a offsety nie, model sa rozsype na kusy.
        scaled = scale_transform(Transform(xyz=(1000.0, 2000.0, 0.0)), 1e-3)

        assert scaled.xyz == pytest.approx((1.0, 2.0, 0.0))

    def test_rotacia_sa_neskaluje(self) -> None:
        scaled = scale_transform(Transform(rpy=(0.5, 0.0, 0.0)), 1e-3)

        assert scaled.rpy == pytest.approx((0.5, 0.0, 0.0))


class TestPodobneCesty:
    def test_najde_podobnu_cestu_pre_chybovu_spravu(self) -> None:
        # Bez tohto je „uzol sa nenašiel" nepoužiteľná chyba pri tisícke uzlov.
        assembly = build_paths((raw("base", raw("portal"), raw("kryt")),))

        assert "base/portal" in assembly.similar_paths("portal")

    def test_neznama_cesta_vrati_prazdno_alebo_kandidatov(self) -> None:
        assembly = build_paths((raw("base"),))

        assert isinstance(assembly.similar_paths("zzz_neexistuje"), tuple)
