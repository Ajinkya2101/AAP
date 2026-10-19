import io

from PIL import Image

from aap.ingest.filetypes import detect


def test_detect_types():
    assert detect(b"%PDF-1.7\n...") == "pdf"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    assert detect(buf.getvalue()) == "png"
    eml = b"From: a@b.example\r\nTo: ap@acse.local\r\nSubject: Invoice\r\nMessage-ID: <1@b>\r\n\r\nhello"
    assert detect(eml) == "eml"
    assert detect(b"item,qty,price\nA,1,2.00\nB,3,4.00\n") == "csv"
    assert detect(b"<html><body>Invoice</body></html>") == "html"
    assert detect(b"just some text") == "txt"
