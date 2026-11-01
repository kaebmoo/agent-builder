"""Generating the same spec twice yields byte-identical packages (AGENTS.md: generator must be deterministic)."""
from pathlib import Path

from forge.generate import generate
from forge.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]


def test_generate_is_byte_identical(tmp_path: Path) -> None:
    spec = load_spec(ROOT / "fixtures" / "text-reviewer-standalone" / "spec.yaml")
    first, second = tmp_path / "a" / "pkg", tmp_path / "b" / "pkg"
    generate(spec, first)
    generate(spec, second)
    listing = [sorted(p.relative_to(d) for p in d.rglob("*") if p.is_file()) for d in (first, second)]
    assert listing[0]
    assert listing[0] == listing[1]
    for relative in listing[0]:
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), relative
