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

Dans Jellyfin, un [plugin](integrations/jellyfin/README.md) (en anglais) le fait
automatiquement pour chaque nouveau sous-titre, et depuis le menu d'un film ou d'un épisode.

## Pourquoi

Les sous-titres téléchargés pour un film ou un épisode sont souvent faits pour une autre version
de la même vidéo : une autre cadence d'images (23,976 ou 25 i/s), une scène ajoutée ou coupée,
un générique différent. Résultat : un sous-titre qui dérive, ou qui est juste pendant 20 minutes
puis décalé de 4 secondes.

Or la plupart des vidéos contiennent déjà un sous-titre parfaitement calé : la piste intégrée,
souvent en version originale. SemanticSubSync s'en sert comme référence et apparie les répliques
**par leur sens**, grâce à un petit modèle de phrases multilingue : « Where did you put the
keys? » et « Où as-tu mis les clés ? » sont reconnues comme la même réplique.

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

Deux échecs reviennent dans cet exemple et dans le [benchmark](docs/results.md) (en anglais) :

- **Coupes et scènes ajoutées.** Se caler sur l'audio ou sur le rythme d'un autre sous-titre
  règle un décalage ou un changement de cadence ; après une scène absente ou ajoutée, une partie
  du fichier reste souvent décalée. SemanticSubSync sait quelle réplique correspond à laquelle :
  il trouve où est la coupe et déplace chaque partie de son propre décalage.
- **Échecs annoncés comme réussis.** Un fichier resté décalé d'une minute, ou recalé sur une
  piste commentaire, ressort comme une réussite. SemanticSubSync compte les répliques qu'il a pu
  apparier : sous 25 %, il s'arrête (**pas sûr**) et n'écrit rien.

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

## Fonctionnement

Un trait par réplique, en bleu ce qui est calé, en orange ce qui est décalé, en vert ce qui est
corrigé :

![Répliques manquantes dans le français : le français après le trou est décalé plus tard](docs/fix-missing-lines.svg)

![Mauvaise référence : aucune réplique du commentaire ne correspond au français, le fichier est laissé tel quel](docs/fix-wrong-reference.svg)

- Chaque réplique est appariée aux répliques de la référence qui disent la même chose, dans
  l'ordre du dialogue.
- Une seule cadence d'images est retenue pour tout le fichier, puis la chronologie est découpée
  en segments à décalage constant : cela absorbe les coupes et les scènes ajoutées.
- Les lignes qui n'ont pas de place dans la vidéo (une scène qu'elle n'a pas, un « Précédemment
  dans… ») sont retirées.
- Moins de 25 % des répliques appariées : **ignoré (pas sûr)**, le fichier n'est pas modifié.
  Toutes les corrections sous 0,5 s : le fichier **n'est pas modifié**.

Aucun service d'IA, aucun accès réseau une fois le modèle téléchargé, et la même entrée donne
toujours le même résultat. Les autres cas (décalage, cadence, lignes en trop), chaque étape et
chaque garde-fou : [How it works](docs/how-it-works.md) (en anglais).

## Résultats

Ces chiffres décrivent les fichiers sur lesquels ils ont été mesurés, pas ce que l'outil fera
sur n'importe quelle vidéo.

- **Benchmark** (105 pistes intégrées déformées : décalages, changements de cadence, coupes,
  scènes ajoutées et retirées) : SemanticSubSync a réussi 104 cas ; alass en a réussi 84, avec
  10 échecs graves dont aucun signalé.
- **Jeux de données réels** (114 paires de sous-titres téléchargés, en 16 langues) : 66 laissées
  telles quelles, déjà synchronisées ; 44 corrigées, la part médiane de répliques à moins de 1 s
  de leur traduction passant de 4,5 % à 91,5 % ; 4 ignorées (pas sûr), dont les fichiers
  appartenaient à un autre épisode.

Méthode, vitesse sur un NAS et tous les chiffres : [Results](docs/results.md) (en anglais).

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

## Démarrer

### Jellyfin

Un plugin qui installe lui-même le moteur, sans conteneur en plus. Dans **Tableau de bord >
Extensions > Dépôts**, ajoutez

```
https://github.com/ludosch/SemanticSubSync/releases/latest/download/manifest.json
```

puis installez **SemanticSubSync** depuis le catalogue et redémarrez Jellyfin. Jellyfin doit
pouvoir écrire dans les dossiers des médias : voir le [guide du plugin](integrations/jellyfin/README.md)
(en anglais).

### Bazarr

Chaque sous-titre téléchargé est vérifié par un worker en arrière-plan et corrigé si besoin ; la
version téléchargée reste disponible comme piste supplémentaire. Voir le
[guide Bazarr](integrations/bazarr/README.md) (en anglais).

### Ligne de commande

Python 3.11 ou plus récent, sur Linux, macOS ou Windows 64 bits (x86-64 ou ARM64) ; ffmpeg si la
référence est une vidéo.

```bash
pip install "semantic-subsync[model] @ git+https://github.com/ludosch/SemanticSubSync"
```

Le modèle par défaut (environ 220 Mo) est téléchargé depuis Hugging Face à la première utilisation.

Chaque [release](https://github.com/ludosch/SemanticSubSync/releases) contient aussi le paquet
Python et une image Docker pour amd64 et arm64, à charger avec `docker load` :

```bash
gh release download v0.12.0 -R ludosch/SemanticSubSync -p "*docker-amd64*"
docker load -i semantic-subsync-0.12.0-docker-amd64.tar.gz
```

```bash
semantic-subsync SOUS-TITRE REFERENCE [-o SORTIE]
```

- `REFERENCE` est un `.srt` calé sur la vidéo, ou la vidéo elle-même (son sous-titre texte
  intégré le plus complet est utilisé).
- L'entrée n'est jamais modifiée : le résultat va dans `SOUS-TITRE.synced.srt` ; rien n'est
  écrit s'il est déjà synchronisé.
- Code de sortie : `0` corrigé ou déjà synchronisé, `1` ignoré (pas sûr, rien n'est écrit),
  `2` erreur.

Toutes les options, les encodages et l'API Python : [Usage](docs/usage.md). Les deux modèles de
phrases et les langues qu'ils couvrent : [Models](docs/models.md). Ces pages sont en anglais.

## Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md) (tests, release, en anglais) et le
[journal des versions](CHANGELOG.md).

## Remerciements

- [ffsubsync](https://github.com/smacke/ffsubsync) et [alass](https://github.com/kaegi/alass),
  les outils de référence auxquels ce projet a été comparé.
- [DuoSubs](https://github.com/CK-Explorer/DuoSubs), dont l'approche par phrases a inspiré
  l'appariement des phrases coupées en deux.

## Licence

[MIT](LICENSE)
