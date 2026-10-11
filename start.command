#!/bin/bash
# Double-clique sur ce fichier pour lancer le générateur.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/Library/Python/3.9/bin:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

if [ ! -x .venv/bin/python ]; then
  echo ""
  echo "  Première installation (2 à 3 minutes, une seule fois)…"
  echo ""
  if ! command -v uv >/dev/null 2>&1; then
    python3 -m pip install --user -q uv || { echo "Impossible d'installer l'outil uv. Vérifie ta connexion Internet."; read -r -p "Appuie sur Entrée pour fermer."; exit 1; }
  fi
  uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt \
    || { echo "L'installation a échoué. Vérifie ta connexion Internet puis relance."; read -r -p "Appuie sur Entrée pour fermer."; exit 1; }
fi

# assure que les calques du personnage existent
[ -f assets/character/rig.json ] || .venv/bin/python -m pipeline.prepare_character

exec .venv/bin/python server.py
