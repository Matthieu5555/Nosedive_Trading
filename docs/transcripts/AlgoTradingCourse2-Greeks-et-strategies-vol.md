# AlgoTradingCourse 2 — Greeks, surface de vol & stratégies de volatilité

> Transcript de cours nettoyé (auto-transcription très bruitée reconstruite et synthétisée).
> Jargon décodé : *pouce* = put, *col* = call, *aile gauche/droite* = downside/upside,
> *strame* = strike, *oscure* = skew, *RT / Routime Vega* = running / annualized vega,
> *Banna* = Vanna. Les passages inaudibles ou purement répétitifs ont été supprimés.

## Executive summary

Le cours énonce, sous forme orale, un **cahier des charges d'architecture analytics options**.
En une phrase : le moteur doit produire, **par (sous-jacent, strike, maturité, right)**, l'**IV
propre**, le **jeu complet de Greeks 1ᵉʳ/2ᵉ ordre en brut *et* en cash (€/$)**, le **RT-Vega**,
avec **taux `r` paramétrable** (défaut 0), une **précision à 6 chiffres significatifs**, et les
données de marché **bid/ask + volume** ; le tout adossé à un **moteur de PnL-attribution par
décomposition de Greeks**, afin de pouvoir implémenter et backtester des **stratégies de vol
décorrélées (dispersion, calendaires) avec delta-hedge en bande**.

Points à retenir :

1. **Skew** — l'IV diffère entre put et call pour un même (K, T) ; calibrer l'IV *par option*, jamais mutualisée.
2. **`r` = paramètre**, pas constante en dur (init à 0 possible, mais modifiable).
3. **Greeks en double** : forme brute **et** forme monétaire (€/$ selon le sous-jacent).
4. **RT-Vega** (vega annualisé) ajouté sur chaque strike.
5. **Précision** : ≥ 6 sig-figs, notation scientifique (les ordres de grandeur des Greeks diffèrent fortement).
6. **Tradeable** : sans **bid/ask** (pas le mid) ni **volume**, les analytics restent inexploitables.
7. **PnL attribution** : ΔPrix = Σ (Greek × variation de variable) ; ~90 % capté en 1ᵉʳ ordre, résidu en 2ᵉ ordre.
8. **Finalité** : combiner des stratégies vol décorrélées (dispersion, calendaires) ; delta-hedge en bande.

---

## 1. Skew : une IV par option, jamais mutualisée

Pour **un même strike et une même maturité**, la volatilité implicite du **put** n'est **pas**
celle du **call**. C'est similaire mais déformé : typiquement le put est plus cher sur l'aile
gauche (downside) et le call plus cher sur l'aile droite (upside). Cette asymétrie de prise en
compte du risque par le marché est le **skew**.

**Conséquence d'architecture** : calculer l'IV **sur chaque option / chaque right type
séparément**. Ne jamais supposer une vol unique put/call par (K, T). Quand on décompose
l'aile, on doit garder le gamma, le theta, etc. propres à chaque jambe.

## 2. Taux d'intérêt : un paramètre, pas une constante en dur

On peut **initialiser `r = 0`** pour simplifier — aucun problème. **Mais l'architecture doit
rester robuste** : si on doit appliquer un vrai taux (reward) à un client, on doit pouvoir le
modifier. Le taux doit donc **apparaître dans l'architecture** comme variable (même si constante
au départe), pas être effacé du modèle.

## 3. Greeks en double forme : brut **et** cash (€/$)

Produire :

- la **version brute** des Greeks (Delta, Gamma, Vega, Theta, + 2ᵉ ordre : Vanna, Volga, Charm/Color…),
- **et** la **version monétaire** en €/$ **selon le sous-jacent** (Eurostoxx → €, options US → $).

Il faut **les deux** : ce sont les cash Greeks qui alimentent le PnL final.

## 4. Running-Time Vega (RT-Vega)

Ajouter, **sur chaque strike**, le **RT-Vega = vega annualisé** (normalisation temporelle du vega).

## 5. Précision numérique

Les Greeks n'ont pas du tout la même amplitude :

- **Delta** varie en général entre **−1 et +1** ;
- **Vega / Gamma** sont d'un autre ordre (≈ ±0,1, ±0,01, voire ±0,001).

Avec une granularité uniforme (ex. arrondi à 2 décimales partout), on **perd l'information** sur
les petits Greeks. → Stocker **au moins 6 chiffres significatifs**, en **notation scientifique**
(mantisse × 10⁻ⁿ), pour que chaque élément garde son information utile.

## 6. Données qui rendent le tout *tradeable*

Les analytics « théoriques » restent **inexploitables en pratique** sans la couche marché. Pour
qu'une stratégie soit implémentable :

- **bid et ask** (surtout **pas le mid**) — acheter au prix offert ≠ prix moyen ;
- **volume disponible** par strike — la liquidité, souvent décisive là où la théorie la néglige.

C'est *cet* élément informatif qui fait passer de l'exercice théorique au trading réel.

## 7. Moteur de PnL-attribution

La **variation du prix de l'option** (source du PnL) se décompose en **somme des dérivées
partielles (Greeks) × variations des variables** disponibles (spot, vol, taux, temps) :

```
ΔPrix ≈ Σ_i  (∂Prix/∂x_i) · Δx_i
```

- Les **Greeks de 1ᵉʳ ordre** expliquent **~90 %** du PnL.
- Le **résidu** est capté par les **Greeks de 2ᵉ ordre** (Vanna, Volga, Charm, Color, Speed,
  Zomma…). En les purgeant un à un, on descend bien en dessous du résidu, mais il en reste
  toujours un peu.

**Pourquoi c'est essentiel** : comprendre la structure du résidu et la variation des Greeks est
ce qui permet ensuite de **se hedger** et de concevoir des stratégies (ex. viser un book
gamma-neutre ou légèrement short gamma, rééquilibrer selon l'évolution).

## 8. Finalité : des stratégies de vol décorrélées

Chaque stratégie d'options a un **profil de risque** distinct (long/short gamma, exposition
theta…). L'objectif est d'en combiner plusieurs **décorrélées** — cette **mutualisation** est ce
qui permet d'atteindre de meilleurs ratios.

Argument clé : sur les **actions**, c'est très dur d'avoir 5 stratégies décorrélées (les
constituants du S&P sont corrélés ~0,7 entre eux ; même le Bitcoin ~0,65 au S&P). La **structure
des options**, elle, le permet. La **barrière à l'entrée** (complexité de l'architecture et des
données) est précisément ce qui crée l'edge des stratégies de volatilité / dispersion.

### Exemple détaillé — la dispersion

- **Construction** : acheter des **straddles ATM** sur les **top 10** (en théorie top 50)
  capitalisations de l'Eurostoxx, puis **delta-hedger contre l'indice** (put / short indice).
- **Mécanique** : la variance de l'indice =
  **Σ (variances individuelles) + 2 · Σ (covariances)**.
  En étant **long les straddles des composants** (long variance individuelle) et **short la
  variance de l'indice**, on **isole / shorte le terme de covariance**.
- **Profil** : c'est une stratégie **Delta-hedgée** qui exhibe la covariance entre composants.
  Elle est **gagnante quand les actifs dispersent** (la corrélation baisse pendant que l'indice
  reste stable).
- **Faiblesse structurelle** : ces positions sont **longues, avec un Theta très négatif**. Il
  faut donc des analytics pour trouver de quoi **compenser le theta sans casser le profil**.

### Delta-hedge en bande (rebalancing)

Un straddle ATM a |Δ| ≈ 0,5. Le lendemain, le spot a bougé, donc |Δ| dérive.

- **Ne pas** re-hedger en continu pour rester pile à |Δ| = 0,5 : ça génère des coûts (arbitrage à
  réaliser à chaque pas).
- **Garder** la position tant que |Δ| reste **dans une bande** (de l'ordre de 0,455–0,46, soit
  ~±0,06 autour de la cible) ; **re-hedger seulement à la sortie de bande**.

### Stratégies calendaires

Jouer la structure par terme : **long une maturité longue, short une maturité courte**.

---

## Récapitulatif des exigences d'architecture

| # | Exigence | Détail |
|---|----------|--------|
| 1 | IV par option | Put et call séparés ; capture le skew par (K, T) |
| 2 | Taux `r` paramétrable | Défaut 0, mais modifiable et présent dans le modèle |
| 3 | Greeks 1ᵉʳ + 2ᵉ ordre | Delta, Gamma, Vega, Theta, Vanna, Volga, Charm, Color… |
| 4 | Greeks brut **et** cash | €/$ selon le sous-jacent |
| 5 | RT-Vega | Vega annualisé, par strike |
| 6 | Précision | ≥ 6 sig-figs, notation scientifique |
| 7 | Données marché | bid, ask (pas mid), volume |
| 8 | PnL-attribution | Σ Greek × Δvariable ; résidu en 2ᵉ ordre |
| 9 | Stratégies cibles | Dispersion, calendaires ; delta-hedge en bande |
