"""The desk's own database. Orders are copied in from their source with our own ids, so the records stand without Odoo.
SQLite when run locally, Postgres on Railway (DATABASE_URL)."""
import os, datetime
from sqlalchemy import create_engine, ForeignKey, String, Text, Integer, Float, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

URL = os.environ.get("DATABASE_URL", "sqlite:///var/desk.sqlite3").replace("postgres://", "postgresql://")
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


class OrderLine(Base):
    __tablename__ = "order_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id"))
    no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[float] = mapped_column(Float, default=1)
    questions: Mapped[list["Question"]] = relationship(order_by="Question.id", cascade="all, delete-orphan")


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


def init():
    Base.metadata.create_all(engine)
