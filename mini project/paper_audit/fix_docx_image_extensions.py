from __future__ import annotations

import re
import zipfile
from pathlib import Path


def main() -> None:
    base = Path(__file__).resolve().parent
    src = base / "IEEE_SCADA_Paper_Final.docx"
    dst = base / "IEEE_SCADA_Paper_ImageFixed.docx"

    media_pattern = re.compile(r"^word/media/(.+)\.undefined$")

    with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        rename_map = {}
        for name in names:
            match = media_pattern.match(name)
            if match:
                rename_map[name] = f"word/media/{match.group(1)}.png"

        for item in zin.infolist():
            data = zin.read(item.filename)
            out_name = rename_map.get(item.filename, item.filename)

            if item.filename == "word/_rels/document.xml.rels":
                text = data.decode("utf-8")
                text = text.replace(".undefined", ".png")
                data = text.encode("utf-8")

            if item.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if 'Extension="png"' not in text:
                    text = text.replace(
                        "<Types ",
                        '<Types <Default Extension="png" ContentType="image/png"/> ',
                        1,
                    )
                data = text.encode("utf-8")

            zout.writestr(out_name, data)

    print(dst)
    print(f"renamed_images={len(rename_map)}")


if __name__ == "__main__":
    main()
