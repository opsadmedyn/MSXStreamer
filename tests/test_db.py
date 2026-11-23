"""La base de una versión anterior se pone al día al abrirla, también si dos procesos la abren a la vez."""

import sqlite3

import db

ANTES_DE_LA_0_4 = """
CREATE TABLE estado_salidas (
    salida TEXT PRIMARY KEY, pid INTEGER, desde REAL, reinicios INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '', kbps REAL, firma TEXT NOT NULL DEFAULT '', visto REAL
);
INSERT INTO estado_salidas(salida, pid, reinicios, firma) VALUES ('youtube-1a2b', 4321, 3, 'abc');
"""


def _base_anterior():
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(db.ESQUEMA.replace("CREATE TABLE IF NOT EXISTS estado_salidas", "CREATE TABLE IF NOT EXISTS sin_usar"))
    c.executescript(ANTES_DE_LA_0_4)
    return c


def test_la_base_de_la_0_3_9_gana_la_columna_nueva_sin_perder_el_estado_de_las_salidas():
    c = _base_anterior()
    db.migrar(c)
    fila = dict(c.execute("SELECT * FROM estado_salidas").fetchone())
    assert fila["intentos"] == 0
    assert (fila["salida"], fila["pid"], fila["reinicios"], fila["firma"]) == ("youtube-1a2b", 4321, 3, "abc")
    db.migrar(c)                                # otra vez no hace nada
    assert c.execute("SELECT COUNT(*) FROM estado_salidas").fetchone()[0] == 1


class _UnInstanteAntes:
    """Una conexión que ve estado_salidas como estaba un instante antes: tras un despliegue el panel
    y el supervisor arrancan a la vez, y el otro ya añadió la columna entre mirar y añadir."""

    def __init__(self, c):
        self.c = c

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info(estado_salidas)"):
            return [r for r in self.c.execute(sql) if r["name"] != "intentos"]
        return self.c.execute(sql, *args)


def test_si_el_panel_y_el_supervisor_ponen_al_dia_la_base_a_la_vez_ninguno_falla():
    c = _base_anterior()
    db.migrar(c)                                # el primero en llegar
    db.migrar(_UnInstanteAntes(c))              # el segundo, que miró antes de que el primero terminara
    assert [r["name"] for r in c.execute("PRAGMA table_info(estado_salidas)")].count("intentos") == 1


def test_un_fallo_que_no_es_ese_no_se_calla():
    c = _base_anterior()
    c.execute("DROP TABLE estado_salidas")
    try:
        db._columna(c, "estado_salidas", "intentos", "INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError as err:
        assert "no such table" in str(err)
    else:
        raise AssertionError("añadir una columna a una tabla que no existe debe fallar")
