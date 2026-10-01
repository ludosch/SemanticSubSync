"""Reading subtitles that are not UTF-8 (most older downloads: 47 of 66 files in the author's
corpus). A single-byte code page decodes any bytes without error, so the language must pick it."""
import pytest

from semantic_subsync import media

SRT = "1\r\n00:00:01,000 --> 00:00:02,000\r\n{}\r\n\r\n"
LINES = {
    "ru": ("cp1251", "Привет, как дела?"),
    "el": ("cp1253", "Γεια σου, τι κάνεις;"),
    "tr": ("cp1254", "Şimdi ağlamıyorum, Işık."),
    "pl": ("cp1250", "Zażółć gęślą jaźń."),
    "fr": ("cp1252", "Où est passé le garçon ?"),
    "ar": ("cp1256", "أين ذهبت؟"),
}


@pytest.mark.parametrize("lang", LINES)
def test_language_tag_in_the_name_picks_the_code_page(tmp_path, lang):
    cp, line = LINES[lang]
    p = tmp_path / f"Show.S01E01.{lang}.srt"
    p.write_bytes(SRT.format(line).encode(cp))
    assert media.read_srt(p)[0][2] == line


def test_explicit_language_wins_over_the_name(tmp_path):
    cp, line = LINES["ru"]
    p = tmp_path / "noname.srt"
    p.write_bytes(SRT.format(line).encode(cp))
    assert media.read_srt(p, "ru")[0][2] == line


@pytest.mark.parametrize("enc", ["utf-8", "utf-8-sig", "utf-16"])
def test_unicode_files_whatever_the_name(tmp_path, enc):
    line = LINES["el"][1]
    p = tmp_path / "Movie.fr.srt"            # wrong tag: a valid Unicode file is never re-decoded
    p.write_bytes(SRT.format(line).encode(enc))
    assert media.read_srt(p)[0][2] == line


def test_unknown_language_is_guessed(tmp_path):
    """No tag: charset-normalizer. Reliable for a non-Latin script given enough text."""
    line = LINES["ru"][1]
    p = tmp_path / "noname.srt"
    p.write_bytes("".join(SRT.replace("1\r\n", f"{i}\r\n", 1).format(line) for i in range(1, 40)).encode("cp1251"))
    assert media.read_srt(p)[0][2] == line


@pytest.mark.parametrize("name,lang", [
    ("Movie.fr.srt", "fr"), ("Show - S01E01.fr.hi.srt", "fr"), ("Show.S01E01.pt-br.srt", "pt-br"),
    ("Show.S01E01.PT-BR.forced.srt", "pt-br"), ("Film.rus.srt", "rus"), ("Film.srt", None),
    ("The.Office.US.S03E10.srt", None),
])
def test_language_of(name, lang):
    assert media.language_of(name) == lang
