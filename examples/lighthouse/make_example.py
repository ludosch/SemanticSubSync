"""Build the examples of the README from an original dialogue (written for this project, no film
extract): an English reference track that is in sync with the video, a downloaded French
subtitle that is not, and the French as it should be (truth.fr.srt, used only for scoring).

The English track is a hearing-impaired (SDH) one: it also has sound cues. The French has its own
line breaks, as real translations do: two short replies often share one cue, long sentences are
sometimes split, and durations follow the French reading speed.

Three situations, one folder each:
  1-missing-scene    The video is the extended edition. The French was timed on a TV broadcast:
                     25 fps instead of 23.976 (it runs 4.3 % fast) and without the extended scene.
  2-extra-scene      The other way round: the video is the theatrical cut, the French was timed on
                     the extended edition. Its lines from the extended scene have no place in the
                     video; every line after them is a minute late.
  3-wrong-reference  The French of situation 1, but the reference given is the director's
                     commentary track: it is in sync, yet says something else. The right answer is
                     to refuse.

Usage: python make_example.py   (writes the files next to this script)
"""
import os, random

# (English, French). "|" splits the French into two cues. EXTENDED brackets the extended scene.
EXTENDED = "--- extended scene ---"
DIALOGUE = [
    ("Is anyone up here?", "Il y a quelqu'un là-haut ?"),
    ("Careful, the third step is broken.", "Attention, la troisième marche est cassée."),
    ("Tom? I thought you left on the morning boat.", "Tom ? Je croyais que tu étais parti avec le bateau du matin."),
    ("The boat never came. The sea was too rough.", "Le bateau n'est jamais venu. La mer était trop forte."),
    ("So you climbed a lighthouse to wait?", "Alors tu as grimpé dans un phare pour attendre ?"),
    ("I came to fix the radio. Nobody else would.", "Je suis venu réparer la radio. Personne d'autre ne voulait le faire."),
    ("That radio hasn't worked in ten years.", "Cette radio ne marche plus depuis dix ans."),
    ("Eleven. My father was the last one to use it.", "Onze. Mon père a été le dernier à s'en servir."),
    ("I'm sorry. I didn't know.", "Je suis désolée. Je ne savais pas."),
    ("Hand me the small screwdriver, would you?", "Tu peux me passer le petit tournevis ?"),
    ("This one?", "Celui-là ?"),
    ("No, the one with the red handle.", "Non, celui avec le manche rouge."),
    ("Why does it matter so much to you?", "Pourquoi est-ce que ça compte autant pour toi ?"),
    ("Because the island has no other way to call for help.", "Parce que l'île n'a aucun autre moyen d'appeler à l'aide."),
    ("The phone line went down with the storm last night.", "La ligne téléphonique est tombée avec la tempête cette nuit."),
    ("And Mrs. Hale is still sick.", "Et Mme Hale est toujours malade."),
    ("The doctor is on the mainland until Friday.", "Le médecin est sur le continent jusqu'à vendredi."),
    ("Then let's make this thing talk.", "Alors faisons parler cet engin."),
    ("Do you know anything about old radios?", "Tu t'y connais en vieilles radios ?"),
    ("My grandmother built one from a kit when I was a child.", "Ma grand-mère en a monté une en kit quand j'étais petite."),
    ("Did it work?", "Elle marchait ?"),
    ("It caught a station from Norway, once.", "Elle a capté une station norvégienne, une fois."),
    ("Only once?", "Une seule fois ?"),
    ("She said once was enough to prove it could be done.", "Elle disait qu'une fois suffisait|pour prouver que c'était possible."),
    ("Look at this wire. It's completely burned.", "Regarde ce fil. Il est complètement brûlé."),
    ("Lightning, probably.", "La foudre, sans doute."),
    ("Is there any copper wire in the storeroom?", "Il y a du fil de cuivre dans la réserve ?"),
    ("There's a whole box under the stairs.", "Il y en a toute une boîte sous l'escalier."),
    ("I'll get it. Don't touch anything.", "Je vais la chercher. Ne touche à rien."),
    ("I wasn't planning to.", "Je n'en avais pas l'intention."),
    ("Here. Is this enough?", "Tiens. Ça suffira ?"),
    ("More than enough. Hold the lamp closer.", "Largement. Approche la lampe."),
    ("Your hands are shaking.", "Tes mains tremblent."),
    ("It's the cold. This room was never heated.", "C'est le froid. Cette pièce n'a jamais été chauffée."),
    ("Take my scarf.", "Prends mon écharpe."),
    ("Thank you.", "Merci."),
    ("Now we need power. The batteries are dead.", "Maintenant il faut du courant. Les batteries sont mortes."),
    ("What about the generator downstairs?", "Et le groupe électrogène en bas ?"),
    ("It needs fuel, and the fuel is in the boathouse.", "Il lui faut du carburant, et le carburant est dans le hangar à bateaux."),
    ("In this wind? You'd be blown into the sea.", "Avec ce vent ? Tu finirais à la mer."),
    ("Then we wait for the wind to drop.", "Alors on attend que le vent tombe."),
    ("Mrs. Hale can't wait that long.", "Mme Hale ne peut pas attendre aussi longtemps."),
    ("Is there a rope somewhere?", "Il y a une corde quelque part ?"),
    ("On the hook by the door. Why?", "Au crochet près de la porte. Pourquoi ?"),
    ("Tie it around my waist. I'm going.", "Attache-la autour de ma taille. J'y vais."),
    ("You're mad.", "Tu es folle."),
    ("Probably. Hold on tight.", "Sans doute. Tiens bon."),
    (EXTENDED, None),
    ("Mara! Can you hear me?", "Mara ! Tu m'entends ?"),
    ("I'm at the boathouse! The door is jammed!", "Je suis au hangar ! La porte est coincée !"),
    ("Kick it! Near the bottom!", "Donne un coup de pied ! En bas !"),
    ("It's open! I can see the cans!", "C'est ouvert ! Je vois les bidons !"),
    ("How many are full?", "Combien sont pleins ?"),
    ("Two! Maybe three!", "Deux ! Peut-être trois !"),
    ("Take two. Don't be greedy.", "Prends-en deux. Ne sois pas gourmande."),
    ("Pull me back slowly!", "Ramène-moi doucement !"),
    ("I've got you. Keep coming.", "Je te tiens. Continue."),
    ("I'm here. I'm here.", "Je suis là. Je suis là."),
    ("You're soaked to the bone.", "Tu es trempée jusqu'aux os."),
    ("But we have fuel.", "Mais on a du carburant."),
    (EXTENDED, None),
    ("Pour it in. Slowly, or it will flood.", "Verse-le. Doucement, sinon il va se noyer."),
    ("Now pull the cord.", "Maintenant tire sur le cordon."),
    ("Nothing.", "Rien."),
    ("Again. Harder this time.", "Encore. Plus fort cette fois."),
    ("It's running!", "Il tourne !"),
    ("The lights are on upstairs!", "Les lumières sont allumées en haut !"),
    ("Quick, before it stalls.", "Vite, avant qu'il cale."),
    ("Switch it on. The big dial on the left.", "Allume-la. Le gros bouton à gauche."),
    ("Only static.", "Que des grésillements."),
    ("Turn it slowly. The coast guard is near the end.", "Tourne doucement. Les garde-côtes sont vers la fin."),
    ("Wait. Did you hear that?", "Attends. Tu as entendu ?"),
    ("A voice. Go back a little.", "Une voix. Reviens un peu en arrière."),
    ("This is the coast guard. Please identify yourself.", "Ici les garde-côtes. Veuillez vous identifier."),
    ("This is Gull Island lighthouse. We need a doctor.", "Ici le phare de l'île aux Mouettes. Nous avons besoin d'un médecin."),
    ("An old woman is sick, and the phone lines are down.", "Une vieille dame est malade,|et les lignes téléphoniques sont coupées."),
    ("Understood, Gull Island. How bad is she?", "Bien reçu, île aux Mouettes. Dans quel état est-elle ?"),
    ("High fever since yesterday. She can't stand.", "Forte fièvre depuis hier. Elle ne tient pas debout."),
    ("We'll send a boat as soon as the wind allows.", "Nous enverrons un bateau dès que le vent le permettra."),
    ("Probably at dawn. Keep this channel open.", "Sans doute à l'aube. Restez sur cette fréquence."),
    ("We will. Thank you.", "Entendu. Merci."),
    ("Your father would have been proud.", "Ton père aurait été fier."),
    ("He would have said I took too long.", "Il aurait dit que j'avais mis trop de temps."),
    ("Eleven years is a long time.", "Onze ans, c'est long."),
    ("Not for the radio. For coming back up here.", "Pas pour la radio. Pour remonter ici."),
    ("Why did you stay away?", "Pourquoi tu n'es pas revenu ?"),
    ("Every step of this staircase reminds me of him.", "Chaque marche de cet escalier me fait penser à lui."),
    ("Even the broken one?", "Même celle qui est cassée ?"),
    ("Especially the broken one. He never fixed it.", "Surtout celle qui est cassée. Il ne l'a jamais réparée."),
    ("Maybe we should leave it like that.", "On devrait peut-être la laisser comme ça."),
    ("Maybe we should.", "Peut-être bien."),
    ("Look. The sky is clearing in the west.", "Regarde. Le ciel se dégage à l'ouest."),
    ("The boat will come.", "Le bateau viendra."),
    ("I'll go and tell Mrs. Hale.", "Je vais prévenir Mme Hale."),
    ("Take the lamp. The path is slippery.", "Prends la lampe. Le chemin est glissant."),
    ("And you?", "Et toi ?"),
    ("Someone has to stay by the radio.", "Il faut bien que quelqu'un reste près de la radio."),
    ("Then I'll bring you some soup.", "Alors je t'apporterai de la soupe."),
    ("Mara?", "Mara ?"),
    ("Yes?", "Oui ?"),
    ("Thank you for staying.", "Merci d'être restée."),
]

PAL = 25 / 23.976          # the TV broadcast runs 4.27 % faster
HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS = ["[thunder]", "[wind howling]", "[waves crashing]", "[door creaks]", "[radio static]"]
COMMENTARY = [
    "We shot this whole sequence in two nights.", "The lighthouse is a set, believe it or not.",
    "The rain machine broke down on the first take.", "She did all her own climbing here.",
    "This was the very first scene we filmed.", "The radio is a real one from the fifties.",
    "We found it in a market in the north.", "Our sound team recorded the wind on location.",
    "Watch the light in the background here.", "That cut was suggested by our editor.",
    "We had a much longer version of this moment.", "The script changed a lot during rehearsals.",
    "I love the silence just before the line.", "The costume department made that scarf.",
    "Here we lost the light, so we came back the next day.", "The boat was borrowed from a fisherman.",
    "Most of the storm was added in post-production.", "This shot took eleven attempts.",
    "The music was written after the edit.", "We wanted the ending to feel quiet.",
]


def fmt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write(folder, name, cues):
    os.makedirs(os.path.join(HERE, folder), exist_ok=True)
    with open(os.path.join(HERE, folder, name), "w", encoding="utf-8", newline="\n") as f:
        for i, (s, e, x) in enumerate(sorted(cues), 1):
            f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{x}\n\n")


def extended_edition(rng):
    """Every cue on the timeline of the extended edition: (english, french, scene_start, scene_end).
    Cues are (start, end, text, in_extended_scene)."""
    t, en, lines, bounds, in_scene = 20.0, [], [], [], False
    for eng, fre in DIALOGUE:
        if eng == EXTENDED:
            if not in_scene:
                t += 6.0
                bounds.append(t)
            else:
                bounds.append(t + 1.0)
                t += 6.0
            in_scene = not in_scene
            continue
        dur = 1.2 + 0.055 * len(eng)
        en.append((t, t + dur, eng, in_scene))
        lines.append((t, dur, fre, in_scene))
        gap = rng.uniform(1.0, 4.5)
        if gap > 3.5 and rng.random() < 0.3:
            en.append((t + dur + 0.6, t + dur + 1.8, rng.choice(SOUNDS), in_scene))
        t += dur + gap
    fr, k = [], 0
    while k < len(lines):
        start, dur, fre, scene = lines[k]
        start += rng.uniform(-0.15, 0.15)            # translators' natural timing bias
        nxt = lines[k + 1] if k + 1 < len(lines) else None
        if (nxt and nxt[3] == scene and "|" not in fre + nxt[2] and len(fre) + len(nxt[2]) < 60
                and nxt[0] - start < 4.0 and rng.random() < 0.7):
            text, end = f"- {fre}\n- {nxt[2]}", nxt[0] + nxt[1]       # two replies, one cue
            k += 2
        else:
            text, end = fre, None
            k += 1
        parts = text.split("|")
        for n, part in enumerate(parts):
            d = 1.0 + 0.05 * len(part)                 # French reading speed, not the English duration
            s0 = start + n * (end - start if end else dur) / len(parts)
            fr.append([s0, end if end and n == len(parts) - 1 else s0 + d, part.strip(), scene])
    fr.sort()
    for n in range(len(fr) - 1):                       # no overlap
        fr[n][1] = min(fr[n][1], fr[n + 1][0] - 0.08)
    return en, [tuple(c) for c in fr], bounds[0], bounds[1], t


def main():
    rng = random.Random(7)
    en, fr, s0, s1, end = extended_edition(rng)
    cut = s1 - s0

    def theatrical(t):      # extended-edition time -> theatrical-cut time (scene removed)
        return t - cut if t >= s1 else t

    def broadcast(t):       # extended-edition time -> TV broadcast time (scene removed, 25 fps)
        return theatrical(t) / PAL

    plain = lambda cues: [(s, e, x) for s, e, x, _ in cues]
    kept = lambda cues: [c for c in cues if not c[3]]
    move = lambda cues, f: [(f(s), f(e), x) for s, e, x, _ in cues]

    write("1-missing-scene", "reference.en.srt", plain(en))
    write("1-missing-scene", "downloaded.fr.srt", move(kept(fr), broadcast))
    write("1-missing-scene", "truth.fr.srt", plain(kept(fr)))

    write("2-extra-scene", "reference.en.srt", move(kept(en), theatrical))
    write("2-extra-scene", "downloaded.fr.srt", plain(fr))
    write("2-extra-scene", "truth.fr.srt", move(kept(fr), theatrical))   # scene lines: no true place

    crng, comm, t = random.Random(11), [], 25.0
    while t < end:
        x = COMMENTARY[len(comm) % len(COMMENTARY)]
        comm.append((t, t + 1.2 + 0.055 * len(x), x))
        t += 1.2 + 0.055 * len(x) + crng.uniform(1.0, 6.0)
    write("3-wrong-reference", "reference.commentary.srt", comm)
    write("3-wrong-reference", "downloaded.fr.srt", move(kept(fr), broadcast))

    print(f"{len(en)} English cues, {len(fr)} French cues, extended edition {fmt(end)}, "
          f"extended scene {fmt(s0)} -> {fmt(s1)} ({cut:.0f} s)")


if __name__ == "__main__":
    main()
