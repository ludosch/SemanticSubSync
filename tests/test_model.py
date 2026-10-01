"""End-to-end with the real multilingual model (skipped by default).

Run: SEMSYNC_TEST_MODEL=1 [SEMSYNC_MODEL_DIR=.../minilm-int8g] uv run --extra model pytest -m model
The dialogue below was written for this test (no film extract).
"""
import os

import numpy as np
import pytest

from semantic_subsync import core

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not os.environ.get("SEMSYNC_TEST_MODEL"), reason="set SEMSYNC_TEST_MODEL=1"),
]

PAIRS = [
    ("Where did you put the car keys?", "Où as-tu mis les clés de la voiture ?"),
    ("They're on the kitchen table, next to the mail.", "Elles sont sur la table de la cuisine, à côté du courrier."),
    ("We're going to be late for the meeting again.", "On va encore être en retard à la réunion."),
    ("Relax, the traffic is light on Sundays.", "Détends-toi, il y a peu de circulation le dimanche."),
    ("Today is Monday.", "On est lundi."),
    ("Then we really are in trouble.", "Alors on a vraiment des ennuis."),
    ("Did you call your sister back?", "Tu as rappelé ta sœur ?"),
    ("I tried twice, she never answers her phone.", "J'ai essayé deux fois, elle ne répond jamais au téléphone."),
    ("Maybe she's still angry about the wedding.", "Elle est peut-être encore fâchée à cause du mariage."),
    ("That was three years ago!", "C'était il y a trois ans !"),
    ("Some people hold a grudge for a long time.", "Certaines personnes sont rancunières longtemps."),
    ("The bridge is closed because of the storm.", "Le pont est fermé à cause de la tempête."),
    ("We'll have to take the road through the forest.", "Il faudra prendre la route qui traverse la forêt."),
    ("I hate driving in the dark.", "Je déteste conduire dans le noir."),
    ("I can drive if you want.", "Je peux conduire si tu veux."),
    ("You don't even have your license.", "Tu n'as même pas ton permis."),
    ("I passed the exam last week, remember?", "J'ai réussi l'examen la semaine dernière, tu te souviens ?"),
    ("The doctor said the results were normal.", "Le médecin a dit que les résultats étaient normaux."),
    ("So why are you still worried?", "Alors pourquoi tu t'inquiètes encore ?"),
    ("Because he didn't look me in the eyes.", "Parce qu'il ne m'a pas regardé dans les yeux."),
    ("The restaurant downstairs has a new chef.", "Le restaurant d'en bas a un nouveau chef."),
    ("His soup was much too salty.", "Sa soupe était beaucoup trop salée."),
    ("I'll write a review tonight.", "J'écrirai un avis ce soir."),
    ("Please don't, the owner is my cousin.", "S'il te plaît, non, le propriétaire est mon cousin."),
    ("The train to Lyon leaves at seven.", "Le train pour Lyon part à sept heures."),
    ("Buy the tickets online, it's cheaper.", "Achète les billets en ligne, c'est moins cher."),
    ("My grandmother kept every letter he sent her.", "Ma grand-mère a gardé chaque lettre qu'il lui a envoyée."),
    ("They are in a box in the attic.", "Elles sont dans une boîte au grenier."),
    ("Someone broke the window of the garage.", "Quelqu'un a cassé la fenêtre du garage."),
    ("The neighbours saw two kids running away.", "Les voisins ont vu deux gamins s'enfuir."),
]


def timeline(seed=0):
    rng = np.random.default_rng(seed)
    t, out = 20.0, []
    for _ in PAIRS:
        d = float(rng.uniform(1.5, 4.0)); out.append((t, t + d)); t += d + float(rng.uniform(0.5, 8))
    return out


@pytest.mark.parametrize("shift", [4.0, -6.5])
def test_real_model_constant_offset(shift):
    tl = timeline()
    ref = [[s, e, en] for (s, e), (en, _) in zip(tl, PAIRS)]
    tgt = [[s + shift, e + shift, fr] for (s, e), (_, fr) in zip(tl, PAIRS)]
    out, st = core.sync(tgt, ref)
    assert out is not None, st
    assert st["coverage"] >= 0.8, st
    assert all(abs(a[0] - r[0]) < 0.3 for a, r in zip(out, ref)), st


def test_real_model_translation_pairs_score_above_min_sim():
    v_en = core.embed([en for en, _ in PAIRS]); v_fr = core.embed([fr for _, fr in PAIRS])
    sims = np.sum(v_en * v_fr, axis=1)
    assert sims.min() >= core.P["min_sim"], sorted(zip(sims.round(2), (en for en, _ in PAIRS)))[:3]
