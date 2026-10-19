# Générateur TikTok Shop

Tu colles des liens de vidéos TikTok → tu récupères des vidéos prêtes à poster :

1. la vidéo est téléchargée puis passée en **miroir** ;
2. des **captures d'écran** de la vidéo sont analysées par Claude pour écrire un **script vendeur** (jamais au hasard) qui se termine toujours par
   « Si tu veux commander, je te laisse cliquer sur le panier orange. » ;
3. une **voix off française** (toujours la même) est générée, les blancs sont coupés ;
4. la durée de la voix colle à la durée de la vidéo : le script est écrit pour la bonne longueur, puis la voix et/ou la vidéo sont légèrement
   accélérées ou ralenties pour tomber pile ;
5. ton **personnage** (fond vert retiré) est placé à droite, au-dessus des boutons et de la légende TikTok, et **s'anime** selon ce que dit le texte
   (bouche et dents synchronisées sur la voix, regard qui bouge, sourcils, tête qui tourne, bras qui se plient : main sur la hanche, sur le ventre, paume ouverte…) ;
6. export en 1080×1920, dans le dossier `outputs/`.

## Lancer

Double-clique sur **`start.command`** (la première fois, ça installe tout seul en 2–3 minutes). Le dashboard s'ouvre sur http://localhost:8765.
Laisse la fenêtre noire ouverte tant que tu l'utilises.

**Scripts sans clé API** : si tu n'as pas de clé Anthropic, c'est Claude (dans la conversation Claude Code) qui regarde les captures et écrit chaque script. Le dashboard affiche « Claude écrit… » puis continue tout seul (voix, montage). Avec une clé API (Réglages), tout est automatique.

Dans **Réglages**, tu peux coller une clé API Anthropic (facultatif) et choisir la voix, la position du personnage
(avec un aperçu de l'écran TikTok) et le nombre de vidéos générées en même temps.

## Poster sur TikTok depuis le téléphone (QR code)

Sur chaque vidéo, bouton **« Poster sur TikTok »** → un **QR code** s'affiche avec la **légende en français** (modifiable, bouton « Copier »).
Scanne-le avec ton téléphone : la page s'ouvre avec la vidéo, la légende, et un bouton **« Ouvrir dans TikTok »** (la légende est copiée,
la vidéo part dans TikTok via le menu « Partager »). Dans TikTok : Suivant → appui long dans la description → **Coller** → panier orange → Publier.

- **Même Wi-Fi** : marche tout de suite, mais le téléphone ne peut alors que télécharger la vidéo (à importer dans TikTok avec « + »).
- **Ouverture directe dans TikTok** : bouton « Activer l'ouverture directe dans TikTok » dans la fenêtre du QR code. Il crée un lien sécurisé
  temporaire (HTTPS, gratuit, via Cloudflare ; l'outil est téléchargé une fois dans `.tools/`). Seule l'adresse secrète de la vidéo est
  accessible depuis Internet : le reste du dashboard (réglages, clés, vidéos) reste fermé. Les liens expirent après 3 h.
- La légende est la **traduction française** de la description de la vidéo d'origine (écrite avec le script).

## Fichiers

- `server.py` — le dashboard · `web/` — l'interface
- `pipeline/` — téléchargement, script (Claude), voix off, animation (`animator.py` = les mouvements, `character.py` + `face.py` = le dessin), montage
- `assets/character/` — le personnage découpé en calques (`python -m pipeline.prepare_character` les régénère depuis `assets/screenshot_source.webp`)
- `outputs/` — les vidéos terminées · `settings.json` — tes réglages (dont la clé API, jamais envoyée ailleurs que chez Anthropic)

## Détails techniques

- Script : modèle `claude-opus-5-5` (modifiable dans Réglages › Avancé), avec relance automatique sur un autre modèle si la requête est déclinée.
- Voix : Microsoft Edge TTS (gratuit, nécessite Internet), une phrase à la fois, silences coupés.
- Pour changer de personnage il faut refaire le découpage en calques (`pipeline/prepare_character.py`).
