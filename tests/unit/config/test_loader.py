"""Testy načítania definície stroja.

Prevody jednotiek majú vlastnú triedu s konkrétnymi číslami — je to
najpravdepodobnejší tichý bug v projekte.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pssim.config.loader import load_machine
from pssim.domain.errors import ConfigError
from pssim.domain.machine import JointType


def write_machine(tmp_path: Path, body: str) -> Path:
    machines_dir = tmp_path / "machines"
    machines_dir.mkdir(exist_ok=True)
    path = machines_dir / "test.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
machine: test
step_file: models/test.step
units: mm
joints:
  - name: os_x
    parent: base
    child: portal
    type: prismatic
    axis: [1, 0, 0]
    limits: [0.0, 2.5]
    signal:
      node: "ns=2;s=X"
      scale: 0.001
"""


class TestNacitanie:
    def test_nacita_platnu_definiciu(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.machine.name == "test"

    def test_nacita_klby(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.machine.joint("os_x").type is JointType.PRISMATIC

    def test_nacita_vazby(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.node_ids == ("ns=2;s=X",)

    def test_step_file_je_absolutna_cesta(self, machine_yaml: Path) -> None:
        loaded = load_machine(machine_yaml)

        assert loaded.step_file.is_absolute()
        assert loaded.step_file.name == "test.step"


class TestJednotky:
    def test_mm_da_scale_0_001(self, machine_yaml: Path) -> None:
        assert load_machine(machine_yaml).scale_to_m == pytest.approx(1e-3)

    def test_metre_daju_scale_1(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("units: mm", "units: m"))

        assert load_machine(path).scale_to_m == pytest.approx(1.0)

    def test_binding_prevedie_mm_na_metre(self, machine_yaml: Path) -> None:
        binding = load_machine(machine_yaml).bindings[0]

        assert binding.to_internal(1500.0) == pytest.approx(1.5)

    def test_binding_aplikuje_offset_az_po_scale(self, tmp_path: Path) -> None:
        # Poradie je zafixované: raw * scale + offset. Zmena by ticho rozbila YAML.
        body = BASE.replace("scale: 0.001", "scale: 0.001\n      offset: 1.25")
        path = write_machine(tmp_path, body)

        binding = load_machine(path).bindings[0]

        assert binding.to_internal(1000.0) == pytest.approx(2.25)

    def test_neznama_jednotka_je_chyba(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("units: mm", "units: stopa"))

        with pytest.raises(ConfigError):
            load_machine(path)


class TestNormalizaciaOsi:
    def test_neznormalizovana_os_sa_znormalizuje(self, tmp_path: Path) -> None:
        # V YAML je [0,0,1] aj [0,0,2] ten istý zámer — doména neznormalizovanú
        # os odmieta, tak sa normalizuje tu, na hranici.
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [0, 0, 5]"))

        axis = load_machine(path).machine.joint("os_x").axis

        assert axis == pytest.approx((0.0, 0.0, 1.0))

    def test_sikma_os_sa_znormalizuje(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [3, 4, 0]"))

        axis = load_machine(path).machine.joint("os_x").axis

        assert axis == pytest.approx((0.6, 0.8, 0.0))

    def test_nulova_os_je_chyba(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("axis: [1, 0, 0]", "axis: [0, 0, 0]"))

        with pytest.raises(ConfigError):
            load_machine(path)


class TestValidacia:
    def test_neznamy_kluc_je_chyba(self, tmp_path: Path) -> None:
        # Preklep sa inak prejaví ako „nefunguje to a neviem prečo".
        path = write_machine(tmp_path, BASE + "\nunknown_key: 1\n")

        with pytest.raises(ConfigError, match="unknown_key"):
            load_machine(path)

    def test_pohyblivy_klb_bez_signalu_je_chyba(self, tmp_path: Path) -> None:
        body = BASE.split("    signal:")[0]
        path = write_machine(tmp_path, body)

        with pytest.raises(ConfigError, match="without a signal: os_x"):
            load_machine(path)

    def test_fixed_klb_signal_nepotrebuje(self, tmp_path: Path) -> None:
        body = (
            BASE
            + """
  - name: kryt
    parent: base
    child: kryt
    type: fixed
"""
        )
        path = write_machine(tmp_path, body)

        assert len(load_machine(path).machine.joints) == 2

    def test_nulovy_scale_je_chyba(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("scale: 0.001", "scale: 0.0"))

        with pytest.raises(ConfigError, match="scale"):
            load_machine(path)

    def test_neplatny_yaml_je_chyba(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, "machine: [nedokoncene\n")

        with pytest.raises(ConfigError, match="invalid YAML"):
            load_machine(path)

    def test_chybajuci_subor_je_chyba(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="cannot be read"):
            load_machine(tmp_path / "nic.yaml")

    def test_chybova_sprava_obsahuje_cestu_k_suboru(self, tmp_path: Path) -> None:
        path = write_machine(tmp_path, BASE.replace("type: prismatic", "type: nezmysel"))

        with pytest.raises(ConfigError, match="test.yaml"):
            load_machine(path)


class TestRenderDelay:
    def test_bez_zadania_je_dvojnasobok_intervalu(self, machine_yaml: Path) -> None:
        source = load_machine(machine_yaml).source

        assert source.effective_render_delay_s() == pytest.approx(0.1)

    def test_revidovany_interval_ma_prednost_pred_ziadanym(self, machine_yaml: Path) -> None:
        source = load_machine(machine_yaml).source

        assert source.effective_render_delay_s(revised_interval_ms=200) == pytest.approx(0.4)

    def test_explicitne_zadany_delay_vyhrava(self, tmp_path: Path) -> None:
        body = BASE.replace("joints:", "source:\n  render_delay_ms: 30\n\njoints:")
        path = write_machine(tmp_path, body)

        source = load_machine(path).source

        assert source.effective_render_delay_s(revised_interval_ms=500) == pytest.approx(0.03)


class TestPrikladVRepozitari:
    def test_machines_priklad_yaml_je_platny(self) -> None:
        # Referenčný súbor v repozitári musí zostať načítateľný — je to zároveň
        # regresný test na nekompatibilné zmeny schémy.
        path = Path(__file__).resolve().parents[3] / "machines" / "priklad.yaml"

        loaded = load_machine(path)

        assert len(loaded.machine.moving_joints) == 3

    def test_priklad_os_c_prevedie_tisiciny_stupna_na_radiany(self) -> None:
        path = Path(__file__).resolve().parents[3] / "machines" / "priklad.yaml"
        loaded = load_machine(path)

        binding = loaded.bindings_by_joint["os_c"]

        # 90 000 tisícin stupňa = 90° = pi/2
        assert binding.to_internal(90_000.0) == pytest.approx(math.pi / 2, rel=1e-9)
