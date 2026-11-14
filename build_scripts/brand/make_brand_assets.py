"""Generate the MaoKaraoke pixel-art brand assets.

Run from the repository root:  python build_scripts/brand/make_brand_assets.py

Writes the splash logo, the app icons and the small web icons from the
sprite drawn below, so the artwork can be tweaked and rebuilt in one place.
"""

from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "pikaraoke" / "static"

PALETTE = {
    "O": (29, 21, 48, 255),  # outline
    "F": (255, 179, 92, 255),  # orange fur
    "D": (233, 128, 52, 255),  # tabby stripes
    "C": (255, 241, 220, 255),  # cream
    "P": (255, 143, 177, 255),  # ears / blush
    "N": (255, 99, 146, 255),  # nose / tongue
    "W": (255, 255, 255, 255),  # paws / eye shine
    "M": (196, 201, 226, 255),  # mic grille light
    "m": (112, 116, 150, 255),  # mic grille dark
    "H": (59, 51, 85, 255),  # mic handle
    "A": (126, 224, 208, 255),  # mint note
    "B": (255, 143, 177, 255),  # pink note
}

# A tabby singing into a microphone held in both paws, tail curled behind.
CAT = [
    "...OO.............OO..........A..",
    "..OPFO...........OFPO........AA..",
    "..OPPFO.........OFPPO........A.A.",
    "..OPPFFOOOOOOOOOFFPPO........A...",
    ".OFFFFFFFDFDFDFFFFFFFO.....AAA...",
    ".OFFFFFFFFDFDFFFFFFFFO.....AAA...",
    ".OFFFFFFFFFFFFFFFFFFFO...........",
    ".OFFFOWFFFFFFFFFOWFFFO.......B...",
    ".OFFFOOFFFFFFFFFOOFFFO.......BB..",
    ".OFPPFFFFFFNFFFFFFPPFO.......B.B.",
    ".OFFFFFFFCOOOCFFFFFFFO.....BBB...",
    ".ODFFFFFFCONOCFFFFFFDO.....BBB...",
    ".ODFFFFFFCCOCCFFFFFFDO...........",
    "..OFFFFFOOOOOOOFFFFFO............",
    "..OFFFFOMmMmMmMOFFFFO............",
    "..OFFFFOmMmMmMmOFFFFO............",
    "..OFFFFOMmMmMmMOFFFFO...OO.......",
    "..OFFFFFOOOOOOOFFFFFO..ODFO......",
    "..OFFOWWWOHHHOWWWOFFO..OFFO......",
    "..OFFOWWWOHHHOWWWOFFO..ODFO......",
    "..OFFFOOOOHHHOOOOFFFO..OFFO......",
    ".OFCCCCCCOHHHOCCCCCCFO.ODFO......",
    ".OFFCCCCCCOOOCCCCCCFFOOFFO.......",
    ".OFFFCCCCCCCCCCCCCFFFOFFO........",
    ".OFFFFOFFFFFFFFFOFFFFOFO.........",
    ".OFFCCCOFFFFFFFOCCCFFOO..........",
    "..OOOOOOOOOOOOOOOOOOO............",
]

# Cat face only, for square icons where the full body is too small to read.
HEAD = CAT[:13] + ["..OOOOOOOOOOOOOOOOOO............"]

FONT = {
    "M": ["O...O", "OO.OO", "O.O.O", "O.O.O", "O...O", "O...O", "O...O"],
    "a": [".....", ".....", ".OOO.", "....O", ".OOOO", "O...O", ".OOOO"],
    "o": [".....", ".....", ".OOO.", "O...O", "O...O", "O...O", ".OOO."],
    "K": ["O...O", "O..O.", "O.O..", "OO...", "O.O..", "O..O.", "O...O"],
    "r": [".....", ".....", "O.OO.", "OO..O", "O....", "O....", "O...."],
    "k": ["O....", "O....", "O..O.", "O.O..", "OO...", "O.O..", "O..O."],
    "e": [".....", ".....", ".OOO.", "O...O", "OOOOO", "O....", ".OOO."],
}


def sprite(rows, colors=PALETTE):
    width = max(len(r) for r in rows)
    image = Image.new("RGBA", (width, len(rows)), (0, 0, 0, 0))
    for y, row in enumerate(rows):
        for x, key in enumerate(row):
            if key in colors:
                image.putpixel((x, y), colors[key])
    return image


def sticker(image, border=1, color=(255, 255, 255, 255)):
    """Add a pixel border around the opaque area, like a die-cut sticker."""
    padded = Image.new("RGBA", (image.width + 2 * border, image.height + 2 * border))
    padded.paste(image, (border, border))
    alpha = padded.getchannel("A").point(lambda a: 255 if a else 0)
    grown = alpha.filter(ImageFilter.MaxFilter(2 * border + 1))
    base = Image.new("RGBA", padded.size, color)
    base.putalpha(grown)
    base.alpha_composite(padded)
    return base


def text(word, split, scale_colors):
    first, rest = scale_colors
    glyphs = []
    for i, char in enumerate(word):
        color = first if i < split else rest
        glyphs.append(sprite(FONT[char], {"O": color}))
    width = sum(g.width for g in glyphs) + len(glyphs) - 1
    image = Image.new("RGBA", (width, 7))
    x = 0
    for glyph in glyphs:
        image.paste(glyph, (x, 0))
        x += glyph.width + 1
    return image


def scaled(image, factor):
    return image.resize((image.width * factor, image.height * factor), Image.NEAREST)


def main():
    cat = sticker(sprite(CAT))
    wordmark = sticker(text("MaoKaraoke", 3, ((255, 179, 92, 255), (255, 255, 255, 255))), color=PALETTE["O"])

    # Splash logo: same 2:1 canvas as the old one so layouts keep working.
    logo = Image.new("RGBA", (2048, 1024))
    cat_big = scaled(cat, 30)
    logo.alpha_composite(cat_big, (70, (1024 - cat_big.height) // 2))
    cat_top = (1024 - cat_big.height) // 2
    word_big = scaled(wordmark, 18)
    # Start the wordmark just right of the tail (sprite column 27).
    word_x = min(70 + 28 * 30, 2048 - word_big.width - 20)
    word_y = cat_top + cat_big.height - word_big.height - 40
    logo.alpha_composite(word_big, (word_x, word_y))
    logo.save(STATIC / "images" / "logo.png", optimize=True)

    # Square head icon for favicons, the navbar and the desktop installers.
    head = sticker(sprite([row[:22] for row in HEAD]))
    side = max(head.width, head.height) + 2
    square = Image.new("RGBA", (side, side))
    square.alpha_composite(head, ((side - head.width) // 2, (side - head.height) // 2))
    icon = scaled(square, 1024 // side + 1).resize((1024, 1024), Image.NEAREST)
    icon.save(STATIC / "images" / "maokaraoke-icon.png", optimize=True)
    icon.resize((64, 64), Image.NEAREST).save(STATIC / "images" / "favicon.png", optimize=True)
    icon.save(
        STATIC / "icons" / "logo.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    icon.save(STATIC / "icons" / "logo.icns")

    # Wordmark for the web navbar (displayed at 22px tall, so 4x is crisp on HiDPI).
    scaled(wordmark, 10).save(STATIC / "images" / "wordmark.png", optimize=True)


if __name__ == "__main__":
    main()
