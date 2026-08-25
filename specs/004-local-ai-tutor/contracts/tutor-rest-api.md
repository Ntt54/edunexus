# Contract: Tutor REST API (`/api/tutor/*`)

Same-origin guard applies to every mutating route (POST/PUT/PATCH/DELETE), as
for existing endpoints. JSON bodies validated by pydantic models. Errors use
HTTPException codes: 400 validation/import failure, 404 unknown id, 409
conflicting state (e.g., indexing already running).

## Subjects

```http
GET    /api/tutor/subjects                       → [{id,name,active,books,concepts}]
POST   /api/tutor/subjects            {name}     → Subject          # 400 empty/duplicate
POST   /api/tutor/subjects/{id}/rename {name}    → Subject
DELETE /api/tutor/subjects/{id}                  → 204              # cascades
POST   /api/tutor/subjects/{id}/select           → Subject          # sets active subject
```

## Documents & indexing

```http
GET    /api/tutor/subjects/{id}/books            → [Book] incl. status+progress
POST   /api/tutor/subjects/{id}/books {path}     → Book             # starts background indexing
       # multipart alternative: file upload field "file" (stored under
       # ~/.config/ollama-tui/tutor/uploads/)
DELETE /api/tutor/subjects/{sid}/books/{bid}     → 204
POST   /api/tutor/books/{bid}/cancel             → Book             # back to pending
```

Book shape: `{id,title,format,status,error,chunks_done,chunks_total,fingerprint}`.
Status lifecycle per data-model.md (`pending→indexing→done|failed`).

## Concepts, progress, path

```http
GET    /api/tutor/subjects/{id}/progress         → [{concept,score,label,path_rank}]
PUT    /api/tutor/subjects/{id}/path             {order:[concept_id,…]} → [...]
GET    /api/tutor/subjects/{id}/gaps             → [{concept,score,recent_failures}]
```

`label` ∈ non étudié | faible | moyen | maîtrisé (derived, D7).

## Practice

```http
POST   /api/tutor/exercises {concept_id,difficulty} → Exercise (no solution field)
POST   /api/tutor/exercises/{id}/answers {answer}   → {verdict,feedback,hint_level,hint?}
POST   /api/tutor/exercises/{id}/solution           → {solution}      # explicit only
```

## Revision

```http
POST   /api/tutor/subjects/{id}/prepare            → PrepareReport{flashcards_new,glossary_new,concepts_new,skipped}
GET    /api/tutor/subjects/{id}/reviews/due        → [Flashcard]     # no LLM call
POST   /api/tutor/reviews/{flashcard_id}/grade {success} → {next_due,streak_index}
```

## Quizzes & exams

```http
POST   /api/tutor/subjects/{id}/quizzes {size,kinds}         → Quiz (questions without answers)
POST   /api/tutor/subjects/{id}/exams {size,time_limit_s}    → ExamSession
POST   /api/tutor/quizzes/{id}/submit {answers}              → QuizReport{score,strengths,weaknesses}
GET    /api/tutor/quizzes/{id}                               → Quiz + report when completed
```

Exam rules enforced server-side: no hint/solution routes ever succeed for
`kind="exam"` quizzes (409), expiry auto-submits unanswered as incorrect.

## Sessions & knowledge tools

```http
GET    /api/tutor/subjects/{id}/sessions            → [TutoringSession]
POST   /api/tutor/sessions/{id}/close               → SessionSummary
GET    /api/tutor/subjects/{id}/resume              → ResumeBriefing{last_topic,difficulties,proposal}
GET    /api/tutor/subjects/{id}/locate {notion}     → [{book,chapter,page,score}]   # FR-031
GET    /api/tutor/subjects/{id}/rank-books {notion} → [{book,score}]                # FR-032
POST   /api/tutor/subjects/{id}/compare {notion}    → streamed via WS ask (FR-033)
GET    /api/tutor/subjects/{id}/glossary            → [GlossaryTerm]
GET    /api/tutor/subjects/{id}/map                 → {nodes:[Concept],edges:[relation]}  # FR-035
```

## Static surface

```http
GET /tutor → web/static/tutor.html   # 404 with activation hint when tutor.enabled=false
```

(mirrors the `/agent` gating pattern).
