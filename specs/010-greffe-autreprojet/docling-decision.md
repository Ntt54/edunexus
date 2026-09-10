# Décision Docling — écarté du cœur (2026-09-10)

**Statut** : écarté pour le cœur, réévaluable en extra isolé.

## Verdict

Ne pas intégrer `docling-project/docling` (pipeline standard) comme dépendance
d'EduNexus. Garder l'hybride actuel : pypdf → OCR conditionnel léger →
Granite-Docling-258M-GGUF (~315 Mo, ~400 Mo RAM) via le provider existant.

## Chiffres (recherche 2026-09-10 : README, pyproject, docs, issues, arXiv)

- Deps incompressibles : torch + torchvision + transformers + rapidocr +
  onnxruntime + pypdfium2 + pandas/scipy ; images officielles 4,4-11,4 Go.
- RAM CPU observée : 2-4 Go standard, pics 3-4 Go, 15,3 Go mesurés (tables+code) ;
  recommandation mainteneur 8-16 Go. Cible EduNexus : CPU modeste.
- Temps CPU : ~3,1 s/page en moyenne (médiane 0,79 s, p95 16,3 s) ; OCR ~13 s/page.
  20 pages scannées ≈ 4-6 min.
- Offline possible (pré-chargement `docling-tools models download`), mais pièges :
  torch CPU vs torchvision CUDA (issue #3494), numpy 1 vs 2, pydantic v2.12,
  `docling-slim` exige encore torch à l'import (issue #3805 non mergée).
- Python 3.12 OK ; EasyOCR défaut sans modèle `fr` dédié (latin générique).

## Pourquoi écarté ici

1. **Constitution V** : 4+ Go + torch + modèles pour un gain marginal — nos
   8 PDF texte sont déjà bien extraits par pypdf.
2. Le besoin réel (pages scannées occasionnelles, structure) est couvert à
   <500 Mo par l'hybride existant ; RapidOCR-ONNX (~80 Mo, 0,9 s/page) ou
   Tesseract-fra en conditionnel si besoin.
3. Si tableaux/ordre de lecture deviennent un vrai manque RAG : extra optionnel
   `pip install -e ".[docling]"`, import lazy, désactivé par défaut, pipeline
   bridé (`do_ocr=False`, tables FAST, pas de formules/code), après fusion du
   lazy-import torch.
