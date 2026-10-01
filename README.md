# SemanticSubSync

Recale un sous-titre (ex. FR téléchargé) sur un sous-titre de référence dans une autre langue
(ex. la piste VO intégrée à la vidéo) en appariant les répliques **par leur sens**, pas par la
forme du timing ni par l'audio.

1. nettoyage + embedding de chaque cue (MiniLM multilingue, local, déterministe) ;
2. candidats : pour chaque cue cible, les 3 meilleures cues de référence (seules ou fusionnées par 2) ;
3. plus longue chaîne croissante pondérée → ancres monotones, filtre de voisinage sur les offsets ;
4. dérive de framerate globale (ratios fixes 23.976/24/25/29.97/30) puis segments à offset constant
   (coupes, scènes ajoutées/retirées) ;
5. garde-fous : couverture < 0,25 → refus (référence qui ne dit pas la même chose, ex. commentaire) ;
   correction < 0,5 s partout → fichier laissé tel quel (biais naturel FR/VO).

## Utilisation

```bash
semantic-subsync cible.fr.srt reference.en.srt sortie.srt     # stats JSON sur stdout
```

Le worker (`semantic-subsync-worker run`) traite la file `/data/.semsync/queue` alimentée par le
post-traitement Bazarr (`bazarr/enqueue.py`) et écrit `<vidéo>.semsync.<langue>.srt` à côté de la vidéo.
Variables : `SEMSYNC_MODEL_DIR` (copie int8 du modèle), `SEMSYNC_DIR`, `SEMSYNC_CACHE`.

## Tests

```bash
mise x -- uv run pytest                      # rapide, sans modèle (embedder factice déterministe)
SEMSYNC_TEST_MODEL=1 SEMSYNC_MODEL_DIR=~/subsync-lab/models/minilm-int8g \
  mise x -- uv run --extra model pytest -m model   # bout en bout avec le vrai modèle
```

Les tests unitaires utilisent des dialogues **synthétiques** (`tests/synth.py`) : chaque réplique
porte un « concept » `k17` que l'embedder factice transforme en vecteur fixe ; FR et VO d'un même
concept ont un cosinus ~0,9, deux concepts différents ~0. On teste ainsi l'algorithme
indépendamment du modèle, avec chaque déformation du bench (offset, fps, coupes, scènes en plus,
phrases découpées différemment, répliques courtes répétées, référence sans rapport).
Aucun extrait de film n'est versionné.

Le bench sur vrais films (15 vidéos × 7 déformations) reste dans le labo WSL `~/subsync-lab`.
