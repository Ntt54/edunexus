# Direction UI — EduNexus Vue

## Trois pistes envisagées

### 1. Atelier de progression

**Très brève introduction :** Un espace clair et structuré qui traite l’apprentissage comme une trajectoire visible, avec une prochaine action unique et des preuves de maîtrise. L’intention est d’apaiser l’élève tout en rendant l’avancement concret.

**Probabilité :** 0.07

### 2. Carnet scientifique

**Très brève introduction :** Une esthétique de cahier d’étude, annotations et repères documentaires, mettant les sources et les notions au premier plan. L’expérience est plus éditoriale et contemplative.

**Probabilité :** 0.04

### 3. Studio d’entraînement

**Très brève introduction :** Une interface plus dense et orientée performance, avec des séries d’exercices, indicateurs de maîtrise et raccourcis de séance. L’intention est énergique et pragmatique.

**Probabilité :** 0.09

## Direction retenue — Atelier de progression

### Mouvement de design

Le produit adopte une interprétation contemporaine du **soft utilitarianism** pour l’éducation : une interface lumineuse, structurée, accueillante, qui montre le travail réel sans décorer artificiellement les données.

### Principes directeurs

1. La première question traitée à l’écran est toujours : « quelle est ma prochaine étape ? ».
2. Les données d’apprentissage deviennent compréhensibles par de petits repères explicites plutôt que par des graphiques abstraits.
3. Les sources, le parcours et les preuves de maîtrise restent visibles et reliés sans surcharger la séance.
4. La convivialité vient de la clarté, de l’espace et de micro-interactions sobres, pas de la gamification excessive.

### Philosophie de couleur

L’indigo est la couleur de continuité et de concentration ; il pilote la navigation, le parcours actif et les actions principales. Un orange terre cuite, rare et chaleureux, signale la prochaine action et les révisions importantes. Les gris bleutés créent un fond calme qui laisse les contenus et le progrès respirer.

### Paradigme de mise en page

Le produit repose sur une **colonne de décisions** : une barre latérale stable pour l’orientation, un espace central qui fait remonter la prochaine étape, et une colonne contextuelle pour les métriques, sources et recommandations. Il évite la juxtaposition de cartes sans priorité.

### Éléments signature

1. Une carte « Prochaine étape » à accent indigo avec un marqueur orange.
2. Une frise de parcours verticale où l’étape en cours est reliée visuellement à l’action du jour.
3. Des « preuves de maîtrise » textuelles accompagnant toutes les métriques principales.

### Philosophie d’interaction

Chaque clic doit soit poursuivre une étape, soit préciser le contexte. Les actions irréversibles sont séparées et les actions principales sont toujours libellées avec un verbe. Le système confirme visuellement le changement de statut sans déplacer brutalement la mise en page.

### Animation

Les éléments entrent avec un léger fondu vertical de 8 à 12 px en 180 à 240 ms. Les barres et états changent avec des transitions d’opacité et de transformation, jamais par animation de largeur coûteuse. Les animations non essentielles sont neutralisées pour `prefers-reduced-motion`.

### Système typographique

Les titres utilisent **Fraunces** pour donner de la personnalité et distinguer les objectifs ; le corps utilise **DM Sans** pour la lisibilité des consignes et données. Les titres de page ont une hiérarchie nette, les chiffres de maîtrise utilisent des variantes tabulaires et les informations secondaires restent au minimum à 14 px.

### Essence de marque

**EduNexus est un atelier d’apprentissage local qui transforme les sources personnelles d’un élève en prochaines étapes vérifiables et adaptées.**

Personnalité : méthodique, encourageante, transparente.

### Voix de marque

Les titres sont directs, orientés action et sans formulation générique. Les CTA décrivent exactement l’action qui suivra ; les microcopies expliquent le pourquoi d’une recommandation.

Exemples : « Reprendre les boucles : 12 minutes suffisent pour avancer. »

Exemples : « Cette révision revient aujourd’hui parce que votre dernier rappel date de 6 jours. »

### Wordmark et logo

Le mot-symbole est associé à un signe de liaison abstrait : trois nœuds reliés par une trajectoire qui évoque simultanément la connaissance, les sources et la progression. Il est utilisé comme icône identifiable sans dépendre du texte.

### Couleur signature

**Nexus Indigo — `#4F46E5`**.

## Décisions de style

- Ne pas utiliser de mode sombre, de halo néon, de dégradé violet décoratif, ni d’icônes emoji.
- Réserver l’orange terre cuite aux priorités et aux appels à l’action importants.
- Préserver un contraste de texte d’au moins 4,5:1 et des cibles interactives de 44 px ou plus.
- Le motif Nexus « Sources → Notions → Étape active » est visible sur toutes les vues majeures et relie la provenance au parcours.
- Toute métrique de maîtrise est accompagnée d’une preuve textuelle courte : étapes validées, rappels et exercices, dernières réponses ou documents exploitables.
- Les surfaces privilégient la clarté d’atelier et de document : séparateurs visibles et ombres légères plutôt qu’un effet de cartes SaaS glossy.
