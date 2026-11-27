"""The desk's own database. Orders are copied in from their source with our own ids, so the records stand without Odoo.
SQLite when run locally, Postgres on Railway (DATABASE_URL)."""
import os, datetime
from sqlalchemy import create_engine, ForeignKey, String, Text, Integer, Float, DateTime, JSON, UniqueConstraint, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

URL = os.environ.get("DATABASE_URL", "sqlite:///var/desk.sqlite3")
if URL.startswith(("postgres://", "postgresql://")):          # always the psycopg (v3) driver, whatever SQLAlchemy's default is
    URL = "postgresql+psycopg://" + URL.split("://", 1)[1]
if URL.startswith("sqlite:///") and not URL.startswith("sqlite:////"):
    os.makedirs(os.path.dirname(URL[len("sqlite:///"):]) or ".", exist_ok=True)
engine = create_engine(URL)
Session = sessionmaker(engine, expire_on_commit=False)
now = lambda: datetime.datetime.now(datetime.timezone.utc)


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "client"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact: Mapped[str] = mapped_column(String(200), default="")
    folder_no: Mapped[str] = mapped_column(String(10), default="")     # the client's folder number in drawing numbers


class Order(Base):
    __tablename__ = "order_"
    __table_args__ = (UniqueConstraint("source", "source_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"))
    source: Mapped[str] = mapped_column(String(40))            # where it was read from: odoo, sample, ...
    source_ref: Mapped[str] = mapped_column(String(80))        # its number there; kept only as a reference
    job_order: Mapped[str] = mapped_column(String(80), default="")
    salesperson: Mapped[str] = mapped_column(String(120), default="")
    delivery: Mapped[list] = mapped_column(JSON, default=list)
    read_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)
    client: Mapped[Client] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(order_by="OrderLine.no", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(order_by="Message.id", cascade="all, delete-orphan")
    steps: Mapped[list["Step"]] = relationship(order_by="Step.id", cascade="all, delete-orphan")
    not_drawn: Mapped[list["NoDrawingLine"]] = relationship(order_by="NoDrawingLine.id", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[float] = mapped_column(Float, default=1)
    questions: Mapped[list["Question"]] = relationship(order_by="Question.id", cascade="all, delete-orphan")
    drawing: Mapped["Drawing | None"] = relationship(cascade="all, delete-orphan", uselist=False)
    changes: Mapped[list["Change"]] = relationship(order_by="Change.id", cascade="all, delete-orphan")
    revisions: Mapped[list["Revision"]] = relationship(order_by="Revision.id", cascade="all, delete-orphan")
    sets: Mapped[list["ProductionSet"]] = relationship(order_by="ProductionSet.id", cascade="all, delete-orphan")
    marks: Mapped[list["Mark"]] = relationship(order_by="Mark.no", cascade="all, delete-orphan")


class NoDrawingLine(Base):
    """A quotation line that gets no drawing: bought-out items, services, installation, delivery (see nodrawing.py)."""
    __tablename__ = "no_drawing_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    name: Mapped[str] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Float, default=1)
    reason: Mapped[str] = mapped_column(String(80), default="")


class Question(Base):
    __tablename__ = "question"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    key: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(Text, default="")
    answered_by: Mapped[str] = mapped_column(String(120), default="")
    answered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    __tablename__ = "message"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    who: Mapped[str] = mapped_column(String(120))              # "desk" or the user's name
    text: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Drawing(Base):
    """One drawing of one order line. A preview until it is issued; each run of the engine replaces the preview."""
    __tablename__ = "drawing"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="preview")      # preview | failed | issued (issue is not built yet)
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)                   # the client specification the engine was given
    files: Mapped[dict] = mapped_column(JSON, default=dict)                  # pdf / dxf / png paths
    error: Mapped[str] = mapped_column(Text, default="")
    made_by: Mapped[str] = mapped_column(String(120), default="")
    made_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    """A member of staff allowed into the desk. The list is ours; Google only proves who is signing in."""
    __tablename__ = "user_"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="drawing")      # admin | drawing | coordinator
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Finish(Base):
    """A shade in the finishes library."""
    __tablename__ = "finish"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)       # as written on orders, upper case
    kind: Mapped[str] = mapped_column(String(40), default="")          # laminate, fabric, edgeband, powder coat ...
    hex: Mapped[str] = mapped_column(String(7))
    source: Mapped[str] = mapped_column(String(40), default="")        # past drawings | catalogue | estimate | photo
    added_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Revision(Base):
    """An issued drawing. Never changed after it is written; a change is a new revision."""
    __tablename__ = "revision"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    number: Mapped[str] = mapped_column(String(80))                    # Encore-Nov-2026-06-02
    rev: Mapped[str] = mapped_column(String(4))
    reason: Mapped[str] = mapped_column(Text, default="")
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    issued_by: Mapped[str] = mapped_column(String(120), default="")
    issued_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Change(Base):
    """A change request on one line: what was typed, how it was understood, and whether the user confirmed it."""
    __tablename__ = "change"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    typed: Mapped[str] = mapped_column(Text)
    understood: Mapped[str] = mapped_column(Text, default="")
    values: Mapped[dict] = mapped_column(JSON, default=dict)           # what it sets on the specification
    status: Mapped[str] = mapped_column(String(20), default="asked")   # asked | confirmed | rejected | cannot
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Step(Base):
    """What happened to an order after drawing: issued, sent to the client, approved, released to production, completed.
    A log, never edited; the order's stage is worked out from it."""
    __tablename__ = "step"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    kind: Mapped[str] = mapped_column(String(20))      # issued | sent | approved | released | completed | reopened
    note: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class EngineRequest(Base):
    """A request for an addition or change to the engines, raised by a user when the engine cannot build something.
    The administrator approves or declines it here. Approval means "build this"; the built change is still
    approved separately in the approved-engines repository before any drawing can use it."""
    __tablename__ = "engine_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_ref: Mapped[str] = mapped_column(String(80), default="")
    product: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="asked")   # asked | approved | declined | built
    asked_by: Mapped[str] = mapped_column(String(120), default="")
    asked_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Photo(Base):
    """A photograph of a product, kept by product name so the same product shows it on every order."""
    __tablename__ = "photo"
    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(String(200), unique=True)     # product name, lower case
    path: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProductionSet(Base):
    """The production (or subcontractor) drawings of one product, made from one issued revision."""
    __tablename__ = "production_set"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    revision_id: Mapped[int] = mapped_column(ForeignKey("revision.id"))
    kind: Mapped[str] = mapped_column(String(20))                      # PRODUCTION | VENDOR
    files: Mapped[list] = mapped_column(JSON, default=list)            # [dict(title, pdf, pages, name)]
    details: Mapped[dict] = mapped_column(JSON, default=dict)          # job order no., date, deadline
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    made_by: Mapped[str] = mapped_column(String(120), default="")
    made_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Mark(Base):
    """A numbered marker a user placed on a drawing, to point at a part when describing a change. Numbers run on per product."""
    __tablename__ = "mark"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    shown: Mapped[str] = mapped_column(String(40))                    # which sheet: r<revision id>, or d<time of the preview>
    no: Mapped[int] = mapped_column(Integer)
    x: Mapped[float] = mapped_column(Float)                           # position on the sheet, per cent of its width and height
    y: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


def init():
    Base.metadata.create_all(engine)
