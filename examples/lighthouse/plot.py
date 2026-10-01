"""Draw what SemanticSubSync corrects, one SVG per situation: constant offset, frame rate, missing
lines, extra lines, wrong reference. It is a schematic: 40 seconds of dialogue, one mark per line,
a few seconds of lines missing or added. Each picture has three strips: the reference (the
English subtitle of the video), the external French subtitle and the fixed one. What each tool
really does with the full files of the example is measured by compare.py.

Usage: python examples/lighthouse/plot.py OUTPUT_DIR
"""
import os, random, sys

W, LABEL, X0, X1, SECONDS, H, GAP = 680, 10, 118, 668, 40, 16, 28
INK, MUTED, BAD = "#1f2328", "#57606a", "#c4302b"
SYNC, OFF, FIXED = "#2f6fdb", "#e07b39", "#2f9e6e"     # in sync (and the reference), out of sync, fixed


def X(t):
    return X0 + (X1 - X0) * t / SECONDS


def timeline(seed, length, gap):
    rng, t, lines = random.Random(seed), 1.0, []
    while t < SECONDS - 1:
        d = rng.uniform(*length)
        lines.append((t, min(t + d, SECONDS)))
        t += d + rng.uniform(*gap)
    return lines


class Svg:
    def __init__(self, title, top=0):
        self.s, self.t, self.y = [], [], 34 + top
        self.text(LABEL, 20, title, size=14, weight="600")

    def text(self, x, y, body, color=INK, size=12, anchor="start", weight="400"):
        self.t.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" class="h" fill="{color}" font-size="{size}" font-weight="{weight}">{body}</text>')

    def rows(self, labels):
        ys = []
        for label in labels:
            ys.append(self.y)
            self.text(LABEL, self.y + 12, label, weight="600")
            self.s.append(f'<rect x="{X0}" y="{self.y}" width="{X1 - X0}" height="{H}" rx="2" fill="#f1f3f5"/>')
            self.y += H + GAP
        self.y -= GAP
        return ys

    def marks(self, y, marks):
        for a, b, c in marks:
            self.s.append(f'<rect x="{X(a):.1f}" y="{y}" width="{X(b) - X(a):.1f}" height="{H}" rx="2" fill="{c}"/>')

    def hatch(self, s0, s1, y):
        self.s.append(f'<rect x="{X(s0):.1f}" y="{y}" width="{X(s1) - X(s0):.1f}" height="{H}" fill="url(#hatch)"/>')

    def arrow(self, t0, y0, t1, y1):
        self.s.append(f'<line x1="{X(t0):.1f}" y1="{y0 + H + 2}" x2="{X(t1):.1f}" y2="{y1 - 3}" stroke="{FIXED}" stroke-width="2" marker-end="url(#arrow)"/>')

    def save(self, path, note=None):
        y = self.y + 18
        if note:
            self.text(X1, y, note, BAD, anchor="end", weight="600")
            y += 18
        self.text(X0, y, "0 s", MUTED, size=11, anchor="middle")
        self.text(X1, y, f"{SECONDS} s", MUTED, size=11, anchor="middle")
        lx = (X0 + X1) / 2 - 130
        for c, label in ((SYNC, "in sync"), (OFF, "out of sync"), (FIXED, "fixed")):
            self.s.append(f'<rect x="{lx:.1f}" y="{y - 9}" width="10" height="10" rx="2" fill="{c}"/>')
            self.text(lx + 14, y, label, MUTED, size=11)
            lx += 30 + 6 * len(label)
        y += 8
        head = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {y}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="13">',
                '<style>.h{paint-order:stroke;stroke:#fff;stroke-width:4px;stroke-linejoin:round}</style>',
                f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{FIXED}"/></marker>'
                '<pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
                '<rect width="6" height="6" fill="#ffffff" fill-opacity="0.5"/><rect width="2.5" height="6" fill="#8a8f98" fill-opacity="0.7"/></pattern></defs>',
                f'<rect width="{W}" height="{y}" rx="8" fill="#ffffff"/>']
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(head + self.s + self.t + ["</svg>"]) + "\n")


def main(folder):
    lines = timeline(5, (1.4, 2.6), (0.6, 1.6))
    scene = [i for i, (a, _) in enumerate(lines) if 15 <= a < 23]
    s0, s1 = lines[scene[0]][0] - 0.4, lines[scene[-1]][1] + 0.4
    cut = s1 - s0
    kept = [l for i, l in enumerate(lines) if i not in scene]
    early = lambda a: a - cut if a >= s1 else a        # timeline without those lines
    sync = lambda a, color: SYNC if a < s0 else color   # lines before them are already in sync
    labels = ["Reference (EN)", "External (FR)", "Fixed (FR)"]
    mid = lambda l: (l[0] + l[1]) / 2
    picks = (lines[2], lines[len(lines) // 2], lines[-3])

    g = Svg("1. Offset: the French is 3 s late")
    yr, ye, yf = g.rows(labels)
    late = lambda t: min(t + 3, SECONDS)
    g.marks(yr, [(a, b, SYNC) for a, b in lines])
    g.marks(ye, [(late(a), late(b), OFF) for a, b in lines if b + 3 <= SECONDS])
    g.marks(yf, [(a, b, FIXED) for a, b in lines])
    for l in picks:
        g.arrow(late(mid(l)), ye, mid(l), yf)
    g.save(os.path.join(folder, "fix-offset.svg"))

    g = Svg("2. Frame rate: the French was timed on a 25 fps copy, the video is 23.976 fps")
    yr, ye, yf = g.rows(labels)
    fast = lambda a: a * 23.976 / 25
    g.marks(yr, [(a, b, SYNC) for a, b in lines])
    g.marks(ye, [(fast(a), fast(b), OFF) for a, b in lines])
    g.marks(yf, [(a, b, FIXED) for a, b in lines])
    g.text(X1, ye - 5, "drifts more and more", MUTED, size=11, anchor="end")
    for l in picks:
        g.arrow(fast(mid(l)), ye, mid(l), yf)
    g.save(os.path.join(folder, "fix-frame-rate.svg"))

    g = Svg("3. Lines missing from the French")
    yr, ye, yf = g.rows(labels)
    g.marks(yr, [(a, b, SYNC) for a, b in lines])
    g.hatch(s0, s1, yr)
    g.text(X((s0 + s1) / 2), yr + H + 13, "missing from the French", MUTED, size=11, anchor="middle")
    g.marks(ye, [(early(a), early(b), sync(a, OFF)) for a, b in kept])
    g.marks(yf, [(a, b, sync(a, FIXED)) for a, b in kept])
    l = next(l for l in kept if l[0] >= s1)
    g.arrow(early(mid(l)), ye, mid(l), yf)
    g.save(os.path.join(folder, "fix-missing-lines.svg"))

    g = Svg("4. Extra lines in the French")
    yr, ye, yf = g.rows(labels)
    g.marks(yr, [(early(a), early(b), SYNC) for a, b in kept])
    g.marks(ye, [(a, b, sync(a, OFF)) for a, b in lines])
    g.hatch(s0, s1, ye)
    g.text(X((s0 + s1) / 2), ye - 5, "extra lines in the French", MUTED, size=11, anchor="middle")
    g.marks(yf, [(early(a), early(b), sync(a, FIXED)) for a, b in kept])   # the extra lines are left out
    g.arrow(mid(l), ye, early(mid(l)), yf)
    g.save(os.path.join(folder, "fix-extra-lines.svg"))

    g = Svg("5. Wrong reference: the director's commentary", top=14)
    yr, ye, yf = g.rows(["Reference (EN)", "External (FR)", "Result (FR)"])
    comm = timeline(9, (2.0, 3.8), (1.5, 4.5))
    g.marks(yr, [(a, b, SYNC) for a, b in comm])
    g.marks(ye, [(a, b, OFF) for a, b in lines])
    g.marks(yf, [(a, b, OFF) for a, b in lines])       # unchanged
    c = comm[2]
    f = min(lines, key=lambda l: abs(l[0] - c[0]))
    x = X((mid(c) + mid(f)) / 2)
    g.text(x, yr - 5, "“This was the very first scene we filmed.”", size=11, anchor="middle")
    g.text(x, yr + H + GAP / 2 + 6, "≠", BAD, size=18, weight="700", anchor="middle")
    g.text(x, ye + H + 13, "“Il y a quelqu'un là-haut ?”", size=11, anchor="middle")
    g.save(os.path.join(folder, "fix-wrong-reference.svg"), "no line matches: the file is left as it is")


if __name__ == "__main__":
    main(sys.argv[1])
