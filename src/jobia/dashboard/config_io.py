"""Lectura/escritura de config/searches.yaml y config/rules.yaml para el
dashboard. Usa ruamel.yaml (round-trip) en vez de pyyaml: pyyaml.safe_dump
borraria todos los comentarios que documentan por que cada valor es el que
es -- varios de esos comentarios vienen de decisiones tomadas a base de
datos reales, no son decorativos.

Solo expone los campos simples pactados para editar desde la UI (numeros,
strings, la lista de queries). El arbol completo de title_rules (R0-R6:
listas de kill/bonus/penalty) se queda editable solo por archivo por ahora
-- ver conversacion de diseño, es fast-follow deliberado.
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

SEARCHES_PATH = Path("config/searches.yaml")
RULES_PATH = Path("config/rules.yaml")

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # no reformatear/partir lineas largas al reescribir
# Mismo estilo de indentado que ya usan los .yaml del repo (listas con
# sangria propia, no pegadas al margen de la clave): sin esto, ruamel
# reescribe TODO el fichero con su indentado por defecto en el primer save.
_yaml.indent(mapping=2, sequence=4, offset=2)


def load_searches() -> dict:
    return _yaml.load(SEARCHES_PATH.read_text(encoding="utf-8"))


def save_searches(data) -> None:
    with SEARCHES_PATH.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def load_rules() -> dict:
    return _yaml.load(RULES_PATH.read_text(encoding="utf-8"))


def save_rules(data) -> None:
    with RULES_PATH.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)
