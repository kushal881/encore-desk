"""The desk's own database. Orders are copied in from their source with our own ids, so the records stand without Odoo.
SQLite when run locally, Postgres on Railway (DATABASE_URL)."""
import os, datetime
from sqlalchemy import create_engine, inspect, text, literal, ForeignKey, String, Text, Integer, Float, DateTime, JSON, UniqueConstraint, Boolean
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
    delivery_date: Mapped[str] = mapped_column(String(40), default="")     # Odoo's Delivery Date (production deadline); "" = not entered
    read_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)
    checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # quotation last compared with Odoo
    client: Mapped[Client] = relationship()
    lines: Mapped[list["OrderLine"]] = relationship(order_by="OrderLine.no", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(order_by="Message.id", cascade="all, delete-orphan")
    steps: Mapped[list["Step"]] = relationship(order_by="Step.id", cascade="all, delete-orphan")
    not_drawn: Mapped[list["NoDrawingLine"]] = relationship(order_by="NoDrawingLine.id", cascade="all, delete-orphan")
    added: Mapped[list["AddedLine"]] = relationship(order_by="AddedLine.id", cascade="all, delete-orphan")


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
    areas: Mapped[list["LineArea"]] = relationship(order_by="LineArea.id", cascade="all, delete-orphan")
    keep: Mapped[bool] = mapped_column(Boolean, default=False)          # added back to the drawings on purpose: the no-drawing rule leaves it
    source_line: Mapped[str] = mapped_column(String(40), default="")    # the quotation line it is tied to (Odoo sale.order.line id); "" = not tied
    cancelled_by: Mapped[str] = mapped_column(String(120), default="")  # dropped from the quotation after issue: Cancelled stamp, left out of the set
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NoDrawingLine(Base):
    """A quotation line that gets no drawing: bought-out items, services, installation, delivery (see nodrawing.py)."""
    __tablename__ = "no_drawing_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    name: Mapped[str] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Float, default=1)
    reason: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")              # kept so the line can be added back to drawings
    no: Mapped[int] = mapped_column(Integer, default=0)                      # its former product number when it was removed; never reused
    by: Mapped[str] = mapped_column(String(120), default="")                 # who removed it ("" = set aside by the rule)


class AddedLine(Base):
    """A product added in the desk that is not in the quotation: the salesperson must update the quotation."""
    __tablename__ = "added_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


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
    engines_dismissed: Mapped[str] = mapped_column(String(20), default="")   # the newer engine version whose notice they closed


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
    option: Mapped[str] = mapped_column(String(4), default="")            # A: this client only (product file) | B: engine change


class ChangeRequest(Base):
    """A change that needs an administrator's permission: a change after client approval (unlocks that drawing for one
    new revision), or a revision raise. The decision creates a task for the person who asked."""
    __tablename__ = "change_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))                        # after_approval | revision
    order_ref: Mapped[str] = mapped_column(String(80))
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    product: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="asked")     # asked | approved | rejected
    asked_by: Mapped[str] = mapped_column(String(120), default="")
    asked_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Task(Base):
    """Something a person has to do because of an administrator's decision (their request approved or rejected)."""
    __tablename__ = "task"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignee: Mapped[str] = mapped_column(String(120))
    order_ref: Mapped[str] = mapped_column(String(80), default="")
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="")          # engine:<id> | change:<id>
    status: Mapped[str] = mapped_column(String(10), default="open")      # open | done
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)
    done_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Photo(Base):
    """A photograph of a product, kept by product name so the same product shows it on every order."""
    __tablename__ = "photo"
    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(String(200), unique=True)     # product name, lower case
    path: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ClientAction(Base):
    """What staff did with a client's answer on one issued drawing (issue #34): applied the change (sent to the change
    panel), re-issued the next revision, replied, or rejected. Replies and rejections are shown to the client on the portal."""
    __tablename__ = "client_action"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    approval_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))                       # applied | reissued | reply | reject
    text: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuoteVersion(Base):
    """A saved copy of the quotation's lines each time Odoo shows a difference (version 1 = as first read)."""
    __tablename__ = "quote_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    n: Mapped[int] = mapped_column(Integer, default=1)
    lines: Mapped[list] = mapped_column(JSON, default=list)             # [dict(ref, name, description, qty, price, no_drawing)]
    why: Mapped[str] = mapped_column(String(40), default="")            # first read | webhook | project opened | check now | nightly
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuoteChange(Base):
    """One difference between two quotation versions, with what it means for the product's drawing at its stage
    (UI_BRIEF, quotation change rules) and how it was handled."""
    __tablename__ = "quote_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    version: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20))                       # added | dropped | qty | spec | price
    line_ref: Mapped[str] = mapped_column(String(40), default="")
    line_no: Mapped[int] = mapped_column(Integer, default=0)            # the desk product, 0 = none yet
    name: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    stage: Mapped[str] = mapped_column(String(20), default="")          # not drawn | drawn | issued | approved | production
    status: Mapped[str] = mapped_column(String(20), default="open")     # open | handled | ignored
    handled_by: Mapped[str] = mapped_column(String(120), default="")
    handled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
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
    prod_no: Mapped[int] = mapped_column(Integer, default=1)              # P1, P2 ...: a released set changed later becomes the next P
    spec: Mapped[dict] = mapped_column(JSON, default=dict)               # the specification it was drawn from ({} = the revision's, older sets)
    released_by: Mapped[str] = mapped_column(String(120), default="")    # released to the shop floor ("" = not yet)
    released_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProdChange(Base):
    """A construction change on a product's production drawing (screen 11): frame, supports, joinery, hardware placement,
    edge band, cut sizes. Allowed only when the client drawing stays exactly as approved. Before release it changes the
    current P; after release it needs an administrator (P1 -> P2) and production gets a stop alert."""
    __tablename__ = "prod_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    set_id: Mapped[int] = mapped_column(Integer, default=0)              # the production set it was asked on
    marker: Mapped[int] = mapped_column(Integer, default=0)
    typed: Mapped[str] = mapped_column(Text)
    said: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="")          # answer | cannot | refused | failed | trial | accepted | undone | asked | approved | rejected
    reason: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)               # the production specification with the change
    files: Mapped[list] = mapped_column(JSON, default=list)              # the trial production set
    bom: Mapped[list] = mapped_column(JSON, default=list)                # BOM effect lines: [dict(item, before, after)]
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


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


class Review(Base):
    """Someone looked at a drawing in the viewer, its automatic checks passed, and they marked it ready to issue.
    Tied to the exact drawing shown (key d<time of the preview>): a redraw needs a new check."""
    __tablename__ = "review"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    shown: Mapped[str] = mapped_column(String(40))
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProdPreview(Base):
    """A production drawing made only to look at, before the real production set exists. Job order and deadline are
    placeholders (JO-[NO.], [DATE]) until they are known. Never issued, never sent to production."""
    __tablename__ = "prod_preview"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    shown: Mapped[str] = mapped_column(String(40))                    # the client drawing it was made from
    pdf: Mapped[str] = mapped_column(Text, default="")
    pages: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(Text, default="")
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    made_by: Mapped[str] = mapped_column(String(120), default="")
    made_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class AiTurn(Base):
    """One request to the AI change assistant on one product: what was typed, what it answered or proposed, and for a
    proposed change the trial drawing the engine made from it (never shown as the drawing until someone accepts it)."""
    __tablename__ = "ai_turn"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    shown: Mapped[str] = mapped_column(String(40), default="")          # the drawing it was asked on (d<time> / r<id>)
    typed: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))                       # change | answer | cannot
    said: Mapped[str] = mapped_column(Text, default="")                  # the change in plain words
    answer: Mapped[str] = mapped_column(Text, default="")
    values: Mapped[dict] = mapped_column(JSON, default=dict)             # what it sets in the product file
    status: Mapped[str] = mapped_column(String(20), default="")         # answer | cannot | trial | failed | accepted | undone | withdrawn
    spec: Mapped[dict] = mapped_column(JSON, default=dict)               # the trial specification
    files: Mapped[dict] = mapped_column(JSON, default=dict)              # the trial drawing (pdf, png, parts, checks)
    error: Mapped[str] = mapped_column(Text, default="")                 # why the engine refused the trial
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    change_id: Mapped[int] = mapped_column(Integer, default=0)           # the confirmed change made on Accept
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class LineArea(Base):
    """Where a product goes, when the client asks: several areas, each with a quantity, adding up to the product's qty.
    Printed in the sheet's Area field. No rows = the Area field stays as the quotation line has it (or blank)."""
    __tablename__ = "line_area"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("order_line.id"))
    area: Mapped[str] = mapped_column(Text)
    qty: Mapped[float] = mapped_column(Float, default=1)
    by: Mapped[str] = mapped_column(String(120), default="")


class ProductionFile(Base):
    """A file the approved engines made for production of the whole order: the bill of material (Excel) with its cut list.
    panel_saw: made with the panel-saw cutting rules (4 mm saw cut, cut sizes, grain, optimised boards)."""
    __tablename__ = "production_file"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    kind: Mapped[str] = mapped_column(String(20), default="bom")
    panel_saw: Mapped[bool] = mapped_column(Boolean, default=False)
    path: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(Text, default="")
    revisions: Mapped[str] = mapped_column(Text, default="")             # the issued drawings it was made from
    engine_version: Mapped[str] = mapped_column(String(80), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    made_by: Mapped[str] = mapped_column(String(120), default="")
    made_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortalAccess(Base):
    """Who may sign in to the client portal for one order. Only these emails can; CC addresses and forwarded mails cannot.
    can_approve: approve drawings; otherwise view and comment. Every addition and removal is logged in the conversation."""
    __tablename__ = "portal_access"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    email: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200), default="")
    can_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    added_by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailLog(Base):
    """An email sent (or tried) to the client from the Issue screen."""
    __tablename__ = "email_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    kind: Mapped[str] = mapped_column(String(20), default="first")
    to: Mapped[str] = mapped_column(Text, default="")
    cc: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachment: Mapped[str] = mapped_column(Text, default="")            # file name of the combined PDF, "" = none
    error: Mapped[str] = mapped_column(Text, default="")                 # "" = sent
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class Approval(Base):
    """The client's answer on one issued drawing (decision 11): approved, or changes asked. Recorded by staff when the
    client replied by email or phone (the portal records its own later, code-verified). Kept with the fingerprint of
    the exact PDF approved. An approval locks that drawing for the client."""
    __tablename__ = "approval"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    revision_id: Mapped[int] = mapped_column(ForeignKey("revision.id"))
    outcome: Mapped[str] = mapped_column(String(20))                    # approved | changes
    name: Mapped[str] = mapped_column(String(200), default="")
    designation: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    how: Mapped[str] = mapped_column(String(40), default="")            # email | phone | signed PDF | portal
    on: Mapped[str] = mapped_column(String(20), default="")             # the date the client gave it
    note: Mapped[str] = mapped_column(Text, default="")
    proof: Mapped[str] = mapped_column(Text, default="")                # attached mail / signed PDF (path)
    proof_name: Mapped[str] = mapped_column(Text, default="")
    pdf_sha256: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(60), default="")
    code_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    by: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class StoredFile(Base):
    """A file the desk keeps in the private storage bucket (storage.py): where it was made on the desk's disk, and its
    name in the bucket. The disk copy is only a working copy; the bucket copy is the one kept."""
    __tablename__ = "stored_file"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(600), unique=True)          # the local path recorded in the drawing / revision
    key: Mapped[str] = mapped_column(Text)                                # its name in the bucket
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class FileLog(Base):
    """Every view and download of a kept file, by staff or by a client on the portal (brief: log every view, download, approval)."""
    __tablename__ = "file_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    who: Mapped[str] = mapped_column(String(200), default="")             # staff name, or the client's email
    side: Mapped[str] = mapped_column(String(10), default="desk")         # desk | portal
    action: Mapped[str] = mapped_column(String(20), default="view")       # view | download
    name: Mapped[str] = mapped_column(Text, default="")                   # the file name shown
    key: Mapped[str] = mapped_column(Text, default="")                    # bucket name, "" = served from the desk's disk
    order_id: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str] = mapped_column(String(60), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


class PortalCode(Base):
    """A one-time 6-digit code emailed to a client on the portal: to sign in, or to approve drawings (decision 11).
    Only its fingerprint is kept. Valid 10 minutes, 5 tries, used once."""
    __tablename__ = "portal_code"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(20), default="signin")   # signin | approve
    order_id: Mapped[int] = mapped_column(Integer, default=0)             # approve: the order it is for
    code_hash: Mapped[str] = mapped_column(String(64))
    expires: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    tries: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str] = mapped_column(String(60), default="")
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=now)


# Columns that hold an order / line id WITHOUT a database link, kept on purpose when an order is removed (issue #50):
# logs and one-time codes stay as history; nothing refuses the delete because of them.
UNLINKED_KEPT = {("file_log", "order_id"), ("portal_code", "order_id"), ("client_action", "approval_id")}


def _children(table):
    """Every (table, column) whose foreign key points at table."""
    return [(t, fk.parent) for t in Base.metadata.sorted_tables for fk in t.foreign_keys if fk.column.table is table]


def purge(s, table, ids):
    """Deletes rows of table (by id) and, first, every row anywhere that points at them, following every foreign key -
    so a table added later can never block the delete again (issue #50). Runs in the caller's transaction. -> rows deleted."""
    from sqlalchemy import select as _select
    ids = list(ids)
    if not ids:
        return 0
    n = 0
    for child, col in _children(table):
        if child is table:
            continue
        pk = list(child.primary_key.columns)[0]
        n += purge(s, child, s.execute(_select(pk).where(col.in_(ids))).scalars().all())
    n += s.execute(table.delete().where(list(table.primary_key.columns)[0].in_(ids))).rowcount
    return n


def delete_order(s, order_id):
    """Removes one order and everything that belongs to it, in one transaction (the caller commits)."""
    return purge(s, Order.__table__, [order_id])


def delete_line(s, line_id):
    """Removes one product (order line) and everything that belongs to it (the caller commits)."""
    return purge(s, OrderLine.__table__, [line_id])


def _default_sql(col, dialect):
    """The value existing rows get when a column is added: the column's own default, as SQL. None = no default."""
    d = col.default
    if d is None:
        return None
    if d.is_callable:
        fn = getattr(d.arg, "__wrapped__", d.arg)
        name = getattr(fn, "__name__", "")
        if isinstance(col.type, DateTime):                           # existing rows: the time of the upgrade
            return "CURRENT_TIMESTAMP" if dialect.name == "postgresql" else "'%s'" % now().strftime("%Y-%m-%d %H:%M:%S")
        if name in ("dict", "list"):
            return "'{}'" if name == "dict" else "'[]'"
        return None
    v = d.arg
    if isinstance(v, (dict, list)):
        import json
        return "'%s'" % json.dumps(v).replace("'", "''")
    return str(literal(v).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def add_column_sql(table, col, dialect):
    """ALTER TABLE ... ADD COLUMN for one model column that the database does not have yet. Only ever adds: existing rows
    take the column's default. Foreign keys and unique rules are not added to an existing table (the column itself is)."""
    q = dialect.identifier_preparer.quote
    sql = "ALTER TABLE %s ADD COLUMN %s%s %s" % (q(table.name), "IF NOT EXISTS " if dialect.name == "postgresql" else "",
                                                 q(col.name), col.type.compile(dialect=dialect))
    default = _default_sql(col, dialect)
    if default is not None:
        sql += " DEFAULT %s" % default
        if not col.nullable:
            sql += " NOT NULL"
    return sql


def upgrade(eng=None):
    """Brings tables made by an earlier version of the desk up to date: adds every column the models have and the table
    lacks. Never drops, renames or changes a column, and never touches the rows' existing values. -> ["table.column", ...]"""
    eng = eng or engine
    insp = inspect(eng)
    added = []
    with eng.begin() as con:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue                                              # create_all makes new tables whole
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                if col.primary_key:
                    raise RuntimeError("Table %s has no primary key column %s; it cannot be repaired automatically" % (table.name, col.name))
                con.execute(text(add_column_sql(table, col, eng.dialect)))
                added.append("%s.%s" % (table.name, col.name))
    for a in added:
        print("Database: added column %s" % a, flush=True)
    return added


def init():
    Base.metadata.create_all(engine)
    upgrade(engine)
