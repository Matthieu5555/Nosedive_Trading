# Surface de volatilité implicite — Guide

> Document d'onboarding du moteur de pricing / risk options. À l'issue de cette lecture, vous serez en mesure de **lire et interpréter les outputs du moteur** : prix, Greeks, smile par maturité, surface 3D, violations calendaires.
>
> Les graphiques sont des données **synthétiques**, générées analytiquement et
> volontairement exagérées pour la lisibilité. Le code qui les produit vit dans le
> notebook jumeau `vol_surface_pedagogique.ipynb` ; ici on ne garde que les rendus et
> l'interprétation, angle trader : *ce qu'on fait de l'information*, pas seulement ce
> qu'on voit.

---

## Vocabulaire de base

Trois repères suffisent pour tout le document :

- **ATM / ITM / OTM** (*at / in / out of the money*) — position du strike par rapport au
  sous-jacent. Un call est **ATM** quand strike ≈ spot, **ITM** quand il a déjà de la valeur
  intrinsèque (spot > strike), **OTM** quand il n'en a pas encore (spot < strike). Pour un
  put, c'est l'inverse. La **valeur intrinsèque** est ce que l'option vaudrait si elle
  expirait maintenant ; le reste de la prime est la **valeur temps**.
- **Forward `F`** — le prix à terme du sous-jacent pour l'échéance considérée (spot ajusté
  du portage : taux et dividendes). C'est la vraie référence « à la monnaie », pas le spot.
- **Log-moneyness `k = ln(K/F)`** — la façon standard de positionner un strike :
  `k = 0` à la monnaie (strike = forward), `k < 0` côté puts, `k > 0` côté calls. On
  l'utilise parce qu'il rend les smiles comparables d'une échéance à l'autre.

---

## Bloc 1 — Les fondations : option, volatilité, vol implicite

### 1.1 — Le profil de payoff d'une option

![Profils de P&L à expiry](assets/vol_surface/1-1_payoff.png)

Une option n'est pas un actif symétrique. Les quatre profils ci-dessus le disent d'un
coup d'œil : le gain et la perte ne sont jamais le miroir l'un de l'autre autour du
strike.

- **Long call** — perte plafonnée à la prime payée (5 $), gain illimité à la hausse.
- **Long put** — perte plafonnée à la prime (4,5 $), gain qui grossit quand le sous-jacent
  s'effondre (borné seulement par un spot à zéro).
- **Short call / short put** — la même chose retournée : on encaisse la prime tout de
  suite, on porte un risque non borné (short call) ou très large (short put).

**Ce qu'un trader en retient.** Acheter une option, c'est payer un ticket pour un pari
**directionnel asymétrique** : risque connu et limité d'un côté, exposition convexe de
l'autre. Le coude au niveau du strike est l'endroit où toute la valeur se joue — c'est lui
que les Greeks (Bloc 2) vont mesurer.

### 1.2 — La volatilité comme paramètre de dispersion

![Trois régimes de volatilité](assets/vol_surface/1-2_dispersion.png)

Trois marchés synthétiques, même prix de départ (100), même rendement moyen (nul). Seule
la volatilité change : 10 % (profil obligataire), 25 % (actions), 60 % (crypto). À gauche,
les trajectoires ; à droite, la distribution des rendements journaliers.

La distribution dit tout : à 10 %, les rendements se serrent autour de zéro — le marché
respire à peine. À 60 %, la cloche s'étale, des journées à ±5 % deviennent banales. La vol
ne décrit **pas une direction** : elle décrit l'**amplitude** des mouvements, à la hausse
comme à la baisse.

**Ce qu'un trader en retient.** La volatilité est le seul paramètre qui, toutes choses
égales par ailleurs, fait monter la valeur d'une option. Plus la dispersion est large, plus
la probabilité de finir profondément dans la monnaie augmente — donc plus l'option vaut
cher. C'est le pont direct vers la section suivante.

### 1.3 — Prix d'option en fonction de la volatilité

![Prix du call vs volatilité](assets/vol_surface/1-3_prix_vs_vol.png)

On fixe tout sauf la vol : un call à 3 mois, spot 100, taux 3 %. On fait varier la
volatilité de 0 à 100 % et on lit le prix Black-Scholes. La relation est **monotone
croissante** : plus de vol = plus de prix, sans exception.

Les trois courbes (ATM, OTM K=110, ITM K=90) montrent que la sensibilité dépend du strike.
La courbe ATM est la plus « droite » — quasi linéaire autour des niveaux de marché
courants. Loin de la monnaie, la courbe est plus plate à basse vol (l'option a peu de
chances d'aboutir) puis se redresse quand la vol devient assez grande pour rendre le strike
atteignable.

**Ce qu'un trader en retient.** Acheter une option, c'est **acheter de la volatilité**. Un
trader d'options ne raisonne pas en prix absolu (« le call vaut 5 $ ») mais en vol (« il se
traite à 25 % de vol »). Le prix n'est qu'une conséquence ; la vol est la vraie monnaie.

**Réserve essentielle.** Ce raccourci ne vaut que pour une option **couverte en delta** : on
neutralise l'exposition directionnelle (on se hedge contre les mouvements du spot) pour ne
garder que le pari de vol. **Achetée nue, une option reste avant tout un pari directionnel** —
son P&L dépendra surtout du spot, pas de la vol. C'est la nuance qui sépare un trade de vol
d'un simple achat d'option.

### 1.4 — L'inversion : du prix de marché à la vol implicite

![Inversion prix → vol implicite](assets/vol_surface/1-4_inversion.png)

La courbe prix→vol se lit dans les deux sens. En pratique, le marché ne nous donne pas la
vol : il nous donne un **prix**. On inverse alors la relation — on cherche quelle
volatilité, injectée dans Black-Scholes, reproduit exactement le prix coté. C'est la
**volatilité implicite**.

Les trois droites horizontales (prix = 2,50 / 5,00 / 8,50) coupent la courbe à des niveaux
de vol croissants. Un prix plus élevé pour la même option n'a qu'une explication possible :
le marché price plus d'agitation future.

**Ce qu'un trader en retient.** La vol implicite est le **consensus du marché sur
l'agitation à venir**. C'est la seule information qu'un prix d'option contient au-delà des
paramètres observables (spot, strike, échéance, taux). Tout le métier de la vol consiste à
comparer cette anticipation au réel.

### 1.5 — Pourquoi la vol implicite n'est pas plate : trois raisons de marché

![Trois raisons du smile](assets/vol_surface/1-5_pourquoi_pas_plate.png)

Black-Scholes suppose une volatilité **constante** quel que soit le strike, et des
rendements **normaux**. Le marché sait que les deux hypothèses sont fausses, et le prix des
options le reflète. Trois mécanismes l'expliquent :

1. **Queues épaisses.** La distribution réelle des rendements (en bleu) a des extrêmes
   bien plus fréquents que la loi normale (en rouge). Les krachs et les rallyes violents
   existent ; la normale les sous-estime massivement. Les options loin de la monnaie
   protègent contre ces extrêmes — elles valent donc plus que ne le dirait la normale.
2. **Demande structurelle de puts.** Les gérants achètent en continu des puts hors de la
   monnaie pour couvrir leurs portefeuilles. Cette pression d'achat mécanique gonfle le
   prix — donc la vol implicite — des puts OTM, indépendamment de leur valeur « juste ».
3. **Clusters de volatilité.** La vol n'est pas stable dans le temps : elle arrive par
   régimes. Une journée agitée en annonce d'autres (la crise de 2008, mars 2020). Le marché
   price cette persistance.

**Ce qu'un trader en retient.** Le **smile** (Bloc 3) est la correction de marché à ces
trois mensonges de Black-Scholes. Là où le modèle dit « une seule vol », le marché répond
« une vol par strike et par échéance » — et c'est précisément cette structure que le moteur
reconstruit et que vous allez apprendre à lire.

---

## Bloc 2 — Les Greeks : sensibilités du prix d'option

Les Greeks mesurent **comment le prix d'une option réagit à chaque paramètre de marché**.
Ce sont les premiers chiffres qu'un trader lit sur son book. Les trois premiers (Delta,
Vega, Gamma) sont directement liés à la surface de volatilité — ils expliquent pourquoi la
surface intéresse un trader avant même de savoir la lire. Theta et Rho complètent le tableau
de bord. Référence commune ci-dessous : call ATM, spot 100, σ = 25 %, r = 3 %.

### 2.1 — Delta : la directionnalité

![Delta vs strike et maturité](assets/vol_surface/2-1_delta.png)

Le Delta est l'**exposition directionnelle** au sous-jacent : de combien bouge le prix de
l'option quand le spot bouge de 1 €.

Le profil en S (graphe de gauche) résume tout. Profondément **dans la monnaie**, le call a
un Delta proche de 1 — il se comporte presque comme le sous-jacent lui-même. **À la
monnaie**, le Delta est d'environ 0,5 : si le spot monte de 1 €, l'option gagne ~0,50 €.
Profondément **hors de la monnaie**, le Delta s'écrase vers 0 — l'option ne réagit
quasiment plus. Le put (en teal) est l'image décalée : son Delta va de 0 (loin OTM) à −1
(loin ITM).

À droite, le Delta ATM monte légèrement avec l'échéance (de ~0,52 à ~0,56) : l'effet du
carry (taux > 0) le pousse un peu au-dessus de 0,5, sans le sortir de la quasi-neutralité
directionnelle à moitié.

**Lien surface.** Le skew (Bloc 3) déforme ce profil : sur un marché à skew négatif fort,
la vol implicite plus élevée des puts OTM relève leur Delta par rapport à un monde
Black-Scholes plat. Le Delta qu'affiche le moteur intègre déjà la surface — il n'est pas le
Delta « théorique » d'un manuel.

### 2.2 — Vega : la sensibilité à la volatilité

![Vega vs strike et maturité](assets/vol_surface/2-2_vega.png)

Le Vega répond à : **combien vaut 1 % de vol implicite en plus**, en termes de prix. C'est
le Greek central du trader de surface.

Le Vega est **maximal à la monnaie** et **s'écrase aux ailes** : les options ATM sont les
plus sensibles aux mouvements de vol implicite. Il **croît aussi avec la maturité** (les
courbes longues sont au-dessus) — une option longue a plus de « temps de vol » à capturer,
donc plus de Vega.

**Lien surface — le plus direct de tous.** Trader la surface, *c'est* gérer son Vega. Une
position **long Vega** gagne quand la surface « se soulève » (la vol implicite monte) ;
**short Vega** quand elle s'aplatit. Quand le moteur affiche un Vega par strike et par
échéance, il vous dit exactement où, sur la surface, votre book est exposé.

### 2.3 — Gamma : la convexité

![Gamma vs strike et maturité](assets/vol_surface/2-3_gamma.png)

Le Gamma est la **vitesse à laquelle le Delta change**. C'est la mesure de la **convexité**
de la position.

Le pic est **très prononcé à la monnaie** et il **s'accentue à l'approche de l'expiry** :
une option ATM à quelques jours de l'échéance a un Gamma explosif. Concrètement, son Delta
bascule de 0 à 1 sur un tout petit intervalle de spot. (Sur le panneau « Gamma ATM vs
maturité », l'axe va du court terme à gauche au long terme à droite : « proche de l'expiry »
= **petites maturités, à gauche** — le pic est d'autant plus haut que l'échéance est courte.)

Pourquoi un trader paie pour ça : une position **long Gamma** gagne sur les **grands
mouvements dans les deux sens**, parce que le Delta s'ajuste favorablement (il monte quand
le spot monte, baisse quand il baisse). C'est la convexité — et c'est pourquoi les options
ATM court terme sont chères en Gamma : elles offrent le maximum de convexité au moment où
les mouvements peuvent encore tout changer.

**Lien surface.** La **courbure** du smile (sa forme en U, Bloc 3) est l'image directe du
Gamma de marché : un smile très courbé signifie que le marché price beaucoup de jump risk,
donc beaucoup de Gamma.

### 2.4 — Theta : le coût du temps

![Theta vs strike et maturité](assets/vol_surface/2-4_theta.png)

Le Theta mesure l'**érosion journalière** de la valeur de l'option : chaque jour qui passe
sans mouvement, l'option perd un peu de valeur temps. Il est négatif pour une position
longue.

Le Theta est maximal (en valeur absolue) **à la monnaie** et il **s'accélère** fortement à
l'approche de l'expiry (graphe de droite) — la valeur temps fond de plus en plus vite dans
les derniers jours.

**Point clé.** Le Theta n'est **pas un Greek de surface** — on ne *trade* pas la surface avec
lui, contrairement au Vega — mais il est **mécaniquement affecté par elle via le Gamma** : un
niveau de vol implicite plus élevé relève le Gamma, donc accélère le Theta. Il est surtout la
**contrepartie du Gamma** : long Gamma = short Theta (pour une option proche de la monnaie).
On paie le temps (Theta) pour avoir la
convexité (Gamma). Un portefeuille long options « saigne du Theta » en silence — c'est le
coût de détention de la convexité et du Vega.

### 2.5 — Rho : la sensibilité aux taux

![Rho — prix vs taux](assets/vol_surface/2-5_rho.png)

Le Rho mesure la sensibilité au **taux sans risque**. Les calls **bénéficient** d'une hausse
des taux (le coût de portage du sous-jacent augmente, le call vaut plus) ; les puts en
**souffrent**.

C'est le Greek le **moins utilisé** au quotidien — sur options de courte maturité, l'impact
est marginal. Il reprend de l'importance dans les environnements de taux très mouvants
(2022-2023, remontée brutale des banques centrales), où un repricing de la courbe se
répercute visiblement sur les books d'options longues.

### 2.6 — Tableau de bord Greeks

![Tableau de bord des Greeks](assets/vol_surface/2-6_dashboard.png)

Les cinq Greeks d'un **long call ATM** de référence, avec le signe attendu sous chaque
panneau, tous tracés en fonction de la **maturité** (court terme à gauche, long terme à
droite — Gamma et Theta y sont donc les plus extrêmes à gauche, près de l'expiry). C'est le
mode d'emploi d'un risk report : en 30 secondes, savoir si un book est long ou short chaque
Greek, et ce que ça implique en P&L.

Un long call ATM est : **long Delta** (profite de la hausse), **long Gamma** (profite des
grands mouvements), **long Vega** (profite d'une hausse de la vol), **short Theta** (paie le
temps) et **long Rho** (légèrement aidé par une hausse des taux). Lire ces signes, c'est
lire la stratégie implicite d'une position sans connaître l'intention du trader.

---

## Bloc 3 — Lire un smile à maturité fixée

### 3.1 — Les quatre formes caractéristiques du smile

![Les quatre formes du smile](assets/vol_surface/3-1_quatre_smiles.png)

Une « tranche » de la surface = la vol implicite en fonction du strike, à échéance fixée.
Quatre formes types, lues en log-moneyness `k = ln(K/F)` (0 = à la monnaie) :

- **Plate (Black-Scholes)** — vol identique à tous les strikes. Le monde du modèle, point
  de départ de toute lecture. On ne l'observe jamais vraiment sur le marché.
- **Smile symétrique (crypto / pré-événement)** — vol minimale ATM, qui remonte
  symétriquement des deux côtés. Régime d'incertitude directionnelle totale : le marché
  price un grand mouvement sans savoir dans quel sens (typique du BTC, ou d'un actif avant
  un événement binaire).
- **Skew négatif (actions)** — puts OTM très chers, calls OTM bon marché. C'est la signature
  des indices actions : la demande structurelle de protection (Bloc 1.5) tire la vol des
  puts vers le haut. La peur est asymétrique — on craint le krach plus que le rallye.
- **Skew positif (matières premières)** — l'inverse : calls OTM chers. Le marché craint un
  **rally violent** plus qu'une chute (pétrole, gaz, short squeeze potentiel).

**Ce qu'un trader en retient.** La forme seule du smile permet d'**identifier le type
d'actif et le régime de marché**, avant même de regarder le prix du sous-jacent.

### 3.2 — Les trois paramètres du smile

![Les trois paramètres du smile](assets/vol_surface/3-2_trois_parametres.png)

Tout smile se résume à trois nombres — c'est ainsi que le moteur le paramètre et que vous
devez le lire :

1. **Niveau ATM** — la hauteur du smile à la monnaie. C'est le **niveau de peur absolu** du
   marché, dont le VIX est une approximation directe. (En toute rigueur, le VIX intègre
   toute l'aile de puts/calls OTM, pas seulement l'ATM : en stress, le skew s'écarte et le
   VIX monte plus vite que la vol ATM.)
2. **Pente (skew)** — l'inclinaison gauche/droite. C'est la **direction de la peur** : pente
   vers la gauche = crash redouté ; vers la droite = rally redouté.
3. **Courbure (convexité)** — l'intensité du U. C'est l'**intensité du jump risk** anticipé,
   et le lien direct avec le **Gamma de marché** (Bloc 2.3) : plus le smile est courbé, plus
   le marché price de gros sauts.

**Ce qu'un trader en retient.** Niveau / pente / courbure résument l'intégralité du
consensus de marché sur une échéance. Trois chiffres, et vous savez à quel point le marché a
peur, dans quelle direction, et avec quelle intensité.

---

## Bloc 4 — La surface complète

### 4.1 — De la tranche à la surface

![De la tranche à la surface](assets/vol_surface/4-1_tranche_surface.png)

La surface n'est rien d'autre que l'**empilement des smiles** à différentes échéances. À
gauche, quatre tranches (30 / 60 / 90 / 180 jours) superposées en 2D ; à droite, les mêmes
données en 3D. On voit l'aplatissement progressif du smile et la réduction du skew quand
l'échéance s'allonge.

**Ce qu'un trader en retient.** Une surface = strike (log-moneyness) × échéance × vol
implicite. Rien de plus qu'une pile de tranches — mais c'est cette vue d'ensemble qui permet
d'arbitrer entre échéances.

### 4.2 — La term structure : trois régimes

![Term structure — trois régimes](assets/vol_surface/4-2_term_structure.png)

À skew identique, on fait varier la **structure par terme** (l'évolution du niveau ATM avec
l'échéance) :

- **Backwardation** — vol courte **supérieure** à la vol longue. Signature d'un **événement
  de risque imminent** : résultats, décision de banque centrale, échéance politique. Le
  marché price beaucoup d'agitation tout de suite, qui se normalise ensuite.
- **Flat** — pas de vue temporelle ; le marché ne distingue pas court et long terme.
- **Contango** — vol courte **inférieure** à la vol longue. **Sérénité de court terme,
  incertitude structurelle de long terme.** C'est le régime « normal » hors crise.

**Ce qu'un trader en retient.** La term structure dit *quand* le marché situe le risque. Une
bascule de contango vers backwardation est un signal fort : quelque chose d'imminent vient
d'entrer dans le radar du marché.

### 4.3 — Surface réaliste actions

![Surface réaliste actions](assets/vol_surface/4-3_surface_actions.png)

La surface qu'on observe sur le SPY ou l'Euro Stoxx 50 hors crise : **skew négatif
permanent** (demande institutionnelle de puts), **contango** (le court terme est
relativement calme) et **aplatissement progressif des ailes** à long terme (le jump risk
perçu se dilue avec l'horizon).

**Ce qu'un trader en retient.** C'est votre référence « monde normal ». Toute déformation
par rapport à cette forme — skew qui se raidit, contango qui s'inverse — est un signal à
interpréter.

### 4.4 — Surface réaliste crypto

![Surface réaliste crypto](assets/vol_surface/4-4_surface_crypto.png)

BTC en régime incertain : **smile symétrique** (pas de biais directionnel — la crypto craint
autant le pump que le dump), **backwardation de court terme** (agitation immédiate) et
**aplatissement à long terme** (normalisation progressive avec l'horizon).

**Ce qu'un trader en retient.** La différence avec la surface actions est immédiatement
lisible : un smile symétrique au lieu d'un skew, une backwardation au lieu d'un contango. La
forme de la surface trahit la classe d'actif sans qu'on ait à la nommer.

### 4.5 — Violations calendaires

![Violations calendaires](assets/vol_surface/4-5_violations.png)

Les croix rouges marquent un **arbitrage calendaire**. La règle de non-arbitrage est
simple : la **variance totale** `σ²·T` doit être **strictement croissante** avec l'échéance
`T`. Intuitivement, plus on laisse de temps, plus l'incertitude cumulée doit augmenter — pas
diminuer.

Ici, à **log-moneyness fixé** (donc à forward-moneyness comparable, **pas** à strike dollar
identique), la tranche 60 jours a été placée **sous** la tranche 30 jours — la variance
totale décroît : impossible économiquement. Sur une vraie surface, ce motif n'est jamais un signal
de trading — c'est un **drapeau de qualité de données** : illiquidité, cotation incohérente,
quote périmée. À vérifier en priorité avant toute décision.

**Ce qu'un trader en retient.** Quand le moteur signale une violation calendaire, on ne
trade pas dessus : on **audite la donnée**. Le moteur fait ce contrôle automatiquement et
vous montre exactement où la cohérence se casse.

### 4.6 — Coupes pratiques : lire la surface en 30 secondes

![Coupes pratiques de la surface](assets/vol_surface/4-6_coupes.png)

La surface 3D, c'est la vision globale ; les **coupes**, c'est l'exécution. Deux suffisent :

- **Coupes horizontales** (smile par maturité) — on lit l'aplatissement et la réduction du
  skew quand l'échéance s'allonge.
- **Coupes verticales** (term structure par strike) — chaque strike a sa propre dynamique
  temporelle : l'ATM, le put OTM (k = −0,3) et le call OTM (k = +0,3) ne vieillissent pas de
  la même façon.

**Ce qu'un trader en retient.** Lire ces deux coupes en 30 secondes suffit pour prendre une
décision de vol. Et le fil rouge revient au **Vega** (Bloc 2.2) : gérer la surface, c'est
gérer son exposition Vega **bucket par bucket** — par strike et par échéance. La surface
vous dit où vous êtes exposé ; les coupes vous disent quoi faire.

---

## En résumé — lire un output du moteur

| Output moteur | Ce que vous lisez | Section |
|---|---|---|
| Prix d'option | une vol implicite déguisée — pensez en vol | 1.3 / 1.4 |
| Delta | exposition directionnelle (0 à ±1) | 2.1 |
| Vega | exposition à la vol, par strike/échéance | 2.2 |
| Gamma / Theta | convexité et son coût en temps (les deux faces) | 2.3 / 2.4 |
| Niveau / pente / courbure du smile | peur : combien, dans quel sens, avec quelle intensité | 3.2 |
| Term structure | *quand* le marché situe le risque | 4.2 |
| Forme de surface | classe d'actif et régime de marché | 4.3 / 4.4 |
| Violation calendaire | drapeau qualité de données, pas un signal | 4.5 |

Vous avez maintenant la grille de lecture complète. Le reste — calibration SVI, provenance,
reconstruction déterministe — est la mécanique interne du moteur ; ce guide vous suffit pour
en **interpréter** chaque sortie.
