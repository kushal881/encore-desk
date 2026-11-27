"""Tables made by an earlier version of the desk are brought up to date on start-up: missing columns are added, nothing
is dropped or changed, existing rows keep their values and get each new column's default.
Runs on Postgres when TEST_POSTGRES_URL is set (as on Railway), otherwise on SQLite."""
import os, sys, tempfile
os.environ["DATABASE_URL"] = os.environ.get("TEST_POSTGRES_URL") or "sqlite:///" + os.path.join(tempfile.mkdtemp(), "t.sqlite3")
os.environ["FILES_DIR"] = tempfile.mkdtemp()
os.environ["DESK_DEV_LOGIN"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from sqlalchemy import text, inspect
from fastapi.testclient import TestClient
from desk import db

A = "/orders/Q-2026-10-444"
# columns an earlier version did not have: text, number, JSON, a required date, an optional date, a float with a default
OLD = [("no_drawing_line", "description"), ("no_drawing_line", "no"), ("no_drawing_line", "by"), ("ai_turn", "values"),
       ("drawing", "made_at"), ("question", "answered_at"), ("order_line", "qty")]


def _drop(eng, table, col):
    q = eng.dialect.identifier_preparer.quote
    with eng.begin() as con:
        con.execute(text("ALTER TABLE %s DROP COLUMN %s" % (q(table), q(col))))


def test_old_tables_are_brought_up_to_date():
    eng = db.engine
    db.Base.metadata.drop_all(eng)
    db.init()
    from desk.app import app
    c = TestClient(app)
    c.post("/login/dev/2")
    assert c.get(A).status_code == 200                                   # the order and its lines are in the database
    with db.Session() as s:
        n_lines, n_aside = s.query(db.OrderLine).count(), s.query(db.NoDrawingLine).count()
        s.add(db.Drawing(line_id=s.query(db.OrderLine).first().id)); s.commit()
    assert n_lines > 0

    for t, col in OLD:                                                   # the tables as an earlier version made them
        _drop(eng, t, col)
    assert "description" not in {x["name"] for x in inspect(eng).get_columns("no_drawing_line")}
    with pytest.raises(Exception):                                       # what staging showed: the project page fails
        c.get(A)

    added = db.upgrade(eng)
    assert sorted(added) == sorted("%s.%s" % x for x in OLD)
    assert db.upgrade(eng) == []                                         # a second start changes nothing
    assert c.get(A).status_code == 200 and "Q-2026-10-444" in c.get(A).text

    with db.Session() as s:                                              # existing rows kept, new columns at their defaults
        assert s.query(db.OrderLine).count() == n_lines and s.query(db.NoDrawingLine).count() == n_aside
        assert {l.qty for l in s.query(db.OrderLine)} == {1.0}
        assert all(x.description == "" and x.no == 0 and x.by == "" for x in s.query(db.NoDrawingLine))
        assert s.query(db.Drawing).one().made_at is not None
        assert {q.answered_at for q in s.query(db.Question)} == {None}
        s.add(db.AiTurn(line_id=s.query(db.OrderLine).first().id, typed="x", kind="answer")); s.commit()
        assert s.query(db.AiTurn).one().values == {}


def test_only_adds():
    """The statements it writes only ever add a column; Postgres gets IF NOT EXISTS so two starts at once cannot clash."""
    from sqlalchemy.dialects import postgresql, sqlite
    t = db.Base.metadata.tables
    pg = db.add_column_sql(t["no_drawing_line"], t["no_drawing_line"].c["description"], postgresql.dialect())
    assert pg == "ALTER TABLE no_drawing_line ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '' NOT NULL"
    assert db.add_column_sql(t["question"], t["question"].c["answered_at"], postgresql.dialect()).endswith("answered_at TIMESTAMP WITH TIME ZONE")
    assert "IF NOT EXISTS" not in db.add_column_sql(t["no_drawing_line"], t["no_drawing_line"].c["description"], sqlite.dialect())
    for table in t.values():
        for col in table.columns:
            if not col.primary_key:
                s_ = db.add_column_sql(table, col, postgresql.dialect())
                assert s_.startswith("ALTER TABLE ") and " ADD COLUMN IF NOT EXISTS " in s_ and "DROP" not in s_


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
