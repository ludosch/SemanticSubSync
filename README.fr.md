[English](README.md) | **Français**

# SemanticSubSync

Recale un sous-titre (par exemple un sous-titre français téléchargé) sur un sous-titre de référence
dans une autre langue (par exemple la piste VO intégrée à la vidéo) en appariant les répliques
**par leur sens**, et non par la forme du timing ni par l'audio.

## Fonctionnement

1. Chaque réplique est nettoyée (balises, sous-titres pour sourds, noms de personnages) puis
   transformée en vecteur par un modèle de phrases multilingue (MiniLM, local et déterministe).
2. Candidats : pour chaque réplique cible, les 3 meilleures répliques de référence, seules ou fusionnées par deux.
3. Une plus longue chaîne croissante pondérée donne des ancres monotones ; un filtre de voisinage
   écarte les ancres dont le décalage contredit celui de leurs voisines.
4. Une dérive de framerate globale est choisie parmi des ratios fixes (23,976 / 24 / 25 / 29,97 / 30),
   puis la chronologie est découpée en segments à décalage constant (coupes, scènes ajoutées ou retirées).
5. Garde-fous :
   - couverture inférieure à 0,25 : refus (la référence ne dit pas la même chose, par exemple une piste commentaire) ;
   - toutes les corrections inférieures à 0,5 s : le fichier n'est pas modifié (biais naturel entre deux langues) ;
   - un segment de moins de 60 s est un désaccord local, pas une coupe.

## Utilisation

```bash
semantic-subsync cible.fr.srt reference.en.srt sortie.srt     # statistiques JSON sur stdout
```

### Worker

`semantic-subsync-worker run` traite le dossier `/data/.semsync/queue`, alimenté par le
post-traitement personnalisé de Bazarr (`bazarr/enqueue.py`). Pour chaque sous-titre téléchargé, il
extrait les sous-titres texte intégrés à la vidéo (hors pistes forcées), prend le plus complet comme
référence et écrit `<vidéo>.semsync.<langue>.srt` à côté de la vidéo quand une correction est
nécessaire. Le fichier téléchargé n'est jamais modifié.

| Variable | Rôle |
|---|---|
| `SEMSYNC_MODEL_DIR` | Copie locale du modèle (par exemple la version quantifiée int8) |
| `SEMSYNC_DIR` | Dossier de la file, des tâches en échec et du journal (par défaut `/data/.semsync`) |
| `SEMSYNC_CACHE` | Cache disque facultatif des vecteurs |

## Développement

```bash
mise x -- uv run pytest                                    # rapide, sans modèle
SEMSYNC_TEST_MODEL=1 SEMSYNC_MODEL_DIR=/chemin/vers/minilm-int8g \
  mise x -- uv run --extra model pytest -m model           # de bout en bout avec le vrai modèle
```

Les tests unitaires tournent sur des dialogues **synthétiques** (`tests/synth.py`). Chaque réplique
porte un « concept » comme `k17`, qu'un faux modèle déterministe transforme en vecteur fixe : les
deux langues d'un même concept se ressemblent à ~0,9, deux répliques sans rapport à ~0,2-0,3. On teste
ainsi l'algorithme indépendamment du modèle, sur chaque déformation du bench : décalage, framerate,
coupes, scènes en plus, phrases découpées différemment, répliques courtes répétées, référence sans
rapport. Aucun extrait de film n'est versionné.
