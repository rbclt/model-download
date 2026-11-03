"""NFR6.4, NFR6.6: every business rule ID in rules.md is named by at least one test.

The rules of u1-import-slice (BR1.1 to BR5.6) and of u2-import-rules (BR6.1 to BR11.3) are read
from the record folder of their units. A test names a rule when its file holds the ID in a name or
docstring, written as ``BR3.2`` or, in a function name, as ``br3_2``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSTRUCTION = ROOT / "aidlc/spaces/default/intents/261030-model-import-scan/construction"
RULES = CONSTRUCTION / "u1-import-slice/functional-design/rules.md"
U2_RULES = CONSTRUCTION / "u2-import-rules/functional-design/rules.md"
RULE_ID = re.compile(r"^\s*-\s*id:\s*(BR\d+\.\d+)\s*$", re.MULTILINE)


def rule_ids(path: Path = RULES) -> list[str]:
    assert path.is_file(), f"rules.md not found at {path}"
    return RULE_ID.findall(path.read_text(encoding="utf-8"))


def named_in_tests(rule_id: str) -> bool:
    number = rule_id.removeprefix("BR")
    pattern = re.compile(
        rf"(?<![A-Za-z0-9])BR{re.escape(number)}(?!\d)"
        rf"|(?<![A-Za-z0-9])br{number.replace('.', '_')}(?!\d)"
    )
    for path in (ROOT / "tests").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            return True
    return False


def test_nfr6_4_the_rule_list_is_read_and_holds_all_of_br1_1_to_br5_6() -> None:
    ids = rule_ids()

    assert len(ids) == len(set(ids)) == 25
    assert ids[0] == "BR1.1" and ids[-1] == "BR5.6"


def test_nfr6_4_every_rule_id_is_named_by_some_test() -> None:
    unnamed = [rule_id for rule_id in rule_ids() if not named_in_tests(rule_id)]

    assert unnamed == [], f"no test names these rules: {unnamed}"


U2_EXPECTED = (
    [f"BR6.{n}" for n in range(1, 5)]
    + [f"BR7.{n}" for n in range(1, 3)]
    + [f"BR8.{n}" for n in range(1, 5)]
    + [f"BR9.{n}" for n in range(1, 7)]
    + [f"BR10.{n}" for n in range(1, 6)]
    + [f"BR11.{n}" for n in range(1, 4)]
)


def test_nfr6_6_the_rule_list_of_u2_is_read_and_holds_all_of_br6_1_to_br11_3() -> None:
    ids = rule_ids(U2_RULES)

    assert ids == U2_EXPECTED and len(ids) == len(set(ids)) == 24


def test_nfr6_6_every_rule_id_of_u2_is_named_by_some_test() -> None:
    unnamed = [rule_id for rule_id in rule_ids(U2_RULES) if not named_in_tests(rule_id)]

    assert unnamed == [], f"no test names these rules: {unnamed}"
