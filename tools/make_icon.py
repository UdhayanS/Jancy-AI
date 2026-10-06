"""Generate assets/jancy.ico (multi-size, PNG-compressed) from the app's own icon drawing."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from jancy.ui import make_icon_pixmap  # noqa: E402

app = QGuiApplication(sys.argv)
sizes = [16, 24, 32, 48, 64, 128, 256]
images = []
for s in sizes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    make_icon_pixmap(s).save(buf, "PNG")
    images.append(bytes(buf.data()))

out = ROOT / "assets" / "jancy.ico"
out.parent.mkdir(exist_ok=True)
header = struct.pack("<HHH", 0, 1, len(sizes))
offset = 6 + 16 * len(sizes)
entries = b""
for s, data in zip(sizes, images):
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
out.write_bytes(header + entries + b"".join(images))
print("wrote", out)
