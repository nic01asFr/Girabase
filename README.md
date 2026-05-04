# Girabase — Modernisation

> Fork de [CEREMA/territoires-ville.Girabase](https://github.com/CEREMA/territoires-ville.Girabase)  
> Logiciel original : CERTU/CEREMA — Licence GPL-3.0

Ce fork modernise Girabase tout en préservant intégralement le code source VB6 original (racine du dépôt). Il propose deux apports complémentaires : un **moteur Python** (port complet du modèle Siegloch) et un **widget Grist** — outil web autonome sans serveur.

---

## Ce que ce fork ajoute

### 🌐 Widget Grist — l'apport principal

**Fichier unique, aucune installation, fonctionne dans un navigateur.**

Le widget Grist est une réimplémentation complète de Girabase en JavaScript autonome. Il peut être utilisé de trois façons :

- **Directement dans [Grist](https://getgrist.com)** — widget personnalisé lié à une base de données Grist (5 tables créées automatiquement) qui stocke les giratoires, leurs bras, les périodes de trafic, les matrices O-D et les résultats.
- **Localement** — ouvrir `web/static/grist_widget.html` dans un navigateur (mode démo intégré avec données d'exemple).
- **Via GitHub Pages** — accessible sans aucune infrastructure.

**Fonctionnalités :**

- Moteur Siegloch CERTU complet embarqué en JavaScript (port fidèle de `engine.py`)
- Saisie géométrique avec calcul instantané des paramètres dérivés (Rext, RU, LAU, LEU, LImax, Tg, Te, Tf1)
- Tableau des bras : LE 4m/15m, LI, LS, évasée, rampe, tourne-à-droite, piétons, sortie-seule
- Matrices O-D éditables par période avec **heatmap** et **coller depuis Excel** (Ctrl+V)
- Résultats : réserve de capacité RC%, files d'attente (M/M/1), délais, interprétation CERTU
- **Schéma SVG interactif** : flux O-D visualisés comme arcs pondérés, coloration RC% par bras, tooltips
- **Import OSM** : recherche Nominatim + Overpass API, détection automatique des bras depuis la topologie, carte Leaflet avec ortho IGN Géoplateforme
- **Note de calcul PDF** : export imprimable complet (géométrie, O-D, résultats, conclusion)
- Recalcul automatique différé
- Mode démo sans Grist

**Schéma Grist (5 tables créées automatiquement) :**

| Table | Contenu | Formules Python Grist |
|-------|---------|----------------------|
| `Giratoires` | Géométrie + métadonnées | Rext, RU, LAU, LEU, LImax, Tg, Te, Tf1 |
| `Bras` | Caractéristiques par bras | LE effectif, Tf |
| `Periodes` | Périodes de trafic | — |
| `FluxOD` | Matrice O-D normalisée | Giratoire (lookup) |
| `Resultats` | Résultats écrits par le widget | rc_level, rc_text, saturated |

### 🐍 Moteur Python + API FastAPI

Port Python complet et validé du modèle CERTU/Girabase (VB6, CETE de l'Ouest, 1998).

**`web/engine.py`** — Toutes les formules référencées par §section de la Note de Calcul :

- Paramètres géométriques globaux : RU, LAU, LEU, LImax, KI, KE (§2.1–§2.3)
- Capacité Siegloch : Ci = (3600/Tf) × exp(−QG/3600 × (Tg − Tf/2)), Cvh = Ci × (LE/3.5)^Te (§2.5)
- Flux giratoire gênant avec correction sortant KS (§2.2.1)
- Correction piétons Cp (§2.5.3)
- Files d'attente M/M/1 (Wq, Lq, Lmax, délai total)
- Mini-giratoire (R=0), entrée évasée, rampe (Tf×1.35), tourne-à-droite bypass
- 3 milieux : RC / PU / CV

**`web/api.py`** — API FastAPI exposant le moteur :

| Endpoint | Description |
|----------|-------------|
| `POST /api/capacite` | Calcul complet (géométrie + bras + matrices O-D multi-périodes) |
| `POST /api/sensitivity` | Sensibilité RC% / paramètre (LE4m, QE, R, LA) — parallèle |
| `POST /api/growth_matrix` | Projection multi-horizons (T0 / MES / H+10) |
| `GET /api/milieux` | Constantes Tg/Te/Tf1 par milieu |
| `GET /health` | Statut service |

**`web/test_engine.py`** — Suite pytest en 9 sections couvrant :
- Paramètres géométriques (RU, LAU, LEU, LImax, KI, KE, mini-giratoire)
- Paramètres par bras (LE évasée, LEGirabase, Tf rampe, TTP, KS)
- Logique `_passes_entry` (port de `numReelBranche` VB6)
- Trafic giratoire et correction gêne sortant (`TraficGenant`)
- Capacité Siegloch et réserve RC%
- Correction piétons (`RéserveCapacité`)
- Cas spéciaux (tourne-à-droite, sortie-seule, saturation)
- Mini-giratoire (coefficients RC forcés)
- Cohérence globale (conservation débit, formule RC%, M/M/1)

---

## Structure du fork

```
Girabase/
├── [fichiers VB6 originaux]          ← source CEREMA, inchangée
├── Version_anglaise/                 ← source CEREMA, inchangée
├── Version_wallonne/                 ← source CEREMA, inchangée
├── README.md                         ← ce fichier
└── web/                              ← version modernisée (ce fork)
    ├── engine.py                     ← moteur Python (port VB6 complet)
    ├── api.py                        ← API FastAPI
    ├── test_engine.py                ← suite pytest (9 sections)
    ├── requirements.txt
    ├── Dockerfile
    └── static/
        ├── index.html                ← UI web autonome (standalone)
        └── grist_widget.html         ← widget Grist (★ apport principal)
```

---

## Utilisation

### Widget Grist (recommandé)

**Option 1 — Dans Grist :**
1. Dans un document Grist, ajouter un widget personnalisé
2. URL : `https://nic01asfr.github.io/Girabase/web/static/grist_widget.html`
3. Accès requis : **Complet**
4. Les 5 tables sont créées automatiquement au premier chargement
5. Cliquer sur un giratoire dans la table pour l'éditer dans le widget

**Option 2 — Local (mode démo) :**
Ouvrir `web/static/grist_widget.html` directement dans un navigateur.
Des données d'exemple (4 bras, 2 périodes) se chargent automatiquement.

### API FastAPI

```bash
cd web
pip install -r requirements.txt
uvicorn api:app --port 8090
# Documentation interactive : http://localhost:8090/docs
```

**Ou via Docker :**
```bash
docker build -t girabase-web ./web
docker run -p 8090:8090 girabase-web
```

### Tests

```bash
cd web
pip install pytest
pytest test_engine.py -v
```

---

## Modèle de calcul

**Méthode Siegloch adaptée CERTU (1998)**

Capacité théorique d'une entrée i :

```
Ci  = (3600 / Tf) × exp(−QG_i / 3600 × (Tg − Tf/2))
Cvh = Ci × (LEGirabase / 3.5)^Te
```

Constantes par milieu :

| Milieu | Tg (s) | Te | Tf1 (s) |
|--------|--------|----|---------|
| Rase Campagne (RC) | 4.75 | 0.70 | 2.25 |
| Périurbain (PU) | 4.55 | 0.80 | 2.05 |
| Centre-Ville (CV) | 4.40 | 0.85 | 1.80 |

Interprétation de la réserve de capacité RC% = (1 − QE/Cvh) × 100 :

| RC% | Interprétation |
|-----|---------------|
| > 80% | Giratoire probablement non justifié |
| 50–80% | Entrée surdimensionnée |
| 25–50% | ✅ Bon fonctionnement |
| 5–25% | ⚠ Files d'attente possibles aux hyperpointes |
| < 5% | 🔴 Saturation sévère |

---

## Crédits

- **Modèle de calcul** : CERTU/CEREMA — Guide technique "Capacité des carrefours giratoires" (1998)
- **Logiciel VB6 original** : CETE de l'Ouest — diffusé sous GPL-3.0 par le CEREMA
- **Port Python/JS et widget Grist** : fork nic01asFr — GPL-3.0

Ce fork ne modifie pas le logiciel VB6 original. Le répertoire `web/` contient uniquement des fichiers nouveaux.
