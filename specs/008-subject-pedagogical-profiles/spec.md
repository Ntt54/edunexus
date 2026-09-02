# Feature Specification: EduNexus adaptatif — profil pédagogique, parcours explicable, capture de programme & carnet de matière

**Feature Branch**: `008-subject-pedagogical-profiles`

**Created**: 2026-08-27

**Status**: Draft

**Input**: User description: "Analyse le document « Analyse des méthodes d'apprentissage et conception d'un EduNexus adaptatif » et cherche comment l'intégrer au projet. Périmètre retenu (choix utilisateur) : intégrer l'ensemble des recommandations du document dans une seule spécification — profil pédagogique de matière, graphe de compétences, parcours explicable, adaptation après séance, capture de programme par OCR (photo/PDF), import de photo dans les conversations et carnet de matière inspiré de NotebookLM."

## Clarifications

### Session 2026-08-27

- Q: Comment la capture vidéo (images clés) doit-elle être traitée sur une machine CPU modeste ? → A: Retirer la capture vidéo de la spec ; ne conserver que la capture photo/PDF. Ajouter en plus l'import de photo directement dans les conversations de tutorat.
- Q: Combien de photos/pages un utilisateur peut-il capturer en une seule fois pour un programme ? → A: Traitement incrémental photo par photo (file d'attente, statut visible), sans limite stricte.
- Q: Quels éléments du document doivent être exclus du périmètre ? → A: Inclure le multi-utilisateur (famille de plusieurs personnes sur un seul PC) ; laisser le reste (synthèse vocale, aperçus audio/vidéo, fine-tuning, LMS, VR/AR) sans exclusion explicite.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Créer une matière avec un profil pédagogique (Priority: P1)

Un apprenant crée une matière (ex. « Java », « Mathématiques ») et, au lieu d'un simple conteneur de livres, la configure comme un **profil pédagogique explicite** : domaine, niveau (primaire → supérieur), objectif (examen, projet, remise à niveau…), échéance, temps disponible, prérequis connus, compétences visées, style d'explication, activités autorisées et critères de maîtrise. Le formulaire est présenté en deux niveaux : configuration rapide puis options avancées. Des modèles prédéfinis (Programmation, Mathématiques, Sciences expérimentales, SVT, Matière scolaire générale, Langue, Profil libre) préremplissent les activités et types de preuves, mais restent entièrement modifiables.

**Why this priority**: C'est l'apport central du document : sans profil structuré, le moteur ne sait pas *comment* enseigner ni *dans quel ordre*. C'est la fondation de tout le reste.

**Independent Test**: Créer une matière « Java » via le modèle Programmation, vérifier que les activités et preuves sont préremplies, puis les modifier ; vérifier que le profil est persisté et réutilisé par le moteur. Livre un profil de matière exploitable.

**Acceptance Scenarios**:

1. **Given** un utilisateur créant une matière, **When** il choisit le modèle « Programmation », **Then** les activités (exemples résolus, code à trous, Parsons, débogage, mini-projets, tests) et les preuves de maîtrise (écrire, compléter, réordonner, tester, corriger, expliquer du code) sont préremplies et modifiables.
2. **Given** un profil de matière enregistré, **When** l'utilisateur rouvre la matière, **Then** le profil est restauré tel quel.
3. **Given** un utilisateur qui ne connaît pas la pédagogie, **When** il choisit « apprendre Java pour créer des projets », **Then** le système convertit ce choix en paramètres internes (approche orientée projet, importance du débogage, exercices pratiques, progression par prérequis) sans lui demander de connaître la pédagogie.

---

### User Story 2 - Construire un graphe de compétences à partir des livres (Priority: P1)

Le système analyse les tables des matières, titres de chapitres, sections, index et premiers passages des livres importés pour construire un **graphe de compétences** : chaque nœud est une notion/compétence, chaque arête est un prérequis (`requires`), un support d'activité (`supports`), une couverture par une source (`covered_by`) ou une confusion fréquente (`contrasts_with`). Les éléments de plusieurs livres sont fusionnés par identifiant conceptuel, en conservant les références aux livres/chapitres/pages et en détectant les prérequis probables. Le LLM peut proposer des concepts et relations candidates, mais le moteur fonctionne avec des règles simples et chaque proposition conserve sa source et son niveau de confiance.

**Why this priority**: Le graphe est le « centre du système » recommandé par le document ; il rend le parcours déterministe, traçable et explicable au lieu de dépendre d'un LLM.

**Independent Test**: Importer deux livres Java présentant les mêmes concepts dans un ordre différent, vérifier que les concepts sont fusionnés par identifiant, que les prérequis sont détectés et que chaque nœud référence ses sources. Livre un graphe de compétences consultable.

**Acceptance Scenarios**:

1. **Given** deux livres Java couvrant les mêmes notions, **When** le graphe est construit, **Then** les notions communes sont fusionnées en un seul nœud conservant les références aux deux livres.
2. **Given** un graphe construit, **When** on consulte un nœud, **Then** on voit ses prérequis, ses sources (livre/chapitre/page), son niveau de confiance et la raison de sa position.
3. **Given** une proposition générée par le LLM, **When** elle est affichée, **Then** elle est clairement distinguée du texte extrait des livres et soumise à validation.

---

### User Story 3 - Générer un parcours d'apprentissage explicable (Priority: P1)

À partir du graphe de compétences, du diagnostic initial et des objectifs du profil, le système génère un **parcours d'apprentissage ordonné** (ex. pour Java : types/variables → conditions/boucles → méthodes → tableaux/collections → classes/objets → héritage/interfaces → exceptions/entrées-sorties → tests/débogage → projet intégrateur). Chaque étape affiche « pourquoi maintenant », ses prérequis, ses sources, l'activité prévue et la preuve de maîtrise attendue. L'utilisateur peut déplacer, fusionner ou exclure une étape. Le parcours est généré par sélection de nœuds non maîtrisés dont les prérequis sont couverts, suivie d'un ordonnancement topologique — le LLM n'invente pas le programme.

**Why this priority**: C'est la valeur visible pour l'apprenant : un parcours vérifiable et explicable, contrairement à une liste produite une seule fois par un LLM.

**Independent Test**: Avec un profil Java et un graphe construit, générer un parcours et vérifier que chaque étape est justifiée (prérequis, sources, activité, preuve) et que l'ordre respecte les prérequis ; déplacer une étape et vérifier la mise à jour. Livre un parcours explicable et éditable.

**Acceptance Scenarios**:

1. **Given** un graphe et un profil, **When** le parcours est généré, **Then** chaque étape affiche « pourquoi maintenant », prérequis, sources, activité prévue et preuve de maîtrise.
2. **Given** un parcours généré, **When** l'utilisateur déplace, fusionne ou exclut une étape, **Then** le changement est enregistré et le parcours reste cohérent.
3. **Given** un parcours, **When** une étape a des prérequis non maîtrisés, **Then** ces prérequis apparaissent avant elle dans l'ordre.

---

### User Story 4 - Adapter le parcours après chaque séance (Priority: P2)

Après chaque activité, le système met à jour le profil de maîtrise et **révise seulement une fenêtre de quelques étapes** du parcours (pas une régénération complète). La boucle : sélectionner une compétence cible → vérifier les prérequis → choisir une activité selon le type d'erreur → feedback gradué → enregistrer réponse/temps/indices/source → proposer une variante ou une révision espacée → valider la compétence seulement après plusieurs preuves différentes (rappel, exercice guidé, problème de transfert) → régénérer la portion suivante. Une portion de stabilité est conservée : objectifs de séance, notion principale et critère de réussite restent visibles.

**Why this priority**: L'adaptation continue est ce qui rend le tuteur « adaptatif » au sens sérieux du document (ce qui est adapté, à partir de quelles données, selon quelle règle, dans quel but, avec correction humaine).

**Independent Test**: Réussir puis échouer sur une notion, vérifier que seule la fenêtre proche du parcours est recalculée et que la compétence n'est validée qu'après plusieurs preuves différentes. Livre une adaptation locale et stable.

**Acceptance Scenarios**:

1. **Given** une activité terminée, **When** le profil de maîtrise est mis à jour, **Then** seule une fenêtre de quelques étapes du parcours est recalculée, pas l'ensemble.
2. **Given** une compétence, **When** elle n'a qu'une seule preuve de réussite (ex. un QCM), **Then** elle n'est pas encore considérée « maîtrisée » ; elle l'est seulement après plusieurs preuves différentes.
3. **Given** une séance en cours, **When** l'adaptation se produit, **Then** les objectifs de séance, la notion principale et le critère de réussite restent affichés (pas de sur-adaptation instable).

---

### User Story 5 - Consulter le tableau de bord du profil (Priority: P3)

L'utilisateur consulte un tableau de bord de la matière : sources, notions couvertes, notions non couvertes, contradictions entre sources, éléments non confirmés et prochaines activités. Chaque proposition de parcours conserve sa source, sa page/section, son extrait justificatif, sa confiance et son statut de validation. Le système distingue visuellement le texte extrait des livres, la proposition générée par l'IA et l'élément confirmé par l'utilisateur.

**Why this priority**: La transparence et la traçabilité sont des garde-fous centraux du document (anti-hallucination, confiance de l'utilisateur) ; c'est un enrichissement qui s'appuie sur les stories 1–3.

**Independent Test**: Après construction du graphe et génération du parcours, ouvrir le tableau de bord et vérifier la distinction des trois catégories (extrait / généré / confirmé) et l'affichage des notions couvertes vs non couvertes.

**Acceptance Scenarios**:

1. **Given** un graphe et un parcours, **When** l'utilisateur ouvre le tableau de bord, **Then** il voit les notions couvertes, non couvertes, contradictoires et incertaines.
2. **Given** une proposition de parcours, **When** elle est affichée, **Then** elle montre sa source, son extrait justificatif, sa confiance et son statut de validation.
3. **Given** un élément du tableau de bord, **When** il provient d'une source ou d'une génération, **Then** il est visuellement distingué (extrait de livre / généré par IA / confirmé par l'utilisateur).

---

### User Story 6 - Capturer un programme par photo ou PDF (OCR) (Priority: P2)

L'apprenant photographie ou importe en PDF la table des matières / le programme officiel de sa matière (le manuel, l'établissement, le pays ou l'année ne sont pas connus de l'IA). Le système exécute un pipeline local : capture → prétraitement (redressement, recadrage, contraste, suppression des doublons) → OCR (extraction du texte et des titres) → structuration (détection des chapitres, sous-parties, numéros, compétences → arbre de programme éditable) → rapprochement (comparaison avec les livres importés et le profil) → validation (l'étudiant corrige les titres ou confirme les éléments incertains) → planification (création des étapes selon prérequis, échéance et maîtrise). Le système distingue visuellement le texte reconnu dans l'image, l'élément extrait d'un livre et la proposition générée par l'IA ; l'étudiant corrige une erreur d'OCR avant que le parcours soit généré.

**Why this priority**: C'est l'étape 2 du document : elle répond à la limite réelle que l'IA ne connaît pas le programme local. Elle alimente directement le graphe et le parcours (stories 2–3).

**Independent Test**: Importer une photo d'une table des matières, vérifier que le texte est extrait par OCR, structuré en arbre éditable, que les éléments incertains sont signalés et que l'utilisateur peut corriger avant génération du parcours. Livre un programme local validé.

**Acceptance Scenarios**:

1. **Given** une photo ou un PDF d'une table des matières, **When** le pipeline OCR s'exécute, **Then** le texte est extrait localement, structuré en chapitres/sous-parties et affiché comme arbre éditable.
2. **Given** un passage OCR incertain (formule, numéro de chapitre, terme scientifique), **When** il est affiché, **Then** il est signalé comme incertain et l'utilisateur doit le confirmer ou le corriger avant qu'il ne contraigne le parcours.
3. **Given** un programme capturé, **When** il est rapproché des livres importés, **Then** le système montre les correspondances et les éléments non couverts, en distinguant texte OCR / extrait de livre / généré par IA.

---

### User Story 7 - Importer une photo dans une conversation (Priority: P2)

L'apprenant peut importer une photo directement dans une conversation de tutorat (ex. une page de livre, un exercice, un schéma, un énoncé) et le tuteur l'analyse dans le contexte de la matière active. La photo est traitée localement par OCR, le texte reconnu est affiché et confirmable, puis intégré à la conversation comme une source. Le système distingue le texte reconnu dans l'image de la proposition générée par l'IA.

**Why this priority**: L'import de photo dans la conversation étend la capture photo au flux de tutorat quotidien, pas seulement à la création d'un programme ; c'est un usage fréquent et à forte valeur.

**Independent Test**: Dans une conversation, importer une photo d'un énoncé, vérifier que le texte est extrait par OCR, affiché et confirmable, puis que le tuteur répond en s'appuyant sur ce texte. Livre un import de photo conversationnel fonctionnel.

**Acceptance Scenarios**:

1. **Given** une conversation de tutorat, **When** l'utilisateur importe une photo, **Then** le texte est extrait localement par OCR, affiché et confirmable avant d'être utilisé.
2. **Given** une photo importée dans une conversation, **When** le tuteur répond, **Then** il s'appuie sur le texte reconnu et le distingue de sa propre génération.
3. **Given** une photo avec un passage incertain, **When** elle est importée, **Then** le passage est signalé comme incertain et soumis à confirmation.

---

### User Story 8 - Utiliser le carnet de matière (inspiré NotebookLM) (Priority: P3)

À la création d'une matière, l'utilisateur ouvre un **carnet de matière** local : un espace qui regroupe les livres, le programme fourni (photo/PDF), les objectifs, le niveau, les compétences, les notes personnelles et le parcours. Le carnet propose des actions simples : « résumer cette source », « comparer deux chapitres », « créer une fiche », « me questionner sans afficher la réponse », « expliquer avec un exemple », « trouver les prérequis », « créer un parcours » et « vérifier ce qui manque dans mon programme ». Un tableau de bord affiche les sources, les notions couvertes, les contradictions, les éléments non confirmés et les prochaines activités. Les réponses, fiches et exercices sont produits dans le contexte de ce carnet, avec les sources visibles.

**Why this priority**: C'est la synthèse finale du document (NotebookLM pour l'exploration d'un corpus + EduNexus pour l'apprentissage adaptatif) ; c'est un enrichissement qui s'appuie sur toutes les stories précédentes.

**Independent Test**: Ouvrir le carnet d'une matière, ajouter une note personnelle, lancer « résumer cette source » et « me questionner sans afficher la réponse », vérifier que les sorties sont liées aux sources et supprimables. Livre un carnet de matière fonctionnel.

**Acceptance Scenarios**:

1. **Given** un carnet de matière, **When** l'utilisateur ajoute des livres, un programme, des objectifs et des notes, **Then** tout est regroupé dans le carnet et les réponses/fiches/exercices sont produits dans ce contexte avec sources visibles.
2. **Given** le carnet, **When** l'utilisateur lance « me questionner sans afficher la réponse », **Then** le système pose une question de rappel sans révéler la réponse.
3. **Given** une sortie du carnet (fiche, résumé, carte), **When** elle est générée, **Then** elle reste liée aux sources et peut être supprimée.

---

### User Story 9 - Utiliser EduNexus en famille sur un seul PC (multi-utilisateur) (Priority: P2)

Plusieurs membres d'une famille utilisent EduNexus sur le même PC, chacun avec son propre profil d'apprenant. Chaque utilisateur a ses propres matières, profils pédagogiques, graphes, parcours, progression, conversations et carnet de matière. Le système permet de créer/sélectionner un utilisateur au démarrage et de basculer entre eux, sans que les données d'un membre ne se mélangent à celles d'un autre. Les données restent locales sur le PC.

**Why this priority**: Le multi-utilisateur familial est une demande explicite de l'utilisateur ; il garantit que chaque membre conserve un parcours et une progression personnels sur un même appareil.

**Independent Test**: Créer deux utilisateurs, créer une matière distincte pour chacun, vérifier que les matières, parcours et progressions sont isolés et que la bascule entre utilisateurs fonctionne. Livre un usage familial multi-profil.

**Acceptance Scenarios**:

1. **Given** plusieurs utilisateurs créés, **When** un utilisateur se connecte, **Then** il voit uniquement ses propres matières, parcours, progression et conversations.
2. **Given** deux utilisateurs, **When** chacun crée une matière du même nom, **Then** les deux matières restent distinctes et isolées.
3. **Given** un utilisateur actif, **When** il bascule vers un autre utilisateur, **Then** le contexte (matières, progression, carnet) change pour celui du nouvel utilisateur.

---

### Edge Cases

- Que se passe-t-il quand deux livres présentent les mêmes concepts dans un ordre différent ? Les éléments sont fusionnés par identifiant conceptuel, en conservant les références aux deux livres ; l'ordre d'un seul livre n'est pas utilisé comme vérité.
- Que se passe-t-il quand le LLM propose un prérequis non confirmé ? La proposition est marquée « incertaine » et soumise à validation ; elle n'est jamais présentée comme un fait.
- Que se passe-t-il quand une matière n'a aucun livre importé ? Le graphe est vide et le parcours ne peut pas être généré ; le système le dit clairement et invite à importer des sources.
- Que se passe-t-il quand l'utilisateur modifie le profil après la génération du parcours ? Le parcours est recalculé (fenêtre ou complet selon l'ampleur du changement) en conservant les validations manuelles déjà faites.
- Que se passe-t-il quand une compétence échoue plusieurs fois ? Le système conserve une portion de stabilité (objectifs, notion principale, critère) et adapte le niveau d'aide et la variante, sans changer d'activité à chaque erreur.
- Que se passe-t-il quand une source est retirée de la matière ? Les nœuds du graphe qui n'étaient couverts que par cette source sont marqués « non couverts » et le parcours est révisé en conséquence.
- Que se passe-t-il quand une photo de programme est floue, inclinée ou partiellement masquée ? Le système affiche les passages incertains et demande confirmation ; il ne fait jamais passer une reconstruction probabiliste pour le programme officiel.
- Que se passe-t-il quand l'OCR confond une formule, un numéro de chapitre ou un terme scientifique ? Le passage est signalé comme incertain et soumis à validation avant d'être utilisé comme contrainte de parcours.
- Que se passe-t-il quand une table des matières ne correspond pas à une progression pédagogique optimale ? Le parcours utilise la table des matières comme indice, puis vérifie les prérequis dans le contenu et le profil de matière.
- Que se passe-t-il quand une photo est importée dans une conversation avec un passage incertain ? Le passage est signalé comme incertain et soumis à confirmation avant d'être utilisé par le tuteur.
- Que se passe-t-il quand une matière n'a ni livre ni programme capturé ? Le graphe est vide et le parcours ne peut pas être généré ; le système le dit clairement et invite à importer des sources ou à capturer un programme.
- Que se passe-t-il quand deux membres de la famille utilisent le même PC en même temps ? Le système isole les données par profil ; chaque membre ne voit que ses propres matières, parcours et progression, sans mélange.
- Que se passe-t-il quand un membre supprime son profil ? Ses matières, graphes, parcours, progression, conversations et carnet sont supprimés avec lui, sans affecter les autres profils.

## Requirements *(mandatory)*

### Functional Requirements

**Profil pédagogique de matière**

- **FR-001**: System MUST allow creating a subject with a structured pedagogical profile containing at least: domain, teaching level, objective, deadline/pace, available time, known prerequisites, targeted competencies, explanation style, allowed activities, and mastery criteria.
- **FR-002**: System MUST present the profile form in two levels: quick configuration (identity, level, domain, objective) and advanced options (prerequisites, competencies, style, activities, evaluation, sources, constraints).
- **FR-003**: System MUST provide predefined pedagogical templates (at minimum: Programmation, Mathématiques, Sciences expérimentales, SVT, Matière scolaire générale, Langue, Profil libre) that prefill activities and proof types but remain fully editable.
- **FR-004**: System MUST convert plain-language goals (e.g., "apprendre Java pour créer des projets") into internal pedagogical parameters without requiring the user to know pedagogy.
- **FR-005**: System MUST persist the subject profile and restore it when the subject is reopened.
- **FR-006**: System MUST NOT let the profile lock the subject into one approach; the template is a starting point, not a constraint.

**Graphe de compétences**

- **FR-007**: System MUST build a competency graph from imported books (tables of contents, chapter titles, sections, index, opening passages), where each node is a notion/competency and edges represent `requires` (prerequisite), `supports` (activity), `covered_by` (source), and `contrasts_with` (frequent confusion).
- **FR-008**: System MUST merge concepts from multiple books by conceptual identifier, preserving references to each book/chapter/page and detecting probable prerequisites.
- **FR-009**: System MUST associate a mastery score with each node for the learner.
- **FR-010**: System MUST keep every graph proposition with its source and confidence level; LLM-generated proposals MUST be clearly distinguished from text extracted from books and MUST be subject to validation.
- **FR-011**: System MUST be able to build and order the graph with deterministic rules (not solely dependent on the LLM).

**Parcours explicable**

- **FR-012**: System MUST generate an ordered learning path from the graph, the initial diagnostic, and the profile objectives, by selecting non-mastered nodes whose prerequisites are sufficiently covered, followed by topological ordering.
- **FR-013**: System MUST display for each path step: "why now", prerequisites, sources, planned activity, and expected proof of mastery.
- **FR-014**: System MUST allow the user to move, merge, or exclude a path step, and persist these edits.
- **FR-015**: System MUST NOT let the LLM decide the program alone or invent unconfirmed prerequisites.

**Adaptation après séance**

- **FR-016**: System MUST update the mastery profile after each activity and recompute only a window of a few path steps, not the whole path.
- **FR-017**: System MUST validate a competency as "mastered" only after several different proofs (e.g., recall question, guided exercise, transfer problem), not a single one.
- **FR-018**: System MUST record for each activity the answer, time, hints used, and source.
- **FR-019**: System MUST preserve a stability portion during adaptation: session objectives, main notion, and success criterion remain visible.

**Tableau de bord & traçabilité**

- **FR-020**: System MUST provide a subject dashboard showing covered notions, uncovered notions, contradictions between sources, unconfirmed elements, and next activities.
- **FR-021**: System MUST visually distinguish three categories: text extracted from a book, AI-generated proposal, and user-confirmed element.
- **FR-022**: System MUST keep for every path proposal its source, page/section, justifying excerpt, confidence, and validation status.

**Capture de programme par OCR (photo/PDF)**

- **FR-023**: System MUST accept a photo or PDF of a program/table of contents and run a local pipeline: capture → preprocessing (deskew, crop, contrast, duplicate removal) → OCR (text and title extraction) → structuring (chapters, sub-parts, numbers, competencies → editable program tree) → reconciliation (comparison with imported books and profile) → validation (user corrects titles or confirms uncertain elements) → planning (path steps by prerequisites, deadline, mastery). Processing MUST be incremental, photo by photo, via a queue with visible status, without a strict limit on the number of photos.
- **FR-024**: System MUST run OCR locally (no cloud service) and keep captured images and recognized text local.
- **FR-025**: System MUST flag uncertain OCR passages (formulas, chapter numbers, scientific terms) and require user confirmation before they constrain the path.
- **FR-026**: System MUST visually distinguish three categories in the captured program: text recognized in the image, element extracted from a book, and AI-generated proposal.
- **FR-027**: System MUST let the user correct an OCR error before the path is generated.
- **FR-028**: System MUST use the table of contents as a hint, then verify prerequisites in the content and profile, rather than treating the table of contents as the optimal progression.

**Import de photo dans une conversation**

- **FR-029**: System MUST allow importing a photo directly into a tutoring conversation, process it locally by OCR, display the recognized text for confirmation, and use it as a source in the conversation.
- **FR-030**: System MUST distinguish in the conversation the text recognized in the image from the AI-generated response.
- **FR-031**: System MUST flag uncertain OCR passages in a conversation photo and require confirmation before the tutor relies on them.

**Carnet de matière (inspiré NotebookLM)**

- **FR-032**: System MUST provide a subject notebook that groups books, captured program, objectives, level, competencies, personal notes, and the learning path.
- **FR-033**: System MUST offer notebook actions including at least: "summarize this source", "compare two chapters", "create a study sheet", "quiz me without showing the answer", "explain with an example", "find prerequisites", "create a path", and "check what's missing in my program".
- **FR-034**: System MUST produce answers, study sheets, and exercises in the context of the notebook, with visible sources.
- **FR-035**: System MUST keep notebook outputs linked to their sources and deletable.
- **FR-036**: System MUST NOT treat any AI-generated synthesis as automatically trustworthy; outputs must show provenance and allow opening the source passage.

**Multi-utilisateur familial**

- **FR-037**: System MUST support multiple learner profiles on a single machine, each with its own subjects, pedagogical profiles, graphs, paths, progression, conversations, and notebook.
- **FR-038**: System MUST allow creating, selecting, and switching between learner profiles, isolating each member's data from the others.
- **FR-039**: System MUST keep all learner data local on the machine (no cloud account required).

### Key Entities *(include if feature involves data)*

- **SubjectProfile (Profil de matière)**: Structured pedagogical configuration of a subject (domain, level, objective, deadline, prerequisites, competencies, style, activities, mastery criteria, constraints).
- **PedagogicalTemplate (Modèle pédagogique)**: Predefined profile preset (Programmation, Mathématiques, etc.) that prefills activities and proof types.
- **CompetencyNode (Nœud de compétence)**: A notion/competency in the graph, with mastery score, sources, confidence, and validation status.
- **GraphEdge (Arête du graphe)**: A relation between nodes: `requires`, `supports`, `covered_by`, `contrasts_with`.
- **LearningPath (Parcours)**: Ordered sequence of path steps generated from the graph, diagnostic, and profile.
- **PathStep (Étape de parcours)**: A step with "why now", prerequisites, sources, planned activity, and expected proof of mastery.
- **MasteryProof (Preuve de maîtrise)**: A recorded evidence of mastery (recall, guided exercise, transfer problem) with answer, time, hints, and source.
- **SourceReference (Référence de source)**: Book/chapter/page reference with justifying excerpt and confidence.
- **CapturedProgram (Programme capturé)**: A program/table of contents captured from photo or PDF, with recognized text, structure, and validation status.
- **ProgramNode (Nœud de programme)**: A chapter/sub-part/competency in the captured program tree, with origin (OCR / book / AI-generated) and validation status.
- **CaptureImage (Image de capture)**: A retained sharp page from a photo or PDF, with preprocessing state.
- **ConversationPhoto (Photo de conversation)**: A photo imported into a tutoring conversation, with recognized text, confirmation status, and source linkage.
- **SubjectNotebook (Carnet de matière)**: A local space grouping books, captured program, objectives, level, competencies, notes, and path.
- **NotebookOutput (Sortie de carnet)**: A generated artifact (summary, study sheet, quiz, comparison) linked to its sources and deletable.
- **LearnerProfile (Profil d'apprenant)**: A family member's profile on a single machine, owning its own subjects, graphs, paths, progression, conversations, and notebook.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can create a subject with a complete pedagogical profile in under 3 minutes using a predefined template.
- **SC-002**: Importing two books covering the same concepts produces a graph where shared concepts are merged into single nodes with references to both books.
- **SC-003**: Generating a learning path from a graph and profile completes in under 5 seconds on the target modest hardware.
- **SC-004**: Every generated path step displays "why now", prerequisites, sources, planned activity, and expected proof of mastery.
- **SC-005**: After an activity, only a window of a few path steps is recomputed, never the entire path.
- **SC-006**: A competency is marked "mastered" only after at least two different proof types, never after a single proof.
- **SC-007**: The dashboard distinguishes covered, uncovered, contradictory, and unconfirmed notions, and visually separates extracted / AI-generated / user-confirmed elements.
- **SC-008**: The complete feature operates locally on a CPU-only machine with 8 GB RAM without exhausting memory or requiring network access.
- **SC-009**: Capturing a program from a photo or PDF produces an editable program tree with uncertain OCR passages flagged, in under 30 seconds on the target hardware.
- **SC-010**: A captured program can be corrected by the user before it constrains the path, and the correction is persisted.
- **SC-011**: A user can import a photo into a tutoring conversation, see the OCR text confirmed, and receive a tutor response that relies on that text and distinguishes it from the AI's own generation.
- **SC-012**: A user can open a subject notebook, add a note, and run at least two notebook actions (e.g., summarize a source, quiz without showing the answer) with outputs linked to sources and deletable.
- **SC-013**: A family can create multiple learner profiles on one machine, and each member sees only their own subjects, paths, progression, and conversations after switching.

## Assumptions

- The feature reuses the existing subject/document/ingestion infrastructure from spec 004 (subjects, books, chunks, embeddings, RAG) and the existing progression/diagnostic data; it does not rebuild them.
- The competency graph is built primarily from extractable text (tables of contents, titles, sections, index) using deterministic rules; the LLM is used only to propose concepts/relations and generate activities, always subject to validation.
- Program capture (photo/PDF) and photo import in conversations run entirely locally (OCR, preprocessing) with no cloud service, consistent with the project's local-first and low-spec (≤8 GB RAM) constraints.
- Video capture is out of scope for this feature (user decision 2026-08-27); only photo/PDF capture and photo import in conversations are covered.
- The "carnet de matière" (NotebookLM-inspired subject notebook) is implemented as a local space reusing the existing sources, RAG, and progression infrastructure; it does not introduce cloud services.
- Multi-user is limited to a family on a single machine (multiple local learner profiles), with no accounts, no remote authentication, and no concurrent multi-user concurrency; all data stays local.
- Mastery is a continuous score per node, consistent with spec 004's per-notion mastery model.
- The feature follows the constitution: core logic lives in UI-agnostic services under `src/ollama_tutor/tutor/` (no fastapi/textual imports), the web layer is a thin transport, and all tests run offline via `httpx.MockTransport`.
