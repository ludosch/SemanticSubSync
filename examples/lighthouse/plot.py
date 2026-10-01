"""Draw the timing error of every line against its position in the video, before and after each
tool, as the SVG charts of the README. Run compare.py first: it writes the out.*.srt files.

Usage: python examples/lighthouse/plot.py SITUATION_FOLDER OUTPUT.svg "Title"
"""
import os, sys

from semantic_subsync import media

case, out, title = sys.argv[1], sys.argv[2], sys.argv[3]
tr = {x: s for s, e, x in media.read_srt(os.path.join(case, "truth.fr.srt"))}
series = [("Downloaded (before)", "downloaded.fr.srt", "#8a8f98", "6 4"),
          ("ffsubsync / LAPSE", "out.ffsubsync.srt", "#e5484d", ""),
          ("SemanticSubSync", "out.semantic-subsync.srt", "#2f9e6e", ""),
          ("alass", "out.alass.srt", "#b8860b", "3 4")]
pts = {name: [(tr[x] / 60, s - tr[x]) for s, e, x in media.read_srt(os.path.join(case, f)) if x in tr] for name, f, _, _ in series}
ys = [y for p in pts.values() for _, y in p]
ymin, ymax = min(min(ys), -5), max(max(ys), 5)
ymin, ymax = 10 * (ymin // 10), 10 * (-(-ymax // 10))
W, Hh, L, R, T, B = 760, 340, 72, 20, 46, 76
X = lambda m: L + (W - L - R) * m / 10
Y = lambda v: T + (Hh - T - B) * (ymax - v) / (ymax - ymin)
s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {Hh}" font-family="system-ui, sans-serif" font-size="13">',
     f'<rect width="{W}" height="{Hh}" rx="8" fill="#ffffff"/>',
     f'<text x="{L}" y="26" font-size="15" font-weight="600" fill="#1f2328">{title}</text>']
step = 10 if ymax - ymin <= 60 else 20
v = ymin
while v <= ymax:
    s.append(f'<line x1="{L}" x2="{W-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{"#57606a" if v == 0 else "#d8dee4"}" stroke-width="{1.2 if v == 0 else 1}"/>')
    s.append(f'<text x="{L-8}" y="{Y(v)+4:.1f}" text-anchor="end" fill="#57606a">{v:+.0f} s</text>'.replace("+0 s", "0 s"))
    v += step
for m in range(0, 11, 2):
    s.append(f'<text x="{X(m):.1f}" y="{Hh-B+22}" text-anchor="middle" fill="#57606a">{m} min</text>')
s.append(f'<text x="{(L+W-R)/2}" y="{Hh-B+40}" text-anchor="middle" fill="#57606a">position in the video</text>')
for name, f, col, dash in series:
    p = pts[name]      # no line across a gap of more than 30 s: there is no dialogue there
    d = " ".join(f"{'M' if i == 0 or x - p[i-1][0] > 0.5 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(p))
    s.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{3 if name == "SemanticSubSync" else 2}" stroke-dasharray="{dash}" stroke-linejoin="round"/>')
lx = L
for name, f, col, dash in series:
    s.append(f'<line x1="{lx}" x2="{lx+22}" y1="{Hh-14}" y2="{Hh-14}" stroke="{col}" stroke-width="3" stroke-dasharray="{dash}"/>')
    s.append(f'<text x="{lx+28}" y="{Hh-10}" fill="#1f2328">{name}</text>')
    lx += 40 + 7.2 * len(name)
s.append(f'<text transform="translate(16 {(T+Hh-B)/2}) rotate(-90)" text-anchor="middle" fill="#57606a" font-size="12">timing error of each line</text>')
s.append("</svg>")
open(out, "w", encoding="utf-8").write("\n".join(s) + "\n")
