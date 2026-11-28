"""Issue (screen 6): email templates, the combined PDF, approval fingerprints and sending mail.

Mail goes out from the company address through Google Workspace with an app password (decided 27-Nov-2026):
SMTP_PASSWORD (required, set in Railway), SMTP_USER / MAIL_FROM (default info@encoreprimefurniture.com),
SMTP_HOST (smtp.gmail.com), SMTP_PORT (587). Until the client portal exists, the combined PDF is attached and the
client approves by replying."""
import hashlib, os, re, smtplib, datetime
from email.message import EmailMessage

COMPANY = "info@encoreprimefurniture.com"

TEMPLATES = {
    "first": ("Drawings for approval (first issue)", "Drawings for approval: {quote} · {client}",
              "Dear {contact},\n\nThe drawings for quotation {quote} ({count} drawing{s}, {revs}) are ready for your approval. "
              "The complete set is attached as one PDF.\n\nPlease reply to this email to approve them, or tell us any change you need "
              "and the drawing number it is on.\n\nRegards,\n{user}\nEncore Prime Furniture"),
    "revised": ("Revised drawings", "Revised drawings: {quote} · {client}",
                "Dear {contact},\n\nAs discussed, the drawings for quotation {quote} have been revised ({revs}). The updated set "
                "is attached as one PDF.\n\nPlease reply to this email to approve them, or tell us any further change.\n\nRegards,\n{user}\nEncore Prime Furniture"),
    "additional": ("Additional products", "Additional drawings: {quote} · {client}",
                   "Dear {contact},\n\nThe drawings for the products added to quotation {quote} ({revs}) are ready for your approval. "
                   "The set is attached as one PDF.\n\nPlease reply to this email to approve them.\n\nRegards,\n{user}\nEncore Prime Furniture"),
    "cancelled": ("Cancelled products", "Cancelled products: {quote} · {client}",
                  "Dear {contact},\n\nSome products have been cancelled from quotation {quote}. The updated set is attached as one PDF; "
                  "the cancelled drawings are marked.\n\nPlease reply to confirm.\n\nRegards,\n{user}\nEncore Prime Furniture"),
}


def fill(kind, quote, client, contact, revs, user):
    """-> (subject, body) of a template with the order's details filled in."""
    _, subject, body = TEMPLATES[kind]
    v = dict(quote=quote, client=client, contact=contact or "Sir / Madam", count=len(revs), s="" if len(revs) == 1 else "s",
             revs=", ".join(revs) if revs else "no drawings", user=user)
    return subject.format(**v), body.format(**v)


def emails(text):
    """'a@x.com, b@y.com' -> ['a@x.com', 'b@y.com'], or raises ValueError naming the address that is not one."""
    out = []
    for e in re.split(r"[,;\s]+", text or ""):
        if not e:
            continue
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", e):
            raise ValueError("%s is not an email address" % e)
        out.append(e.lower())
    return out


def fingerprint(path):
    """The approved file's fingerprint (sha256), kept with every approval record."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def combined_name(client, day=None):
    day = day or datetime.date.today()
    return "%s-%s.pdf" % (re.sub(r"[^\w .&-]", "", client).strip(), day.strftime("%d-%b-%Y"))


def combine(pdfs, out):
    """One PDF from the drawings in the order given (serial order). The desk only joins finished PDFs; it draws nothing."""
    import pymupdf
    doc = pymupdf.open()
    for p in pdfs:
        with pymupdf.open(p) as d:
            doc.insert_pdf(d)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    doc.save(out)
    doc.close()
    return out


def configured():
    return bool(os.environ.get("SMTP_PASSWORD"))


def send(to, cc, subject, body, attachment=None, attachment_name=None):
    """Sends one email from the company address. Raises RuntimeError with a plain reason when it cannot."""
    if not configured():
        raise RuntimeError("Email is not set up yet: add SMTP_PASSWORD (the Google app password of %s) in Railway." % COMPANY)
    m = EmailMessage()
    m["From"] = os.environ.get("MAIL_FROM", "Encore Prime Furniture <%s>" % COMPANY)
    m["To"], m["Subject"] = ", ".join(to), subject
    if cc:
        m["Cc"] = ", ".join(cc)
    m.set_content(body)
    if attachment:
        with open(attachment, "rb") as f:
            m.add_attachment(f.read(), maintype="application", subtype="pdf", filename=attachment_name or os.path.basename(attachment))
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(os.environ.get("SMTP_USER", COMPANY), os.environ["SMTP_PASSWORD"])
            s.send_message(m)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("Google refused the email password. Check SMTP_PASSWORD (an app password of %s) in Railway." % COMPANY)
    except (smtplib.SMTPException, OSError) as e:
        raise RuntimeError("The email could not be sent (%s). Nothing was recorded as sent." % type(e).__name__)
