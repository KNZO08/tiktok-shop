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

## Envoyer sur TikTok (brouillons)

Bouton **« Envoyer dans mes brouillons TikTok »** sur chaque vidéo : elle arrive dans l'appli TikTok (notification ou Boîte de réception).
Tu y ajoutes le **panier orange** (le lien produit TikTok Shop s'ajoute dans l'appli, au moment de publier), tu colles la légende
(bouton « Copier la légende ») et tu publies. Il faut un compte sur **upload-post.com** (offre payante pour TikTok), ton TikTok connecté,
puis la clé API et le nom du profil dans Réglages › « Publier sur TikTok ».

## Fichiers

- `server.py` — le dashboard · `web/` — l'interface
- `pipeline/` — téléchargement, script (Claude), voix off, animation (`animator.py` = les mouvements, `character.py` + `face.py` = le dessin), montage
- `assets/character/` — le personnage découpé en calques (`python -m pipeline.prepare_character` les régénère depuis `assets/screenshot_source.webp`)
- `outputs/` — les vidéos terminées · `settings.json` — tes réglages (dont la clé API, jamais envoyée ailleurs que chez Anthropic)

## Détails techniques

- Script : modèle `claude-opus-5-5` (modifiable dans Réglages › Avancé), avec relance automatique sur un autre modèle si la requête est déclinée.
- Voix : Microsoft Edge TTS (gratuit, nécessite Internet), une phrase à la fois, silences coupés.
- Pour changer de personnage il faut refaire le découpage en calques (`pipeline/prepare_character.py`).
