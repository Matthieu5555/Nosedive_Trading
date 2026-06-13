# Cahier des charges — Dashboard options, page 1 : Vision marché

Version 1.0 · Brief d'implémentation front

> **Statut : référence d'intention, pas une spec stricte (ruling owner, 2026-06-10).** Ce
> document guide l'**emplacement et la fonction des blocs** (l'ordre de lecture, ce qui existe) ;
> il ne contraint **pas** l'implémentation pixel-près. **Ignorer ses couleurs** (le design system
> du repo prime). Décisions load-bearing déjà actées : la nappe / les coupes 2D / les greeks sont
> au **niveau indice** (§3.4–3.6), pas par composant ; la rangée composants (§3.2) ne pilote que
> la **courbe de prix** du composant sélectionné. Les jours qc-failing sont **affichés avec un
> badge QC**, pas masqués (§3.1/§5).

## 1. Objectif et périmètre

Page de lecture du marché options pour un indice donné, à une date choisie (live ou journée
passée rejouée). Lecture seule : aucune position, aucun passage d'ordre sur cette page (réservés
aux pages 2 et 3). La page consomme les outputs du pipeline de chaîne d'options et les présente
selon une logique descendante : du résumé chiffré vers le détail de sensibilité.

Cette page est la première des trois prévues :
- Page 1 (ce document) : vision marché.
- Page 2 : stress-test et pricing.
- Page 3 : passage d'ordres.

## 2. Principe de lecture (ordre des blocs)

L'ordre vertical suit la façon dont un trader raisonne, du panorama au détail :

1. Barre de contrôle (indice, date, statut QC).
2. Historiques de prix (indice puis composants).
3. Scorecards de volatilité (le résumé chiffré : nerveux ou calme ?).
4. Nappe de volatilité, heatmap puis 3D (l'objet complet, vue d'ensemble du risque).
5. Coupes 2D : smile et term structure (inspection d'une échéance, d'un strike).
6. Greeks (le détail de sensibilité sur la tranche sélectionnée).

La nappe précède volontairement les coupes 2D : la nappe est l'objet complet, le smile et la
term structure n'en sont que deux tranches orthogonales. On lit l'objet entier avant ses coupes.

## 3. Détail des blocs

### 3.1 Barre de contrôle

Sélecteur d'indice (dropdown). Sélecteur de date (date picker, défaut = jour courant). Libellé du
snapshot affiché (horodatage + type EOD ou intraday). Badge de statut QC à droite : pass / warn /
fail avec compteur de checks (ex. 12/12). Le badge QC reflète la validation du pipeline pour le
snapshot affiché et conditionne la confiance dans tout le reste de la page.

### 3.2 Historiques de prix

Historique de prix de l'indice : pleine largeur, en haut.

En dessous, deux blocs de hauteur identique :
- À gauche, bloc carré listant les composants : colonnes titre, poids, variation. Triable. Ligne
  sélectionnable avec surbrillance. Par défaut, le premier composant (poids le plus élevé) est
  sélectionné.
- À droite, historique de prix du composant sélectionné, remplissant la même hauteur que la
  liste. Cliquer une ligne de la liste met à jour ce graphe.

### 3.3 Scorecards de volatilité

Un sélecteur d'échéance global pilote les quatre scorecards. Cartes : vol ATM, skew 25-delta,
convexité, vol réalisée (pour comparaison implicite vs réalisé). Tous les nombres affichés sont
arrondis à la précision adaptée (1 décimale pour les pourcentages, etc.).

### 3.4 Nappe de volatilité

Deux représentations de la même surface, empilées en pleine largeur, partageant strictement la
même échelle de couleur (ramp violet, foncé = vol élevée). Une légende d'échelle de couleur est
affichée sous la heatmap.

- Heatmap strike × échéance (en haut) : lignes = échéances, colonnes = log-moneyness, couleur de
  cellule = vol implicite. Cellules cliquables (mène au smile / contrat correspondant).
- Vue 3D (en dessous) : même surface, rotation libre, même échelle de couleur que la heatmap
  pour que les deux vues soient cohérentes visuellement.

### 3.5 Coupes 2D

Deux graphes côte à côte, chacun avec son propre sélecteur d'échéance, indépendant du
sélecteur global des scorecards.

- Smile / skew : vol implicite par strike (axe configurable log-moneyness / delta / strike), repère
  ATM marqué. Coupe à échéance fixe.
- Term structure ATM : vol ATM par échéance, bascule niveau / variation jour. Coupe à strike fixe.

### 3.6 Greeks

Quatre cartes (delta, gamma, vega, theta) montrant la forme de chaque sensibilité le long du
strike pour l'échéance sélectionnée. Sur cette page sans position, ce sont des profils de forme, pas
des sommes de portefeuille (celles-ci arrivent en page 2/3). Le delta sert aussi de coordonnée
alternative pour le smile.

## 4. Sources de données

Tous les blocs de volatilité consomment les partitions du pipeline pour le snapshot et la date
sélectionnés :

| Bloc | Partition source |
|------|------------------|
| Histo indice et composants | market_state_snapshots ou barres historiques |
| Scorecards | surface_parameters, forward_curve, qc_results |
| Heatmap et 3D | surface_grid (grille régularisée) |
| Smile | iv_points (points acceptés) superposés à la tranche de surface_grid |
| Term structure | surface_grid coupe ATM |
| Greeks | pricing_results |
| Badge QC | qc_results |

## 5. Notes d'implémentation

- Charger la grille régularisée (surface_grid) pour heatmap et 3D, pas les points bruts. Mais garder
  les points acceptés (iv_points) disponibles pour les superposer sur le smile : ne jamais jeter les
  points bruts après le fit.
- Afficher les états dégradés visiblement plutôt que de les masquer. Le badge QC et les flags de
  qualité par échéance restent visibles : une nappe peut être lisse mais bâtie sur peu de points,
  l'opérateur doit le voir.
- Heatmap et 3D doivent partager le même mapping valeur → couleur (même min/max d'échelle),
  sinon les deux vues deviennent incomparables.
- Tous les nombres affichés passent par un arrondi explicite.

## 6. Contraintes de style (système de design)

- Surfaces blanches plates, bordures fines (0.5px), pas de gradient ni d'ombre.
- Coins arrondis : radius-md pour les éléments, radius-lg pour les cartes.
- Mode clair et sombre tous deux supportés : utiliser des variables CSS pour les couleurs de texte
  et de fond, jamais de hex en dur pour le texte.
- Casse de phrase partout, jamais de Title Case ni de MAJUSCULES.
- Deux graisses seulement : 400 régulier, 500 gras.
- Pour les couleurs de la nappe, échelle violette à 7 stops (du clair #EEEDFE au foncé #26215C).

## 7. Annexe — HTML de référence (maquette statique)

Le fragment ci-dessous est une maquette statique servant de référence visuelle pour le layout,
l'ordre des blocs et l'échelle de couleur de la nappe. Les graphes y sont des SVG factices : à
remplacer par les composants de charting réels branchés sur les partitions de données. Les
variables CSS (`--color-*`, `--border-radius-*`) sont fournies par l'environnement hôte ; les
adapter au design system du repo si besoin.

```html
<div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:1rem;">
  <select style="width:150px;"><option>SX5E (Euro Stoxx 50)</option><option>SPX</option><option>CAC 40</option></select>
  <input type="date" value="2026-04-06" style="width:150px;" />
  <span style="font-size:13px; color:var(--color-text-secondary);">snapshot 17:30 CET · EOD</span>
  <span style="margin-left:auto; font-size:12px; background:var(--color-background-success); color:var(--color-text-success); padding:4px 10px; border-radius:var(--border-radius-md);">QC pass · 12/12</span>
</div>

<!-- Histo indice : pleine largeur -->
<div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:1rem 1.1rem; margin-bottom:12px;">
  <div style="font-size:14px; font-weight:500; margin-bottom:10px;">Historique de prix — indice</div>
  <!-- composant chart indice -->
</div>

<!-- Composants (carré, gauche) + histo composant sélectionné (droite), même hauteur -->
<div style="display:grid; grid-template-columns:1fr 1.4fr; gap:12px; margin-bottom:1.5rem; align-items:stretch;">
  <div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1rem;">
    <div style="font-size:14px; font-weight:500; margin-bottom:10px;">Composants (50)</div>
    <!-- table triable, ligne sélectionnable, 1re sélectionnée par défaut -->
  </div>
  <div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1rem; display:flex; flex-direction:column;">
    <div style="font-size:14px; font-weight:500; margin-bottom:10px;">Historique — composant sélectionné</div>
    <!-- composant chart, flex:1 pour égaliser la hauteur -->
  </div>
</div>

<!-- En-tête analyse vol + sélecteur d'échéance global (pilote les scorecards) -->
<div style="display:flex; gap:10px; align-items:center; margin-bottom:0.75rem;">
  <span style="font-size:16px; font-weight:500;">Analyse de volatilité</span>
  <span style="font-size:12px; color:var(--color-text-secondary);">échéance</span>
  <select style="width:90px; height:30px;"><option>1M</option><option>3M</option><option>6M</option></select>
</div>

<!-- Scorecards -->
<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:1.25rem;">
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.85rem 1rem;"><div style="font-size:13px; color:var(--color-text-secondary);">Vol ATM</div><div style="font-size:22px; font-weight:500;">14,8 %</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.85rem 1rem;"><div style="font-size:13px; color:var(--color-text-secondary);">Skew 25Δ</div><div style="font-size:22px; font-weight:500;">−3,2 pts</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.85rem 1rem;"><div style="font-size:13px; color:var(--color-text-secondary);">Convexité</div><div style="font-size:22px; font-weight:500;">1,4</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.85rem 1rem;"><div style="font-size:13px; color:var(--color-text-secondary);">Vol réalisée</div><div style="font-size:22px; font-weight:500;">12,1 %</div></div>
</div>

<!-- Nappe heatmap : pleine largeur. Échelle de couleur partagée avec la 3D. -->
<div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1.1rem; margin-bottom:12px;">
  <div style="font-size:13.5px; font-weight:500;">Nappe de volatilité — heatmap</div>
  <div style="font-size:11.5px; color:var(--color-text-secondary); margin-bottom:10px;">strike × échéance · foncé = vol élevée · cellule cliquable</div>
  <!-- grille échéances x log-moneyness, couleur cellule = vol -->
  <!-- légende d'échelle de couleur : #EEEDFE → #26215C -->
</div>

<!-- Nappe 3D : pleine largeur, même échelle de couleur -->
<div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1.1rem; margin-bottom:1.25rem;">
  <div style="font-size:13.5px; font-weight:500;">Nappe de volatilité — vue 3D</div>
  <div style="font-size:11.5px; color:var(--color-text-secondary); margin-bottom:10px;">même échelle de couleur que la heatmap · rotation libre</div>
  <!-- surface 3D (ex. plotly), mapping couleur identique à la heatmap -->
</div>

<!-- Coupes 2D : smile + term structure côte à côte, sélecteur d'échéance propre à chacun -->
<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:12px;">
  <div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1rem;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
      <span style="font-size:13.5px; font-weight:500;">Smile / skew</span>
      <select style="width:80px; height:28px;"><option>1M</option><option>3M</option><option>6M</option></select>
    </div>
    <div style="font-size:11.5px; color:var(--color-text-secondary); margin-bottom:8px;">coupe à échéance fixe</div>
    <!-- chart smile, axe configurable log-moneyness/delta/strike, repère ATM -->
  </div>
  <div style="background:var(--color-background-primary); border:0.5px solid var(--color-border-tertiary); border-radius:var(--border-radius-lg); padding:0.9rem 1rem;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
      <span style="font-size:13.5px; font-weight:500;">Term structure ATM</span>
      <select style="width:90px; height:28px;"><option>Niveau</option><option>Δ jour</option></select>
    </div>
    <div style="font-size:11.5px; color:var(--color-text-secondary); margin-bottom:8px;">coupe à strike fixe</div>
    <!-- chart term structure -->
  </div>
</div>

<!-- Greeks : 4 cartes de forme, échéance sélectionnée -->
<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px;">
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.8rem 0.9rem;"><div style="font-size:12.5px; color:var(--color-text-secondary);">Delta</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.8rem 0.9rem;"><div style="font-size:12.5px; color:var(--color-text-secondary);">Gamma</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.8rem 0.9rem;"><div style="font-size:12.5px; color:var(--color-text-secondary);">Vega</div></div>
  <div style="background:var(--color-background-secondary); border-radius:var(--border-radius-md); padding:0.8rem 0.9rem;"><div style="font-size:12.5px; color:var(--color-text-secondary);">Theta</div></div>
</div>
```

### Palette de couleurs nappe (violet, 7 stops)

| Stop | Hex | Usage |
|------|-----|-------|
| 50 | #EEEDFE | vol la plus faible |
| 100 | #CECBF6 | |
| 200 | #AFA9EC | |
| 400 | #7F77DD | vol moyenne |
| 600 | #534AB7 | |
| 800 | #3C3489 | |
| 900 | #26215C | vol la plus élevée |
