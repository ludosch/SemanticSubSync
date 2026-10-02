[English](README.md) | **Français**

# SemanticSubSync

Recale un sous-titre téléchargé dans les cas où les outils de synchronisation habituels
échouent : une scène ajoutée ou coupée, une autre cadence d'images, ou les deux. Il compare
**ce qui est dit**, réplique par réplique, avec un sous-titre déjà synchronisé (en général celui
intégré à la vidéo), même dans une autre langue. Quand cette référence ne correspond pas, il le
dit et laisse le fichier tel quel au lieu d'annoncer une réussite.

```bash
semantic-subsync Film.fr.srt Film.mkv     # référence : le sous-titre intégré à la vidéo
```

Dans Jellyfin, un [plugin](integrations/jellyfin/README.md) (en anglais) le fait depuis le menu
d'un film ou d'un épisode, et automatiquement pour les nouveaux sous-titres des bibliothèques
choisies.

## Là où les autres outils échouent

Un exemple, construit pour ce projet ([`examples/lighthouse`](examples/lighthouse), en
anglais). Chaque outil reçoit les mêmes fichiers de sous-titres, avec un sous-titre de référence
calé sur la vidéo.

| Outil | Scène absente + 25 i/s | Scène en trop | Mauvaise référence (piste commentaire) |
|---|---|---|---|
| alass 2.0.0 | ✅ synchronisé | ❌ une partie du fichier décalée | ❌ réécrit le fichier au lieu de le laisser tel quel |
| ffsubsync 0.5.1 (avec ou sans `--split-penalty`) | ❌ la moitié du fichier décalée | ❌ la moitié du fichier décalée | ❌ réécrit le fichier au lieu de le laisser tel quel |
| LAPSE 2.2.3 | ❌ la moitié du fichier décalée, se dit « solid » | ❌ la moitié du fichier décalée, se dit « solid » | ❌ réécrit le fichier et se dit « solid » |
| **SemanticSubSync** | ✅ **synchronisé** | ✅ **synchronisé** | ✅ **ignoré (pas sûr), fichier intact** |

Cela ne décrit que ces fichiers, pas toutes les vidéos. Les chiffres, les détails et les scripts
pour le rejouer sont dans le dossier de l'exemple.

Deux échecs reviennent dans cet exemple et dans le [benchmark](#résultats) :

- **Coupes et scènes ajoutées.** Se caler sur l'audio ou sur le rythme d'un autre sous-titre
  règle un décalage ou un changement de cadence ; après une scène absente ou ajoutée, une partie
  du fichier reste souvent décalée. SemanticSubSync sait quelle réplique correspond à laquelle :
  il trouve où est la coupe et déplace chaque partie de son propre décalage.
- **Échecs annoncés comme réussis.** Un fichier resté décalé d'une minute, ou recalé sur une
  piste commentaire, ressort comme une réussite. SemanticSubSync compte les répliques qu'il a pu
  apparier : sous 25 %, il s'arrête (**pas sûr**) et n'écrit rien.

Sur le benchmark (105 pistes intégrées déformées), SemanticSubSync a réussi 104 cas ; alass en a
réussi 84, avec 10 échecs graves dont aucun signalé.

## Pourquoi ce projet existe

Les sous-titres téléchargés pour un film ou un épisode sont souvent faits pour une autre version
de la même vidéo :

- une autre cadence d'images (23,976 ou 25 i/s) ;
- une scène ajoutée ou coupée ;
- un générique différent.

Résultat : un sous-titre qui dérive, ou qui est juste pendant 20 minutes puis décalé de
4 secondes.

Or la plupart des vidéos contiennent déjà un sous-titre parfaitement calé : la piste intégrée,
souvent en version originale.

SemanticSubSync s'en sert comme référence et apparie les répliques **par leur sens**, grâce à
un petit modèle de phrases multilingue : « Where did you put the keys? » et « Où as-tu mis les
clés ? » sont reconnues comme la même réplique.

Quand trop peu de répliques correspondent (mauvaise référence, piste commentaire, montage
différent), il **ignore le fichier (pas sûr)** et n'y touche pas.

## Exemple

Un sous-titre français a été téléchargé pour un épisode de 10 minutes (un
[dialogue original](examples/lighthouse) écrit pour ce projet).

- Il a été calé sur une diffusion TV : 25 i/s au lieu des 23,976 de la vidéo, et une scène de
  70 secondes en moins.
- La vidéo contient un sous-titre anglais, bien calé.

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

### Adapté

- La vidéo contient un sous-titre intégré au format **texte** (SRT, ASS, WebVTT, mov_text),
  dans n'importe quelle langue.
- Ou vous avez un autre fichier de sous-titres dont vous savez qu'il est calé sur votre vidéo.
- Le sous-titre à corriger est un `.srt` classique.

### Pas adapté

- Aucune référence : cet outil n'écoute pas l'audio. Utilisez ffsubsync ou alass.
- Sous-titres intégrés uniquement en image (PGS, VobSub) : il faudrait de l'OCR, hors du
  périmètre.
- Une référence qui vient d'une autre version que votre vidéo (par exemple un sous-titre
  anglais téléchargé) : elle a les mêmes défauts de timing que le fichier à corriger.

## Fonctionnement

Ce qu'il corrige, un cas à la fois, sur 40 secondes de dialogue. C'est un schéma : un trait par
réplique, en bleu ce qui est calé, en orange ce qui est décalé, en vert ce qui est corrigé.

![Décalage : chaque réplique française a 3 s de retard et est avancée de 3 s](docs/fix-offset.svg)

![Cadence d'images : le français dérive de plus en plus et est réétiré](docs/fix-frame-rate.svg)

![Répliques manquantes dans le français : le français après le trou est décalé plus tard](docs/fix-missing-lines.svg)

![Répliques en trop dans le français : le français qui suit est avancé et les répliques en trop sont retirées](docs/fix-extra-lines.svg)

![Mauvaise référence : aucune réplique du commentaire ne correspond au français, le fichier est laissé tel quel](docs/fix-wrong-reference.svg)

### Étapes

1. **Vecteurs.** Chaque réplique est nettoyée (balises, annotations pour sourds et
   malentendants, noms des personnages), puis transformée en vecteur par un modèle de phrases
   multilingue, exécuté en local sur le processeur (voir [Modèle](#modèle)).
2. **Candidates.** Chaque réplique, seule ou fusionnée avec la suivante (une phrase coupée en
   deux), est comparée aux répliques de la référence, seules ou fusionnées par deux. Les
   3 meilleures candidates au-dessus d'un seuil de similarité sont gardées.
3. **Ancres.** Une plus longue chaîne croissante pondérée garde les candidates qui respectent
   l'ordre du dialogue. Les ancres dont le décalage contredit celui de leurs voisines sont
   écartées.
4. **Chronologie.** Une seule cadence d'images est retenue pour tout le fichier parmi les
   valeurs standard (23,976 / 24 / 25 / 29,97 / 30). La chronologie est ensuite découpée en
   segments à décalage constant, ce qui absorbe les coupes et les scènes ajoutées.
5. **Lignes en trop.** Certaines lignes n'ont pas de place dans la vidéo :
   - à une coupe, une réplique qui tomberait là où la référence ne dit rien appartient à une
     scène absente de la vidéo ;
   - une réplique qui commencerait avant 0 s est typiquement un « Précédemment dans… » que la
     vidéo n'a pas.

   Par défaut, elles sont toutes retirées. Avec `--extra-lines keep`, elles ne sont gardées que
   là où rien n'est affiché ni dit, les lignes consécutives formant un bloc gardé en entier ou
   pas du tout : le crédit du traducteur reste, un récapitulatif ou une scène entière part.
   Elles ne chevauchent jamais une autre ligne.

   Le résultat est toujours dans l'ordre chronologique.

### Garde-fous

- Moins de 25 % des répliques ancrées : **ignoré (pas sûr)**, le fichier n'est pas modifié : la
  référence ne dit pas la même chose.
- Toutes les corrections sous 0,5 s : le fichier **n'est pas modifié**. C'est l'écart naturel
  entre deux langues, pas un problème de synchro.
- Des répliques qui se chevauchent dans le fichier lui-même (un filigrane, deux personnages) ne
  justifient pas de le réécrire, et une correction les conserve.
- Les segments de moins de 60 s sont traités comme des erreurs locales, pas comme des coupes.

Aucun service d'IA, aucun accès réseau une fois le modèle téléchargé, et la même entrée donne
toujours le même résultat.

## Résultats

Ces chiffres décrivent le benchmark et les jeux de données ci-dessous. Ils montrent comment
l'outil s'est comporté sur ces fichiers, pas ce qu'il fera sur n'importe quelle vidéo : avec
d'autres fichiers, d'autres langues ou un autre matériel, ils seront différents.

Ils ont été mesurés avec le modèle minilm. Le modèle static, celui par défaut depuis la 0.11, a
été repassé sur le même benchmark et les mêmes jeux de données réels : il a réussi autant de cas
du benchmark et pris les mêmes décisions sur les jeux de données (voir [Modèle](#modèle)).

### Benchmark sur pistes intégrées

- **Vidéos :** 15 qui contiennent à la fois un sous-titre français et un sous-titre en version
  originale intégrés.
- **Déformations :** la piste française est déformée de 7 façons réalistes : décalage constant,
  changement de cadence dans les deux sens, 3 coupes, scènes ajoutées et retirées, coupes avec
  changement de cadence.
- **Tâche :** la recaler sur la piste en version originale.
- **Réussite :** au moins 95 % des répliques commencent à moins de 300 ms de leur vraie
  position.

| Outil (sous-titre intégré comme référence) | Cas réussis | Échecs graves (< 80 % des répliques) |
|---|---|---|
| **SemanticSubSync** (0.7) | **104 / 105** | 1 |
| alass | 84 / 105 | 10, aucun signalé |

**Cas invalides.** Sur 11 cas avec une piste commentaire ou une piste partielle comme
référence, SemanticSubSync a ignoré (pas sûr) ceux où il aurait fait des dégâts :

- la couverture des répliques ancrées y était de 0,07 au plus, contre 0,32 à 0,82 sur les cas
  valides ;
- le seuil (0,25) se situe dans cet écart ; une autre vidéothèque peut demander une
  autre valeur (`--min-coverage`).

**Outils basés sur l'audio seul,** pour comparaison, sur le même genre de déformations :
ffsubsync 29 / 44, alass 24 / 44, subaligner 2 / 44, sans aucun signal de confiance en cas
d'échec.

**Vitesse** sur un NAS avec un Celeron J4025 à 2 cœurs, pour un film complet (vidéo de 9,7 Go,
1528 répliques) :

| Modèle (voir [Modèle](#modèle)) | Durée | RAM au maximum |
|---|---|---|
| static | environ 97 s | 680 Mo |
| minilm int8 | environ 175 s | 620 Mo |

Avec static, l'essentiel du temps sert à extraire le sous-titre intégré de la vidéo (ffmpeg).
Elle dépend du matériel, de la taille de la vidéo et du nombre de répliques.

### Jeux de données réels

Le benchmark ci-dessus part de pistes correctes. Pour voir le comportement sur des fichiers tels
qu'on les trouve en ligne, l'outil a aussi été passé sur des jeux de données réels :

- 66 sous-titres téléchargés sur un site public de sous-titres, pour 8 épisodes de séries ;
- 16 langues : allemand, anglais, arabe, chinois, espagnol, français, grec, hongrois, italien,
  japonais, polonais, portugais (Portugal et Brésil), russe, suédois, turc ;
- chacun recalé sur le sous-titre anglais de son épisode, puis l'anglais sur lui : 114 paires.

Ces fichiers ne sont pas dans le dépôt. [`tools/bench_real.py`](tools/bench_real.py) refait la
même mesure sur n'importe quel dossier de ce type.

Il n'y a pas de vérité terrain ici, la mesure est donc indirecte. Les répliques qui ont **une
seule** traduction évidente dans l'autre fichier (similarité d'au moins 0,70, et 0,15 de plus
que toute autre réplique) devraient commencer à moins de 0,5 s / 1 s d'elle. Deux traductions
sont rarement découpées de la même façon : même une paire synchronisée reste sous 100 %.

| Décision | Paires | Répliques à moins de 0,5 s / 1 s de leur traduction (médiane) |
|---|---|---|
| Laissé tel quel (déjà synchronisé) | 66 | 92,8 % / 97,4 % |
| Corrigé | 44 | avant : 1,9 % / 4,5 % — après : 82,7 % / 91,5 % |
| Ignoré (pas sûr) | 4 | — |

- **Paires corrigées :** elles couvrent les cas pour lesquels l'outil est fait : 25 i/s contre
  23,976 i/s (3 épisodes), coupes et scènes ajoutées ou retirées (3 à 6 segments, dont une
  version longue DVD), un récapitulatif de 90 s présent dans une seule version.
  - 28 des 44 atteignent 90 % ou plus à 1 s.
  - La plus basse est à 69 % (une traduction portugaise découpée très différemment de
    l'anglaise).
  - 2 des 44 finissent légèrement en dessous du fichier non modifié (un sous-titre grec, dans
    les deux sens : 95,7 % puis 93,2 % à 1 s), pour une correction d'environ 0,5 s, juste
    au-dessus du seuil.
- **Ignorés (pas sûr) :** 2 fichiers dans les deux sens, dont le contenu appartient à un autre épisode que
  celui de leur nom (ni le dialogue ni la durée ne correspondent à la référence).
- **Lignes en trop :** 170 n'avaient pas de place dans la vidéo, dont 140 correspondent au
  récapitulatif de 35 répliques d'une version, recalée sur des versions qui ne l'ont pas.
  - Mesuré avec `--extra-lines keep` : 9 lignes isolées sont restées dans des silences, 161 ont
    été retirées.
  - Par défaut (`drop`), les 170 sont retirées. Auparavant, elles auraient été empilées à
    00:00:00.
- **Invariants :** aucune réplique n'est sortie hors de l'ordre chronologique ou avant 0 s, et
  aucun chevauchement de plus de 0,3 s n'a été créé.
- **Encodages :** 47 des 66 fichiers n'étaient pas en UTF-8 (voir [Utilisation](#utilisation)).

Ce n'est pas un benchmark publié. La suite de tests reproduit chaque déformation sur des
dialogues synthétiques (voir [Développement](#développement)).

## Installation

- Python 3.11 ou plus récent, sur Linux, macOS ou Windows 64 bits.
- ffmpeg / ffprobe, seulement si la référence est une vidéo.
- L'ARM64 (matériel de type Raspberry Pi) est couvert par la CI : tests, les deux modèles,
  l'exemple et l'image Docker tournent sur une machine ARM64, avec les mêmes résultats qu'en
  x86-64.
- Un Raspberry Pi doit avoir un système 64 bits : le moteur ONNX n'existe pas en ARM 32 bits.

```bash
pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync"
```

Le modèle (environ 240 Mo) est téléchargé depuis Hugging Face à la première utilisation.

Chaque [release](https://github.com/ludosch/SemanticSubSync/releases) contient aussi le paquet
Python et une image Docker pour amd64 et arm64, à charger avec `docker load` :

```bash
gh release download v0.11.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"
docker load -i semantic-subsync-0.11.0-docker-amd64.tar.gz
```

## Utilisation

```bash
semantic-subsync SOUS-TITRE REFERENCE [-o SORTIE] [--track INDEX] [--lang CODE]
                 [--extra-lines drop|keep] [--min-coverage X] [--json]
```

**Référence.** `REFERENCE` est un `.srt` calé sur la vidéo, ou la vidéo elle-même. Avec une
vidéo, le sous-titre texte intégré le plus complet est utilisé, quelle que soit sa langue, hors
pistes forcées. `--track` choisit une piste par son index ffprobe.

**Sortie.**

- Le sous-titre d'entrée n'est jamais modifié. Le résultat va par défaut dans
  `SOUS-TITRE.synced.srt` (`-o` pour le changer).
- Déjà synchronisé : rien n'est écrit.
- Code de sortie : `0` corrigé ou déjà synchronisé, `1` ignoré (pas sûr, rien n'est écrit), `2` erreur.
- `--json` affiche la décision et les statistiques (couverture, segments, décalages, répliques
  retirées, lignes en trop gardées).

**Lignes en trop.** Les lignes pour lesquelles la vidéo n'a pas de place sont retirées (voir
[Fonctionnement](#fonctionnement)). `--extra-lines keep` les laisse là où rien n'est affiché ni
dit.

**Encodages.** Les fichiers qui ne sont pas en UTF-8 (la plupart des téléchargements anciens)
sont lus dans la page de code de leur langue :

- prise dans le nom du fichier (`Film.ru.srt`, `Serie.S01E01.pt-BR.srt`) ou dans `--lang ru` ;
- sans l'un ni l'autre, une page de code occidentale est supposée, sauf si le texte ressemble
  alors à un autre alphabet : l'encodage est alors deviné.

**Depuis Python :**

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
  vérifié automatiquement par un worker en arrière-plan et corrigé si besoin. La version
  téléchargée reste disponible comme piste supplémentaire, et chaque décision est journalisée.
- [**Jellyfin**](integrations/jellyfin/README.md) (en anglais) : un plugin qui installe lui-même le
  moteur. « Sync subtitles » dans le menu d'un film ou d'un épisode resynchronise ses sous-titres ;
  dans les bibliothèques choisies, les nouvelles vidéos et les nouveaux fichiers de sous-titres sont
  traités automatiquement. L'élément est rafraîchi pour que les pistes apparaissent tout de suite. Pas de
  conteneur en plus ; Jellyfin doit pouvoir écrire dans les dossiers des médias.

## Modèle

Deux modèles de phrases multilingues, tous deux publiés par
[sentence-transformers](https://www.sbert.net/) sous licence Apache 2.0 :

| Nom | Modèle | |
|---|---|---|
| `static` (par défaut) | [static-similarity-mrl-multilingual-v1](https://huggingface.co/sentence-transformers/static-similarity-mrl-multilingual-v1), 512 premières dimensions | Moyenne de vecteurs de mots, aucun réseau de neurones à exécuter : sur le benchmark, ses vecteurs ont pris environ 1 % du temps de minilm (voir la vitesse sur des fichiers entiers dans [Benchmark](#benchmark-sur-pistes-intégrées)) |
| `minilm` | [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), via [fastembed](https://github.com/qdrant/fastembed) | Un petit transformer : plus lent, un peu meilleur sur certains cas difficiles |

Sur le benchmark et les jeux de données réels, les deux ont pris les mêmes décisions ;
minilm a mieux corrigé quelques fichiers difficiles (par exemple un changement de fréquence
d'images sur un autre montage). Le modèle se choisit avec `--model`, `SEMSYNC_MODEL` ou, pour un
seul sous-titre dans le worker, `one --model` (voir le
[guide Bazarr](integrations/bazarr/README.md#test-one-pair-by-hand)). Chaque modèle a son propre
seuil de similarité, fixé dans le code.

| Variable | Rôle |
|---|---|
| `SEMSYNC_MODEL` | `static` (par défaut) ou `minilm` |
| `SEMSYNC_MODEL_DIR` | Dossier des copies locales, un sous-dossier par modèle : `static/` (`tokenizer.json`, `model.safetensors`), `minilm/` (par exemple la version int8, voir ci-dessous). Un modèle sans sous-dossier est téléchargé depuis Hugging Face à la première utilisation |
| `SEMSYNC_CACHE` | Dossier facultatif où les vecteurs sont mis en cache sur disque |

**minilm int8.** [`tools/quantize_model.py`](tools/quantize_model.py) produit une copie int8 de
112 Mo. Sur un NAS avec un Celeron J4025 à 2 cœurs, elle était environ 40 % plus rapide que le
minilm d'origine et utilisait 2,5 fois moins de RAM, avec les mêmes résultats.

**Langues.** Les deux sous-titres doivent être dans des langues sur lesquelles le modèle a été
entraîné. La fiche de minilm les liste ainsi (celle de static donne les mêmes, avec zh pour les
deux variantes du chinois) : ar, bg, ca, cs, da, de, el, en, es, et, fa, fi, fr, fr-ca,
gl, gu, he, hi, hr, hu, hy, id, it, ja, ka, ko, ku, lt, lv, mk, mn, mr, ms, my, nb, nl, pl, pt,
pt-br, ro, ru, sk, sl, sq, sr, sv, th, tr, uk, ur, vi, zh-cn, zh-tw. Une autre langue peut
marcher en partie, sans garantie (non testé).

## Développement

```bash
mise x -- uv run pytest                                               # rapide, sans le modèle
SEMSYNC_TEST_MODEL=1 mise x -- uv run --extra model pytest -m model   # de bout en bout avec chaque vrai modèle
SEMSYNC_CORPUS=~/corpus SEMSYNC_CACHE=~/corpus/emb mise x -- uv run --extra model pytest -m corpus   # sous-titres réels locaux
```

**Tests unitaires :** ils tournent sur des dialogues **synthétiques** (`tests/synth.py`) :

- chaque réplique porte un jeton de « concept » comme `k17`, qu'un faux modèle déterministe
  transforme en vecteur fixe ;
- les deux langues d'un même concept se ressemblent à environ 0,9, deux répliques sans rapport
  à environ 0,1, comme avec le vrai modèle ;
- on teste ainsi l'algorithme indépendamment du modèle, sur chaque déformation du benchmark.
  Aucun extrait de film n'est stocké dans le dépôt.

**Tests corpus** (`-m corpus`) : ils passent toutes les paires d'un dossier local de
sous-titres réels (voir [Jeux de données réels](#jeux-de-données-réels)). Ils vérifient les
invariants, et qu'aucune correction n'est nettement pire que le fichier non modifié. Ils sont
sautés si `SEMSYNC_CORPUS` n'est pas défini.

**CI :** elle tourne sur les pushs vers `main` qui modifient le code, pas sur les changements
de documentation seule.

Voir [CONTRIBUTING.md](CONTRIBUTING.md) (en anglais) et le [journal des versions](CHANGELOG.md).

## Remerciements

- [ffsubsync](https://github.com/smacke/ffsubsync) et [alass](https://github.com/kaegi/alass),
  les outils de référence auxquels ce projet a été comparé.
- [DuoSubs](https://github.com/CK-Explorer/DuoSubs), dont l'approche par phrases a inspiré
  l'appariement des phrases coupées en deux.

## Licence

[MIT](LICENSE)
