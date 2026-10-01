[English](README.md) | **Français**

# SemanticSubSync

Recale un sous-titre en comparant **ce qui est dit**, réplique par réplique, avec un sous-titre
déjà synchronisé, même quand les deux sont dans des langues différentes.

```bash
semantic-subsync Film.fr.srt Film.mkv     # référence : le sous-titre intégré à la vidéo
```

## En bref

Un exemple, construit pour ce projet ([`examples/lighthouse`](examples/lighthouse), en
anglais) : les mêmes fichiers de sous-titres confiés à chaque outil, avec un sous-titre de
référence calé sur la vidéo.

| Outil | Scène absente + 25 i/s | Scène en trop | Mauvaise référence (piste commentaire) |
|---|---|---|---|
| alass 2.0.0 | ✅ 100 % | ❌ 87 %, jusqu'à 17 s d'écart | ❌ déplace des répliques de 96 s au plus |
| ffsubsync 0.5.1 (avec ou sans `--split-penalty`) | ❌ 52 %, 70 à 81 s d'écart | ❌ 52 %, 61 à 70 s d'écart | ❌ déplace des répliques de 96 s au plus |
| LAPSE 2.2.3 | ❌ 52 %, 70 s d'écart, se dit « solid » | ❌ 52 %, 70 s d'écart, se dit « solid » | ❌ déplace des répliques de 75 s au plus, se dit « solid » |
| **SemanticSubSync** | ✅ **100 %** | ✅ **100 %** | ✅ **refuse, fichier intact** |

Part des répliques qui finissent à moins de 300 ms de leur vraie position, sur cet exemple
seulement : les chiffres décrivent ces fichiers, pas toutes les vidéos. Les détails et les
scripts pour le rejouer sont dans le dossier de l'exemple.

## Pourquoi ce projet existe

Les sous-titres téléchargés pour un film ou un épisode sont souvent faits pour une autre version
de la même vidéo : une autre cadence d'images (23,976 ou 25 i/s), une scène ajoutée ou coupée, un
générique différent. Résultat : un sous-titre qui dérive, ou qui est juste pendant 20 minutes
puis décalé de 4 secondes.

Les outils habituels se calent sur l'**audio** (ffsubsync, alass en mode audio) ou sur le
**rythme** d'un autre sous-titre (alass, ffsubsync avec un sous-titre de référence). Dans nos
essais :

- ils ont souvent échoué sur les coupes et les scènes ajoutées ;
- ils **signalaient rarement leurs échecs** : un sous-titre décalé d'une minute ou plus
  pouvait être annoncé comme réussi.

Or la plupart des vidéos contiennent déjà un sous-titre parfaitement calé : la piste intégrée,
souvent en version originale. SemanticSubSync s'en sert comme référence et apparie les
répliques **par leur sens** grâce à un petit modèle de phrases multilingue : « Where did you put
the keys? » et « Où as-tu mis les clés ? » sont reconnues comme la même réplique. Quand trop peu
de répliques correspondent (mauvaise référence, piste commentaire, montage différent), il
**refuse** et ne touche pas au fichier.

## Exemple

Un sous-titre français a été téléchargé pour un épisode de 10 minutes (un
[dialogue original](examples/lighthouse) écrit pour ce projet). Il a été calé sur une diffusion
TV : 25 i/s au lieu des 23,976 de la vidéo, et une scène de 70 secondes en moins. La vidéo
contient un sous-titre anglais, bien calé.

```console
$ semantic-subsync downloaded.fr.srt reference.en.srt
corrected -> downloaded.fr.synced.srt (2 segment(s), largest shift +90.69 s) [reference: reference.en.srt]
```

| Réplique | Dite dans la vidéo à | Avant | Après |
|---|---|---|---|
| Il y a quelqu'un là-haut ? | 0:20.0 | 0:19.2 (0,8 s d'avance) | 0:20.0 ✅ |
| Au crochet près de la porte. Pourquoi ? | 4:18.9 | 4:08.3 (10,6 s d'avance) | 4:18.9 ✅ |
| *la scène absente* | de 4:45 à 5:55 | | |
| Encore. Plus fort cette fois. | 6:18.7 | 4:55.9 (82,8 s d'avance) | 6:18.7 ✅ |
| Oui ? | 9:26.5 | 7:56.0 (90,5 s d'avance) | 9:26.4 ✅ |

## Cadre d'utilisation

**Adapté**
- La vidéo contient un sous-titre intégré au format **texte** (SRT, ASS, WebVTT, mov_text),
  dans n'importe quelle langue.
- Ou vous avez un autre fichier de sous-titres dont vous savez qu'il est calé sur votre vidéo.
- Le sous-titre à corriger est un `.srt` classique.

**Pas adapté**
- Aucune référence : cet outil n'écoute pas l'audio. Utilisez ffsubsync ou alass.
- Sous-titres intégrés uniquement en image (PGS, VobSub) : il faudrait de l'OCR, hors du
  périmètre.
- Une référence qui vient d'une autre version que votre vidéo (par exemple un sous-titre
  anglais téléchargé) : elle a les mêmes défauts de timing que le fichier à corriger.

## Fonctionnement

1. Chaque réplique est nettoyée (balises, annotations pour sourds et malentendants, noms des
   personnages), puis transformée en vecteur par un modèle de phrases multilingue
   ([paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2),
   exécuté en local sur le processeur via [fastembed](https://github.com/qdrant/fastembed)).
2. Chaque réplique, seule ou fusionnée avec la suivante (une phrase coupée en deux), est
   comparée aux répliques de la référence, seules ou fusionnées par deux. Les 3 meilleures
   candidates au-dessus d'un seuil de similarité sont gardées.
3. Une plus longue chaîne croissante pondérée garde les candidates qui respectent l'ordre du
   dialogue. Les ancres dont le décalage contredit celui de leurs voisines sont écartées.
4. Une seule cadence d'images est retenue pour tout le fichier parmi les valeurs standard
   (23,976 / 24 / 25 / 29,97 / 30). La chronologie est ensuite découpée en segments à décalage
   constant, ce qui absorbe les coupes et les scènes ajoutées.
5. Garde-fous :
   - moins de 25 % des répliques ancrées : **refusé**, la référence ne dit pas la même chose ;
   - toutes les corrections sous 0,5 s : le fichier **n'est pas modifié** (c'est l'écart naturel
     entre deux langues, pas un problème de synchro) ;
   - les segments de moins de 60 s sont traités comme des erreurs locales, pas comme des coupes.

Aucun service d'IA, aucun accès réseau une fois le modèle téléchargé, et la même entrée donne
toujours le même résultat.

## Résultats

Ces chiffres viennent de la vidéothèque de l'auteur. Ils montrent comment l'outil s'y est
comporté, pas ce qu'il fera sur n'importe quelle vidéo : avec d'autres fichiers, d'autres
langues ou un autre matériel, ils seront différents.

Mesurés sur 15 vidéos qui contiennent à la fois un sous-titre français et un sous-titre en
version originale intégrés. La piste française est déformée de 7 façons réalistes (décalage
constant, changement de cadence dans les deux sens, 3 coupes, scènes ajoutées et retirées,
coupes avec changement de cadence), puis recalée sur la piste en version originale. Un cas est
réussi quand au moins 95 % des répliques commencent à moins de 300 ms de leur vraie position.

| Outil (sous-titre intégré comme référence) | Cas réussis | Échecs graves (< 80 % des répliques) |
|---|---|---|
| **SemanticSubSync** (0.7) | **104 / 105** | 1 |
| alass | 84 / 105 | 10, aucun signalé |

Sur 11 cas invalides (piste commentaire ou piste partielle prise comme référence),
SemanticSubSync a refusé ceux où il aurait fait des dégâts : la couverture des répliques
ancrées y était de 0,07 au plus, contre 0,32 à 0,82 sur les cas valides. Le seuil de refus
(0,25) se situe dans cet écart ; une autre vidéothèque peut demander une autre valeur
(`--min-coverage`).

Pour comparaison, les outils basés sur l'audio seul, sur le même genre de déformations :
ffsubsync 29 / 44, alass 24 / 44, subaligner 2 / 44, sans aucun signal de confiance en cas
d'échec.

Vitesse sur le NAS de l'auteur (Celeron J4025, 2 cœurs), avec le modèle int8 (voir
[Modèle](#modèle)) : environ 85 s et 590 Mo de RAM au maximum pour un film complet. Elle dépend
du matériel et du nombre de répliques.

Ce n'est pas un benchmark publié. La suite de tests reproduit chaque déformation sur des
dialogues synthétiques (voir [Développement](#développement)).

## Installation

Python 3.11 ou plus récent, sur Linux, macOS ou Windows 64 bits. ffmpeg / ffprobe ne sont
nécessaires que si la référence est une vidéo. L'ARM64 (matériel de type
Raspberry Pi) est couvert par la CI : tests, les deux modèles, l'exemple et l'image Docker
tournent sur une machine ARM64 à chaque commit, avec les mêmes résultats qu'en x86-64. Un
Raspberry Pi doit avoir un système 64 bits : le moteur ONNX n'existe pas en ARM 32 bits.

```bash
pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync"
```

Le modèle (environ 240 Mo) est téléchargé depuis Hugging Face à la première utilisation.

Chaque [release](https://github.com/ludosch/SemanticSubSync/releases) contient aussi le paquet
Python et une image Docker pour amd64 et arm64, à charger avec `docker load` :

```bash
gh release download v0.9.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"
docker load -i semantic-subsync-0.9.0-docker-amd64.tar.gz
```

## Utilisation

```bash
semantic-subsync SOUS-TITRE REFERENCE [-o SORTIE] [--track INDEX] [--min-coverage X] [--json]
```

- `REFERENCE` est un `.srt` calé sur la vidéo, ou la vidéo elle-même. Avec une vidéo, le
  sous-titre texte intégré le plus complet est utilisé, quelle que soit sa langue, hors pistes
  forcées. `--track` choisit une piste par son index ffprobe.
- Le sous-titre d'entrée n'est jamais modifié. Le résultat va par défaut dans
  `SOUS-TITRE.synced.srt`.
- Déjà synchronisé : rien n'est écrit.
- Code de sortie : `0` corrigé ou déjà synchronisé, `1` refusé, `2` erreur.
- `--json` affiche la décision et les statistiques (couverture, segments, décalages).

Depuis Python :

```python
from semantic_subsync import core, media

status, cues, stats = core.resync(media.read_srt("Film.fr.srt"), core.parse("Film.en.srt"))
if status == "corrected":
    core.write("Film.fr.synced.srt", cues)
```

## Intégrations

Le moteur ne connaît ni serveur multimédia ni gestionnaire de sous-titres. Les intégrations sont
dans [`integrations/`](integrations) :

- [**Bazarr**](integrations/bazarr/README.md) (en anglais) : chaque sous-titre téléchargé est
  vérifié automatiquement par un worker en arrière-plan, et une copie corrigée est écrite à côté
  de la vidéo si besoin.

## Modèle

| Variable | Rôle |
|---|---|
| `SEMSYNC_MODEL_DIR` | Dossier d'une copie locale du modèle, par exemple la version int8 produite par [`tools/quantize_model.py`](tools/quantize_model.py) : 112 Mo ; sur le NAS de l'auteur, il était environ 40 % plus rapide et utilisait 2,5 fois moins de RAM, avec les mêmes résultats |
| `SEMSYNC_CACHE` | Dossier facultatif où les vecteurs sont mis en cache sur disque |

Le modèle est publié par [sentence-transformers](https://www.sbert.net/) sous licence Apache 2.0.

## Développement

```bash
mise x -- uv run pytest                                    # rapide, sans le modèle
SEMSYNC_TEST_MODEL=1 mise x -- uv run --extra model pytest -m model   # de bout en bout avec le vrai modèle
```

Les tests unitaires tournent sur des dialogues **synthétiques** (`tests/synth.py`). Chaque
réplique porte un jeton de « concept » comme `k17`, qu'un faux modèle déterministe transforme
en vecteur fixe : les deux langues d'un même concept se ressemblent à environ 0,9, deux
répliques sans rapport à environ 0,1, comme avec le vrai modèle. On teste ainsi l'algorithme
indépendamment du modèle, sur chaque déformation du benchmark. Aucun extrait de film n'est
stocké dans le dépôt.

Voir [CONTRIBUTING.md](CONTRIBUTING.md) (en anglais) et le [journal des versions](CHANGELOG.md).

## Remerciements

- [ffsubsync](https://github.com/smacke/ffsubsync) et [alass](https://github.com/kaegi/alass),
  les outils de référence auxquels ce projet a été comparé.
- [DuoSubs](https://github.com/CK-Explorer/DuoSubs), dont l'approche par phrases a inspiré
  l'appariement des phrases coupées en deux.

## Licence

[MIT](LICENSE)
