# AlgoTradingCourse 2 — Architecture de l'app : données → risque → ordres (les 3 onglets)

> Transcript de cours nettoyé (auto-transcription **très bruitée** reconstruite et synthétisée).
> Jargon décodé avec le contexte du projet (options indicielles, EuroStoxx, IBKR) : *IPKR* = IBKR
> (Interactive Brokers), *Staking Workstation* = Trader Workstation (TWS), *grèves* = Greeks,
> *pôles et poutres* = puts et calls, *formule de Bagage* = formule de Black, *straddle ATR* =
> straddle ATM, *Eurostat* = EuroStoxx (50), *OSN500* = S&P 500, *l'espace* = le spot,
> *l'économie* = le taux, *choquer / numérisme* = stresser / l'analyse de risque.
> Les passages inaudibles ou purement répétitifs ont été supprimés. **Rien n'est inventé** :
> là où le transcript est trop dégradé pour être sûr, c'est marqué *(transcript incertain)* et
> aucune valeur n'est fabriquée pour combler.

## Executive summary

Le cours décrit, à l'oral, l'**architecture applicative en trois onglets** qui mène de la donnée
brute jusqu'à l'envoi d'ordres, en s'appuyant sur l'exemple écrit déjà distribué (« vous l'avez
vu en lisant le document »). En une phrase : **(1) un onglet Données** totalement agnostique qui
capture l'**indice et ses constituants** (futures multi-maturités + options de **−30Δ → ATM →
+30Δ**), en dérive la **volatilité implicite**, le **smile / la surface**, et les **Greeks bruts
et cash** rangés par **maturité × delta** ; **(2) un onglet Risque** où l'on compose un book et on
le **choque** (≈ **±50 % spot, ±50 % vol, ±10 % taux**) ; **(3) un onglet Ordres** (passage /
backtest). Le tout sert à construire, via la **PnL-attribution**, des **stratégies de vol
décorrélées** (ex. dispersion).

Points à retenir :

1. **Source de données** : IBKR — préférer une connexion **API / passerelle** robuste plutôt que la TWS.
2. **Onglet 1 = données agnostiques** : l'**indice + ses actions constituantes**. Tester d'abord sur **EuroStoxx (50)**, pas le S&P 500 (~500 lignes, trop long pour démarrer).
3. **Périmètre de capture** : **futures sur plusieurs maturités** (≈ 1 mois → 3 ans) et **options puts & calls** sur la bande **−30Δ → ATM → +30Δ**.
4. **Volatilité implicite** : inverser le prix observé (Black) → une IV **par option** ; on observe un **smile** (IV plus élevée sur les ailes).
5. **Greeks brut + cash**, rangés en **accordéon par maturité × delta** → la **surface de vol**.
6. **Onglet 2 = risque** : composer un book (UI ergonomique) puis le **stresser** (±50 % spot, ±50 % vol, ±10 % taux).
7. **Onglet 3 = ordres** : indispensable une fois la stratégie prête (passage d'ordres, backtest).
8. **PnL-attribution → stratégies décorrélées** : décomposer le PnL en Greeks × Δvariable, puis chercher des stratégies qui se compensent (ex. « je paye du theta → quoi pour le rembourser ? »), en empiler 5-6 décorrélées.

---

## 1. Source de données : IBKR, via l'API plutôt que la TWS

La donnée vient d'**IBKR**. Pour ceux qui s'y sont connectés, la **Trader Workstation (TWS)**
« galère » : mieux vaut éviter de s'appuyer directement dessus et passer par une **connexion API /
passerelle**, qui donne un **pipeline plus robuste**. La connexion API arrive souvent en mode
**lecture seule** *(décodage probable de « Ridony » = read-only — transcript incertain)* ; on peut
le désactiver côté IBKR, mais **pour démarrer, le read-only suffit**.

## 2. Onglet 1 — des données totalement agnostiques : indice + constituants

Le **premier onglet ne fait que récupérer des données**, de façon **agnostique** (aucune logique
de stratégie). Deux niveaux :

- **l'indice global**,
- **et surtout les actions qui le composent**.

**Recommandation** : faire le test sur l'**EuroStoxx** (≈ **50** constituants) plutôt que sur le
**S&P 500** (~**500** lignes) — ce dernier est trop long pour commencer. C'est la couche que le
prof dit n'avoir « rien implémenté » : à chacun de la bâtir.

## 3. Périmètre à capturer : futures multi-maturités + options sur la bande ±30Δ

Pour l'indice **et** pour chaque constituant :

- **Futures sur plusieurs maturités** — une structure par terme allant d'environ **1 mois à 3 ans**
  (clairement cités : 1 mois, 3 mois, …, 12 mois, 18 mois, 2 ans, 3 ans ; les **maturités
  intermédiaires sont inaudibles** dans le transcript et ne sont **pas** reconstituées ici).
- **Options puts & calls**, en couvrant **tous les strikes de −30Δ, en passant par l'ATM, jusqu'à
  +30Δ**, avec leur **prix**.

**Pourquoi la bande et pas seulement l'ATM** : les stratégies se montent à l'ATM, mais **l'ATM
d'aujourd'hui n'est pas celui de demain** (le spot bouge). Il faut donc disposer des strikes
**autour** de l'ATM (la bande ±30Δ), pas d'un point unique.

## 4. Volatilité implicite & smile

À partir du **prix observé**, on **inverse la formule de Black** pour trouver la valeur du
paramètre vol qui le reproduit : c'est la **volatilité implicite**. Il y a **une IV par option**,
et elle **n'est pas constante** d'un strike à l'autre : on observe un **smile de volatilité** —
en s'éloignant de l'ATM (vers les ailes en delta), **l'IV est plus élevée**.

## 5. Greeks (brut **et** cash), en accordéon par maturité × delta → la surface

Sur chaque option, on calcule les **Greeks** : **delta, gamma, theta, vega…** Deux exigences :

- **leur traduction en cash** — « le delta seul ne suffit pas, il faut ce qu'il représente en
  **dollars** » (Greeks bruts **et** monétisés) ;
- une organisation **en accordéon par maturité** : pour **chaque maturité**, une **bande de
  deltas** (autour de l'ATM, sur ±30Δ — le pas exact de delta est *transcript incertain*).

En croisant **maturité × delta**, on obtient le **graphe / la surface de volatilité**. Ce premier
onglet « donne déjà toutes les infos dont on a besoin » ; les **futures** y serviront ensuite à
**couvrir / gérer la position**.

## 6. Onglet 2 — le risque : composer un book, puis le choquer

Le **deuxième onglet** entre dans l'**analyse numérique / de risque**. On y **sélectionne les
actions et options** d'un **book**, via une interface **ergonomique** (composer un panier : achat,
option, etc.). Puis on **choque** ce book — la grille de stress citée est :

- **spot : −50 % … +50 %**,
- **vol : −50 % … +50 %**,
- **taux : −10 % … +10 %**,

« et on regarde comment ça évolue ». *(C'est exactement la grille à trois axes spot/vol/taux du
projet.)*

## 7. Onglet 3 — les ordres

Le **troisième onglet** est l'**onglet des ordres**. Le premier onglet « ne raconte pas
grand-chose » au début, **mais devient indispensable le jour où l'on a une stratégie** : c'est lui
qui alimente le **backtest** et l'**envoi des ordres**. *(Les détails d'implémentation de cet
onglet sont trop dégradés dans le transcript pour être restitués.)*

## 8. PnL-attribution → enchaîner des stratégies décorrélées

Une fois les **Greeks** disponibles, on explique la **variation de PnL** comme une **décomposition
sur les variables** (spot, vol, taux, temps) — comprendre « ce qu'il a fallu bouger ». Cette
lecture sert à **concevoir des stratégies qui se compensent** :

> « Je paye toujours du **theta** — quelle stratégie pourrais-je trouver pour me **rembourser** ce
> theta ? »

En empilant **5-6 petites stratégies décorrélées** les unes des autres, on forme un **portefeuille**.
C'est « la première démarche » : la partie « sexy » (vues de risque, backtest, ordres) devient
**quasi instantanée** **une fois que les données sont là et solides**. D'où l'insistance : la
**première étape, c'est d'avoir toutes les données** — le reste suit.

### Exemple évoqué — la dispersion

Le prof prend en exemple des **straddles ATM sur les 10 premiers constituants** de l'indice (sur
une maturité donnée), portés et **re-hedgés** au fil des jours, l'**indice / les futures** servant
à la couverture. *(L'anecdote chiffrée associée — « ~20 points » sur un sous-jacent — est trop
bruitée pour être restituée fidèlement et n'est donc pas reprise.)* Le profil et la mécanique
détaillés de la dispersion sont développés dans le transcript complémentaire
[`AlgoTradingCourse2-Greeks-et-strategies-vol.md`](AlgoTradingCourse2-Greeks-et-strategies-vol.md).

---

## Récapitulatif des exigences d'architecture

| # | Exigence | Détail |
|---|----------|--------|
| 1 | Source IBKR | Connexion **API / passerelle** (pas la TWS) ; read-only OK pour démarrer |
| 2 | Onglet 1 — données | Agnostique ; **indice + constituants** ; tester sur **EuroStoxx 50** |
| 3 | Périmètre capture | **Futures** ~1 mois→3 ans ; **options puts & calls** de **−30Δ → ATM → +30Δ** + prix |
| 4 | IV par option | Inversion de **Black** ; **smile** (IV plus haute sur les ailes) |
| 5 | Greeks brut **et** cash | Delta/Gamma/Theta/Vega… + monétisation ; **accordéon maturité × delta** → surface |
| 6 | Onglet 2 — risque | Composer un book + **choc ±50 % spot / ±50 % vol / ±10 % taux** |
| 7 | Onglet 3 — ordres | Passage d'ordres + backtest ; indispensable une fois la stratégie prête |
| 8 | PnL-attribution | Décomposition Greeks × Δvariable → enchaîner **5-6 stratégies décorrélées** |
