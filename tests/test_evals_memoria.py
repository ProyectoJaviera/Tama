import sqlite3

from evals import memoria


def test_script_has_ten_facts_and_natural_filler():
    script = memoria.day_one_script()
    assert len(memoria.FACTS) == 10
    assert all(f.told in script for f in memoria.FACTS)
    assert len(script) > 10
    assert len(memoria.NEGATIVE) >= 3


def test_recalled_ignores_case_and_accents():
    fact = memoria.Fact("x", "y", "portugu")
    assert memoria.recalled(fact, "Estás aprendiendo PORTUGUÉS.")
    assert not memoria.recalled(fact, "Estás aprendiendo francés.")


def test_does_not_know_detection():
    assert memoria.said_it_does_not_know("No lo tengo anotado.")
    assert not memoria.said_it_does_not_know("Tu dentista es el Dr. Pérez.")


def test_entity_quality_finds_duplicates_and_bad_names(tmp_path):
    path = tmp_path / "memoria.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE entities (norm TEXT)")
    names = ["lucia", "lucia perez", "bruno", "mi perro bruno que tiene cuatro anos"]
    db.executemany("INSERT INTO entities VALUES (?)", [(n,) for n in names])
    db.commit()
    db.close()
    quality = memoria.entity_quality(path)
    assert quality["entidades"] == 4
    assert "lucia / lucia perez" in quality["duplicadas"]
    assert quality["mal_nombradas"] == ["mi perro bruno que tiene cuatro anos"]


def test_entity_quality_without_db(tmp_path):
    assert memoria.entity_quality(tmp_path / "nada.db")["entidades"] == 0
